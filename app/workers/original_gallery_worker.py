import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImageReader

from app.domain.online_download import (
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_CLEANING,
    ORIGINAL_STATE_REPLACING_BASE,
    ORIGINAL_STATE_REPLACING_ORIGINAL,
    ORIGINAL_STATE_STAGED,
)
from app.sources.ehviewer_source import IMAGE_SUFFIXES


class OriginalGalleryFileSignals(QObject):
    stageChanged = Signal(str)
    completed = Signal(int, str)
    failed = Signal(int, str)


class OriginalGalleryFileWorker(QRunnable):
    """Resume the local original-image promotion or explicit backup cleanup."""

    REPLACE = "replace"
    CLEANUP = "cleanup"

    def __init__(
        self,
        record,
        manga_root,
        user_repository,
        action,
        ehviewer_repository=None,
    ):
        super().__init__()
        self.record = record
        self.manga_root = Path(manga_root)
        self.user_repository = user_repository
        self.action = str(action)
        self.ehviewer_repository = ehviewer_repository
        self.signals = OriginalGalleryFileSignals()

    def run(self):
        gid = int(self.record.gid)
        try:
            folder = self._validated_folder()
            if self.action == self.REPLACE:
                self._replace(folder)
            elif self.action == self.CLEANUP:
                self._cleanup(folder)
            else:
                raise ValueError("未知的原图文件操作")
            self.signals.completed.emit(gid, self.action)
        except Exception as error:
            message = str(error) or error.__class__.__name__
            current = self.user_repository.gallery_original_state(gid)
            if current is not None:
                self.user_repository.update_gallery_original_state(
                    gid, current.state, error=message
                )
            self.signals.failed.emit(gid, message)

    def _replace(self, folder):
        gid = int(self.record.gid)
        total = int(self.record.page_count)
        original = folder / "original"
        backup = folder / "history" / "del"
        state = self.user_repository.gallery_original_state(gid)
        if state is None or state.state not in {
            ORIGINAL_STATE_STAGED,
            ORIGINAL_STATE_REPLACING_BASE,
            ORIGINAL_STATE_REPLACING_ORIGINAL,
        }:
            raise ValueError("当前画廊没有可提升的完整原图下载内容")

        if state.state == ORIGINAL_STATE_STAGED:
            originals = self._numbered_images(original)
            self._require_complete(originals, total, "原图下载暂存目录")
            self.user_repository.update_gallery_original_state(
                gid, ORIGINAL_STATE_REPLACING_BASE, error=""
            )
            state = self.user_repository.gallery_original_state(gid)

        if state.state == ORIGINAL_STATE_REPLACING_BASE:
            self.signals.stageChanged.emit("正在归档基础压缩图…")
            backup.mkdir(parents=True, exist_ok=True)
            for _index, source in sorted(self._numbered_images(folder).items()):
                target = backup / source.name
                if target.exists():
                    raise FileExistsError(f"压缩图归档目标已存在：{target.name}")
                source.rename(target)
            if self._numbered_images(folder):
                raise ValueError("基础压缩图尚未全部归档")
            self.user_repository.update_gallery_original_state(
                gid, ORIGINAL_STATE_REPLACING_ORIGINAL, error=""
            )

        self.signals.stageChanged.emit("正在将原图下载内容提升为画廊页面…")
        originals = self._numbered_images(original)
        active = self._numbered_images(folder)
        for index in range(total):
            if index in active:
                continue
            source = originals.get(index)
            if source is None:
                raise ValueError(f"缺少待提升的第 {index + 1} 页下载内容")
            target = folder / source.name
            if target.exists():
                raise FileExistsError(f"原图提升目标已存在：{target.name}")
            source.rename(target)
            active[index] = target
        self._require_complete(self._numbered_images(folder), total, "画廊根目录")
        if self._numbered_images(original):
            raise ValueError("original 目录仍有尚未提升的下载内容")
        try:
            original.rmdir()
        except OSError:
            pass
        if self.ehviewer_repository is not None:
            self.ehviewer_repository.touch_download_time(gid)
        self.user_repository.update_gallery_original_state(
            gid,
            ORIGINAL_STATE_ACTIVE,
            completed_pages=total,
            page_count=total,
            error="",
        )

    def _cleanup(self, folder):
        gid = int(self.record.gid)
        backup = folder / "history" / "del"
        state = self.user_repository.gallery_original_state(gid)
        if state is None or state.state not in {
            ORIGINAL_STATE_ACTIVE,
            ORIGINAL_STATE_CLEANING,
        }:
            raise ValueError("当前画廊没有可清理的压缩图备份")
        self.user_repository.update_gallery_original_state(
            gid, ORIGINAL_STATE_CLEANING, error=""
        )
        self.signals.stageChanged.emit("正在删除用户确认的压缩图备份…")
        if backup.is_dir():
            for path in sorted(
                backup.rglob("*"), key=lambda entry: len(entry.parts), reverse=True
            ):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            backup.rmdir()
        self.user_repository.update_gallery_original_state(
            gid, ORIGINAL_STATE_ACTIVE, error=""
        )

    def _validated_folder(self):
        root = self.manga_root.resolve()
        folder = (root / self.record.dirname).resolve()
        if not folder.is_dir():
            raise FileNotFoundError("找不到原图画廊目录")
        if os.path.commonpath((str(root), str(folder))) != str(root):
            raise ValueError("原图画廊目录超出配置的漫画根目录")
        return folder

    @staticmethod
    def _numbered_images(folder):
        folder = Path(folder)
        if not folder.is_dir():
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
    def _require_complete(paths, total, label):
        if set(paths) != set(range(total)):
            raise ValueError(f"{label} 图片集合不完整")
        for index, path in paths.items():
            reader = QImageReader(str(path))
            if not (
                path.stat().st_size > 0
                and reader.canRead()
                and reader.size().isValid()
            ):
                raise ValueError(f"{label} 第 {index + 1} 页无法解码")
