import inspect
import math
import os
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QImageReader

from app.domain.gallery_update import (
    UPDATE_COMPLETED,
    UPDATE_FAILED,
    UPDATE_PAUSED,
    UPDATE_RUNNING,
)
from app.domain.online_download import (
    GallerySyncRecord,
    ORIGINAL_PAGE_MODE_BASE,
    ORIGINAL_PAGE_MODE_ORIGINAL,
    ORIGINAL_STATE_ACTIVE,
    normalize_original_page_modes,
)
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryPreview,
    gallery_preview_page_count,
)
from app.repositories.gallery_update_state_repository import (
    GalleryUpdateStateRepository,
)
from app.services.online_download_builder import online_detail_metadata
from app.sources.eh_online_source import OriginalImageUnavailableError
from app.sources.ehviewer_source import IMAGE_SUFFIXES
from app.workers.online_gallery_download_worker import _image_extension


_MARKED_PAGE_RE = re.compile(
    r"^(?P<number>\d{8})-(?P<index>\d+)-(?P<token>[0-9a-fA-F]{10})"
    r"(?P<suffix>\.[^.]+)$"
)
_GALLERY_URL_RE = re.compile(r"/g/(?P<gid>\d+)/(?P<token>[0-9a-fA-F]+)/?")


@dataclass(frozen=True)
class UpdateSidecar:
    start_page_index: int
    gid: int
    gallery_token: str
    page_count: int
    page_tokens: tuple


class GalleryUpdateSignals(QObject):
    stageChanged = Signal(str)
    checkpointChanged = Signal(int)
    progressChanged = Signal(int, int)
    speedChanged = Signal(float)
    targetResolved = Signal(int, str, int)
    completed = Signal(int, int)
    failed = Signal(int, str)
    paused = Signal(int)


class _UpdateCancelled(RuntimeError):
    pass


