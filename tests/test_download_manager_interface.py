import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.domain.online_download import OnlineGalleryDownloadRecord
from app.view.download_manager_interface import DownloadManagerInterface


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


if __name__ == "__main__":
    unittest.main()
