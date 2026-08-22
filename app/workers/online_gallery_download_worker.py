import inspect
import os
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Lock

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_DIRECT,
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    DOWNLOAD_MODE_STANDARD,
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_DOWNLOADING,
    ORIGINAL_STATE_FAILED,
    ORIGINAL_STATE_PAUSED,
    ORIGINAL_STATE_STAGED,
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_DOWNLOADING,
    ONLINE_DOWNLOAD_FAILED,
    ONLINE_DOWNLOAD_PAUSED,
    GalleryOriginalState,
    OnlineGalleryDownloadRecord,
    ORIGINAL_PAGE_MODE_BASE,
    ORIGINAL_PAGE_MODE_ORIGINAL,
    normalize_original_page_modes,
)
from app.domain.online_gallery import (
    OnlineGalleryPreviewPage,
    gallery_preview_page_count,
    gallery_preview_page_number,
)
from app.repositories.ehviewer_download_repository import (
    EH_STATE_FAILED,
    EH_STATE_FINISHED,
)
from app.services.online_download_builder import online_detail_metadata
from app.sources.eh_online_source import OriginalImageUnavailableError


class OnlineDownloadRegistrationSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(int, str)


class OnlineDownloadRegistrationWorker(QRunnable):
    """Run local download registration without blocking the GUI thread."""

    def __init__(self, gid, operation):
        super().__init__()
        self.gid = int(gid)
        self.operation = operation
        self.cancelled = False
        self.signals = OnlineDownloadRegistrationSignals()

    def run(self):
        if self.cancelled:
            return
        try:
            result = self.operation()
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(self.gid, str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.succeeded.emit(result)
            except RuntimeError:
                pass


class OnlineGalleryDownloadSignals(QObject):
    stageChanged = Signal(str)
    progressChanged = Signal(int, int)
    speedChanged = Signal(float)
    galleryRegistered = Signal(int, str)
    sidecarReady = Signal(int, str)
    pageSaved = Signal(int, int, str, int, int)
    originalPageSaved = Signal(int, int, str, int, int)
    completed = Signal(int, str)
    failed = Signal(int, str)
    paused = Signal(int)


class _DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class _PageDownloadResult:
    page_index: int
    page_mode: str = ""
    page_path: str = ""
    error: Exception = None


class _GalleryPageDownloadTask:
    def __init__(
        self,
        worker,
        preview,
        page_index,
        target_folder,
        result_queue,
        total,
    ):
        self.worker = worker
        self.preview = preview
        self.page_index = int(page_index)
        self.target_folder = target_folder
        self.result_queue = result_queue
        self.total = int(total)
        self._result_lock = Lock()
        self._result_sent = False

    def run(self):
        try:
            self.worker._check_cancelled()
            self.worker.signals.stageChanged.emit(
                f"正在下载第 {self.page_index + 1} / {self.total} 页…"
            )
            started_at = time.monotonic()
            speed_was_reported = False

            def report_speed(speed):
                nonlocal speed_was_reported
                speed_was_reported = True
                self.worker._update_page_speed(self.page_index, speed)

            data, page_mode = self.worker._download_page(
                self.preview,
                self.page_index,
                report_speed,
                self.total,
                speed_key=self.page_index,
            )
            elapsed = max(0.001, time.monotonic() - started_at)
            if not speed_was_reported:
                self.worker._update_page_speed(
                    self.page_index,
                    len(data) / elapsed,
                )
            page_path = self.worker.ehviewer_repository.write_page(
                self.target_folder,
                self.page_index,
                _image_extension(data),
                data,
            )
            self._sendResult(
                _PageDownloadResult(
                    self.page_index,
                    page_mode,
                    str(page_path),
                )
            )
            return True
        except Exception as error:
            self._sendResult(
                _PageDownloadResult(self.page_index, error=error)
            )
            return False
        finally:
            self.worker._clear_page_speed(self.page_index)

    def cancelPending(self):
        self._sendResult(
            _PageDownloadResult(
                self.page_index,
                error=_DownloadCancelled(),
            )
        )

    def _sendResult(self, result):
        with self._result_lock:
            if self._result_sent:
                return
            self._result_sent = True
        self.result_queue.put(result)


class OnlineGalleryDownloadWorker(QRunnable):
    """Persist metadata, then resume pages through the shared page scheduler."""

    def __init__(
        self,
        provider,
        detail,
        cover_data,
        gallery_cache,
        ehviewer_repository,
        user_repository,
        site,
        target_label="",
        download_mode=DOWNLOAD_MODE_STANDARD,
        existing_folder=None,
        retry_count=3,
        page_download_scheduler=None,
    ):
        super().__init__()
        self.provider = provider
        self.detail = detail
        self.cover_data = bytes(cover_data or b"")
        self.gallery_cache = gallery_cache
        self.ehviewer_repository = ehviewer_repository
        self.user_repository = user_repository
        self.site = str(site)
        self.target_label = str(target_label or "").strip()
        self.download_mode = str(download_mode or DOWNLOAD_MODE_STANDARD)
        if self.download_mode not in {
            DOWNLOAD_MODE_STANDARD,
            DOWNLOAD_MODE_ORIGINAL_DIRECT,
            DOWNLOAD_MODE_ORIGINAL_LOCAL,
        }:
            raise ValueError("未知的画廊下载模式")
        self.existing_folder = Path(existing_folder) if existing_folder else None
        self.retry_count = max(1, int(retry_count))
        self.page_download_scheduler = page_download_scheduler
        self._page_download_key = (int(detail.gallery.gid), id(self))
        self.cancelled = False
        self._smoothed_speed = 0.0
        self._page_speeds = {}
        self._speed_lock = Lock()
        self._page_modes = []
        self._page_state_lock = Lock()
        self._completed_pages = 0
        self._available_page_indexes = set()
        self._available_page_lock = Lock()
        self.signals = OnlineGalleryDownloadSignals()

    def cancel(self):
        self.cancelled = True
        if self.page_download_scheduler is not None:
            self.page_download_scheduler.cancel(self._page_download_key)
        cancel_requests = getattr(self.provider, "cancel_pending_requests", None)
        if cancel_requests is not None:
            cancel_requests()

    def markPageAvailable(self, page_index):
        with self._available_page_lock:
            self._available_page_indexes.add(int(page_index))

    def _pageWasMadeAvailable(self, page_index):
        with self._available_page_lock:
            return int(page_index) in self._available_page_indexes

    def run(self):
        gid = int(self.detail.gallery.gid)
        folder = None
        completed_pages = 0
        try:
            self._check_cancelled()
            self.signals.stageChanged.emit("正在保存画廊信息与评论…")
            if self.download_mode == DOWNLOAD_MODE_ORIGINAL_LOCAL:
                folder = self._validated_existing_folder()
                dirname = str(
                    folder.relative_to(
                        self.ehviewer_repository.manga_root.resolve()
                    )
                )
            else:
                dirname, folder = self.ehviewer_repository.prepare_download(
                    self.detail,
                    self.target_label,
                )
            previous = (
                self.user_repository.gallery_original_state(gid)
                if self._is_original_download
                else None
            )
            total = int(self.detail.page_count)
            self._page_modes = list(
                normalize_original_page_modes(
                    previous.page_modes if previous is not None else (),
                    total,
                    previous.completed_pages if previous is not None else 0,
                    previous.fallback_to_standard if previous is not None else False,
                )
            )
            target_folder = self._target_folder(folder)
            existing_indexes = self._existing_page_indexes(target_folder)
            if self._is_original_download:
                completed_indexes = {
                    index
                    for index in existing_indexes
                    if index < len(self._page_modes) and self._page_modes[index]
                }
            else:
                completed_indexes = existing_indexes
            completed_pages = len(completed_indexes)
            self._completed_pages = completed_pages
            record = OnlineGalleryDownloadRecord(
                gid=gid,
                site=self.site,
                token=self.detail.gallery.token,
                title=self.detail.title,
                dirname=dirname,
                page_count=int(self.detail.page_count),
                completed_pages=completed_pages,
                state=ONLINE_DOWNLOAD_DOWNLOADING,
                download_mode=self.download_mode,
                metadata=online_detail_metadata(
                    self.detail,
                    self.target_label,
                ),
            )
            self.user_repository.save_online_gallery_download(
                record,
                self.detail.comments,
            )
            if self._is_original_download:
                self.user_repository.save_gallery_original_state(
                    GalleryOriginalState(
                        gid=gid,
                        site=self.site,
                        token=self.detail.gallery.token,
                        dirname=dirname,
                        mode=self.download_mode,
                        state=ORIGINAL_STATE_DOWNLOADING,
                        completed_pages=completed_pages,
                        page_count=int(self.detail.page_count),
                        fallback_to_standard=(
                            ORIGINAL_PAGE_MODE_BASE in self._page_modes
                        ),
                        page_modes=tuple(self._page_modes),
                        metadata=online_detail_metadata(
                            self.detail,
                            self.target_label,
                        ),
                        created_at=previous.created_at if previous else 0,
                    )
                )
            self.signals.progressChanged.emit(
                completed_pages, int(self.detail.page_count)
            )

            if self.download_mode != DOWNLOAD_MODE_ORIGINAL_LOCAL:
                self._save_thumbnail(folder)
                self.signals.galleryRegistered.emit(gid, str(folder))
            self.signals.stageChanged.emit("正在获取全部页面 ID…")
            previews = self._load_all_previews()
            page_tokens = {
                index: preview.page_token for index, preview in previews.items()
            }
            if self.download_mode != DOWNLOAD_MODE_ORIGINAL_LOCAL:
                self.ehviewer_repository.write_spider_info(
                    folder, self.detail, page_tokens
                )
                self.signals.sidecarReady.emit(gid, str(folder))

            if self.page_download_scheduler is None:
                completed_pages = self._download_pages_sequential(
                    gid,
                    previews,
                    target_folder,
                    completed_indexes,
                    total,
                )
            else:
                completed_pages = self._download_pages_parallel(
                    gid,
                    previews,
                    target_folder,
                    completed_indexes,
                    total,
                )

            if self.download_mode != DOWNLOAD_MODE_ORIGINAL_LOCAL:
                self.ehviewer_repository.mark_state(gid, EH_STATE_FINISHED)
            self.user_repository.update_online_download(
                gid,
                total,
                ONLINE_DOWNLOAD_COMPLETED,
            )
            if self._is_original_download:
                self.user_repository.update_gallery_original_state(
                    gid,
                    (
                        ORIGINAL_STATE_ACTIVE
                        if self.download_mode == DOWNLOAD_MODE_ORIGINAL_DIRECT
                        else ORIGINAL_STATE_STAGED
                    ),
                    total,
                    total,
                    fallback_to_standard=(
                        ORIGINAL_PAGE_MODE_BASE in self._page_modes
                    ),
                    page_modes=tuple(self._page_modes),
                )
            original_count = self._page_modes.count(ORIGINAL_PAGE_MODE_ORIGINAL)
            base_count = self._page_modes.count(ORIGINAL_PAGE_MODE_BASE)
            self.signals.stageChanged.emit(
                f"下载完成（{original_count} 张原图，{base_count} 张基础图）"
                if self._is_original_download and base_count
                else "下载完成"
            )
            self.signals.progressChanged.emit(total, total)
            self.signals.completed.emit(gid, str(folder))
        except _DownloadCancelled:
            if self.download_mode != DOWNLOAD_MODE_ORIGINAL_LOCAL:
                self.ehviewer_repository.mark_state(gid, EH_STATE_FAILED)
            record = self.user_repository.online_gallery_download(gid)
            if record is not None:
                self.user_repository.update_online_download(
                    gid,
                    max(
                        completed_pages,
                        self._completed_pages,
                        int(record.completed_pages),
                    ),
                    ONLINE_DOWNLOAD_PAUSED,
                )
            if self._is_original_download:
                self.user_repository.update_gallery_original_state(
                    gid,
                    ORIGINAL_STATE_PAUSED,
                    max(
                        completed_pages,
                        self._completed_pages,
                        int(record.completed_pages),
                    )
                    if record is not None else completed_pages,
                    int(self.detail.page_count),
                )
            self.signals.paused.emit(gid)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            if self.download_mode != DOWNLOAD_MODE_ORIGINAL_LOCAL:
                self.ehviewer_repository.mark_state(gid, EH_STATE_FAILED)
            record = self.user_repository.online_gallery_download(gid)
            if record is not None:
                self.user_repository.update_online_download(
                    gid,
                    max(
                        completed_pages,
                        self._completed_pages,
                        int(record.completed_pages),
                    ),
                    ONLINE_DOWNLOAD_FAILED,
                    message,
                )
            if self._is_original_download:
                self.user_repository.update_gallery_original_state(
                    gid,
                    ORIGINAL_STATE_FAILED,
                    max(
                        completed_pages,
                        self._completed_pages,
                        int(record.completed_pages),
                    )
                    if record is not None else completed_pages,
                    int(self.detail.page_count),
                    message,
                )
            self.signals.failed.emit(gid, message)

    @property
    def _is_original_download(self):
        return self.download_mode in {
            DOWNLOAD_MODE_ORIGINAL_DIRECT,
            DOWNLOAD_MODE_ORIGINAL_LOCAL,
        }

    def _download_pages_sequential(
        self,
        gid,
        previews,
        target_folder,
        completed_indexes,
        total,
    ):
        for index in range(total):
            self._check_cancelled()
            externally_available = self._pageWasMadeAvailable(index)
            if index in completed_indexes or (
                externally_available and not self._is_original_download
            ):
                completed_indexes.add(index)
                continue
            self.signals.stageChanged.emit(
                f"正在下载第 {index + 1} / {total} 页…"
            )
            started_at = time.monotonic()
            speed_was_reported = False

            def report_speed(speed):
                nonlocal speed_was_reported
                speed_was_reported = True
                self._update_speed(speed)

            data, page_mode = self._download_page(
                previews[index],
                index,
                report_speed,
                total,
            )
            elapsed = max(0.001, time.monotonic() - started_at)
            if not speed_was_reported:
                self._update_speed(len(data) / elapsed)
            page_path = self.ehviewer_repository.write_page(
                target_folder,
                index,
                _image_extension(data),
                data,
            )
            self._commit_downloaded_page(
                gid,
                index,
                page_mode,
                page_path,
                completed_indexes,
                total,
            )
        return len(completed_indexes)

    def _download_pages_parallel(
        self,
        gid,
        previews,
        target_folder,
        completed_indexes,
        total,
    ):
        pending_indexes = []
        for index in range(total):
            externally_available = self._pageWasMadeAvailable(index)
            if index in completed_indexes or (
                externally_available and not self._is_original_download
            ):
                completed_indexes.add(index)
            else:
                pending_indexes.append(index)
        if not pending_indexes:
            return len(completed_indexes)

        self.signals.stageChanged.emit(
            f"正在并行下载 {len(pending_indexes)} 个缺失页面…"
        )
        results = Queue()
        self.page_download_scheduler.submitMany(
            self._page_download_key,
            (
                _GalleryPageDownloadTask(
                    self,
                    previews[index],
                    index,
                    target_folder,
                    results,
                    total,
                )
                for index in pending_indexes
            ),
        )

        first_error = None
        for _ in pending_indexes:
            result = results.get()
            if result.error is not None:
                if (
                    first_error is None
                    and not isinstance(result.error, _DownloadCancelled)
                ):
                    first_error = result.error
                continue
            self._commit_downloaded_page(
                gid,
                result.page_index,
                result.page_mode,
                Path(result.page_path),
                completed_indexes,
                total,
            )
        if self.cancelled:
            raise _DownloadCancelled()
        if first_error is not None:
            raise first_error
        return len(completed_indexes)

    def _commit_downloaded_page(
        self,
        gid,
        index,
        page_mode,
        page_path,
        completed_indexes,
        total,
    ):
        completed_indexes.add(index)
        completed_pages = len(completed_indexes)
        self._completed_pages = completed_pages
        self.user_repository.update_online_download(
            gid,
            completed_pages,
            ONLINE_DOWNLOAD_DOWNLOADING,
        )
        if self._is_original_download:
            with self._page_state_lock:
                self._page_modes[index] = page_mode
                self.user_repository.update_gallery_original_state(
                    gid,
                    ORIGINAL_STATE_DOWNLOADING,
                    completed_pages,
                    total,
                    fallback_to_standard=(
                        ORIGINAL_PAGE_MODE_BASE in self._page_modes
                    ),
                    page_modes=tuple(self._page_modes),
                )
            signal = (
                self.signals.originalPageSaved
                if self.download_mode == DOWNLOAD_MODE_ORIGINAL_LOCAL
                else self.signals.pageSaved
            )
        else:
            signal = self.signals.pageSaved
        signal.emit(
            gid,
            index,
            str(page_path),
            completed_pages,
            total,
        )
        self.signals.progressChanged.emit(completed_pages, total)

    def _validated_existing_folder(self):
        if self.existing_folder is None or not self.existing_folder.is_dir():
            raise FileNotFoundError("找不到本地画廊目录，无法下载原图")
        root = self.ehviewer_repository.manga_root.resolve()
        folder = self.existing_folder.resolve()
        if os.path.commonpath((str(root), str(folder))) != str(root):
            raise ValueError("本地画廊目录超出配置的漫画根目录")
        return folder

    def _target_folder(self, folder):
        folder = Path(folder)
        if self.download_mode == DOWNLOAD_MODE_ORIGINAL_LOCAL:
            target = folder / "original"
            target.mkdir(parents=False, exist_ok=True)
            return target
        return folder

    def _save_thumbnail(self, folder):
        data = self.cover_data
        if not data and self.detail.cover_url:
            data = self._retry(
                lambda: self._provider_call(
                    "load_thumbnail", self.detail.cover_url
                ),
                "封面",
            )
        if data and not QImage.fromData(data).isNull():
            self.ehviewer_repository.write_thumbnail(folder, data)

    def _load_all_previews(self):
        page_count = int(self.detail.page_count)
        existing = {
            int(preview.page_index): preview
            for preview in self.detail.previews
            if preview.page_token
        }
        if set(existing) == set(range(page_count)):
            return existing
        preview_pages = gallery_preview_page_count(
            self.detail.gallery, page_count
        )
        previews = {}
        for page_number in range(1, preview_pages + 1):
            self._check_cancelled()
            if len(previews) >= page_count:
                break
            page = self.gallery_cache.get_preview_page(
                self.site, self.detail.gallery, page_number
            )
            if page is None and page_number == 1 and self.detail.previews:
                page = OnlineGalleryPreviewPage(
                    gallery=self.detail.gallery,
                    page_number=1,
                    page_count=preview_pages,
                    items=self.detail.previews,
                )
            if page is None:
                page = self._retry(
                    lambda current=page_number: self._provider_call(
                        "load_gallery_preview_page", self.detail.gallery, current
                    ),
                    f"预览第 {page_number} 页",
                )
            self.gallery_cache.put_preview_page(self.site, page)
            for preview in page.items:
                index = int(preview.page_index)
                if 0 <= index < page_count and preview.page_token:
                    previews[index] = preview
        expected = set(range(page_count))
        if set(previews) != expected:
            missing = len(expected.difference(previews))
            raise ValueError(f"画廊页面 ID 不完整，缺少 {missing} 页")
        return previews

    def _retry(self, operation, label, speed_key=None):
        last_error = None
        for attempt in range(1, self.retry_count + 1):
            self._check_cancelled()
            try:
                return operation()
            except OriginalImageUnavailableError:
                raise
            except Exception as error:
                last_error = error
                if speed_key is None:
                    self._smoothed_speed = 0.0
                else:
                    self._clear_page_speed(speed_key)
                self._check_cancelled()
                if attempt >= self.retry_count:
                    break
                self.signals.stageChanged.emit(
                    f"{label}请求失败，正在重试（{attempt + 1}/{self.retry_count}）…"
                )
                self._cancelable_wait(attempt)
        raise last_error

    def _download_page(
        self,
        preview,
        index,
        progress_callback=None,
        total=0,
        speed_key=None,
    ):
        page_mode = ""
        if self._is_original_download:
            with self._page_state_lock:
                page_mode = self._page_modes[index]
        if not self._is_original_download or page_mode == ORIGINAL_PAGE_MODE_BASE:
            method = "load_gallery_page_image"
            page_mode = ORIGINAL_PAGE_MODE_BASE
            data = self._retry(
                lambda: self._provider_call(
                    method,
                    self.detail.gallery,
                    preview,
                    progress_callback=progress_callback,
                ),
                f"第 {index + 1} 页",
                speed_key=speed_key,
            )
        else:
            try:
                data = self._retry(
                    lambda: self._provider_call(
                        "load_gallery_page_original",
                        self.detail.gallery,
                        preview,
                        progress_callback=progress_callback,
                    ),
                    f"第 {index + 1} 页原图",
                    speed_key=speed_key,
                )
                page_mode = ORIGINAL_PAGE_MODE_ORIGINAL
            except OriginalImageUnavailableError:
                page_mode = ORIGINAL_PAGE_MODE_BASE
                self._checkpoint_original_fallback(index, total)
                self.signals.stageChanged.emit(
                    f"第 {index + 1} 页没有原图，正在下载基础图…"
                )
                data = self._retry(
                    lambda: self._provider_call(
                        "load_gallery_page_image",
                        self.detail.gallery,
                        preview,
                        progress_callback=progress_callback,
                    ),
                    f"第 {index + 1} 页基础图",
                    speed_key=speed_key,
                )
        if not data or QImage.fromData(data).isNull():
            raise ValueError(f"第 {index + 1} 页不是有效图片")
        return data, page_mode

    def _provider_call(self, method_name, *args, progress_callback=None):
        method = getattr(self.provider, method_name)
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        keywords = {}
        if "should_cancel" in parameters:
            keywords["should_cancel"] = lambda: self.cancelled
        if progress_callback is not None and "progress_callback" in parameters:
            keywords["progress_callback"] = progress_callback
        return method(*args, **keywords)

    def _update_speed(self, current_speed):
        current_speed = max(0.0, float(current_speed or 0))
        self._smoothed_speed = (
            current_speed
            if self._smoothed_speed <= 0
            else self._smoothed_speed * 0.65 + current_speed * 0.35
        )
        self.signals.speedChanged.emit(self._smoothed_speed)

    def _update_page_speed(self, page_index, current_speed):
        page_index = int(page_index)
        current_speed = max(0.0, float(current_speed or 0))
        with self._speed_lock:
            previous = self._page_speeds.get(page_index, 0.0)
            self._page_speeds[page_index] = (
                current_speed
                if previous <= 0
                else previous * 0.65 + current_speed * 0.35
            )
            aggregate = sum(self._page_speeds.values())
        self.signals.speedChanged.emit(aggregate)

    def _clear_page_speed(self, page_index):
        with self._speed_lock:
            self._page_speeds.pop(int(page_index), None)
            aggregate = sum(self._page_speeds.values())
        if aggregate > 0:
            self.signals.speedChanged.emit(aggregate)

    def _checkpoint_original_fallback(self, page_index, total):
        with self._page_state_lock:
            self._page_modes[int(page_index)] = ORIGINAL_PAGE_MODE_BASE
            self.user_repository.update_gallery_original_state(
                self.detail.gallery.gid,
                ORIGINAL_STATE_DOWNLOADING,
                completed_pages=self._completed_pages,
                page_count=total,
                error="",
                fallback_to_standard=True,
                page_modes=tuple(self._page_modes),
            )

    def _cancelable_wait(self, seconds):
        deadline = time.monotonic() + max(0, float(seconds))
        while time.monotonic() < deadline:
            self._check_cancelled()
            time.sleep(min(0.1, deadline - time.monotonic()))

    def _existing_page_indexes(self, folder):
        return {
            index
            for index in range(int(self.detail.page_count))
            if (
                (path := self.ehviewer_repository.find_page_file(folder, index))
                is not None
                and self._is_valid_file(path)
            )
        }

    @staticmethod
    def _is_valid_file(path):
        reader = QImageReader(str(path))
        return path.stat().st_size > 0 and reader.canRead() and reader.size().isValid()

    def _check_cancelled(self):
        if self.cancelled:
            raise _DownloadCancelled()

def _image_extension(data):
    data = bytes(data or b"")
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
        return ".avif"
    return ".jpg"


class LocalGalleryPageDownloadSignals(QObject):
    speedChanged = Signal(float)
    saved = Signal(int, int, str, int, int)
    failed = Signal(int, int, str)


class LocalGalleryPageDownloadWorker(QRunnable):
    """Download exactly one missing page into an existing EhViewer gallery."""

    def __init__(
        self,
        provider,
        detail,
        page_index,
        folder,
        gallery_cache,
        ehviewer_repository,
        site,
        original=False,
    ):
        super().__init__()
        self.provider = provider
        self.detail = detail
        self.page_index = int(page_index)
        self.folder = folder
        self.gallery_cache = gallery_cache
        self.ehviewer_repository = ehviewer_repository
        self.site = str(site)
        self.original = bool(original)
        self.cancelled = False
        self.signals = LocalGalleryPageDownloadSignals()

    def cancel(self):
        self.cancelled = True
        cancel_requests = getattr(self.provider, "cancel_pending_requests", None)
        if cancel_requests is not None:
            cancel_requests()

    def run(self):
        gid = int(self.detail.gallery.gid)
        try:
            if self.cancelled:
                return
            preview = next(
                (
                    current
                    for current in self.detail.previews
                    if int(current.page_index) == self.page_index
                    and current.page_token
                ),
                None,
            )
            if preview is None:
                page_number = gallery_preview_page_number(
                    self.detail.gallery, self.page_index
                )
                preview_page = self.gallery_cache.get_preview_page(
                    self.site, self.detail.gallery, page_number
                )
                if preview_page is None:
                    preview_page = self.provider.load_gallery_preview_page(
                        self.detail.gallery, page_number
                    )
                    self.gallery_cache.put_preview_page(self.site, preview_page)
                preview = next(
                    current
                    for current in preview_page.items
                    if int(current.page_index) == self.page_index
                )
            started_at = time.monotonic()
            method = (
                self.provider.load_gallery_page_original
                if self.original
                else self.provider.load_gallery_page_image
            )
            speed_was_reported = False

            def report_speed(speed):
                nonlocal speed_was_reported
                speed_was_reported = True
                self.signals.speedChanged.emit(max(0.0, float(speed or 0)))

            try:
                parameters = inspect.signature(method).parameters
            except (TypeError, ValueError):
                parameters = {}
            keywords = {}
            if "should_cancel" in parameters:
                keywords["should_cancel"] = lambda: self.cancelled
            if "progress_callback" in parameters:
                keywords["progress_callback"] = report_speed
            data = method(self.detail.gallery, preview, **keywords)
            elapsed = max(0.001, time.monotonic() - started_at)
            if self.cancelled:
                return
            if not data or QImage.fromData(data).isNull():
                raise ValueError(f"第 {self.page_index + 1} 页不是有效图片")
            if not speed_was_reported:
                self.signals.speedChanged.emit(len(data) / elapsed)
            if not self.original:
                self.gallery_cache.put_page_image(
                    self.site, self.detail.gallery, self.page_index, data
                )
            page_path = self.ehviewer_repository.write_page(
                self.folder,
                self.page_index,
                _image_extension(data),
                data,
            )
            total = int(self.detail.page_count)
            completed = sum(
                self.ehviewer_repository.find_page_file(self.folder, index)
                is not None
                for index in range(total)
            )
            self.signals.saved.emit(
                gid,
                self.page_index,
                str(page_path),
                completed,
                total,
            )
        except Exception as error:
            if not self.cancelled:
                self.signals.failed.emit(
                    gid,
                    self.page_index,
                    str(error) or error.__class__.__name__,
                )
