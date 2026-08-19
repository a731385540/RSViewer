from PySide6.QtCore import QObject, QRunnable, Signal


class ReadingProgressClearSignals(QObject):
    succeeded = Signal(int)
    failed = Signal(int, str)


class ReadingProgressSaveWorker(QRunnable):
    """Persist one reading position without blocking the GUI thread."""

    def __init__(self, repository, gid: int, page_index: int, completed=False):
        super().__init__()
        self.repository = repository
        self.gid = int(gid)
        self.page_index = max(0, int(page_index))
        self.completed = bool(completed)

    def run(self):
        self.repository.save_progress(
            self.gid, self.page_index, completed=self.completed
        )


class ReadingProgressClearWorker(QRunnable):
    def __init__(self, repository, gid: int):
        super().__init__()
        self.repository = repository
        self.gid = int(gid)
        self.signals = ReadingProgressClearSignals()

    def run(self):
        try:
            self.repository.clear_progress(self.gid)
        except Exception as error:
            try:
                self.signals.failed.emit(self.gid, str(error))
            except RuntimeError:
                pass
            return
        try:
            self.signals.succeeded.emit(self.gid)
        except RuntimeError:
            pass


class PlaylistPositionSaveWorker(QRunnable):
    """Persist the active playlist manga without blocking navigation."""

    def __init__(self, repository, playlist_id: int, gid: int):
        super().__init__()
        self.repository = repository
        self.playlist_id = int(playlist_id)
        self.gid = int(gid)

    def run(self):
        self.repository.save_playlist_position(self.playlist_id, self.gid)


class BrowsingHistorySaveWorker(QRunnable):
    """Persist one local gallery visit without blocking navigation."""

    def __init__(self, repository, gid: int):
        super().__init__()
        self.repository = repository
        self.gid = int(gid)

    def run(self):
        self.repository.record_browsing_history(self.gid)