class GalleryUpdateWorker(QRunnable):
    """Crash-resumable local gallery revision update based on page tokens."""

    def __init__(
        self,
        record,
        provider,
        gallery_cache,
        ehviewer_repository,
        user_repository,
        retry_count=3,
    ):
        super().__init__()
        self.record = record
        self.provider = provider
        self.gallery_cache = gallery_cache
        self.ehviewer_repository = ehviewer_repository
        self.user_repository = user_repository
        self.retry_count = max(1, int(retry_count))
        self.cancelled = False
        self.signals = GalleryUpdateSignals()
        self.folder = Path(record.folder)
        self.state_repository = GalleryUpdateStateRepository(self.folder)
        self._speed = 0.0

    def cancel(self):
        self.cancelled = True
        cancel_requests = getattr(self.provider, "cancel_pending_requests", None)
        if cancel_requests is not None:
            cancel_requests()

    def run(self):
        source_gid = int(self.record.source_gid)
        try:
            self._normalize_original_image_mode()
            self._validate_folder()
            current_sidecar = read_update_sidecar(self.folder / ".ehviewer")
            new_sidecar = read_update_sidecar(self.folder / "new.ehviewer")
            if current_sidecar is None and new_sidecar is not None:
                archived_source = read_update_sidecar(
                    self.folder
                    / "history"
                    / f"{self.record.source_gid}-{self.record.source_token}.ehviewer"
                )
                if (
                    archived_source is not None
                    and archived_source.gid == source_gid
                    and archived_source.gallery_token == self.record.source_token
                ):
                    current_sidecar = archived_source

            if (
                current_sidecar is not None
                and int(self.record.target_gid or 0) == current_sidecar.gid
                and self.record.target_token == current_sidecar.gallery_token
                and new_sidecar is None
            ):
                detail = self._load_fixed_target_detail(current_sidecar)
                self._finalize_thumbnail(
                    self.folder / "history",
                    self.record.source_gid,
                    self.record.source_token,
                )
                self._finalize_databases(detail, current_sidecar, None)
                self._finish(detail)
                return

            if current_sidecar is None:
                raise ValueError("本地画廊缺少可识别的 .ehviewer")
            if current_sidecar.gid != source_gid:
                raise ValueError(".ehviewer 的源 GID 与更新任务不一致")
            if current_sidecar.gallery_token != str(self.record.source_token):
                raise ValueError(".ehviewer 的源 gallery token 与更新任务不一致")

            detail, new_sidecar = self._ensure_target(current_sidecar, new_sidecar)
            self.ehviewer_repository.validate_update_target(
                source_gid, detail.gallery.gid, self.folder
            )
            checkpoint = self.state_repository.record(
                detail.gallery.gid, detail.gallery.token
            )
            status = max(0, min(6, int(checkpoint.get("status", self.record.status))))

            self._check_cancelled()
            if status < 5:
                self.signals.stageChanged.emit("正在校验并补齐旧画廊页面标识…")
                self._tag_source_files(current_sidecar)
                if status < 1:
                    status = self._checkpoint(detail, 1)

            self._check_cancelled()
            if status < 5:
                self.signals.stageChanged.emit("正在按最新画廊顺序重排已有图片…")
                self._remap_marked_files(current_sidecar, new_sidecar)
                if status < 2:
                    status = self._checkpoint(detail, 2)
            else:
                self._verify_remapped_files(new_sidecar)

            self._check_cancelled()
            self._prepare_target_page_modes(current_sidecar, new_sidecar)
            if not self._target_pages_complete(new_sidecar):
                self.signals.stageChanged.emit("正在补齐最新画廊缺失页面…")
                self._download_missing_pages(detail, current_sidecar, new_sidecar)
            if status < 3:
                status = self._checkpoint(detail, 3)

            self._check_cancelled()
            self.signals.stageChanged.emit("正在校验最新画廊图片集合…")
            self._verify_target_files(new_sidecar)
            if status < 4:
                status = self._checkpoint(detail, 4)

            self._check_cancelled()
            if status < 5:
                status = self._checkpoint(detail, 5)
            self.signals.stageChanged.emit("正在恢复标准 EhViewer 文件名…")
            self._strip_target_markers(new_sidecar)

            self._check_cancelled()
            self.signals.stageChanged.emit("正在切换画廊版本与数据库记录…")
            mapped_progress = self._mapped_progress(current_sidecar, new_sidecar)
            self._finalize_sidecars(current_sidecar, new_sidecar)
            self._finalize_databases(
                detail, new_sidecar, mapped_progress
            )
            self._finish(detail)
        except _UpdateCancelled:
            self.user_repository.update_gallery_update_state(
                source_gid, UPDATE_PAUSED, error="更新已暂停，可从当前阶段继续"
            )
            self.signals.paused.emit(source_gid)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            self.user_repository.update_gallery_update_state(
                source_gid, UPDATE_FAILED, error=message
            )
            self.signals.failed.emit(source_gid, message)

    def _normalize_original_image_mode(self):
        original = self.user_repository.gallery_original_state(
            self.record.source_gid
        )
        if original is None:
            return
        metadata = dict(self.record.metadata or {})
        if metadata.get("image_mode") == "original":
            return
        metadata["image_mode"] = "original"
        self.record = replace(self.record, metadata=metadata)
        self.user_repository.save_gallery_update(self.record)

    def _ensure_target(self, source_sidecar, staged_sidecar):
        if staged_sidecar is not None:
            detail = self._load_fixed_target_detail(staged_sidecar)
            if detail.page_count != staged_sidecar.page_count:
                raise ValueError("最新画廊页数已变化，请先完成当前更新任务")
            if detail.cover_url and not (self.folder / "new.thumb").is_file():
                self._stage_thumbnail(detail)
            self._save_resolved_record(detail, staged_sidecar, status=0)
            return detail, staged_sidecar

        self.signals.stageChanged.emit("正在解析最新画廊信息…")
        gallery = self._gallery_from_url(self.record.latest_url)
        visited = set()
        detail = None
        for _depth in range(16):
            self._check_cancelled()
            key = (int(gallery.gid), str(gallery.token))
            if key in visited:
                raise ValueError("画廊更新版本链出现循环")
            visited.add(key)
            detail = self._provider_call("load_gallery_detail", gallery)
            if not detail.newer_gallery_urls:
                break
            gallery = self._gallery_from_url(detail.newer_gallery_urls[-1])
        else:
            raise ValueError("画廊更新版本链过长，已停止处理")
        if detail is None or int(detail.page_count) <= 0:
            raise ValueError("最新画廊没有可用页面")

        page_tokens = self._collect_page_tokens(detail)
        if any(not re.fullmatch(r"[0-9a-fA-F]{10}", token) for token in page_tokens):
            raise ValueError("最新画廊存在非十位页面 ID，无法执行安全重排")
        staged_sidecar = UpdateSidecar(
            start_page_index=source_sidecar.start_page_index,
            gid=int(detail.gallery.gid),
            gallery_token=str(detail.gallery.token),
            page_count=int(detail.page_count),
            page_tokens=tuple(page_tokens),
        )
        self.ehviewer_repository.validate_update_target(
            self.record.source_gid, staged_sidecar.gid, self.folder
        )
        atomic_write_bytes(
            self.folder / "new.ehviewer", encode_update_sidecar(staged_sidecar)
        )
        self._stage_thumbnail(detail)
        self.state_repository.write(
            staged_sidecar.gid,
            staged_sidecar.gallery_token,
            0,
            source_gid=int(self.record.source_gid),
            source_token=str(self.record.source_token),
            site=str(self.record.site),
            latest_url=str(detail.gallery.url),
        )
        self._save_resolved_record(detail, staged_sidecar, status=0)
        self.signals.targetResolved.emit(
            staged_sidecar.gid,
            staged_sidecar.gallery_token,
            staged_sidecar.page_count,
        )
        return detail, staged_sidecar

    def _load_fixed_target_detail(self, sidecar):
        gallery = OnlineGallery(
            gid=sidecar.gid,
            token=sidecar.gallery_token,
            url=(
                f"{self.provider.settings.base_url}g/"
                f"{sidecar.gid}/{sidecar.gallery_token}/"
            ),
            title=self.record.title,
            page_count=sidecar.page_count,
        )
        return self._provider_call("load_gallery_detail", gallery)

    def _gallery_from_url(self, url):
        parsed = urlparse(str(url or ""))
        expected_host = urlparse(self.provider.settings.base_url).hostname
        match = _GALLERY_URL_RE.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or match is None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("更新版本地址不属于当前 EH/EX 站点")
        gid = int(match.group("gid"))
        token = match.group("token")
        return OnlineGallery(
            gid=gid,
            token=token,
            url=f"{self.provider.settings.base_url}g/{gid}/{token}/",
            title=self.record.title,
        )

    def _collect_page_tokens(self, detail):
        total = int(detail.page_count)
        previews = {
            int(preview.page_index): preview
            for preview in detail.previews
            if preview.page_token and 0 <= int(preview.page_index) < total
        }
        # EH/EX may return 20 or 40 previews per response depending on the
        # account setting. Fetch response pages in order instead of assuming
        # that one response always maps to a fixed 20-index block.
        page_count = gallery_preview_page_count(detail.gallery, total)
        for page_number in range(1, page_count + 1):
            if len(previews) >= total:
                break
            page = self.gallery_cache.get_preview_page(
                self.record.site, detail.gallery, page_number
            )
            if page is None:
                page = self._provider_call(
                    "load_gallery_preview_page", detail.gallery, page_number
                )
                self.gallery_cache.put_preview_page(self.record.site, page)
            for preview in page.items:
                index = int(preview.page_index)
                if preview.page_token and 0 <= index < total:
                    previews[index] = preview
        missing = set(range(total)).difference(previews)
        if missing:
            raise ValueError(
                f"最新画廊页面 ID 不完整，缺少 {len(missing)} 页"
            )
        return tuple(previews[index].page_token for index in range(total))

    def _tag_source_files(self, source_sidecar):
        available_tokens = self._marked_token_counts(include_history=True)
        missing_tokens = Counter(source_sidecar.page_tokens) - available_tokens
        normal = self._normal_page_files()
        for index, token in enumerate(source_sidecar.page_tokens):
            self._check_cancelled()
            if missing_tokens[token] <= 0:
                continue
            source = normal.get(index)
            if source is None:
                continue
            target = source.with_name(
                f"{index + 1:08d}-{index}-{token}{source.suffix.casefold()}"
            )
            rename_without_overwrite(source, target)
            missing_tokens[token] -= 1
        self._verify_source_tokens_recoverable(source_sidecar)

    def _verify_source_tokens_recoverable(self, source_sidecar, require_root=False):
        root_tokens = self._marked_token_counts(include_history=False)
        all_tokens = (
            root_tokens if require_root else self._marked_token_counts(include_history=True)
        )
        missing = Counter(source_sidecar.page_tokens) - all_tokens
        missing_count = sum(missing.values())
        if missing_count:
            raise ValueError(f"旧画廊仍有 {missing_count} 个页面缺少可恢复标识")

    def _remap_marked_files(self, source_sidecar, target_sidecar):
        target_indexes = defaultdict(deque)
        for index, token in enumerate(target_sidecar.page_tokens):
            target_indexes[token].append(index)
        removed_folder = (
            self.folder
            / "history"
            / "removed"
            / f"{source_sidecar.gid}-{source_sidecar.gallery_token}"
        )
        marked_by_token = defaultdict(list)
        for path, current_index, token in self._marked_page_files():
            marked_by_token[token].append((path, current_index))
        for token, marked_pages in marked_by_token.items():
            self._check_cancelled()
            remaining_targets = list(target_indexes.get(token, ()))
            pending_pages = []
            for path, current_index in sorted(marked_pages, key=lambda value: value[1]):
                if current_index in remaining_targets:
                    remaining_targets.remove(current_index)
                else:
                    pending_pages.append((path, current_index))
            target_queue = deque(remaining_targets)
            for path, _current_index in pending_pages:
                self._check_cancelled()
                if not target_queue:
                    removed_folder.mkdir(parents=True, exist_ok=True)
                    rename_without_overwrite(path, removed_folder / path.name)
                    continue
                target_index = target_queue.popleft()
                target = path.with_name(
                    f"{target_index + 1:08d}-{target_index}-{token}"
                    f"{path.suffix.casefold()}"
                )
                rename_without_overwrite(path, target)
        self._verify_remapped_files(target_sidecar)

    def _verify_remapped_files(self, target_sidecar):
        indexes = {}
        target_tokens = set(target_sidecar.page_tokens)
        for path, index, token in self._marked_page_files():
            if token not in target_tokens:
                raise ValueError(f"发现无法归属到最新画廊的文件：{path.name}")
            if (
                index >= target_sidecar.page_count
                or target_sidecar.page_tokens[index] != token
            ):
                raise ValueError(f"页面文件尚未完成重排：{path.name}")
            if index in indexes:
                raise ValueError(f"最新画廊第 {index + 1} 页存在多个候选文件")
            indexes[index] = path

    def _download_missing_pages(self, detail, source_sidecar, target_sidecar):
        existing = self._valid_target_files(target_sidecar)
        total = target_sidecar.page_count
        page_modes = self._target_original_page_modes(
            source_sidecar, target_sidecar
        )
        if self.record.metadata.get("image_mode") == "original":
            for index in existing:
                if not page_modes[index]:
                    page_modes[index] = ORIGINAL_PAGE_MODE_ORIGINAL
            self._save_target_page_modes(page_modes)
        self.signals.progressChanged.emit(len(existing), total)
        for index, token in enumerate(target_sidecar.page_tokens):
            self._check_cancelled()
            if index in existing:
                continue
            self.signals.stageChanged.emit(
                f"正在下载最新画廊第 {index + 1} / {total} 页…"
            )
            preview = OnlineGalleryPreview(
                page_index=index,
                page_url=(
                    f"{self.provider.settings.base_url}s/{token}/"
                    f"{detail.gallery.gid}-{index + 1}"
                ),
                page_token=token,
            )
            started_at = time.monotonic()
            speed_was_reported = False

            def report_speed(speed):
                nonlocal speed_was_reported
                speed_was_reported = True
                self._update_speed(speed)

            if self.record.metadata.get("image_mode") != "original":
                data = self._retry(
                    lambda: self._provider_call(
                        "load_gallery_page_image",
                        detail.gallery,
                        preview,
                        progress_callback=report_speed,
                    ),
                    f"第 {index + 1} 页",
                )
            elif page_modes[index] == ORIGINAL_PAGE_MODE_BASE:
                data = self._retry(
                    lambda: self._provider_call(
                        "load_gallery_page_image",
                        detail.gallery,
                        preview,
                        progress_callback=report_speed,
                    ),
                    f"第 {index + 1} 页基础图",
                )
            else:
                try:
                    data = self._retry(
                        lambda: self._provider_call(
                            "load_gallery_page_original",
                            detail.gallery,
                            preview,
                            progress_callback=report_speed,
                        ),
                        f"第 {index + 1} 页原图",
                    )
                    page_modes[index] = ORIGINAL_PAGE_MODE_ORIGINAL
                except OriginalImageUnavailableError:
                    page_modes[index] = ORIGINAL_PAGE_MODE_BASE
                    self._save_target_page_modes(page_modes)
                    self.signals.stageChanged.emit(
                        f"最新画廊第 {index + 1} 页没有原图，正在下载基础图…"
                    )
                    data = self._retry(
                        lambda: self._provider_call(
                            "load_gallery_page_image",
                            detail.gallery,
                            preview,
                            progress_callback=report_speed,
                        ),
                        f"第 {index + 1} 页基础图",
                    )
            if not data or QImage.fromData(data).isNull():
                raise ValueError(f"最新画廊第 {index + 1} 页不是有效图片")
            elapsed = max(0.001, time.monotonic() - started_at)
            if not speed_was_reported:
                self._update_speed(len(data) / elapsed)
            extension = _image_extension(data)
            target = self.folder / (
                f"{index + 1:08d}-{index}-{token}{extension}"
            )
            write_new_page(target, data)
            existing[index] = target
            if self.record.metadata.get("image_mode") == "original":
                self._save_target_page_modes(page_modes)
            completed = len(existing)
            self.user_repository.update_gallery_update_state(
                self.record.source_gid,
                UPDATE_RUNNING,
                status=2,
                completed_pages=completed,
                page_count=total,
            )
            self.signals.progressChanged.emit(completed, total)

    def _target_original_page_modes(self, source_sidecar, target_sidecar):
        saved = self.record.metadata.get("target_page_modes")
        if isinstance(saved, list) and len(saved) == target_sidecar.page_count:
            return list(
                normalize_original_page_modes(saved, target_sidecar.page_count)
            )
        original = self.user_repository.gallery_original_state(
            self.record.source_gid
        )
        if original is None:
            return [""] * target_sidecar.page_count
        source_modes = normalize_original_page_modes(
            original.page_modes,
            source_sidecar.page_count,
            original.completed_pages,
            original.fallback_to_standard,
        )
        target_modes = [""] * target_sidecar.page_count
        used_sources = set()
        used_targets = set()
        for index, token in enumerate(source_sidecar.page_tokens):
            if (
                index < target_sidecar.page_count
                and target_sidecar.page_tokens[index] == token
            ):
                target_modes[index] = source_modes[index]
                used_sources.add(index)
                used_targets.add(index)
        sources_by_token = defaultdict(deque)
        targets_by_token = defaultdict(deque)
        for index, token in enumerate(source_sidecar.page_tokens):
            if index not in used_sources:
                sources_by_token[token].append(source_modes[index])
        for index, token in enumerate(target_sidecar.page_tokens):
            if index not in used_targets:
                targets_by_token[token].append(index)
        for token, modes in sources_by_token.items():
            targets = targets_by_token[token]
            while modes and targets:
                target_modes[targets.popleft()] = modes.popleft()
        return target_modes

    def _prepare_target_page_modes(self, source_sidecar, target_sidecar):
        if self.record.metadata.get("image_mode") != "original":
            return
        page_modes = self._target_original_page_modes(
            source_sidecar, target_sidecar
        )
        for index in self._valid_target_files(target_sidecar):
            if not page_modes[index]:
                page_modes[index] = ORIGINAL_PAGE_MODE_ORIGINAL
        self._save_target_page_modes(page_modes)

    def _save_target_page_modes(self, page_modes):
        metadata = dict(self.record.metadata or {})
        metadata["target_page_modes"] = list(page_modes)
        self.record = replace(self.record, metadata=metadata)
        self.user_repository.save_gallery_update(self.record)

    def _verify_marked_target_files(self, target_sidecar):
        valid = self._valid_marked_target_files(target_sidecar)
        if len(valid) != target_sidecar.page_count:
            raise ValueError(
                f"最新画廊图片仍缺少 {target_sidecar.page_count - len(valid)} 页"
            )
        for _path, index, token in self._marked_page_files():
            if index >= target_sidecar.page_count or target_sidecar.page_tokens[index] != token:
                raise ValueError("目录中存在不属于最新画廊的带标记图片")

    def _target_pages_complete(self, target_sidecar):
        return len(self._valid_target_files(target_sidecar)) == target_sidecar.page_count

    def _verify_target_files(self, target_sidecar):
        valid = self._valid_target_files(target_sidecar)
        if len(valid) != target_sidecar.page_count:
            raise ValueError(
                f"Latest gallery is still missing "
                f"{target_sidecar.page_count - len(valid)} page(s)"
            )

    def _valid_target_files(self, target_sidecar):
        """Return valid target pages from either marked or partially stripped names."""
        result = self._valid_marked_target_files(target_sidecar)
        for index, path in self._normal_page_files().items():
            if index >= target_sidecar.page_count:
                continue
            if index in result:
                raise ValueError(
                    f"Page {index + 1} has both a marked and a standard file"
                )
            if not is_valid_image_file(path):
                raise ValueError(f"Page {index + 1} is not a valid image")
            result[index] = path
        return result

    def _strip_target_markers(self, target_sidecar):
        marked = {index: path for path, index, _token in self._marked_page_files()}
        normal = self._normal_page_files()
        for index in range(target_sidecar.page_count):
            self._check_cancelled()
            target = normal.get(index)
            source = marked.get(index)
            if target is not None:
                if source is not None:
                    raise ValueError(
                        f"第 {index + 1} 页的标准文件与标记文件同时存在，拒绝覆盖"
                    )
                if not is_valid_image_file(target):
                    raise ValueError(f"第 {index + 1} 页标准文件已损坏")
                continue
            if source is None:
                raise ValueError(f"第 {index + 1} 页缺少待恢复文件")
            target = self.folder / f"{index + 1:08d}{source.suffix.casefold()}"
            rename_without_overwrite(source, target)
        if self._marked_page_files():
            raise ValueError("仍有页面未恢复为标准文件名")
        normals = self._normal_page_files()
        if set(normals) != set(range(target_sidecar.page_count)):
            raise ValueError("标准页码文件集合与最新画廊不一致")
        if any(not is_valid_image_file(path) for path in normals.values()):
            raise ValueError("标准页码文件中存在损坏图片")

    def _finalize_sidecars(self, source_sidecar, target_sidecar):
        history = self.folder / "history"
        history.mkdir(parents=True, exist_ok=True)
        current = self.folder / ".ehviewer"
        staged = self.folder / "new.ehviewer"
        archived = history / (
            f"{source_sidecar.gid}-{source_sidecar.gallery_token}.ehviewer"
        )
        if current.is_file():
            parsed = read_update_sidecar(current)
            if parsed is not None and parsed.gid == target_sidecar.gid and parsed.gallery_token == target_sidecar.gallery_token:
                pass
            elif archived.exists():
                if current.read_bytes() != archived.read_bytes():
                    raise ValueError("history 中已存在不同内容的旧 sidecar")
                current.unlink()
            else:
                rename_without_overwrite(current, archived)
        if staged.is_file():
            if current.exists():
                if current.read_bytes() != staged.read_bytes():
                    raise ValueError("当前 sidecar 与 new.ehviewer 内容冲突")
                staged.unlink()
            else:
                rename_without_overwrite(staged, current)
        parsed = read_update_sidecar(current)
        if parsed is None or parsed.gid != target_sidecar.gid or parsed.gallery_token != target_sidecar.gallery_token:
            raise ValueError("最新 .ehviewer 切换未完成")
        self._finalize_thumbnail(
            history, source_sidecar.gid, source_sidecar.gallery_token
        )

    def _finalize_thumbnail(self, history, source_gid, source_token):
        history.mkdir(parents=True, exist_ok=True)
        current = self.folder / ".thumb"
        staged = self.folder / "new.thumb"
        archived = history / f"{source_gid}-{source_token}.thumb"
        if staged.is_file():
            if current.is_file():
                if archived.exists():
                    if current.read_bytes() != archived.read_bytes():
                        raise ValueError("history 中已存在不同内容的旧封面")
                    current.unlink()
                else:
                    rename_without_overwrite(current, archived)
            rename_without_overwrite(staged, current)

    def _finalize_databases(self, detail, target_sidecar, mapped_progress):
        self.ehviewer_repository.promote_update(
            self.record.source_gid, detail, self.folder
        )
        self.user_repository.promote_gallery_gid(
            self.record.source_gid,
            detail.gallery.gid,
            mapped_progress,
        )
        original = self.user_repository.gallery_original_state(
            detail.gallery.gid
        )
        if original is not None:
            metadata = dict(original.metadata or {})
            metadata["image_mode"] = "original"
            target_modes = normalize_original_page_modes(
                self.record.metadata.get("target_page_modes") or (),
                detail.page_count,
                detail.page_count,
                original.fallback_to_standard,
            )
            self.user_repository.save_gallery_original_state(
                replace(
                    original,
                    gid=int(detail.gallery.gid),
                    site=str(self.record.site),
                    token=str(detail.gallery.token),
                    state=ORIGINAL_STATE_ACTIVE,
                    completed_pages=int(detail.page_count),
                    page_count=int(detail.page_count),
                    fallback_to_standard=(
                        ORIGINAL_PAGE_MODE_BASE in target_modes
                    ),
                    page_modes=target_modes,
                    metadata=metadata,
                    error="",
                )
            )
        self.user_repository.save_gallery_sync(
            GallerySyncRecord(
                gid=int(detail.gallery.gid),
                site=str(self.record.site),
                token=str(detail.gallery.token),
                metadata=online_detail_metadata(detail),
            ),
            detail.comments,
        )

    def _finish(self, detail):
        self.state_repository.write(
            detail.gallery.gid,
            detail.gallery.token,
            6,
            source_gid=int(self.record.source_gid),
            source_token=str(self.record.source_token),
            site=str(self.record.site),
            latest_url=str(detail.gallery.url),
        )
        self.user_repository.update_gallery_update_state(
            self.record.source_gid,
            UPDATE_COMPLETED,
            status=6,
            completed_pages=detail.page_count,
            page_count=detail.page_count,
            error="",
        )
        self.signals.checkpointChanged.emit(6)
        self.signals.progressChanged.emit(detail.page_count, detail.page_count)
        self.signals.completed.emit(self.record.source_gid, detail.gallery.gid)

    def _save_resolved_record(self, detail, sidecar, status):
        metadata = online_detail_metadata(detail)
        image_mode = self.record.metadata.get("image_mode")
        if image_mode in {"original", "standard_fallback"}:
            image_mode = "original"
            metadata["image_mode"] = image_mode
        target_page_modes = self.record.metadata.get("target_page_modes")
        if isinstance(target_page_modes, list):
            metadata["target_page_modes"] = target_page_modes
        self.record = replace(
            self.record,
            latest_url=str(detail.gallery.url),
            target_gid=int(detail.gallery.gid),
            target_token=str(detail.gallery.token),
            status=int(status),
            state=UPDATE_RUNNING,
            page_count=int(detail.page_count),
            metadata=metadata,
            error="",
        )
        self.user_repository.save_gallery_update(self.record)

    def _checkpoint(self, detail, status):
        self.state_repository.write(
            detail.gallery.gid,
            detail.gallery.token,
            status,
            source_gid=int(self.record.source_gid),
            source_token=str(self.record.source_token),
            site=str(self.record.site),
            latest_url=str(detail.gallery.url),
        )
        self.user_repository.update_gallery_update_state(
            self.record.source_gid,
            UPDATE_RUNNING,
            status=status,
            page_count=detail.page_count,
            error="",
        )
        self.record = replace(self.record, status=status, state=UPDATE_RUNNING)
        self.signals.checkpointChanged.emit(status)
        return status

    def _stage_thumbnail(self, detail):
        if not detail.cover_url:
            return
        data = self._provider_call("load_thumbnail", detail.cover_url)
        if data and not QImage.fromData(data).isNull():
            atomic_write_bytes(self.folder / "new.thumb", data)

    def _mapped_progress(self, source_sidecar, target_sidecar):
        progress = self.user_repository.progress_for_manga(self.record.source_gid)
        if progress is None or not 0 <= int(progress) < source_sidecar.page_count:
            return 0
        source_index = int(progress)
        token = source_sidecar.page_tokens[source_index]
        occurrence = source_sidecar.page_tokens[:source_index + 1].count(token) - 1
        target_indexes = [
            index
            for index, target_token in enumerate(target_sidecar.page_tokens)
            if target_token == token
        ]
        if not target_indexes:
            return 0
        return target_indexes[min(occurrence, len(target_indexes) - 1)]

    def _normal_page_files(self):
        result = {}
        for path in self.folder.iterdir():
            if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
                continue
            if not path.stem.isdigit() or int(path.stem) <= 0:
                continue
            index = int(path.stem) - 1
            if index in result:
                raise ValueError(f"第 {index + 1} 页存在多个标准文件")
            result[index] = path
        return result

    def _marked_page_files(self):
        result = []
        for path in self.folder.iterdir():
            if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
                continue
            match = _MARKED_PAGE_RE.fullmatch(path.name)
            if match is None:
                continue
            result.append(
                (path, int(match.group("index")), match.group("token").casefold())
            )
        return result

    def _all_marked_source_pages(self, include_history):
        pages = {
            (index, token) for _path, index, token in self._marked_page_files()
        }
        if include_history:
            history = self.folder / "history"
            if history.is_dir():
                for path in history.rglob("*"):
                    if not path.is_file():
                        continue
                    match = _MARKED_PAGE_RE.fullmatch(path.name)
                    if match is not None:
                        pages.add(
                            (
                                int(match.group("index")),
                                match.group("token").casefold(),
                            )
                        )
        return pages

    def _marked_token_counts(self, include_history):
        return Counter(
            token
            for _index, token in self._all_marked_source_pages(include_history)
        )

    def _valid_marked_target_files(self, target_sidecar):
        result = {}
        for path, index, token in self._marked_page_files():
            if index >= target_sidecar.page_count:
                continue
            if target_sidecar.page_tokens[index].casefold() != token:
                continue
            if index in result:
                raise ValueError(f"最新画廊第 {index + 1} 页存在多个文件")
            if not is_valid_image_file(path):
                raise ValueError(f"最新画廊第 {index + 1} 页图片已损坏")
            result[index] = path
        return result

    def _retry(self, operation, label):
        last_error = None
        for attempt in range(1, self.retry_count + 1):
            self._check_cancelled()
            try:
                return operation()
            except OriginalImageUnavailableError:
                raise
            except Exception as error:
                last_error = error
                self._speed = 0.0
                self.signals.speedChanged.emit(0.0)
                self._check_cancelled()
                if attempt >= self.retry_count:
                    break
                self.signals.stageChanged.emit(
                    f"{label}请求失败，正在重试（{attempt + 1}/{self.retry_count}）…"
                )
        raise last_error

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
        self._speed = (
            current_speed
            if self._speed <= 0
            else self._speed * 0.65 + current_speed * 0.35
        )
        self.signals.speedChanged.emit(self._speed)

    def _validate_folder(self):
        if not self.folder.is_dir():
            raise FileNotFoundError(f"找不到更新目录：{self.folder}")
        root = self.ehviewer_repository.manga_root.resolve()
        folder = self.folder.resolve()
        if os.path.commonpath((str(root), str(folder))) != str(root):
            raise ValueError("更新目录超出设置中的本地画廊根目录")

    def _check_cancelled(self):
        if self.cancelled:
            raise _UpdateCancelled()


