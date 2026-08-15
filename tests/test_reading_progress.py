import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from app.domain.manga import MangaItem
from app.repositories.user_library_repository import UserLibraryRepository
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import manga_metadata_text
from app.view.manga_detail_interface import MangaDetailInterface, group_manga_tags
from app.workers.reading_progress_worker import ReadingProgressSaveWorker


def make_item(folder: Path, pages=(), progress=None):
    cover = pages[0] if pages else folder / ".thumb"
    return MangaItem(
        gid=123,
        english_title="Progress test",
        original_title="进度测试",
        category=4,
        category_name="漫画",
        primary_label="",
        multiple_labels=(),
        tags=(),
        folder=folder,
        cover_path=cover,
        thumbnail_path=None,
        page_paths=tuple(pages),
        page_count=len(pages),
        progress_page_index=progress,
    )


class ReadingProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.repository = UserLibraryRepository(self.root / "rsviewer.db")

    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(3000)
        self.temp_directory.cleanup()

    def _create_pages(self, count=4):
        pages = []
        for index in range(count):
            path = self.root / f"{index + 1:07}.png"
            image = QImage(80, 120, QImage.Format_RGB32)
            image.fill(QColor(30 * index, 80, 120))
            self.assertTrue(image.save(str(path)))
            pages.append(path)
        return pages

    def test_schema_migrates_and_saves_progress(self):
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        self.repository.initialize()
        ReadingProgressSaveWorker(self.repository, 123, 7).run()

        self.assertEqual(7, self.repository.progress_for_manga(123))
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(UserLibraryRepository.SCHEMA_VERSION, version)

    def test_playlist_order_position_and_taxonomy_are_persisted(self):
        playlist_id = self.repository.create_playlist("周末播放")
        self.repository.assign_label_to_mangas((10, 20, 30), playlist_id)
        self.repository.set_playlist_order(playlist_id, (30, 10, 20))
        self.repository.save_playlist_position(playlist_id, 10)
        root_id = self.repository.create_taxonomy_label("全彩")
        author_id = self.repository.create_taxonomy_label("作者1", root_id)
        duplicate_id = self.repository.create_taxonomy_label("全彩")
        self.repository.assign_taxonomy_to_mangas((10, 20), author_id)

        self.assertEqual((30, 10, 20), self.repository.playlist_items(playlist_id))
        self.assertEqual(10, self.repository.playlist_last_gid(playlist_id))
        self.assertEqual(root_id, duplicate_id)
        self.assertEqual(
            {10: ((author_id, "作者1"),), 20: ((author_id, "作者1"),)},
            self.repository.taxonomy_for_mangas((10, 20, 30)),
        )

        self.repository.delete_playlist(playlist_id)
        self.assertEqual([], self.repository.list_playlists())
        self.assertEqual({}, self.repository.labels_for_manga((10, 20, 30)))

        self.repository.delete_taxonomy_label(root_id)
        self.assertEqual([], self.repository.list_taxonomy_labels())
        self.assertEqual({}, self.repository.taxonomy_for_mangas((10, 20, 30)))

    def test_favorites_and_local_browsing_history_are_persisted(self):
        self.repository.set_favorite((10, 20), True)
        self.assertEqual((20, 10), self.repository.favorite_gids())
        self.assertEqual((10,), self.repository.favorite_gids((10, 30)))

        self.repository.set_favorite((20,), False)
        self.assertEqual((10,), self.repository.favorite_gids())

        self.repository.record_browsing_history(10)
        self.repository.record_browsing_history(20)
        self.repository.record_browsing_history(10)
        self.assertEqual((10, 20), self.repository.browsing_history_gids())

    def test_legacy_primary_label_table_remains_compatible(self):
        self.repository.set_primary_label(123, "稍后阅读")

        self.assertEqual(
            {123: "稍后阅读"},
            self.repository.primary_labels_for_mangas([123, 456]),
        )

    def test_ehviewer_hex_progress_import_and_own_progress_precedence(self):
        sidecar = self.root / ".ehviewer"
        sidecar.write_text("VERSION2\n0000008f\n123\n", encoding="ascii")
        item = make_item(self.root)

        external_progress = EhViewerDataSource.read_ehviewer_progress(item)
        self.assertEqual(143, external_progress)
        self.assertEqual(
            143,
            self.repository.resolve_progress(item.gid, external_progress),
        )
        self.assertEqual(143, self.repository.progress_for_manga(item.gid))

        self.repository.save_progress(item.gid, 9)
        self.assertEqual(
            9,
            self.repository.resolve_progress(item.gid, external_progress),
        )

    def test_missing_or_invalid_ehviewer_progress_is_ignored(self):
        item = make_item(self.root)
        self.assertIsNone(EhViewerDataSource.read_ehviewer_progress(item))
        (self.root / ".ehviewer").write_text(
            "VERSION2\nnot-hex\n", encoding="ascii"
        )
        self.assertIsNone(EhViewerDataSource.read_ehviewer_progress(item))
        self.assertIsNone(self.repository.resolve_progress(item.gid, None))

    def test_progress_is_visible_and_preview_click_opens_exact_page(self):
        pages = self._create_pages()
        item = make_item(self.root, pages, progress=2)
        metadata = manga_metadata_text(item, lambda text: text)
        self.assertIn("进度 3/4", metadata)

        source = EhViewerDataSource(self.root / "unused.db", self.root)
        detail = MangaDetailInterface(source, self.repository)
        requested = []
        detail.readRequested.connect(
            lambda requested_item, page_index: requested.append(
                (requested_item.gid, page_index)
            )
        )
        detail.setManga(item)
        detail.show()
        QApplication.processEvents()
        QTest.mouseClick(detail._preview_tiles[2], Qt.LeftButton)

        self.assertEqual([(item.gid, 2)], requested)
        self.assertIn("第 3 / 4 页", detail.metadataLabel.text())
        detail.cancelLoads()
        detail.close()
        detail.deleteLater()
        QApplication.processEvents()

    def test_detail_tags_are_grouped_deduplicated_and_rendered_as_chips(self):
        grouped = group_manga_tags(
            (
                "Alice",
                "artist:Alice",
                "artist:Bob",
                "female:full color",
                "language:chinese",
                "standalone",
            )
        )
        self.assertEqual(
            (
                ("artist", "作者", "creator", ("Alice", "Bob")),
                ("language", "语言", "language", ("chinese",)),
                ("female", "女性", "female", ("full color",)),
                ("other", "其他", "neutral", ("standalone",)),
            ),
            grouped,
        )

        page = self._create_pages(1)[0]
        item = replace(
            make_item(self.root, (page,)),
            tags=(
                "Alice",
                "artist:Alice",
                "artist:Bob",
                "female:full color",
                "language:chinese",
            ),
        )
        detail = MangaDetailInterface(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
        )
        detail.setManga(item)
        detail.show()
        QApplication.processEvents()

        chips = detail.findChildren(QLabel, "mangaTagChip")
        self.assertEqual(
            {"Alice", "Bob", "full color", "chinese"},
            {chip.text() for chip in chips},
        )
        self.assertEqual(
            ["artist", "language", "female"],
            [group.property("tagNamespace") for group in detail._tagGroupWidgets],
        )
        self.assertIn("mangaTagChip", detail.styleSheet())
        detail.cancelLoads()
        detail.close()
        detail.deleteLater()
        QApplication.processEvents()

    def test_two_thousand_page_preview_only_builds_current_page(self):
        pages = tuple(self.root / f"{index + 1:07}.jpg" for index in range(2000))
        item = make_item(self.root, pages)
        source = EhViewerDataSource(self.root / "unused.db", self.root)
        detail = MangaDetailInterface(source, self.repository)
        requested = []
        detail.readRequested.connect(
            lambda _item, page_index: requested.append(page_index)
        )

        detail.setManga(item)
        detail.show()
        QApplication.processEvents()
        self.assertEqual(40, len(detail._preview_tiles))
        self.assertEqual(50, detail.previewPageSpinBox.maximum())
        self.assertEqual((0, 39), (
            detail._preview_tiles[0].pageIndex,
            detail._preview_tiles[-1].pageIndex,
        ))

        detail._setPreviewPage(50)
        QApplication.processEvents()
        self.assertEqual(40, len(detail._preview_tiles))
        self.assertEqual((1960, 1999), (
            detail._preview_tiles[0].pageIndex,
            detail._preview_tiles[-1].pageIndex,
        ))
        QTest.mouseClick(detail._preview_tiles[-1], Qt.LeftButton)
        self.assertEqual([1999], requested)

        detail.cancelLoads()
        detail.close()
        detail.deleteLater()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
