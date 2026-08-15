from PySide6.QtCore import QObject, QRunnable, Signal

from app.services.manga_title_similarity import find_similar_manga


class SimilarMangaSignals(QObject):
    found = Signal(object)
    failed = Signal(str)


class SimilarMangaWorker(QRunnable):
    """Compare already-loaded manga metadata away from the GUI thread."""

    def __init__(self, reference, items):
        super().__init__()
        self.reference = reference
        self.items = tuple(items)
        self.cancelled = False
        self.signals = SimilarMangaSignals()

    def run(self):
        try:
            matches = find_similar_manga(
                self.reference,
                self.items,
                should_cancel=lambda: self.cancelled,
            )
            if not self.cancelled:
                try:
                    self.signals.found.emit(matches)
                except RuntimeError:
                    pass
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
