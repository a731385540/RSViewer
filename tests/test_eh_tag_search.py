import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, qconfig, setTheme

from app.repositories.user_library_repository import UserLibraryRepository
from app.services.eh_tag_importer import parse_eh_tag_database
from app.services.eh_tag_search import EhTagSearchIndex
from app.services.search_history import SearchHistoryService
from app.view.eh_tag_search_line_edit import EhTagSearchLineEdit


OTHER_MARKDOWN = """---
name: 其他
key: other
abbr: o
---

| 原始标签 | 名称 | 描述 | 外部链接 |
| -------- | ---- | ---- | -------- |
| | == 技术 == | | |
| full color | 全彩 | 每页均为彩色 | |
| full censorship | 全屏蔽 | 描述中允许转义竖线 A\\|B | [参考](https://example.com) |
"""

LANGUAGE_MARKDOWN = """---
name: 语言
key: language
abbr: l
aliases:
  - lang
---

| 原始标签 | 名称 | 描述 | 外部链接 |
| -------- | ---- | ---- | -------- |
| chinese | 汉语 | 中文内容 | |
"""


class EhTagImportAndSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.source = self.root / "database"
        self.source.mkdir()
        (self.source / "other.md").write_text(OTHER_MARKDOWN, encoding="utf-8")
        (self.source / "language.md").write_text(
            LANGUAGE_MARKDOWN, encoding="utf-8"
        )
        self.repository = UserLibraryRepository(self.root / "rsviewer.db")

    def tearDown(self):
        self.temp_directory.cleanup()

    def _import(self):
        snapshot = parse_eh_tag_database(self.source)
        self.repository.replace_eh_tags(
            snapshot.namespace_rows(), snapshot.tag_rows()
        )
        return snapshot

    def test_markdown_snapshot_import_is_idempotent_and_uses_own_schema(self):
        snapshot = self._import()
        self.assertEqual(2, len(snapshot.namespaces))
        self.assertEqual(3, len(snapshot.tags))
        full_censorship = next(
            tag for tag in snapshot.tags if tag.raw_tag == "full censorship"
        )
        self.assertIn("A|B", full_censorship.description)

        self._import()
        self.assertEqual(3, self.repository.eh_tag_count())
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(UserLibraryRepository.SCHEMA_VERSION, version)
        self.assertIn("eh_tags", tables)
        self.assertIn("eh_tag_namespaces", tables)

    def test_index_matches_translation_and_raw_substrings(self):
        self._import()
        index = EhTagSearchIndex.from_repository(self.repository)

        self.assertEqual("汉语", index.translated_name("lang", "chinese"))
        self.assertEqual("", index.translated_name("artist", "missing"))

        chinese_result = index.search("全彩")[0]
        self.assertEqual("other：full color\n全彩", chinese_result.display_text)
        self.assertEqual('other:"full color"', chinese_result.query_token)
        self.assertIn(
            "other：full color\n全彩",
            [suggestion.display_text for suggestion in index.search("full")],
        )
        self.assertEqual(
            ("other:full color", "language:chinese"),
            index.local_query_terms('o:"full color" l:chinese'),
        )
        self.assertEqual(
            'l:"chinese$"', index.exact_query_token("lang", "chinese")
        )
        self.assertEqual(
            'f:"full color$"',
            index.exact_query_token("female", "full color"),
        )
        self.assertEqual(
            ("other:full color", "language:chinese"),
            index.local_query_terms('o:"full color$" l:"chinese$"'),
        )
        self.assertEqual(
            'cos:"unknown name$"',
            EhTagSearchIndex().exact_query_token("cosplayer", "unknown name"),
        )

    def test_completion_replaces_only_current_space_delimited_tag(self):
        self._import()
        index = EhTagSearchIndex.from_repository(self.repository)
        search_edit = EhTagSearchLineEdit(index)

        search_edit.setText("全彩")
        search_edit.setCursorPosition(len(search_edit.text()))
        search_edit.refreshTagSuggestions()
        self.assertEqual(["other：full color\n全彩"], search_edit.suggestionTexts())
        self.assertTrue(search_edit.activateTagSuggestion("other：full color\n全彩"))
        self.assertEqual('other:"full color"', search_edit.text())

        search_edit.setText(search_edit.text() + " 汉语")
        search_edit.setCursorPosition(len(search_edit.text()))
        search_edit.refreshTagSuggestions()
        self.assertTrue(search_edit.activateTagSuggestion("language：chinese\n汉语"))
        self.assertEqual('other:"full color" language:chinese', search_edit.text())

    def test_search_history_is_persisted_limited_and_recent_first(self):
        service = SearchHistoryService(self.repository, 20)
        for index in range(25):
            service.record(f"query {index}")

        self.assertEqual(20, len(service.items))
        self.assertEqual("query 24", service.items[0])
        self.assertNotIn("query 0", service.items)

        service.record("query 10")
        self.assertEqual("query 10", service.items[0])
        service.setLimit(5)
        self.assertEqual(5, len(service.items))
        reloaded = SearchHistoryService(self.repository, 20)
        self.assertEqual(5, len(reloaded.items))
        self.assertEqual("query 10", reloaded.items[0])

    def test_history_precedes_two_line_tag_completions(self):
        self._import()
        index = EhTagSearchIndex.from_repository(self.repository)
        history = SearchHistoryService(self.repository, 20)
        history.record("full color archive")
        search_edit = EhTagSearchLineEdit(index, search_history_service=history)

        search_edit.setText("full")
        search_edit.setCursorPosition(len(search_edit.text()))
        search_edit.refreshTagSuggestions()
        texts = search_edit.suggestionTexts()
        kinds = search_edit.suggestionKinds()
        self.assertEqual("history", kinds[0])
        self.assertEqual("full color archive", texts[0])
        tag_row = texts.index("other：full color\n全彩")
        self.assertEqual("tag", kinds[tag_row])

        self.assertTrue(search_edit.activateTagSuggestion(texts[0]))
        self.assertEqual("full color archive", search_edit.text())
        search_edit.returnPressed.emit()
        self.assertEqual("full color archive", history.items[0])

    def test_completion_popup_is_bounded_and_follows_fluent_theme(self):
        self._import()
        index = EhTagSearchIndex.from_repository(self.repository)
        search_edit = EhTagSearchLineEdit(index)
        search_edit.resize(620, 33)
        search_edit.show()
        original_theme = qconfig.theme
        try:
            search_edit.setText("full")
            search_edit.setCursorPosition(len(search_edit.text()))
            search_edit.setFocus()
            search_edit._onTextEdited(search_edit.text())

            setTheme(Theme.LIGHT)
            self.app.processEvents()
            search_edit._showCompleterMenu()
            self.app.processEvents()
            menu = search_edit._completerMenu
            light_pixel = menu.view.viewport().grab().toImage().pixelColor(3, 3)
            self.assertEqual(8, search_edit._tag_completer.maxVisibleItems())
            self.assertFalse(search_edit._tag_completer.popup().isVisible())
            self.assertGreater(menu.view.count(), 0)
            menu.close()

            setTheme(Theme.DARK)
            self.app.processEvents()
            search_edit.setFocus()
            search_edit._onTextEdited(search_edit.text())
            self.app.processEvents()
            search_edit._showCompleterMenu()
            self.app.processEvents()
            dark_pixel = menu.view.viewport().grab().toImage().pixelColor(3, 3)
            self.assertLess(dark_pixel.lightness(), light_pixel.lightness())
        finally:
            setTheme(original_theme)
            if search_edit._completerMenu:
                search_edit._completerMenu.close()
                QTest.qWait(300)
                search_edit._completerMenu.deleteLater()
                search_edit._completerMenu = None
            search_edit.close()
            search_edit.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

    def test_popup_focus_return_does_not_reopen_closed_suggestions(self):
        self._import()
        index = EhTagSearchIndex.from_repository(self.repository)
        search_edit = EhTagSearchLineEdit(index)
        search_edit.resize(620, 33)
        search_edit.show()
        search_edit.setFocus()
        self.app.processEvents()
        search_edit.setText("full")
        search_edit.setCursorPosition(len(search_edit.text()))
        search_edit._onTextEdited(search_edit.text())
        self.app.processEvents()

        menu = search_edit._completerMenu
        self.assertIsNotNone(menu)
        self.assertTrue(menu.isVisible())
        menu.close()
        self.app.processEvents()
        self.assertFalse(menu.isVisible())

        QApplication.sendEvent(
            search_edit,
            QFocusEvent(QEvent.FocusIn, Qt.PopupFocusReason),
        )
        self.app.processEvents()
        self.app.processEvents()
        self.assertFalse(menu.isVisible())
        self.assertFalse(search_edit._suggestion_timer.isActive())

        QTest.qWait(300)
        search_edit.close()
        search_edit.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
