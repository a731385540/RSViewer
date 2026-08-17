import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.domain.gallery_update import GalleryUpdateRecord
from app.view.update_manager_interface import UpdateManagerInterface


class UpdateManagerInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.interface = UpdateManagerInterface()
        self.interface.resize(900, 600)
        self.interface.show()
        QApplication.processEvents()

    def tearDown(self):
        self.interface.close()
        self.interface.deleteLater()
        QApplication.processEvents()

    def test_task_card_exposes_record_only_delete_action(self):
        record = GalleryUpdateRecord(
            source_gid=42,
            source_token="source-token",
            site="exhentai",
            title="Update task",
            folder="42-update-task",
            latest_url="https://exhentai.org/g/43/target-token/",
            state="failed",
        )
        deleted = []
        self.interface.deleteRequested.connect(deleted.append)

        self.interface.setRecords((record,))
        QApplication.processEvents()
        card = self.interface._cards[42]
        card.deleteButton.click()

        self.assertEqual([42], deleted)
        self.assertIn("保留画廊文件", card.deleteButton.toolTip())


if __name__ == "__main__":
    unittest.main()
