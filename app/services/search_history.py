import re
import unicodedata

from PySide6.QtCore import QObject, Signal


class SearchHistoryService(QObject):
    """Shared, persisted search history for local and online resource inputs."""

    historyChanged = Signal(object)

    def __init__(self, repository, limit=20, parent=None):
        super().__init__(parent)
        self._repository = repository
        self._limit = _bounded_limit(limit)
        repository.trim_search_history(self._limit)
        self._items = tuple(repository.list_search_history(self._limit))

    @property
    def items(self):
        return self._items

    @property
    def limit(self):
        return self._limit

    def record(self, query: str):
        query = " ".join(str(query or "").strip().split())
        if not query:
            return
        self._repository.save_search_history(query, self._limit)
        self._reload()

    def setLimit(self, limit):
        limit = _bounded_limit(limit)
        if limit == self._limit:
            return
        self._limit = limit
        self._repository.trim_search_history(limit)
        self._reload()

    def search(self, query: str):
        query = _normalize(query)
        if not query:
            return list(self._items)
        return [item for item in self._items if query in _normalize(item)]

    def _reload(self):
        items = tuple(self._repository.list_search_history(self._limit))
        if items == self._items:
            return
        self._items = items
        self.historyChanged.emit(items)


def _bounded_limit(value):
    return max(1, min(20, int(value)))


def _normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip()
