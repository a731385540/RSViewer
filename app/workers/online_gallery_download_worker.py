import math
import inspect
import time

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from app.domain.online_download import (
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_DOWNLOADING,
    ONLINE_DOWNLOAD_FAILED,
    ONLINE_DOWNLOAD_PAUSED,
    OnlineGalleryDownloadRecord,
)
from app.domain.online_gallery import OnlineGalleryPreviewPage
from app.repositories.ehviewer_download_repository import (
    EH_STATE_FAILED,
    EH_STATE_FINISHED,
)
from app.services.online_download_builder import online_detail_metadata


class OnlineGalleryDownloadSignals(QObject):
    stageChanged = Signal(str)
    progressChanged = Signal(int, int)
    completed = Signal(int, str)
    failed = Signal(int, str)
    paused = Signal(int)


class _DownloadCancelled(RuntimeError):
    pass


class OnlineGalleryDownloadWorker(QRunnable):
    """Persist metadata first, then resume missing gallery images in order."""

    def __init__(
        self,
        provider,
        detail,
        cover_data,
        gallery_cache,
        ehviewer_repository,
        user_repository,
        site,
        retry_count=3,
    ):
        super().__init__()
        self.provider = provider
        self.detail = detail
        self.cover_data = bytes(cover_data or b"")
        self.gallery_cache = gallery_cache
        self.ehviewer_repository = ehviewer_repository
        self.user_repository = user_repository
        self.site = str(site)
        self.retry_count = max(1, int(retry_count))
        self.cancelled = False
        self.signals = OnlineGalleryDownloadSignals()

    def cancel(self):
        self.cancelled = True
        cancel_requests = getattr(self.provider, "cancel_pending_requests", None)
        if cancel_requests is not None:
            cancel_requests()

    def run(self):
        gid = int(self.detail.gallery.gid)
        folder = None
        completed_pages = 0
        record_saved = False
        try:
            self._check_cancelled()
            self.signals.stageChanged.emit("正在保存画廊信息与评论…")
            dirname, folder = self.ehviewer_repository.prepare_download(self.detail)
            completed_indexes = self._existing_page_indexes(folder)
            completed_pages = len(completed_indexes)
            record = OnlineGalleryDownloadRecord(
                gid=gid,
                site=self.site,
                token=self.detail.gallery.token,
                title=self.detail.title,
                dirname=dirname,
                page_count=int(self.detail.page_count),
                completed_pages=completed_pages,
                state=ONLINE_DOWNLOAD_DOWNLOADING,
                metadata=online_detail_metadata(self.detail),
            )
            self.user_repository.save_online_gallery_download(
                record,
                self.detail.comments,
            )
            record_saved = True
            self.signals.progressChanged.emit(
                completed_pages, int(self.detail.page_count)
            )

            self._save_thumbnail(folder)
            self.signals.stageChanged.emit("正在获取全部页面 ID…")
            previews = self._load_all_previews()
            page_tokens = {
                index: preview.page_token for index, preview in previews.items()
            }
            self.ehviewer_repository.write_spider_info(
                folder, self.detail, page_tokens
            )

            total = int(self.detail.page_count)
            for index in range(total):
                self._check_cancelled()
                if index in completed_indexes:
                    continue
                self.signals.stageChanged.emit(
                    f"正在下载第 {index + 1} / {total} 页…"
                )
                data = self._retry(
                    lambda preview=previews[index], current=index: self._download_page(
                        preview, current
                    ),
                    f"第 {index + 1} 页",
                )
                extension = _image_extension(data)
                self.ehviewer_repository.write_page(
                    folder, index, extension, data
                )
                completed_indexes.add(index)
                completed_pages = len(completed_indexes)
                self.user_repository.update_online_download(
                    gid,
                    completed_pages,
                    ONLINE_DOWNLOAD_DOWNLOADING,
                )
                self.signals.progressChanged.emit(completed_pages, total)

            self.ehviewer_repository.mark_state(gid, EH_STATE_FINISHED)
            self.user_repository.update_online_download(
                gid,
                total,
                ONLINE_DOWNLOAD_COMPLETED,
            )
            self.signals.stageChanged.emit("下载完成")
            self.signals.progressChanged.emit(total, total)
            self.signals.completed.emit(gid, str(folder))
        except _DownloadCancelled:
            self.ehviewer_repository.mark_state(gid, EH_STATE_FAILED)
            if record_saved:
                self.user_repository.update_online_download(
                    gid,
                    completed_pages,
                    ONLINE_DOWNLOAD_PAUSED,
                )
            self.signals.paused.emit(gid)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            self.ehviewer_repository.mark_state(gid, EH_STATE_FAILED)
            if record_saved:
                self.user_repository.update_online_download(
                    gid,
                    completed_pages,
                    ONLINE_DOWNLOAD_FAILED,
                    message,
                )
            self.signals.failed.emit(gid, message)

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
        preview_pages = max(1, math.ceil(page_count / 20))
        previews = {}
        for page_number in range(1, preview_pages + 1):
            self._check_cancelled()
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

    def _retry(self, operation, label):
        last_error = None
        for attempt in range(1, self.retry_count + 1):
            self._check_cancelled()
            try:
                return operation()
            except Exception as error:
                last_error = error
                self._check_cancelled()
                if attempt >= self.retry_count:
                    break
                self.signals.stageChanged.emit(
                    f"{label}请求失败，正在重试（{attempt + 1}/{self.retry_count}）…"
                )
                self._cancelable_wait(attempt)
        raise last_error

    def _download_page(self, preview, index):
        data = self._provider_call(
            "load_gallery_page_image", self.detail.gallery, preview
        )
        if not data or QImage.fromData(data).isNull():
            raise ValueError(f"第 {index + 1} 页不是有效图片")
        return data

    def _provider_call(self, method_name, *args):
        method = getattr(self.provider, method_name)
        try:
            supports_cancel = "should_cancel" in inspect.signature(method).parameters
        except (TypeError, ValueError):
            supports_cancel = False
        if supports_cancel:
            return method(*args, should_cancel=lambda: self.cancelled)
        return method(*args)

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
