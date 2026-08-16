from PySide6.QtCore import QObject, QRunnable, Signal

from app.services.library_organizer import (
    OrganizerActionResult,
    recycle_orphan_gallery_folder,
    scan_orphan_gallery_folders,
    sync_orphan_gallery_folder,
)


class LibraryOrganizerScanSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class LibraryOrganizerScanWorker(QRunnable):
    def __init__(
        self,
        database_path,
        manga_root,
        user_repository,
        default_site,
    ):
        super().__init__()
        self.database_path = database_path
        self.manga_root = manga_root
        self.user_repository = user_repository
        self.default_site = default_site
        self.cancelled = False
        self.signals = LibraryOrganizerScanSignals()

    def run(self):
        try:
            records = scan_orphan_gallery_folders(
                self.database_path,
                self.manga_root,
                self.user_repository,
                self.default_site,
            )
        except Exception as error:
            if not self.cancelled:
                self.signals.failed.emit(str(error))
            return
        if not self.cancelled:
            self.signals.loaded.emit(records)


class LibraryOrganizerActionSignals(QObject):
    progress = Signal(int, int, str)
    completed = Signal(object)


class LibraryOrganizerActionWorker(QRunnable):
    SYNC = "sync"
    DELETE = "delete"

    def __init__(
        self,
        action,
        entries,
        database_path,
        manga_root,
        user_repository,
    ):
        super().__init__()
        if action not in {self.SYNC, self.DELETE}:
            raise ValueError("未知的整理操作")
        self.action = action
        self.entries = tuple(entries)
        self.database_path = database_path
        self.manga_root = manga_root
        self.user_repository = user_repository
        self.cancelled = False
        self.signals = LibraryOrganizerActionSignals()

    def run(self):
        succeeded = []
        failed = []
        total = len(self.entries)
        for index, entry in enumerate(self.entries, 1):
            if self.cancelled:
                break
            self.signals.progress.emit(index, total, entry.title or entry.dirname)
            try:
                if self.action == self.SYNC:
                    sync_orphan_gallery_folder(
                        entry,
                        self.database_path,
                        self.manga_root,
                        self.user_repository,
                    )
                else:
                    recycle_orphan_gallery_folder(entry.folder, self.manga_root)
            except Exception as error:
                failed.append((entry, str(error)))
            else:
                succeeded.append(entry)
        self.signals.completed.emit(
            OrganizerActionResult(tuple(succeeded), tuple(failed))
        )
