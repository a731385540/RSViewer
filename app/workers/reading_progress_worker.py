from PySide6.QtCore import QRunnable


class ReadingProgressSaveWorker(QRunnable):
    """Persist one reading position without blocking the GUI thread."""

    def __init__(self, repository, gid: int, page_index: int):
        super().__init__()
        self.repository = repository
        self.gid = int(gid)
        self.page_index = max(0, int(page_index))

    def run(self):
        self.repository.save_progress(self.gid, self.page_index)


class PlaylistPositionSaveWorker(QRunnable):
    """Persist the active playlist manga without blocking navigation."""

    def __init__(self, repository, playlist_id: int, gid: int):
        super().__init__()
        self.repository = repository
        self.playlist_id = int(playlist_id)
        self.gid = int(gid)

    def run(self):
        self.repository.save_playlist_position(self.playlist_id, self.gid)
