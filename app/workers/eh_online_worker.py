from PySide6.QtCore import QObject, QRunnable, Signal


class OnlineSearchSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class OnlineSearchWorker(QRunnable):
    def __init__(self, source, query="", page_url=""):
        super().__init__()
        self.source = source
        self.query = query
        self.page_url = page_url
        self.cancelled = False
        self.signals = OnlineSearchSignals()

    def run(self):
        try:
            page = self.source.search(self.query, self.page_url)
        except Exception as error:
            if not self.cancelled:
                self.signals.failed.emit(str(error))
            return
        if not self.cancelled:
            self.signals.loaded.emit(page)


class OnlineCoverSignals(QObject):
    loaded = Signal(int, bytes)


class OnlineCoverWorker(QRunnable):
    def __init__(self, source, items):
        super().__init__()
        self.source = source
        self.items = tuple(items)
        self.cancelled = False
        self.signals = OnlineCoverSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            try:
                data = self.source.load_thumbnail(item.thumbnail_url)
            except Exception:
                data = b""
            if self.cancelled:
                return
            self.signals.loaded.emit(item.gid, data)
