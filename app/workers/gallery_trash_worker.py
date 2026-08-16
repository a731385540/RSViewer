from PySide6.QtCore import QObject, QRunnable, Signal

from app.repositories.ehviewer_download_repository import EhViewerDownloadRepository
from app.services.gallery_trash import (
    GalleryTrashActionResult,
    permanently_delete_trashed_gallery,
    restore_trashed_gallery,
    trash_local_gallery,
)


class GalleryTrashSignals(QObject):
    progress = Signal(int, int, str)
    completed = Signal(object)


class GalleryTrashWorker(QRunnable):
    TRASH = "trash"
    RESTORE = "restore"
    DELETE = "delete"

    def __init__(
        self,
        action,
        entries,
        external_repository,
        user_repository,
        manga_root,
    ):
        super().__init__()
        if action not in {self.TRASH, self.RESTORE, self.DELETE}:
            raise ValueError("未知的回收站操作")
        self.action = action
        self.entries = tuple(entries)
        self.external_repository = external_repository
        self.user_repository = user_repository
        self.manga_root = manga_root
        self.cancelled = False
        self.signals = GalleryTrashSignals()

    def run(self):
        succeeded = []
        failed = []
        total = len(self.entries)
        for index, entry in enumerate(self.entries, 1):
            if self.cancelled:
                break
            title = getattr(entry, "display_title", None) or getattr(
                entry, "title", str(getattr(entry, "gid", ""))
            )
            self.signals.progress.emit(index, total, str(title))
            try:
                external_repository = self.external_repository
                manga_root = self.manga_root
                if self.action != self.TRASH:
                    database_path = getattr(entry, "database_path", None)
                    recorded_root = getattr(entry, "manga_root", None)
                    if database_path and recorded_root:
                        external_repository = EhViewerDownloadRepository(
                            database_path, recorded_root
                        )
                        manga_root = recorded_root
                if self.action == self.TRASH:
                    trash_local_gallery(
                        entry,
                        external_repository,
                        self.user_repository,
                    )
                elif self.action == self.RESTORE:
                    restore_trashed_gallery(
                        entry,
                        external_repository,
                        self.user_repository,
                    )
                else:
                    permanently_delete_trashed_gallery(
                        entry,
                        external_repository,
                        self.user_repository,
                        manga_root,
                    )
            except Exception as error:
                failed.append((entry, str(error)))
            else:
                succeeded.append(entry)
        self.signals.completed.emit(
            GalleryTrashActionResult(tuple(succeeded), tuple(failed))
        )
