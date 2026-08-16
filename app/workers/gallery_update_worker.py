import inspect
import math
import os
import re
import time
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
from app.domain.online_download import GallerySyncRecord
from app.domain.online_gallery import OnlineGallery, OnlineGalleryPreview
from app.repositories.gallery_update_state_repository import (
    GalleryUpdateStateRepository,
)
from app.services.online_download_builder import online_detail_metadata
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
            if status < 1:
                self.signals.stageChanged.emit("正在为旧画廊图片写入页面标识…")
                self._tag_source_files(current_sidecar)
                status = self._checkpoint(detail, 1)
            elif status < 5:
                self._verify_source_tokens_recoverable(current_sidecar)

            self._check_cancelled()
            if status < 2:
                self.signals.stageChanged.emit("正在按最新画廊顺序重排已有图片…")
                self._remap_marked_files(current_sidecar, new_sidecar)
                status = self._checkpoint(detail, 2)
            else:
                self._verify_remapped_files(new_sidecar)

            self._check_cancelled()
            if not self._target_pages_complete(new_sidecar):
                self.signals.stageChanged.emit("正在补齐最新画廊缺失页面…")
                self._download_missing_pages(detail, new_sidecar)
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
            if preview.page_token
        }
        page_count = max(1, math.ceil(total / 20))
        for page_number in range(1, page_count + 1):
            if all(index in previews for index in range((page_number - 1) * 20, min(total, page_number * 20))):
                continue
            page = self.gallery_cache.get_preview_page(
                self.record.site, detail.gallery, page_number
            )
            if page is None:
                page = self._provider_call(
                    "load_gallery_preview_page", detail.gallery, page_number
                )
                self.gallery_cache.put_preview_page(self.record.site, page)
            for preview in page.items:
                if preview.page_token:
                    previews[int(preview.page_index)] = preview
        if set(previews).intersection(range(total)) != set(range(total)):
            raise ValueError("最新画廊页面 ID 不完整")
        return tuple(previews[index].page_token for index in range(total))

    def _tag_source_files(self, source_sidecar):
        available_tokens = self._all_marked_tokens(include_history=True)
        normal = self._normal_page_files()
        for index, token in enumerate(source_sidecar.page_tokens):
            self._check_cancelled()
            if token in available_tokens:
                continue
            source = normal.get(index)
            if source is None:
                raise ValueError(f"旧画廊第 {index + 1} 页缺失，请先补齐下载")
            target = source.with_name(
                f"{index + 1:08d}-{index}-{token}{source.suffix.casefold()}"
            )
            rename_without_overwrite(source, target)
            available_tokens.add(token)
        self._verify_source_tokens_recoverable(source_sidecar, require_root=True)

    def _verify_source_tokens_recoverable(self, source_sidecar, require_root=False):
        root_tokens = self._all_marked_tokens(include_history=False)
        all_tokens = (
            root_tokens if require_root else self._all_marked_tokens(include_history=True)
        )
        missing = [token for token in source_sidecar.page_tokens if token not in all_tokens]
        if missing:
            raise ValueError(f"旧画廊仍有 {len(missing)} 个页面缺少可恢复标识")

    def _remap_marked_files(self, source_sidecar, target_sidecar):
        target_indexes = {
            token: index for index, token in enumerate(target_sidecar.page_tokens)
        }
        removed_folder = (
            self.folder
            / "history"
            / "removed"
            / f"{source_sidecar.gid}-{source_sidecar.gallery_token}"
        )
        for path, _old_index, token in tuple(self._marked_page_files()):
            self._check_cancelled()
            target_index = target_indexes.get(token)
            if target_index is None:
                removed_folder.mkdir(parents=True, exist_ok=True)
                rename_without_overwrite(path, removed_folder / path.name)
                continue
            target = path.with_name(
                f"{target_index + 1:08d}-{target_index}-{token}{path.suffix.casefold()}"
            )
            if path != target:
                rename_without_overwrite(path, target)
        self._verify_remapped_files(target_sidecar)

    def _verify_remapped_files(self, target_sidecar):
        indexes = {}
        target_tokens = set(target_sidecar.page_tokens)
        for path, index, token in self._marked_page_files():
            if token not in target_tokens:
                raise ValueError(f"发现无法归属到最新画廊的文件：{path.name}")
            expected_index = target_sidecar.page_tokens.index(token)
            if index != expected_index:
                raise ValueError(f"页面文件尚未完成重排：{path.name}")
            if index in indexes:
                raise ValueError(f"最新画廊第 {index + 1} 页存在多个候选文件")
            indexes[index] = path

    def _download_missing_pages(self, detail, target_sidecar):
        existing = self._valid_target_files(target_sidecar)
        total = target_sidecar.page_count
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
            data = self._retry(
                lambda: self._provider_call(
                    "load_gallery_page_image", detail.gallery, preview
                ),
                f"第 {index + 1} 页",
            )
            if not data or QImage.fromData(data).isNull():
                raise ValueError(f"最新画廊第 {index + 1} 页不是有效图片")
            elapsed = max(0.001, time.monotonic() - started_at)
            current_speed = len(data) / elapsed
            self._speed = (
                current_speed
                if self._speed <= 0
                else self._speed * 0.65 + current_speed * 0.35
            )
            self.signals.speedChanged.emit(self._speed)
            extension = _image_extension(data)
            target = self.folder / (
                f"{index + 1:08d}-{index}-{token}{extension}"
            )
            write_new_page(target, data)
            existing[index] = target
            completed = len(existing)
            self.user_repository.update_gallery_update_state(
                self.record.source_gid,
                UPDATE_RUNNING,
                status=2,
                completed_pages=completed,
                page_count=total,
            )
            self.signals.progressChanged.emit(completed, total)

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
        self.record = replace(
            self.record,
            latest_url=str(detail.gallery.url),
            target_gid=int(detail.gallery.gid),
            target_token=str(detail.gallery.token),
            status=int(status),
            state=UPDATE_RUNNING,
            page_count=int(detail.page_count),
            metadata=online_detail_metadata(detail),
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
        token = source_sidecar.page_tokens[int(progress)]
        try:
            return target_sidecar.page_tokens.index(token)
        except ValueError:
            return 0

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

    def _all_marked_tokens(self, include_history):
        tokens = {token for _path, _index, token in self._marked_page_files()}
        if include_history:
            history = self.folder / "history"
            if history.is_dir():
                for path in history.rglob("*"):
                    if not path.is_file():
                        continue
                    match = _MARKED_PAGE_RE.fullmatch(path.name)
                    if match is not None:
                        tokens.add(match.group("token").casefold())
        return tokens

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
            except Exception as error:
                last_error = error
                self._check_cancelled()
                if attempt >= self.retry_count:
                    break
                self.signals.stageChanged.emit(
                    f"{label}请求失败，正在重试（{attempt + 1}/{self.retry_count}）…"
                )
        raise last_error

    def _provider_call(self, method_name, *args):
        method = getattr(self.provider, method_name)
        try:
            supports_cancel = "should_cancel" in inspect.signature(method).parameters
        except (TypeError, ValueError):
            supports_cancel = False
        if supports_cancel:
            return method(*args, should_cancel=lambda: self.cancelled)
        return method(*args)

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