def read_update_sidecar(path):
    path = Path(path)
    try:
        lines = path.read_text(encoding="ascii").splitlines()
        if len(lines) < 8 or lines[0] != "VERSION2":
            return None
        start_page_index = int(lines[1], 16)
        gid = int(lines[2])
        gallery_token = lines[3].strip()
        page_count = int(lines[7])
        if gid <= 0 or not gallery_token or page_count <= 0:
            return None
        tokens = [""] * page_count
        for line in lines[8:]:
            index_text, token = line.split(maxsplit=1)
            index = int(index_text)
            token = token.strip().casefold()
            if not 0 <= index < page_count or not re.fullmatch(r"[0-9a-fA-F]{10}", token):
                return None
            tokens[index] = token
        if not all(tokens):
            return None
        return UpdateSidecar(
            max(0, start_page_index),
            gid,
            gallery_token,
            page_count,
            tuple(tokens),
        )
    except (OSError, UnicodeError, ValueError):
        return None


def encode_update_sidecar(sidecar):
    lines = [
        "VERSION2",
        f"{int(sidecar.start_page_index):08x}",
        str(int(sidecar.gid)),
        str(sidecar.gallery_token),
        "1",
        str(max(1, math.ceil(int(sidecar.page_count) / 20))),
        "20",
        str(int(sidecar.page_count)),
    ]
    lines.extend(
        f"{index} {token}" for index, token in enumerate(sidecar.page_tokens)
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def rename_without_overwrite(source, target):
    source = Path(source)
    target = Path(target)
    if source == target:
        return
    if target.exists():
        raise FileExistsError(f"目标文件已存在，拒绝覆盖：{target.name}")
    source.rename(target)


def atomic_write_bytes(target, data):
    target = Path(target)
    temporary = target.with_name(target.name + ".part")
    try:
        with temporary.open("wb") as stream:
            stream.write(bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise FileExistsError(f"目标文件已存在，拒绝覆盖：{target.name}")
        temporary.rename(target)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def write_new_page(target, data):
    target = Path(target)
    if target.exists():
        if is_valid_image_file(target):
            return
        raise FileExistsError(f"目标图片已存在但无法解码：{target.name}")
    temporary = target.with_name(target.name + ".part")
    if temporary.exists():
        temporary.unlink()
    try:
        with temporary.open("wb") as stream:
            stream.write(bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        if QImage.fromData(bytes(data)).isNull():
            raise ValueError(f"下载图片无法解码：{target.name}")
        temporary.rename(target)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def is_valid_image_file(path):
    try:
        path = Path(path)
        reader = QImageReader(str(path))
        return path.stat().st_size > 0 and reader.canRead() and reader.size().isValid()
    except OSError:
        return False
