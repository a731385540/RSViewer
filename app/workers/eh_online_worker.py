from PySide6.QtCore import QObject, QRunnable, Signal


class OnlineSearchSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class OnlineSearchWorker(QRunnable):
    def __init__(self, provider, query):
        super().__init__()
        self.provider = provider
        self.query = query
        self.cancelled = False
        self.signals = OnlineSearchSignals()

    def run(self):
        try:
            page = self.provider.search(self.query)
        except Exception as error:
            if not self.cancelled:
                self.signals.failed.emit(str(error))
            return
        if not self.cancelled:
            self.signals.loaded.emit(page)


class OnlineCoverSignals(QObject):
    loaded = Signal(int, bytes)


class OnlineCoverWorker(QRunnable):
    def __init__(self, provider, items):
        super().__init__()
        self.provider = provider
        self.items = tuple(items)
        self.cancelled = False
        self.signals = OnlineCoverSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            try:
                data = self.provider.load_thumbnail(item.thumbnail_url)
            except Exception:
                data = b""
            if self.cancelled:
                return
            self.signals.loaded.emit(item.gid, data)
