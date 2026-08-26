import logging
import time

from PySide6.QtCore import QObject, QThreadPool, Signal

from app.services.gallery_page_download_scheduler import (
    GalleryPageDownloadScheduler,
)


logger = logging.getLogger(__name__)


class MultiWindowCoordinator(QObject):
    """Own process-wide workers and relay state changes between windows."""

    stateChanged = Signal(object, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._windows = []
        self._startupRecoveryClaimed = False
        self._shuttingDown = False
        self._adCleanupOwners = {}
        self.similarGalleryWindow = None
        self.onlineDownloadThreadPool = QThreadPool(self)
        self.onlineDownloadThreadPool.setMaxThreadCount(3)
        self.galleryPageDownloadScheduler = GalleryPageDownloadScheduler(6, self)
        self.downloadRegistrationThreadPool = QThreadPool(self)
        self.downloadRegistrationThreadPool.setMaxThreadCount(1)
        self.galleryUpdateThreadPool = QThreadPool(self)
        self.galleryUpdateThreadPool.setMaxThreadCount(1)
        self.originalFileThreadPool = QThreadPool(self)
        self.originalFileThreadPool.setMaxThreadCount(1)
        self.adCleanupThreadPool = QThreadPool(self)
        self.adCleanupThreadPool.setMaxThreadCount(1)
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
        if self._shuttingDown:
            raise RuntimeError("应用正在退出，不能再注册窗口")
        if window not in self._windows:
            self._windows.append(window)
            logger.info(
                "Window registered window_id=%s window_count=%s",
                id(window),
                len(self._windows),
            )

    def unregister(self, window):
        try:
            self._windows.remove(window)
        except ValueError:
            pass
        logger.info(
            "Window unregistered window_id=%s window_count=%s",
            id(window),
            len(self._windows),
        )
        return not self._windows

    def windows(self):
        return tuple(self._windows)

    def shutdown(self, timeout=4000):
        """Stop process-wide queues after the final application window closes."""

        if self._shuttingDown:
            return True
        self._shuttingDown = True
        logger.info(
            "Shared worker shutdown started windows=%s timeout_ms=%s",
            len(self._windows),
            timeout,
        )
        timeout = max(0, int(timeout))
        deadline = time.monotonic() + timeout / 1000
        pools = (
            self.onlineDownloadThreadPool,
            self.downloadRegistrationThreadPool,
            self.galleryUpdateThreadPool,
            self.originalFileThreadPool,
            self.adCleanupThreadPool,
            self.organizerThreadPool,
            self.trashThreadPool,
        )
        for pool in pools:
            pool.clear()
        remaining = max(0, round((deadline - time.monotonic()) * 1000))
        completed = self.galleryPageDownloadScheduler.shutdown(remaining)
        for pool in pools:
            remaining = max(0, round((deadline - time.monotonic()) * 1000))
            completed = pool.waitForDone(remaining) and completed
        if completed:
            self._adCleanupOwners.clear()
        logger.info("Shared worker shutdown completed=%s", completed)
        return completed

    def publish(self, source, scope, payload=None):
        self.stateChanged.emit(source, str(scope), payload)

    def setDownloadConcurrency(self, count):
        self.onlineDownloadThreadPool.setMaxThreadCount(
            min(3, max(1, int(count)))
        )

    def setPageDownloadThreads(self, count):
        self.galleryPageDownloadScheduler.setThreadCount(count)

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

    def adCleanupOwner(self, gid):
        return self._adCleanupOwners.get(int(gid))

    def registerAdCleanupOwner(self, gid, window):
        gid = int(gid)
        owner = self._adCleanupOwners.get(gid)
        if owner is not None and owner is not window:
            raise ValueError("这个画廊正在执行广告页文件操作")
        self._adCleanupOwners[gid] = window

    def releaseAdCleanupOwner(self, gid, window):
        gid = int(gid)
        if self._adCleanupOwners.get(gid) is window:
            self._adCleanupOwners.pop(gid, None)

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
            or self.adCleanupOwner(gid) is not None
            or self.hasTrashOperation(gid)
        ):
            return True
        for window in self.windows():
            reader = getattr(window, "mangaReaderInterface", None)
            stack = getattr(window, "stackedWidget", None)
            reader_item = getattr(reader, "currentItem", None) if reader else None
            if (
                reader is not None
                and stack is not None
                and stack.currentWidget() is reader
                and reader_item is not None
                and int(reader_item.gid) == gid
            ):
                return True
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
