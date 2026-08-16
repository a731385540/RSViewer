from PySide6.QtCore import QObject, QThreadPool, Signal


class MultiWindowCoordinator(QObject):
    """Own process-wide workers and relay state changes between windows."""

    stateChanged = Signal(object, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._windows = []
        self._startupRecoveryClaimed = False
        self.onlineDownloadThreadPool = QThreadPool(self)
        self.onlineDownloadThreadPool.setMaxThreadCount(3)
        self.galleryUpdateThreadPool = QThreadPool(self)
        self.galleryUpdateThreadPool.setMaxThreadCount(1)
        self.originalFileThreadPool = QThreadPool(self)
        self.originalFileThreadPool.setMaxThreadCount(1)
        self.organizerThreadPool = QThreadPool(self)
        self.organizerThreadPool.setMaxThreadCount(1)
        self.trashThreadPool = QThreadPool(self)
        self.trashThreadPool.setMaxThreadCount(1)

    def claimStartupRecovery(self):
        if self._startupRecoveryClaimed:
            return False
        self._startupRecoveryClaimed = True
        return True

    def register(self, window):
        if window not in self._windows:
            self._windows.append(window)

    def unregister(self, window):
        try:
            self._windows.remove(window)
        except ValueError:
            pass

    def windows(self):
        return tuple(self._windows)

    def publish(self, source, scope, payload=None):
        self.stateChanged.emit(source, str(scope), payload)

    def setDownloadConcurrency(self, count):
        self.onlineDownloadThreadPool.setMaxThreadCount(
            min(3, max(1, int(count)))
        )

    def downloadOwner(self, gid):
        gid = int(gid)
        for window in self.windows():
            if (
                gid in window._onlineDownloadWorkers
                or gid in window._localDownloadPrepareWorkers
            ):
                return window
        return None

    def originalOwner(self, gid):
        gid = int(gid)
        for window in self.windows():
            if gid in window._originalFileWorkers:
                return window
        return None

    def updateOwner(self, gid):
        gid = int(gid)
        for window in self.windows():
            if gid in window._galleryUpdateWorkers:
                return window
        return None

    def downloadActivity(self):
        active = set()
        speeds = {}
        for window in self.windows():
            active.update(int(gid) for gid in window._onlineDownloadWorkers)
            active.update(int(gid) for gid in window._localDownloadPrepareWorkers)
            for gid, speed in window._onlineDownloadSpeeds.items():
                speeds[int(gid)] = float(speed)
        return active, speeds

    def updateActivity(self):
        active = set()
        speeds = {}
        for window in self.windows():
            active.update(int(gid) for gid in window._galleryUpdateWorkers)
            for gid, speed in window._galleryUpdateSpeeds.items():
                speeds[int(gid)] = float(speed)
        return active, speeds

    def hasOriginalOperation(self, gid):
        return self.originalOwner(gid) is not None

    def organizerBusy(self):
        return any(
            getattr(window, "_organizerWorker", None) is not None
            for window in self.windows()
        )

    def trashBusy(self):
        return any(
            getattr(window, "_trashWorker", None) is not None
            for window in self.windows()
        )

    def hasTrashOperation(self, gid):
        gid = int(gid)
        for window in self.windows():
            worker = getattr(window, "_trashWorker", None)
            if worker is not None and any(
                int(getattr(entry, "gid", 0)) == gid
                for entry in getattr(worker, "entries", ())
            ):
                return True
        return False

    def galleryMutationBusy(self, gid):
        gid = int(gid)
        if (
            self.downloadOwner(gid) is not None
            or self.updateOwner(gid) is not None
            or self.originalOwner(gid) is not None
            or self.hasTrashOperation(gid)
        ):
            return True
        for window in self.windows():
            page_workers = getattr(window, "_localPageDownloadWorkers", {})
            if any(int(key[0]) == gid for key in page_workers):
                return True
            worker = getattr(window, "_localMetadataSyncWorker", None)
            item = getattr(worker, "item", None) if worker is not None else None
            if item is not None and int(item.gid) == gid:
                return True
            batch_workers = getattr(window, "_localMetadataBatchWorkers", {})
            if gid in batch_workers.values():
                return True
        return False


_coordinator = None


def application_window_coordinator():
    global _coordinator
    if _coordinator is None:
        _coordinator = MultiWindowCoordinator()
    return _coordinator
