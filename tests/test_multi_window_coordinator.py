import os
import threading
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRunnable
from PySide6.QtWidgets import QApplication

from app.services.multi_window_coordinator import MultiWindowCoordinator


class MultiWindowCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.coordinator = MultiWindowCoordinator()

    @staticmethod
    def _window(downloads=(), preparations=(), updates=(), originals=()):
        return SimpleNamespace(
            _closing=False,
            _onlineDownloadWorkers={gid: object() for gid in downloads},
            _localDownloadPrepareWorkers={gid: object() for gid in preparations},
            _onlineDownloadSpeeds={gid: gid * 10.0 for gid in downloads},
            _galleryUpdateWorkers={gid: object() for gid in updates},
            _galleryUpdateSpeeds={gid: gid * 5.0 for gid in updates},
            _originalFileWorkers={gid: object() for gid in originals},
            _organizerWorker=None,
        )

    def test_startup_recovery_is_claimed_only_once(self):
        self.assertTrue(self.coordinator.claimStartupRecovery())
        self.assertFalse(self.coordinator.claimStartupRecovery())

    def test_activity_and_owner_are_aggregated_across_windows(self):
        first = self._window(downloads=(1,), updates=(7,))
        second = self._window(preparations=(2,), originals=(9,))
        self.coordinator.register(first)
        self.coordinator.register(second)

        active_downloads, speeds = self.coordinator.downloadActivity()
        active_updates, update_speeds = self.coordinator.updateActivity()

        self.assertEqual({1, 2}, active_downloads)
        self.assertEqual({1: 10.0}, speeds)
        self.assertEqual({7}, active_updates)
        self.assertEqual({7: 35.0}, update_speeds)
        self.assertIs(first, self.coordinator.downloadOwner(1))
        self.assertIs(second, self.coordinator.downloadOwner(2))
        self.assertIs(first, self.coordinator.updateOwner(7))
        self.assertTrue(self.coordinator.hasOriginalOperation(9))

    def test_process_wide_pool_limits_are_enforced(self):
        self.coordinator.setDownloadConcurrency(6)
        self.coordinator.setPageDownloadThreads(20)
        self.assertEqual(3, self.coordinator.onlineDownloadThreadPool.maxThreadCount())
        self.assertEqual(
            6,
            self.coordinator.galleryPageDownloadScheduler.threadPool.maxThreadCount(),
        )
        self.assertEqual(
            1,
            self.coordinator.downloadRegistrationThreadPool.maxThreadCount(),
        )
        self.assertEqual(1, self.coordinator.galleryUpdateThreadPool.maxThreadCount())

    def test_gallery_limit_one_keeps_other_galleries_queued(self):
        lock = threading.Lock()
        release = threading.Event()
        first_started = threading.Event()
        all_finished = threading.Event()
        counts = {"started": 0, "finished": 0}

        class GalleryTask(QRunnable):
            def run(self):
                with lock:
                    counts["started"] += 1
                    first_started.set()
                release.wait(2)
                with lock:
                    counts["finished"] += 1
                    if counts["finished"] == 10:
                        all_finished.set()

        self.coordinator.setDownloadConcurrency(1)
        for _ in range(10):
            self.coordinator.onlineDownloadThreadPool.start(GalleryTask())

        self.assertTrue(first_started.wait(1))
        with lock:
            self.assertEqual(1, counts["started"])
        release.set()
        self.assertTrue(all_finished.wait(2))

    def test_active_galleries_share_one_page_thread_limit(self):
        lock = threading.Lock()
        release = threading.Event()
        limit_reached = threading.Event()
        all_finished = threading.Event()
        counts = {"active": 0, "maximum": 0, "finished": 0}

        class PageTask:
            def run(self):
                with lock:
                    counts["active"] += 1
                    counts["maximum"] = max(
                        counts["maximum"], counts["active"]
                    )
                    if counts["active"] == 2:
                        limit_reached.set()
                release.wait(2)
                with lock:
                    counts["active"] -= 1
                    counts["finished"] += 1
                    if counts["finished"] == 8:
                        all_finished.set()
                return True

            def cancelPending(self):
                raise AssertionError("successful tasks must not be cancelled")

        scheduler = self.coordinator.galleryPageDownloadScheduler
        scheduler.setThreadCount(2)
        scheduler.submitMany("gallery-a", (PageTask() for _ in range(4)))
        scheduler.submitMany("gallery-b", (PageTask() for _ in range(4)))

        self.assertTrue(limit_reached.wait(1))
        self.assertEqual(2, scheduler.activeCount())
        release.set()
        self.assertTrue(all_finished.wait(2))
        self.assertEqual(2, counts["maximum"])

    def test_queued_trash_target_blocks_other_gallery_mutations(self):
        window = self._window()
        window._trashWorker = SimpleNamespace(
            entries=(SimpleNamespace(gid=42),)
        )
        self.coordinator.register(window)

        self.assertTrue(self.coordinator.hasTrashOperation(42))
        self.assertTrue(self.coordinator.galleryMutationBusy(42))
        self.assertFalse(self.coordinator.galleryMutationBusy(43))


if __name__ == "__main__":
    unittest.main()
