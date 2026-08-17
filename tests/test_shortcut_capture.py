import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.view.setting_interface import ShortcutCaptureButton
from app.common.config import cfg
from app.view.setting_interface import SettingInterface


class ShortcutCaptureButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_combination_is_confirmed_on_key_press(self):
        button = ShortcutCaptureButton("Z")
        button.show()
        QTest.mouseClick(button, Qt.LeftButton)
        QTest.keyClick(button, Qt.Key_S, Qt.ControlModifier)

        self.assertEqual("Ctrl+S", button.sequence)
        self.assertEqual("Ctrl+S", button.text())

    def test_escape_cancels_capture(self):
        button = ShortcutCaptureButton("Ctrl+K")
        button.show()
        QTest.mouseClick(button, Qt.LeftButton)
        QTest.keyClick(button, Qt.Key_Escape)

        self.assertEqual("Ctrl+K", button.sequence)
        self.assertEqual("Ctrl+K", button.text())

    def test_search_tag_shortcuts_and_hover_option_are_configurable(self):
        original_search = cfg.get(cfg.searchShortcut)
        original_tags = cfg.get(cfg.tagSidebarShortcut)
        original_hover = cfg.get(cfg.mangaSearchHoverEnabled)
        original_history_limit = cfg.get(cfg.searchHistoryLimit)
        settings = SettingInterface()
        try:
            settings.searchShortcutCard.captureButton.sequenceCaptured.emit(
                "Ctrl+Shift+F"
            )
            settings.tagSidebarShortcutCard.captureButton.sequenceCaptured.emit(
                "Alt+L"
            )
            cfg.set(cfg.mangaSearchHoverEnabled, False)
            cfg.set(cfg.searchHistoryLimit, 10)

            self.assertEqual("Ctrl+Shift+F", cfg.get(cfg.searchShortcut))
            self.assertEqual("Alt+L", cfg.get(cfg.tagSidebarShortcut))
            self.assertFalse(cfg.get(cfg.mangaSearchHoverEnabled))
            self.assertFalse(settings.mangaSearchHoverCard.switchButton.isChecked())
            self.assertEqual(10, cfg.get(cfg.searchHistoryLimit))
            self.assertIs(
                cfg.searchHistoryLimit,
                settings.searchHistoryLimitCard.configItem,
            )
        finally:
            cfg.set(cfg.searchShortcut, original_search)
            cfg.set(cfg.tagSidebarShortcut, original_tags)
            cfg.set(cfg.mangaSearchHoverEnabled, original_hover)
            cfg.set(cfg.searchHistoryLimit, original_history_limit)
            settings.close()
            settings.deleteLater()

    def test_database_card_exports_instead_of_selecting_a_runtime_source(self):
        settings = SettingInterface()
        requested = []
        settings.ehViewerExportRequested.connect(requested.append)
        try:
            with patch(
                "app.view.setting_interface.QFileDialog.getSaveFileName",
                return_value=("C:/exports/eh.db", ""),
            ):
                settings.ehViewerDatabaseCard.exportButton.click()
            self.assertEqual(["C:/exports/eh.db"], requested)
            self.assertEqual(
                "导出 EhViewer 数据库",
                settings.ehViewerDatabaseCard.titleLabel.text(),
            )
        finally:
            settings.close()
            settings.deleteLater()


if __name__ == "__main__":
    unittest.main()
