import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
        self.assertEqual(3, self.coordinator.onlineDownloadThreadPool.maxThreadCount())
        self.assertEqual(1, self.coordinator.galleryUpdateThreadPool.maxThreadCount())

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
