import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from app.domain.gallery_ad_cleanup import (
    AD_ACTION_DELETE,
    AD_ACTION_RESTORE,
    AD_ACTION_STAGE,
    AD_CLEANUP_CLEANED,
    AD_CLEANUP_DELETING,
    AD_CLEANUP_FAILED,
    AD_CLEANUP_MOVING,
    AD_CLEANUP_RESTORING,
    AD_CLEANUP_STAGED,
    GalleryAdCleanupRecord,
)
from app.domain.online_download import (
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_CLEANING,
)
from app.sources.ehviewer_source import IMAGE_SUFFIXES


class GalleryAdCleanupSignals(QObject):
    stageChanged = Signal(str)
    completed = Signal(int, str)
    failed = Signal(int, str)


class GalleryAdCleanupWorker(QRunnable):
    """Move, restore or permanently delete a recorded trailing page set."""

    STAGE = AD_ACTION_STAGE
    RESTORE = AD_ACTION_RESTORE
    DELETE = AD_ACTION_DELETE

    def __init__(
        self,
        gid,
        folder,
        manga_root,
        user_repository,
        action,
        page_count=0,
        cutoff_page_index=None,
        original_state=None,
    ):
        super().__init__()
        self.gid = int(gid)
        self.folder = Path(folder)
        self.manga_root = Path(manga_root)
        self.user_repository = user_repository
        self.action = str(action)
        self.page_count = max(0, int(page_count or 0))
        self.cutoff_page_index = (
            None if cutoff_page_index is None else int(cutoff_page_index)
        )
        self.original_state = original_state
        self.signals = GalleryAdCleanupSignals()

    def run(self):
        try:
            folder = self._validated_folder()
            if self.action == self.STAGE:
                self._stage(folder)
            elif self.action == self.RESTORE:
                self._restore(folder)
            elif self.action == self.DELETE:
                self._delete(folder)
            else:
                raise ValueError("未知的广告页文件操作")
            self.signals.completed.emit(self.gid, self.action)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            record = self.user_repository.gallery_ad_cleanup(self.gid)
            if record is not None:
                self.user_repository.update_gallery_ad_cleanup(
                    self.gid,
                    AD_CLEANUP_FAILED,
                    pending_action=self.action,
                    error=message,
                )
            self.signals.failed.emit(self.gid, message)

    def _stage(self, folder):
        existing = self.user_repository.gallery_ad_cleanup(self.gid)
        if existing is None:
            record = self._build_record(folder)
            self.user_repository.save_gallery_ad_cleanup(record)
        else:
            if existing.state == AD_CLEANUP_CLEANED:
                raise ValueError("这个画廊的广告尾页已经被永久清理")
            if existing.pending_action not in {"", AD_ACTION_STAGE}:
                raise ValueError("请先完成上次广告页文件操作")
            record = existing
            self.user_repository.update_gallery_ad_cleanup(
                self.gid, AD_CLEANUP_MOVING, AD_ACTION_STAGE, ""
            )
        self.signals.stageChanged.emit("正在把广告尾页移入 delete 目录…")
        for entry in record.manifest:
            source, target = self._manifest_paths(folder, entry)
            if target.exists():
                if source.exists():
                    raise FileExistsError(f"广告页源文件与暂存文件同时存在：{source.name}")
                continue
            if not source.is_file():
                raise FileNotFoundError(f"找不到待清理页面：{source.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        self.user_repository.update_gallery_ad_cleanup(
            self.gid, AD_CLEANUP_STAGED, "", ""
        )

    def _restore(self, folder):
        record = self._required_record()
        if record.state == AD_CLEANUP_CLEANED:
            raise ValueError("广告页已经永久删除，无法还原")
        if record.pending_action == AD_ACTION_DELETE:
            raise ValueError("广告页已开始永久删除，只能继续完成清理")
        self.user_repository.update_gallery_ad_cleanup(
            self.gid, AD_CLEANUP_RESTORING, AD_ACTION_RESTORE, ""
        )
        self.signals.stageChanged.emit("正在还原广告尾页…")
        for entry in reversed(record.manifest):
            source, target = self._manifest_paths(folder, entry)
            if source.exists():
                if target.exists():
                    raise FileExistsError(f"还原目标与暂存文件同时存在：{source.name}")
                continue
            if not target.is_file():
                raise FileNotFoundError(f"找不到暂存的广告页：{target.name}")
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
        self._remove_empty_delete_tree(folder)
        self.user_repository.delete_gallery_ad_cleanup(self.gid)

    def _delete(self, folder):
        record = self._required_record()
        if record.state == AD_CLEANUP_CLEANED:
            return
        if record.state != AD_CLEANUP_STAGED and not (
            record.state == AD_CLEANUP_FAILED
            and record.pending_action == AD_ACTION_DELETE
        ):
            raise ValueError("请先完成广告页暂存或还原操作")
        self.user_repository.update_gallery_ad_cleanup(
            self.gid, AD_CLEANUP_DELETING, AD_ACTION_DELETE, ""
        )
        self.signals.stageChanged.emit("正在永久删除广告尾页…")
        for entry in record.manifest:
            source, target = self._manifest_paths(folder, entry)
            if source.exists():
                raise ValueError("部分广告页已经回到画廊目录，请先执行还原")
            if target.is_file():
                target.unlink()
            elif target.exists():
                raise ValueError(f"广告页暂存目标不是文件：{target.name}")
        self._remove_empty_delete_tree(folder)
        self.user_repository.update_gallery_ad_cleanup(
            self.gid, AD_CLEANUP_CLEANED, "", ""
        )

    def _build_record(self, folder):
        if self.page_count <= 0:
            raise ValueError("无法确定画廊总页数")
        cutoff = self.cutoff_page_index
        if cutoff is None or not 0 <= cutoff < self.page_count:
            raise ValueError("广告页起始位置超出画廊页数")
        delete_folder = folder / "delete"
        if delete_folder.exists():
            if not delete_folder.is_dir() or delete_folder.is_symlink():
                raise ValueError("画廊内已有不可用的 delete 路径")
            if any(delete_folder.iterdir()):
                raise ValueError("画廊内已有未登记的 delete 内容，拒绝覆盖")

        active_original = bool(
            self.original_state is not None
            and self.original_state.state
            in {ORIGINAL_STATE_ACTIVE, ORIGINAL_STATE_CLEANING}
        )
        sources = []
        if active_original:
            sources.extend(
                (
                    (folder, "original"),
                    (folder / "history" / "del", "standard"),
                )
            )
        else:
            sources.extend(
                (
                    (folder, "standard"),
                    (folder / "original", "original"),
                )
            )

        manifest = []
        seen_targets = set()
        for source_folder, quality in sources:
            for index, path in sorted(self._numbered_images(source_folder).items()):
                if not cutoff <= index < self.page_count:
                    continue
                target = Path("delete") / quality / path.name
                target_key = str(target).casefold()
                if target_key in seen_targets:
                    raise ValueError(f"第 {index + 1} 页存在重复的广告页文件")
                seen_targets.add(target_key)
                manifest.append(
                    {
                        "source": path.relative_to(folder).as_posix(),
                        "target": target.as_posix(),
                    }
                )
        if not manifest:
            raise ValueError("从所选页面开始没有可清理的本地图片")
        return GalleryAdCleanupRecord(
            gid=self.gid,
            dirname=folder.name,
            cutoff_page_index=cutoff,
            page_count=self.page_count,
            state=AD_CLEANUP_MOVING,
            pending_action=AD_ACTION_STAGE,
            manifest=tuple(manifest),
        )

    def _required_record(self):
        record = self.user_repository.gallery_ad_cleanup(self.gid)
        if record is None:
            raise ValueError("这个画廊没有可处理的广告页记录")
        return record

    def _validated_folder(self):
        root = self.manga_root.resolve()
        if self.folder.is_symlink():
            raise ValueError("画廊目录不能是符号链接")
        folder = self.folder.resolve()
        if not folder.is_dir():
            raise FileNotFoundError("找不到广告页所属的画廊目录")
        if folder.parent != root:
            raise ValueError("广告页画廊必须是漫画根目录的直接子目录")
        return folder

    @staticmethod
    def _numbered_images(folder):
        folder = Path(folder)
        if not folder.is_dir() or folder.is_symlink():
            return {}
        result = {}
        for path in folder.iterdir():
            if (
                not path.is_file()
                or path.suffix.casefold() not in IMAGE_SUFFIXES
                or not path.stem.isdigit()
                or int(path.stem) <= 0
            ):
                continue
            index = int(path.stem) - 1
            if index in result:
                raise ValueError(f"第 {index + 1} 页存在多个图片文件")
            result[index] = path
        return result

    @staticmethod
    def _manifest_paths(folder, entry):
        root = Path(folder).resolve()
        paths = []
        for key in ("source", "target"):
            relative = Path(str(entry.get(key) or ""))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("广告页文件清单包含非法路径")
            candidate = (root / relative).resolve()
            if os.path.commonpath((str(root), str(candidate))) != str(root):
                raise ValueError("广告页文件清单超出画廊目录")
            paths.append(candidate)
        if paths[1].parts[: len(root.parts) + 1] != root.parts + ("delete",):
            raise ValueError("广告页暂存目标不在 delete 目录")
        return tuple(paths)

    @staticmethod
    def _remove_empty_delete_tree(folder):
        delete_folder = Path(folder) / "delete"
        if not delete_folder.is_dir() or delete_folder.is_symlink():
            return
        for path in sorted(
            delete_folder.rglob("*"), key=lambda value: len(value.parts), reverse=True
        ):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            delete_folder.rmdir()
        except OSError:
            pass
