import re
import time

from PySide6.QtCore import QObject, QRunnable, Signal

from app.domain.similar_gallery import LatestSimilarSearch
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


class SelectedTitleSearchWorker(QRunnable):
    """Persist the latest literal title-fragment search away from the UI thread."""

    def __init__(self, repository, source_gid, selected_text, items):
        super().__init__()
        self.repository = repository
        self.source_gid = int(source_gid)
        self.selected_text = " ".join(str(selected_text).split())
        self.items = tuple(items)
        self.cancelled = False
        self.signals = SimilarMangaSignals()

    def run(self):
        try:
            effective = re.sub(r"\s+", "", self.selected_text)
            if len(effective) < 2:
                raise ValueError("至少选择两个有效字符")
            needle = self.selected_text.casefold()
            matches = tuple(
                item
                for item in self.items
                if int(item.gid) != self.source_gid
                and any(
                    needle in str(title or "").casefold()
                    for title in (item.english_title, item.original_title)
                )
            )
            matches = tuple(
                sorted(
                    matches,
                    key=lambda item: (item.added_time, item.gid),
                    reverse=True,
                )
            )
            if self.cancelled:
                return
            record = LatestSimilarSearch(
                source_gid=self.source_gid,
                selected_text=self.selected_text,
                result_gids=tuple(item.gid for item in matches),
                searched_at=time.time_ns(),
            )
            self.repository.save_latest_similar_search(record)
            if not self.cancelled:
                self.signals.found.emit((record, matches))
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
