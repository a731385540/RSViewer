from collections import deque
from threading import Lock

from PySide6.QtCore import QObject, QRunnable, QThreadPool


class _ScheduledPageTask(QRunnable):
    def __init__(self, scheduler, gallery_key, task):
        super().__init__()
        self.scheduler = scheduler
        self.gallery_key = gallery_key
        self.task = task

    def run(self):
        keep_gallery_queued = False
        try:
            keep_gallery_queued = self.task.run() is not False
        finally:
            self.scheduler._taskFinished(
                self.gallery_key,
                keep_gallery_queued,
            )


class GalleryPageDownloadScheduler(QObject):
    """Share a bounded page-download pool fairly between active galleries."""

    def __init__(self, max_threads=6, parent=None):
        super().__init__(parent)
        self.threadPool = QThreadPool(self)
        self._lock = Lock()
        self._queues = {}
        self._round_robin = deque()
        self._active_count = 0
        self._max_threads = 1
        self.setThreadCount(max_threads)

    def setThreadCount(self, count):
        count = min(6, max(1, int(count)))
        with self._lock:
            self._max_threads = count
            self.threadPool.setMaxThreadCount(count)
            pending = self._takeRunnableTasksLocked()
        self._startTasks(pending)

    def submit(self, gallery_key, task):
        self.submitMany(gallery_key, (task,))

    def submitMany(self, gallery_key, tasks):
        tasks = tuple(tasks)
        if not tasks:
            return
        with self._lock:
            queue = self._queues.get(gallery_key)
            if queue is None:
                queue = deque()
                self._queues[gallery_key] = queue
                self._round_robin.append(gallery_key)
            queue.extend(tasks)
            pending = self._takeRunnableTasksLocked()
        self._startTasks(pending)

    def cancel(self, gallery_key):
        with self._lock:
            cancelled = self._removeQueuedTasksLocked(gallery_key)
            pending = self._takeRunnableTasksLocked()
        for task in cancelled:
            task.cancelPending()
        self._startTasks(pending)

    def activeCount(self):
        with self._lock:
            return self._active_count

    def queuedCount(self):
        with self._lock:
            return sum(len(queue) for queue in self._queues.values())

    def _taskFinished(self, gallery_key, keep_gallery_queued):
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            cancelled = (
                []
                if keep_gallery_queued
                else self._removeQueuedTasksLocked(gallery_key)
            )
            pending = self._takeRunnableTasksLocked()
        for task in cancelled:
            task.cancelPending()
        self._startTasks(pending)

    def _takeRunnableTasksLocked(self):
        runnable = []
        while (
            self._active_count < self._max_threads
            and self._round_robin
        ):
            gallery_key = self._round_robin.popleft()
            queue = self._queues.get(gallery_key)
            if not queue:
                self._queues.pop(gallery_key, None)
                continue
            task = queue.popleft()
            self._active_count += 1
            runnable.append((gallery_key, task))
            if queue:
                self._round_robin.append(gallery_key)
            else:
                self._queues.pop(gallery_key, None)
        return runnable

    def _removeQueuedTasksLocked(self, gallery_key):
        queue = self._queues.pop(gallery_key, None)
        if self._round_robin:
            self._round_robin = deque(
                key for key in self._round_robin if key != gallery_key
            )
        return list(queue or ())

    def _startTasks(self, tasks):
        for gallery_key, task in tasks:
            self.threadPool.start(
                _ScheduledPageTask(self, gallery_key, task)
            )
