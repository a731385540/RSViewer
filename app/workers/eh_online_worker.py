from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage


class OnlineSearchSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class OnlineSearchWorker(QRunnable):
    def __init__(self, provider, query, display_mode=None):
        super().__init__()
        self.provider = provider
        self.query = query
        self.display_mode = display_mode
        self.cancelled = False
        self.signals = OnlineSearchSignals()

    def run(self):
        try:
            if self.display_mode:
                self.provider.set_display_mode(self.display_mode)
            if self.cancelled:
                return
            page = self.provider.search(self.query)
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.loaded.emit(page)
            except RuntimeError:
                pass


class OnlineCoverSignals(QObject):
    loaded = Signal(int, bytes)
    finished = Signal()


class OnlineCoverWorker(QRunnable):
    def __init__(self, provider, item, cache, site, cache_hours):
        super().__init__()
        self.provider = provider
        self.item = item
        self.cache = cache
        self.site = site
        self.cache_hours = cache_hours
        self.cancelled = False
        self.signals = OnlineCoverSignals()

    def run(self):
        try:
            if self.cancelled or not self.item.thumbnail_url:
                return
            data = self.cache.get(
                self.site,
                self.item.thumbnail_url,
                self.cache_hours,
            )
            if data is not None and QImage.fromData(data).isNull():
                self.cache.discard(self.site, self.item.thumbnail_url)
                data = None
            if data is None:
                try:
                    data = self.provider.load_thumbnail(self.item.thumbnail_url)
                except Exception:
                    data = b""
                if data and QImage.fromData(data).isNull():
                    data = b""
                if data:
                    self.cache.put(self.site, self.item.thumbnail_url, data)
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(self.item.gid, data or b"")
                except RuntimeError:
                    pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass
