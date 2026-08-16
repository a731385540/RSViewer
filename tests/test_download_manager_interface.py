import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.domain.online_download import OnlineGalleryDownloadRecord
from app.view.download_manager_interface import (
    DownloadManagerInterface,
    format_download_speed,
)


class DownloadManagerInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.interface = DownloadManagerInterface()
        self.interface.resize(900, 600)
        self.interface.show()
        QApplication.processEvents()

    def tearDown(self):
        self.interface.close()
        self.interface.deleteLater()
        QApplication.processEvents()

    @staticmethod
    def _record(gid=42, state="paused", completed=2, total=5):
        return OnlineGalleryDownloadRecord(
            gid=gid,
            site="exhentai",
            token="gallerytoken",
            title="Download task",
            dirname="42-download-task",
            page_count=total,
            completed_pages=completed,
            state=state,
        )

    def test_pending_tasks_start_pause_delete_and_completed_tasks_disappear(self):
        starts = []
        pauses = []
        deletes = []
        self.interface.startRequested.connect(starts.append)
        self.interface.pauseRequested.connect(pauses.append)
        self.interface.deleteRequested.connect(deletes.append)

        self.interface.setRecords((self._record(),))
        QApplication.processEvents()
        card = self.interface._cards[42]
        self.assertIn("2 / 5 页", card.metaLabel.text())
        card.actionButton.click()
        card.deleteButton.click()
        self.assertEqual([42], starts)
        self.assertEqual([42], deletes)

        self.interface.setRecords((self._record(state="downloading"),), (42,))
        QApplication.processEvents()
        card.actionButton.click()
        self.assertEqual([42], pauses)

        self.interface.setRecords((self._record(state="completed", completed=5),))
        QApplication.processEvents()
        self.assertEqual({}, self.interface._cards)
        self.assertTrue(self.interface.emptyLabel.isVisible())

    def test_bulk_buttons_follow_active_tasks_and_emit_once(self):
        start_all = []
        pause_all = []
        self.interface.startAllRequested.connect(lambda: start_all.append(True))
        self.interface.pauseAllRequested.connect(lambda: pause_all.append(True))
        records = (
            self._record(gid=42, state="downloading"),
            self._record(gid=43, state="paused"),
        )

        self.interface.setRecords(records, (42,))
        QApplication.processEvents()
        self.assertTrue(self.interface.startAllButton.isEnabled())
        self.assertTrue(self.interface.pauseAllButton.isEnabled())
        self.interface.startAllButton.click()
        self.interface.pauseAllButton.click()
        self.assertEqual([True], start_all)
        self.assertEqual([True], pause_all)

        self.interface.setRecords(records, (42, 43))
        self.assertFalse(self.interface.startAllButton.isEnabled())
        self.assertTrue(self.interface.pauseAllButton.isEnabled())

        self.interface.setRecords(records)
        self.assertTrue(self.interface.startAllButton.isEnabled())
        self.assertFalse(self.interface.pauseAllButton.isEnabled())

    def test_active_task_and_header_show_download_speed(self):
        records = (
            self._record(gid=42, state="downloading"),
            self._record(gid=43, state="downloading"),
        )
        speeds = {42: 2 * 1024 * 1024, 43: 512 * 1024}

        self.interface.setRecords(records, (42, 43), speeds)
        QApplication.processEvents()

        self.assertIn("2.00 MiB/s", self.interface._cards[42].metaLabel.text())
        self.assertIn("512.00 KiB/s", self.interface._cards[43].metaLabel.text())
        self.assertIn("2.50 MiB/s", self.interface.countLabel.text())
        self.assertEqual("900 B/s", format_download_speed(900))

        self.interface.setRecords(records, (42,), {})
        self.assertIn("测速中", self.interface._cards[42].metaLabel.text())
        self.assertNotIn("测速中", self.interface._cards[43].metaLabel.text())


if __name__ == "__main__":
    unittest.main()
