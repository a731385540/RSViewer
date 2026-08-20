import base64
import os
import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt, QThreadPool
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.domain.manga import MangaItem
from app.domain.similar_gallery import LatestSimilarSearch
from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    GalleryOriginalState,
    ORIGINAL_STATE_STAGED,
    ORIGINAL_STATE_ACTIVE,
)
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryComment,
    OnlineGalleryDetail,
    OnlineGalleryLink,
    OnlineGalleryPreview,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.eh_tag_search import EhTagSearchIndex
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import manga_metadata_text
from app.view.manga_detail_interface import MangaDetailInterface, group_manga_tags
from app.view.similar_gallery_browser_window import SimilarGalleryBrowserWindow
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


def make_tag_search_index():
    return EhTagSearchIndex(
        (
            ("artist", "a", "[]", "Alice", "爱丽丝", "作者"),
            ("language", "l", '["lang"]', "chinese", "汉语", "语言"),
            ("female", "f", "[]", "full color", "全彩", "女性"),
        )
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
            download_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(online_gallery_downloads)"
                )
            }
            original_table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'gallery_original_states'"
            ).fetchone()
            original_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(gallery_original_states)"
                )
            }
            online_comment_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(online_gallery_comments)"
                )
            }
            sync_comment_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(gallery_sync_comments)"
                )
            }
            progress_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(manga_reading_progress)"
                )
            }
            similar_table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'latest_similar_search'"
            ).fetchone()
            custom_sort_tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'manga_custom_sort_%'
                    """
                )
            }
        self.assertEqual(UserLibraryRepository.SCHEMA_VERSION, version)
        self.assertIn("download_mode", download_columns)
        self.assertEqual(("gallery_original_states",), original_table)
        self.assertIn("fallback_to_standard", original_columns)
        self.assertIn("page_modes_json", original_columns)
        self.assertIn("gallery_links_json", online_comment_columns)
        self.assertIn("gallery_links_json", sync_comment_columns)
        self.assertTrue(
            {"completed", "cleared", "started"}.issubset(progress_columns)
        )
        self.assertEqual(("latest_similar_search",), similar_table)
        self.assertEqual(
            {"manga_custom_sort_rules", "manga_custom_sort_entries"},
            custom_sort_tables,
        )

    def test_category_and_taxonomy_custom_orders_survive_membership_changes(self):
        category = UserLibraryRepository.CUSTOM_SORT_CATEGORY
        taxonomy = UserLibraryRepository.CUSTOM_SORT_TAXONOMY
        self.repository.save_custom_sort(category, "分类 A", (30, 10, 20))
        root_id = self.repository.create_taxonomy_label("作者")
        child_id = self.repository.create_taxonomy_label("系列", root_id)
        self.repository.assign_taxonomy_to_mangas((10, 20), child_id)
        self.repository.save_custom_sort(taxonomy, root_id, (20, 10))
        self.repository.save_custom_sort(taxonomy, child_id, ())

        self.assertTrue(self.repository.has_custom_sort(category, "分类 a"))
        self.assertEqual(
            (30, 10, 20),
            self.repository.custom_sort_gids(category, "分类 A"),
        )
        self.assertTrue(self.repository.has_custom_sort(taxonomy, child_id))
        self.assertEqual((), self.repository.custom_sort_gids(taxonomy, child_id))

        self.repository.unassign_taxonomy_from_mangas((10,), child_id)
        self.assertEqual(
            (20,), self.repository.custom_sort_gids(taxonomy, root_id)
        )
        self.repository.promote_gallery_gid(20, 21)
        self.assertEqual(
            (21,), self.repository.custom_sort_gids(taxonomy, root_id)
        )
        self.repository.remove_custom_sort_entries(category, (10,))
        self.assertEqual(
            (30, 21), self.repository.custom_sort_gids(category, "分类 A")
        )
        self.repository.purge_gallery(30)
        self.assertEqual(
            (21,), self.repository.custom_sort_gids(category, "分类 A")
        )
        self.repository.delete_taxonomy_label(root_id)
        self.assertFalse(self.repository.has_custom_sort(taxonomy, root_id))
        self.assertFalse(self.repository.has_custom_sort(taxonomy, child_id))

    def test_parent_custom_order_survives_overlapping_child_membership(self):
        taxonomy = UserLibraryRepository.CUSTOM_SORT_TAXONOMY
        root_id = self.repository.create_taxonomy_label("作者")
        first_child_id = self.repository.create_taxonomy_label("短篇", root_id)
        second_child_id = self.repository.create_taxonomy_label("长篇", root_id)
        self.repository.assign_taxonomy_to_mangas((10, 20), first_child_id)
        self.repository.save_custom_sort(taxonomy, root_id, (10, 20))

        self.repository.assign_taxonomy_to_mangas((10,), second_child_id)
        self.repository.unassign_taxonomy_from_mangas((10,), first_child_id)
        self.assertEqual(
            (10, 20), self.repository.custom_sort_gids(taxonomy, root_id)
        )

        self.repository.unassign_taxonomy_from_mangas((10,), second_child_id)
        self.assertEqual(
            (20,), self.repository.custom_sort_gids(taxonomy, root_id)
        )

    def test_v21_migration_distinguishes_default_sidecar_page_from_reading(self):
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            connection.executescript(
                """
                CREATE TABLE manga_reading_progress (
                    gid INTEGER PRIMARY KEY,
                    page_index INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    cleared INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO manga_reading_progress VALUES (1, 0, 1, 0, 0);
                INSERT INTO manga_reading_progress VALUES (2, 1, 2, 0, 0);
                INSERT INTO manga_reading_progress VALUES (3, 0, 3, 1, 0);
                INSERT INTO manga_reading_progress VALUES (4, 0, 4, 0, 1);
                PRAGMA user_version = 21;
                """
            )

        self.repository.initialize()

        self.assertIsNone(self.repository.reading_state_for_manga(1))
        self.assertEqual((1, False), self.repository.reading_state_for_manga(2))
        self.assertEqual((0, True), self.repository.reading_state_for_manga(3))
        self.assertIsNone(self.repository.reading_state_for_manga(4))
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            started = connection.execute(
                "SELECT gid, started FROM manga_reading_progress ORDER BY gid"
            ).fetchall()
        self.assertEqual([(1, 0), (2, 1), (3, 1), (4, 0)], started)

    def test_completed_reading_is_permanent_until_explicit_clear(self):
        self.repository.save_progress(122, 0)
        self.assertEqual((0, False), self.repository.reading_state_for_manga(122))

        self.repository.save_progress(123, 9, completed=True)
        self.repository.save_progress(123, 2)

        self.assertEqual((2, True), self.repository.reading_state_for_manga(123))

        self.repository.clear_progress(123)
        self.assertIsNone(self.repository.reading_state_for_manga(123))
        self.assertIsNone(self.repository.resolve_progress(123, 8))

        self.repository.save_progress(123, 1)
        self.assertEqual((1, False), self.repository.reading_state_for_manga(123))

    def test_latest_similar_search_survives_restart_and_gid_changes(self):
        record = LatestSimilarSearch(10, "Part", (20, 30), 123456)
        self.repository.save_latest_similar_search(record)

        reopened = UserLibraryRepository(self.repository.database_path)
        self.assertEqual(record, reopened.latest_similar_search())

        reopened.promote_gallery_gid(10, 11)
        self.assertEqual(
            LatestSimilarSearch(11, "Part", (20, 30), 123456),
            reopened.latest_similar_search(),
        )
        reopened.promote_gallery_gid(20, 21)
        self.assertEqual((21, 30), reopened.latest_similar_search().result_gids)
        reopened.purge_gallery(21)
        self.assertEqual((30,), reopened.latest_similar_search().result_gids)
        reopened.purge_gallery(11)
        self.assertIsNone(reopened.latest_similar_search())

    def test_similar_browser_replaces_results_and_reuses_detail_window(self):
        page = self._create_pages(1)[0]
        first = replace(make_item(self.root, (page,)), gid=20)
        second = replace(make_item(self.root, (page,)), gid=30)
        browser = SimilarGalleryBrowserWindow(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
        )
        record = LatestSimilarSearch(10, "Progress", (20, 30), 1)
        browser.setSearch(record, (first, second))

        self.assertEqual(2, browser.resultList.count())
        browser._openResult(browser.resultList.item(0))
        self.assertIs(browser.detail, browser.stack.currentWidget())
        browser.showResults()
        self.assertIs(browser.resultPage, browser.stack.currentWidget())

        replacement = LatestSimilarSearch(40, "Other", (30,), 2)
        browser.setSearch(replacement, (second,))
        self.assertEqual(1, browser.resultList.count())
        self.assertIn("Other", browser.summaryLabel.text())

        calls = []
        first_owner = object()
        second_owner = object()
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            browser.bindActions(
                first_owner,
                lambda *_args: calls.append("first-read"),
                lambda *_args: None,
                lambda *_args: None,
                lambda *_args: None,
            )
            browser.bindActions(
                second_owner,
                lambda *_args: calls.append("second-read"),
                lambda *_args: None,
                lambda *_args: None,
                lambda *_args: None,
            )
        browser.readRequested.emit(first, 0)
        self.assertEqual(["second-read"], calls)
        self.assertFalse(
            any(issubclass(entry.category, RuntimeWarning) for entry in captured)
        )
        browser.close()
        browser.deleteLater()

    def test_v17_original_fallback_migrates_to_per_page_base_modes(self):
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            connection.executescript(
                """
                CREATE TABLE gallery_original_states (
                    gid INTEGER PRIMARY KEY,
                    site TEXT NOT NULL,
                    token TEXT NOT NULL,
                    dirname TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    completed_pages INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    fallback_to_standard INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE online_gallery_comments (
                    gid INTEGER NOT NULL,
                    comment_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    posted TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    score INTEGER,
                    is_uploader INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (gid, comment_id)
                );
                CREATE TABLE gallery_sync_comments (
                    gid INTEGER NOT NULL,
                    comment_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    posted TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    score INTEGER,
                    is_uploader INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (gid, comment_id)
                );
                INSERT INTO gallery_original_states(
                    gid, site, token, dirname, mode, state, completed_pages,
                    page_count, fallback_to_standard, metadata_json, error,
                    created_at, updated_at
                ) VALUES (
                    456, 'exhentai', 'token', '456-gallery',
                    'original_direct', 'active', 3, 3, 1, '{}', '', 1, 1
                );
                INSERT INTO online_gallery_comments(
                    gid, comment_id, author, posted, body, score, is_uploader
                ) VALUES (456, 'old', 'reader', 'today', 'legacy comment', 1, 0);
                PRAGMA user_version = 17;
                """
            )

        self.repository.initialize()

        state = self.repository.gallery_original_state(456)
        self.assertEqual(("base", "base", "base"), state.page_modes)
        self.assertTrue(state.fallback_to_standard)
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            self.assertEqual(
                UserLibraryRepository.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            legacy_comment = connection.execute(
                "SELECT body, gallery_links_json "
                "FROM online_gallery_comments WHERE gid = 456"
            ).fetchone()
        self.assertEqual(("legacy comment", "[]"), legacy_comment)

    def test_staged_original_preview_switches_reader_source_and_shows_progress(self):
        standard_pages = self._create_pages(2)
        original_folder = self.root / "original"
        original_folder.mkdir()
        original_pages = []
        for index in range(2):
            path = original_folder / f"{index + 1:08d}.png"
            image = QImage(160, 240, QImage.Format_RGB32)
            image.fill(QColor(180, 20 * index, 80))
            self.assertTrue(image.save(str(path)))
            original_pages.append(path)
        item = replace(
            make_item(self.root, standard_pages),
            original_mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            original_state=ORIGINAL_STATE_STAGED,
            original_page_paths=tuple(original_pages),
            original_completed_pages=2,
            page_tokens=("page-one", "page-two"),
            download_complete=True,
        )
        detail = MangaDetailInterface(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
        )
        requested = []
        detail.readRequested.connect(
            lambda requested_item, page_index: requested.append(
                (requested_item, page_index)
            )
        )
        detail.setManga(item)
        detail.show()
        QApplication.processEvents()

        self.assertFalse(detail.previewSourceSwitch.isHidden())
        detail._setPreviewSource("original")
        QApplication.processEvents()
        QTest.mouseClick(detail._preview_tiles[1], Qt.LeftButton)
        self.assertEqual(1, requested[0][1])
        self.assertEqual(tuple(original_pages), requested[0][0].page_paths)

        state = GalleryOriginalState(
            gid=item.gid,
            site="exhentai",
            token="token",
            dirname=self.root.name,
            mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            state=ORIGINAL_STATE_STAGED,
            completed_pages=2,
            page_count=2,
            page_modes=("original", "original"),
        )
        detail.setOriginalDownloadState(state)
        self.assertEqual(100, detail.originalDownloadProgressBar.value())
        self.assertFalse(detail.originalReplaceButton.isHidden())
        self.assertEqual("基础下载", detail._localDownloadButtonText(item))
        detail.setOriginalDownloadState(
            replace(state, state=ORIGINAL_STATE_ACTIVE)
        )
        self.assertEqual("已使用原图", detail.downloadButton.text())
        self.assertFalse(detail.downloadButton.isEnabled())
        self.assertFalse(detail.fullOriginalBadge.isHidden())

        mixed_state = replace(
            state,
            fallback_to_standard=True,
            page_modes=("original", "base"),
        )
        detail.setOriginalDownloadState(mixed_state)
        self.assertFalse(detail.originalReplaceButton.isHidden())
        detail.setOriginalDownloadState(
            replace(mixed_state, state=ORIGINAL_STATE_ACTIVE)
        )
        self.assertEqual("已是混合原图画廊", detail.originalDownloadButton.text())
        self.assertEqual("已使用混合原图", detail.downloadButton.text())
        self.assertFalse(detail.downloadButton.isEnabled())
        self.assertFalse(detail.previewSourceSwitch.isHidden())
        self.assertEqual("original", detail._preview_source)
        self.assertEqual("1 ORIGINAL", detail.originalCountBadge.text())
        self.assertEqual("1 BASE", detail.baseCountBadge.text())
        self.assertFalse(detail.originalCountBadge.isHidden())
        self.assertFalse(detail.baseCountBadge.isHidden())
        detail.cancelLoads()
        detail.close()
        detail.deleteLater()

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
        self.repository.prepend_mangas_to_playlist((20, 40, 30), playlist_id)
        self.assertEqual(
            (20, 40, 30, 10),
            self.repository.playlist_items(playlist_id),
        )
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
        sidecar.write_text("VERSION2\n00000000\n123\n", encoding="ascii")
        item = make_item(self.root)

        default_progress = EhViewerDataSource.read_ehviewer_progress(item)
        self.assertEqual(0, default_progress)
        self.assertIsNone(
            self.repository.resolve_progress(item.gid, default_progress)
        )
        self.assertIsNone(self.repository.reading_state_for_manga(item.gid))

        sidecar.write_text("VERSION2\n0000008f\n123\n", encoding="ascii")

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
        self.assertNotIn("阅读进度：", detail.metadataLabel.text())
        self.assertTrue(detail.detailMetadataLabel.isHidden())
        self.assertIn("第 3 / 4 页", detail.detailMetadataLabel.text())
        opened = []
        detail.folderOpenRequested.connect(lambda value: opened.append(value.gid))
        detail.openFolderButton.click()
        self.assertEqual([item.gid], opened)
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
            posted="2026-08-15 12:00",
            uploader="download-uploader",
            rating=4.75,
            language="Chinese",
            file_size="18 MiB",
            visible="Yes",
            multiple_labels=("稍后阅读",),
            taxonomy_labels=("单行本",),
            downloaded_page_count=1,
            download_complete=True,
        )
        detail = MangaDetailInterface(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
            tag_search_index=make_tag_search_index(),
        )
        detail.setManga(item)
        detail.show()
        QApplication.processEvents()

        chips = detail.findChildren(QLabel, "mangaTagChip")
        self.assertEqual(
            {"爱丽丝", "Bob", "全彩", "汉语"},
            {chip.text() for chip in chips},
        )
        self.assertEqual(
            {
                "artist:Alice",
                "artist:Bob",
                "female:full color",
                "language:chinese",
            },
            {chip.toolTip() for chip in chips},
        )
        self.assertEqual(
            {"Alice", "Bob", "full color", "chinese"},
            {chip.property("rawTag") for chip in chips},
        )
        self.assertEqual(
            {
                'a:"Alice$"',
                'a:"Bob$"',
                'f:"full color$"',
                'l:"chinese$"',
            },
            {chip.queryToken() for chip in chips},
        )
        language_chip = next(
            chip for chip in chips if chip.property("rawTag") == "chinese"
        )
        tag_menu = language_chip._buildContextMenu()
        self.assertEqual(
            ["复制 Tag"],
            [action.text() for action in tag_menu.menuActions()],
        )
        QApplication.clipboard().clear()
        tag_menu.menuActions()[0].trigger()
        self.assertEqual('l:"chinese$"', QApplication.clipboard().text())
        key_chips = detail.findChildren(QLabel, "mangaKeyTagChip")
        self.assertEqual(
            {"作者：爱丽丝", "作者：Bob", "语言：汉语"},
            {chip.text() for chip in key_chips},
        )
        self.assertEqual(
            {'a:"Alice$"', 'a:"Bob$"', 'l:"chinese$"'},
            {chip.queryToken() for chip in key_chips},
        )
        self.assertEqual(
            ["artist", "language", "female"],
            [group.property("tagNamespace") for group in detail._tagGroupWidgets],
        )
        selectable_flags = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        for label in (
            detail.originalTitleLabel,
            detail.englishTitleLabel,
            detail.metadataLabel,
            detail.detailMetadataLabel,
            *chips,
        ):
            self.assertEqual(
                selectable_flags,
                label.textInteractionFlags() & selectable_flags,
            )
            self.assertEqual(Qt.ClickFocus, label.focusPolicy())
            self.assertEqual(Qt.IBeamCursor, label.cursor().shape())

        clipboard = QApplication.clipboard()
        clipboard.clear()
        detail.metadataLabel.setFocus()
        QTest.keyClick(detail.metadataLabel, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(detail.metadataLabel, Qt.Key_C, Qt.ControlModifier)
        QApplication.processEvents()
        self.assertEqual(detail.metadataLabel.text(), clipboard.text())
        self.assertIn("上传者：download-uploader", detail.metadataLabel.text())
        self.assertIn("评分：4.75", detail.metadataLabel.text())
        self.assertIn("语言：Chinese", detail.metadataLabel.text())
        for field in (
            "归类：",
            "页数：",
            "阅读进度：",
            "已下载：",
            "文件大小：",
            "可见性：",
        ):
            self.assertNotIn(field, detail.metadataLabel.text())
        self.assertTrue(detail.detailMetadataLabel.isHidden())
        self.assertEqual("查看详细", detail.detailMetadataButton.text())
        QTest.mouseClick(detail.detailMetadataButton, Qt.LeftButton)
        self.assertFalse(detail.detailMetadataLabel.isHidden())
        self.assertEqual("收起详细", detail.detailMetadataButton.text())
        self.assertNotIn("播放列表：", detail.detailMetadataLabel.text())
        self.assertIn("归类：单行本", detail.detailMetadataLabel.text())
        self.assertIn("页数：1", detail.detailMetadataLabel.text())
        self.assertIn("已下载：1 / 1 页", detail.detailMetadataLabel.text())
        self.assertIn("文件大小：18 MiB", detail.detailMetadataLabel.text())
        self.assertIn("可见性：Yes", detail.detailMetadataLabel.text())
        self.assertIn("mangaTagChip", detail.styleSheet())
        detail.cancelLoads()
        detail.close()
        detail.deleteLater()
        QApplication.processEvents()

    def test_shared_detail_page_switches_between_online_comments_and_local_preview(self):
        source = EhViewerDataSource(self.root / "unused.db", self.root)
        detail_widget = MangaDetailInterface(
            source,
            self.repository,
            tag_search_index=make_tag_search_index(),
        )
        folder_requests = []
        detail_widget.folderOpenRequested.connect(folder_requests.append)
        sprite = QImage(4, 2, QImage.Format_RGB32)
        sprite.fill(QColor("red"))
        for x in range(2, 4):
            for y in range(2):
                sprite.setPixelColor(x, y, QColor("blue"))
        sprite_data = QByteArray()
        sprite_buffer = QBuffer(sprite_data)
        self.assertTrue(sprite_buffer.open(QIODevice.WriteOnly))
        self.assertTrue(sprite.save(sprite_buffer, "PNG"))

        class Provider:
            settings = SimpleNamespace(site="ehentai")

            def load_preview_thumbnail(self, _preview):
                return bytes(sprite_data)

        provider = Provider()
        cache = OnlineGalleryMemoryCache()
        previews = tuple(
            OnlineGalleryPreview(
                index,
                f"https://e-hentai.org/s/token{index}/456-{index + 1}",
                "https://a.hath.network/sprite.webp",
                thumbnail_width=2,
                thumbnail_height=2,
                thumbnail_x=index * 2,
            )
            for index in range(2)
        )
        gallery = OnlineGallery(
            456,
            "token",
            "https://e-hentai.org/g/456/token/",
            "Online title",
            "Manga",
            posted="2026-08-15 12:00",
            page_count=20,
            tags=("artist:someone",),
            uploader="poster",
            rating=4.0,
        )
        detail_widget.setOnlineLoading(gallery, provider, cache)
        self.assertTrue(detail_widget.openFolderButton.isHidden())
        self.assertTrue(detail_widget.categoryButton.isHidden())
        local_item = SimpleNamespace(gid=gallery.gid, folder=self.root)
        detail_widget.setFolderOpenTarget(local_item)
        self.assertFalse(detail_widget.openFolderButton.isHidden())
        detail_widget.openFolderButton.click()
        self.assertEqual([gallery.gid], [item.gid for item in folder_requests])
        self.assertTrue(detail_widget.isOnlineGallery)
        self.assertFalse(detail_widget.operationCard.isHidden())
        self.assertFalse(detail_widget.previewCard.isHidden())
        self.assertFalse(detail_widget.readButton.isEnabled())
        self.assertFalse(detail_widget.commentsCard.isHidden())

        online_detail = OnlineGalleryDetail(
            gallery=gallery,
            title="Full online title",
            language="Chinese",
            file_size="42 MiB",
            visible="Yes",
            page_count=20,
            tags=("artist:someone", "language:chinese"),
            comments=(
                OnlineGalleryComment(
                    "12",
                    "reader",
                    "15 August 2026",
                    "copyable comment",
                    2,
                    gallery_links=(
                        OnlineGalleryLink(789, "deadbeef01", "Sequel"),
                    ),
                ),
            ),
            previews=previews,
        )
        requested = []
        download_requested = []
        download_cancelled = []
        detail_widget.onlineReadRequested.connect(
            lambda _detail, page_index: requested.append(page_index)
        )
        detail_widget.onlineDownloadRequested.connect(download_requested.append)
        detail_widget.onlineDownloadCancelRequested.connect(
            download_cancelled.append
        )
        gallery_links_requested = []
        detail_widget.onlineGalleryLinkRequested.connect(
            gallery_links_requested.append
        )
        detail_widget.setOnlineDetail(online_detail, provider=provider, cache=cache)
        detail_widget.waitForOnlineLoads(3000)
        QApplication.processEvents()
        online_tag_chips = detail_widget.findChildren(QLabel, "mangaTagChip")
        self.assertEqual(
            {"someone", "汉语"},
            {chip.text() for chip in online_tag_chips},
        )
        self.assertEqual(
            {"artist:someone", "language:chinese"},
            {chip.toolTip() for chip in online_tag_chips},
        )
        comment_bodies = detail_widget.findChildren(QLabel, "onlineCommentBody")
        self.assertEqual(["copyable comment"], [label.text() for label in comment_bodies])
        selectable = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        self.assertEqual(
            selectable,
            comment_bodies[0].textInteractionFlags() & selectable,
        )
        gallery_link_button = detail_widget.findChild(
            QWidget,
            "onlineCommentGalleryLink",
        )
        self.assertIsNotNone(gallery_link_button)
        self.assertIn("Sequel", gallery_link_button.text())
        QTest.mouseClick(gallery_link_button, Qt.LeftButton)
        self.assertEqual(
            [(789, "deadbeef01")],
            [(link.gid, link.token) for link in gallery_links_requested],
        )
        self.assertIn("语言：Chinese", detail_widget.metadataLabel.text())
        self.assertNotIn("页数：", detail_widget.metadataLabel.text())
        self.assertNotIn("文件大小：", detail_widget.metadataLabel.text())
        self.assertNotIn("可见性：", detail_widget.metadataLabel.text())
        self.assertTrue(detail_widget.detailMetadataLabel.isHidden())
        QTest.mouseClick(detail_widget.detailMetadataButton, Qt.LeftButton)
        self.assertIn("页数：20", detail_widget.detailMetadataLabel.text())
        self.assertIn("文件大小：42 MiB", detail_widget.detailMetadataLabel.text())
        self.assertIn("可见性：Yes", detail_widget.detailMetadataLabel.text())
        self.assertEqual([0, 1], [tile.pageIndex for tile in detail_widget._preview_tiles])
        self.assertTrue(
            all(not tile.imageLabel.pixmap().isNull() for tile in detail_widget._preview_tiles)
        )
        preview_colors = [
            tile.imageLabel.pixmap().toImage().pixelColor(0, 0)
            for tile in detail_widget._preview_tiles
        ]
        self.assertEqual(QColor("red"), preview_colors[0])
        self.assertEqual(QColor("blue"), preview_colors[1])
        QTest.mouseClick(detail_widget._preview_tiles[1], Qt.LeftButton)
        self.assertEqual([1], requested)
        detail_widget.setOnlineDownloadState("downloading", 3, 20)
        self.assertIn("3 / 20", detail_widget.downloadButton.text())
        self.assertFalse(detail_widget.downloadProgressBar.isHidden())
        QTest.mouseClick(detail_widget.downloadButton, Qt.LeftButton)
        self.assertEqual([456], download_cancelled)
        detail_widget.resize(760, 700)
        detail_widget.show()
        long_error = "网络连接已经断开，请检查代理设置后继续下载这个画廊"
        detail_widget.setOnlineDownloadState("failed", 3, 20, long_error)
        QApplication.processEvents()
        self.assertIn("3 / 20", detail_widget.downloadButton.text())
        self.assertEqual(long_error, detail_widget.downloadProgressLabel.toolTip())
        self.assertLessEqual(
            detail_widget.readButton.geometry().right(),
            detail_widget.operationCard.contentsRect().right(),
        )
        self.assertGreaterEqual(
            detail_widget.downloadProgressBar.geometry().top(),
            detail_widget.downloadButton.geometry().bottom(),
        )
        QTest.mouseClick(detail_widget.downloadButton, Qt.LeftButton)
        self.assertEqual([online_detail], download_requested)

        page = self._create_pages(1)[0]
        local_detail_item = make_item(self.root, (page,))
        category_requests = []
        detail_widget.categorySelectionRequested.connect(
            category_requests.append
        )
        detail_widget.setManga(local_detail_item)
        QApplication.processEvents()
        self.assertFalse(detail_widget.isOnlineGallery)
        self.assertFalse(detail_widget.categoryButton.isHidden())
        detail_widget.categoryButton.click()
        self.assertEqual([local_detail_item], category_requests)
        self.assertTrue(detail_widget.commentsCard.isHidden())
        self.assertFalse(detail_widget.operationCard.isHidden())
        self.assertFalse(detail_widget.previewCard.isHidden())
        detail_widget.cancelLoads()
        detail_widget.close()
        detail_widget.deleteLater()
        QApplication.processEvents()

    def test_detail_selected_title_can_search_local_or_online(self):
        page = self._create_pages(1)[0]
        detail = MangaDetailInterface(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
        )
        item = replace(
            make_item(self.root, (page,)),
            english_title="A long selected title",
        )
        detail.setManga(item)
        local_requests = []
        online_requests = []
        detail.selectedTitleSearchRequested.connect(
            lambda *args: local_requests.append(args)
        )
        detail.selectedTitleOnlineSearchRequested.connect(
            online_requests.append
        )
        class SelectedLabel(QLabel):
            def selectedText(self):
                return "long"

        selected_label = SelectedLabel(detail)
        menus = []

        with patch(
            "app.view.manga_detail_interface.RoundMenu.exec",
            lambda menu, _position: menus.append(menu),
        ):
            detail._showTitleContextMenu(
                selected_label,
                selected_label.rect().center(),
            )

        actions = {action.text(): action for action in menus[0].menuActions()}
        self.assertIn("在本地搜索所选文本", actions)
        self.assertIn("在线搜索所选文本", actions)
        actions["在本地搜索所选文本"].trigger()
        actions["在线搜索所选文本"].trigger()
        selected = selected_label.selectedText()
        self.assertEqual([(item.gid, selected)], local_requests)
        self.assertEqual([selected], online_requests)
        detail.cancelLoads()
        detail.deleteLater()
        QApplication.processEvents()

    def test_local_detail_sync_updates_comments_and_old_parent_status(self):
        page = self._create_pages(1)[0]
        item = replace(
            make_item(self.root, (page,)),
            gallery_token="localtoken",
        )
        source = EhViewerDataSource(self.root / "unused.db", self.root)
        detail_widget = MangaDetailInterface(source, self.repository)
        requested = []
        update_requested = []
        detail_widget.localMetadataSyncRequested.connect(requested.append)
        detail_widget.galleryUpdateRequested.connect(update_requested.append)
        detail_widget.setManga(item)

        QTest.mouseClick(detail_widget.syncButton, Qt.LeftButton)

        self.assertEqual([item], requested)
        gallery = OnlineGallery(
            123,
            "localtoken",
            "https://e-hentai.org/g/123/localtoken/",
            "Synced title",
            page_count=1,
        )
        synced = OnlineGalleryDetail(
            gallery=gallery,
            title="Synced title",
            page_count=1,
            tags=("artist:updated",),
            comments=(
                OnlineGalleryComment("1", "reader", "today", "new comment"),
            ),
            newer_gallery_urls=(
                "https://e-hentai.org/g/124/newtoken/",
            ),
        )

        resolved = detail_widget.applyLocalSyncedDetail(synced)
        QApplication.processEvents()

        self.assertTrue(resolved.metadata_synced)
        self.assertFalse(detail_widget.commentsCard.isHidden())
        self.assertEqual(
            ["new comment"],
            [
                label.text()
                for label in detail_widget.findChildren(QLabel, "onlineCommentBody")
            ],
        )
        self.assertIn("1", detail_widget.galleryVersionLabel.text())
        self.assertEqual(
            "outdated",
            detail_widget.galleryVersionLabel.property("versionState"),
        )
        self.assertFalse(detail_widget.updateButton.isHidden())
        QTest.mouseClick(detail_widget.updateButton, Qt.LeftButton)
        self.assertEqual([resolved], update_requested)
        detail_widget.resize(560, 700)
        detail_widget.show()
        QApplication.processEvents()
        self.assertLessEqual(
            detail_widget.operationCard.width(),
            detail_widget.scrollArea.viewport().width(),
        )
        self.assertLessEqual(
            detail_widget.readButton.geometry().right(),
            detail_widget.operationCard.contentsRect().right(),
        )
        detail_widget.cancelLoads()
        detail_widget.close()
        detail_widget.deleteLater()
        QApplication.processEvents()

    def test_local_detail_adds_resumed_download_page_to_preview(self):
        first_page, second_page = self._create_pages(2)
        item = replace(
            make_item(self.root, (first_page,)),
            page_count=2,
            downloaded_page_count=1,
            download_complete=False,
        )
        source = EhViewerDataSource(self.root / "unused.db", self.root)
        detail_widget = MangaDetailInterface(source, self.repository)
        detail_widget.setManga(item)

        updated = detail_widget.addDownloadedPage(
            item.gid, 1, second_page, 2, 2
        )
        detail_widget._refreshLocalPreviewAfterDownload()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual((first_page, second_page), updated.page_paths)
        self.assertEqual(2, detail_widget.currentItem.downloaded_page_count)
        self.assertTrue(detail_widget.currentItem.download_complete)
        self.assertEqual(2, len(detail_widget._preview_tiles))
        self.assertIn("共 2 页", detail_widget.previewTitle.text())
        detail_widget.cancelLoads()
        detail_widget.close()
        detail_widget.deleteLater()
        QApplication.processEvents()

    def test_incomplete_sidecar_uses_online_thumbnail_and_patches_one_tile(self):
        pages = self._create_pages(3)
        item = replace(
            make_item(self.root, (pages[0], pages[2])),
            page_count=3,
            downloaded_page_count=2,
            download_complete=False,
            gallery_token="gallery-token",
            page_tokens=("page-1", "page-2", "page-3"),
        )
        gallery = OnlineGallery(
            item.gid,
            item.gallery_token,
            f"https://e-hentai.org/g/{item.gid}/{item.gallery_token}/",
            item.display_title,
            page_count=3,
        )
        previews = tuple(
            OnlineGalleryPreview(
                index,
                f"https://e-hentai.org/s/page-{index + 1}/{item.gid}-{index + 1}",
                f"https://a.hath.network/thumb-{index}.png",
                page_token=f"page-{index + 1}",
            )
            for index in range(3)
        )
        online_detail = OnlineGalleryDetail(
            gallery=gallery,
            title=item.display_title,
            page_count=3,
            previews=previews,
        )
        thumbnail_data = QByteArray()
        thumbnail_buffer = QBuffer(thumbnail_data)
        self.assertTrue(thumbnail_buffer.open(QIODevice.WriteOnly))
        thumbnail = QImage(20, 30, QImage.Format_RGB32)
        thumbnail.fill(QColor("yellow"))
        self.assertTrue(thumbnail.save(thumbnail_buffer, "PNG"))

        class Provider:
            settings = SimpleNamespace(site="ehentai")

            def load_gallery_preview_page(self, _gallery, _page_number):
                from app.domain.online_gallery import OnlineGalleryPreviewPage
                return OnlineGalleryPreviewPage(gallery, 1, 1, previews)

            def load_preview_thumbnail(self, _preview):
                return bytes(thumbnail_data)

        detail_widget = MangaDetailInterface(
            EhViewerDataSource(self.root / "unused.db", self.root),
            self.repository,
        )
        requested = []
        detail_widget.readRequested.connect(
            lambda current_item, index: requested.append((current_item, index))
        )
        detail_widget.setManga(item)
        detail_widget.setLocalOnlineContext(
            online_detail, Provider(), OnlineGalleryMemoryCache()
        )
        for _ in range(3):
            detail_widget.waitForOnlineLoads(3000)
            QThreadPool.globalInstance().waitForDone(3000)
            QApplication.processEvents()

        self.assertEqual([0, 1, 2], [tile.pageIndex for tile in detail_widget._preview_tiles])
        self.assertFalse(detail_widget._preview_tiles[1].imageLabel.pixmap().isNull())
        tile_ids = tuple(id(tile) for tile in detail_widget._preview_tiles)
        metadata_before = detail_widget.detailMetadataLabel.text()

        detail_widget.addDownloadedPage(item.gid, 1, pages[1], 3, 3)
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual(tile_ids, tuple(id(tile) for tile in detail_widget._preview_tiles))
        self.assertEqual(metadata_before, detail_widget.detailMetadataLabel.text())
        self.assertFalse(detail_widget._preview_tiles[1].imageLabel.pixmap().isNull())
        QTest.mouseClick(detail_widget._preview_tiles[1], Qt.LeftButton)
        self.assertEqual(1, requested[-1][1])
        self.assertIn(pages[1], requested[-1][0].page_paths)
        detail_widget.cancelLoads()
        detail_widget.close()
        detail_widget.deleteLater()
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
