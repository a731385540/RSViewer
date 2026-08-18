import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt, QThreadPool
from PySide6.QtGui import QColor, QContextMenuEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from app.common.config import cfg
from app.domain.online_download import (
    ONLINE_DOWNLOAD_COMPLETED,
    OnlineGalleryDownloadRecord,
    ORIGINAL_STATE_ACTIVE,
)
from app.domain.manga import MangaItem
from app.repositories.user_library_repository import UserLibraryRepository
from app.view.local_manga_interface import (
    FluentSplitterHandle,
    CoverLabel,
    LocalMangaInterface,
    MangaLabelSelectionDialog,
    MangaGridCard,
    ORIGINAL_FALLBACK_BADGE_COLOR,
    ORIGINAL_PENDING_BORDER_COLOR,
    PlaylistOrderDialog,
)
from app.view.manga_history_interface import MangaHistoryInterface


class EmptySource:
    def __init__(self):
        self.primary_updates = []
        self.primary_deletes = []
        self.primary_clears = []
        self.items = []

    def list_local_manga(self):
        return list(self.items)

    def list_primary_labels(self):
        return ["分类 A", "分类 B"]

    def set_primary_label(self, gids, label):
        self.primary_updates.append((tuple(gids), label))

    def create_primary_label(self, label):
        return None

    def delete_primary_label(self, label):
        self.primary_deletes.append(label)

    def clear_primary_label(self, gids):
        self.primary_clears.append(tuple(gids))


def make_item(root: Path, gid: int, added_time: int):
    return MangaItem(
        gid=gid,
        english_title=f"Manga {gid}",
        original_title="",
        category=4,
        category_name="漫画",
        primary_label="",
        multiple_labels=(),
        tags=(),
        folder=root / str(gid),
        cover_path=root / f"{gid}.thumb",
        thumbnail_path=None,
        page_paths=(),
        page_count=0,
        added_time=added_time,
    )


class LocalLibraryControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_sort_order = cfg.get(cfg.mangaSortOrder)
        self.original_primary_filter = cfg.get(cfg.mangaPrimaryLabelFilter)
        self.original_search_hover = cfg.get(cfg.mangaSearchHoverEnabled)
        cfg.set(cfg.mangaSortOrder, "desc")
        cfg.set(cfg.mangaPrimaryLabelFilter, "__none__")
        cfg.set(cfg.mangaSearchHoverEnabled, True)
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.repository = UserLibraryRepository(self.root / "rsviewer.db")
        self.source = EmptySource()
        self.interface = LocalMangaInterface(self.source, self.repository)
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.interface._preloadCovers = lambda: None
        self.items = [
            make_item(self.root, 1, 10),
            make_item(self.root, 2, 30),
            make_item(self.root, 3, 20),
        ]
        self.interface._onLoaded(
            (self.items, ["分类 A", "分类 B"], [])
        )

    def test_original_pending_border_uses_dark_yellow(self):
        self.assertEqual("#B8860B", ORIGINAL_PENDING_BORDER_COLOR)

    def test_cover_scaling_is_cached_until_widget_size_changes(self):
        image = QImage(480, 640, QImage.Format_RGB32)
        image.fill(QColor("red"))
        label = CoverLabel(self.root / "unused", image=image)
        label.resize(180, 245)
        label.show()
        QApplication.processEvents()
        cached_key = label._display_pixmap.cacheKey()

        label.grab()
        label.grab()

        self.assertEqual(cached_key, label._display_pixmap.cacheKey())
        label.resize(200, 272)
        QApplication.processEvents()
        self.assertNotEqual(cached_key, label._display_pixmap.cacheKey())
        label.deleteLater()

    def test_original_fallback_card_has_purple_badge_and_original_border(self):
        item = replace(
            self.items[0],
            original_state=ORIGINAL_STATE_ACTIVE,
            original_fallback_to_standard=True,
        )
        card = MangaGridCard(item)
        card.setCardWidth(180)
        card.show()
        QApplication.processEvents()
        image = QImage(card.size(), QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        card.render(image)

        badge = QColor(ORIGINAL_FALLBACK_BADGE_COLOR)
        badge_pixels = [
            image.pixelColor(x, y)
            for x in range(11, 23)
            for y in range(11, 23)
        ]
        self.assertIn(badge, badge_pixels)
        self.assertFalse(card.originalFallbackBadge.isHidden())
        self.assertIn("部分页面没有原图", card.toolTip())
        card.close()
        card.deleteLater()

    def tearDown(self):
        self.interface.cancelLoad()
        self.interface.close()
        self.interface.deleteLater()
        cfg.set(cfg.mangaSortOrder, self.original_sort_order)
        cfg.set(cfg.mangaPrimaryLabelFilter, self.original_primary_filter)
        cfg.set(cfg.mangaSearchHoverEnabled, self.original_search_hover)
        QApplication.processEvents()
        self.temp_directory.cleanup()

    def test_added_time_sort_defaults_to_desc_and_can_switch_to_asc(self):
        self.assertEqual(
            [2, 3, 1],
            [item.gid for item in self.interface._filtered_items],
        )

        self.interface.sortCombo.setCurrentIndex(1)
        QApplication.processEvents()

        self.assertEqual("asc", cfg.get(cfg.mangaSortOrder))
        self.assertEqual(
            [1, 3, 2],
            [item.gid for item in self.interface._filtered_items],
        )

    def test_download_refresh_reveals_new_gallery_and_ignores_old_filters(self):
        downloaded = replace(
            make_item(self.root, 99, 40),
            english_title="Updated downloaded title",
            primary_label="分类 B",
        )
        self.source.items = [*self.items, downloaded]
        self.repository.save_online_gallery_download(
            OnlineGalleryDownloadRecord(
                gid=downloaded.gid,
                site="exhentai",
                token="token",
                title=downloaded.english_title,
                dirname=downloaded.folder.name,
                page_count=24,
                completed_pages=24,
                state=ONLINE_DOWNLOAD_COMPLETED,
                metadata={
                    "uploader": "download-uploader",
                    "posted": "2026-08-15 12:00",
                    "rating": 4.75,
                    "language": "Chinese",
                    "file_size": "18 MiB",
                },
            )
        )
        self.interface.searchEdit.setText("does not match")
        self.interface._primary_label_filter = "分类 A"

        loaded = []
        self.interface.libraryLoaded.connect(loaded.append)
        self.interface.reload(reveal_gid=downloaded.gid)
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual("", self.interface.searchEdit.text())
        self.assertTrue(self.interface._show_all_manga)
        self.assertIn(downloaded.gid, [item.gid for item in self.interface._filtered_items])
        refreshed = next(
            item for item in self.interface.allItems() if item.gid == downloaded.gid
        )
        self.assertEqual("Updated downloaded title", refreshed.english_title)
        self.assertEqual(24, refreshed.page_count)
        self.assertEqual("download-uploader", refreshed.uploader)
        self.assertEqual("18 MiB", refreshed.file_size)
        self.assertEqual(downloaded.gid, loaded[-1][-1].gid)

    def test_regular_refresh_preserves_selected_category_filter(self):
        categorized_items = [
            replace(self.items[0], primary_label="分类 A"),
            replace(self.items[1], primary_label="分类 B"),
            self.items[2],
        ]
        self.source.items = categorized_items
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(1)
        )
        QApplication.processEvents()

        self.interface.reload()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertFalse(self.interface._show_all_manga)
        self.assertEqual("分类 A", self.interface._primary_label_filter)
        self.assertEqual(
            "分类 A", self.interface.primaryLabelTree.currentItem().text(0)
        )
        self.assertEqual(
            [1], [item.gid for item in self.interface._filtered_items]
        )

    def test_incremental_download_update_preserves_active_category_and_search(self):
        categorized = replace(self.items[0], primary_label="分类 A")
        other = replace(self.items[1], primary_label="分类 B")
        self.interface._onLoaded(
            ([categorized, other, self.items[2]], ["分类 A", "分类 B"], [])
        )
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(1)
        )
        self.interface.searchEdit.setText("Manga 1")
        QApplication.processEvents()
        before_scroll = self.interface.scrollArea.verticalScrollBar().value()

        self.interface.upsertItem(
            replace(categorized, page_count=24, standard_download_pending=False)
        )

        self.assertFalse(self.interface._show_all_manga)
        self.assertEqual("分类 A", self.interface._primary_label_filter)
        self.assertEqual("Manga 1", self.interface.searchEdit.text())
        self.assertEqual([1], [item.gid for item in self.interface._filtered_items])
        self.assertEqual(before_scroll, self.interface.scrollArea.verticalScrollBar().value())
        self.assertEqual(24, self.interface._cards[0].item.page_count)

    def test_incremental_nonmatching_registration_does_not_rebuild_cards(self):
        categorized = replace(self.items[0], primary_label="分类 A")
        self.interface._onLoaded(
            ([categorized], ["分类 A", "分类 B"], [])
        )
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(1)
        )
        self.interface._renderCards = MagicMock()

        self.interface.upsertItem(
            replace(self.items[1], primary_label="分类 B")
        )

        self.interface._renderCards.assert_not_called()
        self.assertEqual([1], [item.gid for item in self.interface._filtered_items])
        self.assertEqual({1, 2}, {item.gid for item in self.interface._all_items})

    def test_inflight_library_load_keeps_new_incremental_registration(self):
        worker = object()
        downloaded = replace(self.items[2], primary_label="分类 B")
        self.interface._load_worker = worker

        self.interface.upsertItem(downloaded)
        self.interface._onLoaded(
            ([self.items[0], self.items[1]], ["分类 A", "分类 B"], []),
            worker,
        )

        self.assertEqual({1, 2, 3}, {item.gid for item in self.interface._all_items})
        merged = next(item for item in self.interface._all_items if item.gid == 3)
        self.assertEqual("分类 B", merged.primary_label)

    def test_regular_refresh_preserves_playlist_mode_show_all_and_page(self):
        playlist_id = self.repository.create_playlist("阅读中")
        self.repository.assign_label_to_mangas((1, 2), playlist_id)
        self.source.items = list(self.items)
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_PLAYLIST)
        self.interface.playlistTree.setCurrentItem(
            self.interface._playlist_items[playlist_id]
        )
        self.interface._page_size = 1
        self.interface.applyFilters(reset_page=True)
        self.interface.setPage(2)
        self.interface._selected_gids = {1}

        self.interface.reload()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual(self.interface.TAG_PLAYLIST, self.interface._tag_mode)
        self.assertEqual(playlist_id, self.interface._playlist_filter_id)
        self.assertEqual({1, 2}, {item.gid for item in self.interface._filtered_items})
        self.assertEqual(2, self.interface._page)
        self.assertEqual({1}, self.interface._selected_gids)

        self.interface._showAllManga()
        self.interface.reload()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertTrue(self.interface._show_all_manga)
        self.assertEqual({1, 2, 3}, {item.gid for item in self.interface._filtered_items})

    def test_category_defaults_to_unclassified_and_remembers_selection(self):
        self.assertEqual("未分类", self.interface.primaryLabelTree.currentItem().text(0))
        self.assertNotIn(
            "全部漫画",
            [
                self.interface.primaryLabelTree.topLevelItem(index).text(0)
                for index in range(self.interface.primaryLabelTree.topLevelItemCount())
            ],
        )
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(2)
        )
        self.assertEqual("分类 B", cfg.get(cfg.mangaPrimaryLabelFilter))
        self.interface._populatePrimaryLabels(["分类 A", "分类 B"])
        self.assertEqual("分类 B", self.interface.primaryLabelTree.currentItem().text(0))

    def test_tag_sidebar_is_hidden_by_default_and_can_be_toggled(self):
        self.interface.resize(1100, 760)
        self.interface.show()
        QApplication.processEvents()
        self.assertTrue(self.interface.classificationCard.isHidden())

        self.interface.tagButton.click()
        QApplication.processEvents()
        self.assertTrue(self.interface.classificationCard.isVisible())
        splitter_handle = self.interface.tagSplitter.handle(1)
        self.assertIsInstance(splitter_handle, FluentSplitterHandle)
        self.assertEqual(7, splitter_handle.width())
        QApplication.sendEvent(splitter_handle, QEvent(QEvent.Enter))
        self.assertTrue(splitter_handle._hovered)
        self.interface.upsertItem(
            replace(self.interface._all_items[0], primary_label="分类 A")
        )
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(1)
        )
        QApplication.processEvents()
        self.assertEqual(
            [self.interface._all_items[0].gid],
            [item.gid for item in self.interface._filtered_items],
        )
        expanded_sidebar_viewport_width = self.interface.scrollArea.viewport().width()

        self.interface.tagButton.click()
        QTest.qWait(80)
        QApplication.processEvents()
        self.assertTrue(self.interface.classificationCard.isHidden())
        collapsed_sidebar_viewport_width = self.interface.scrollArea.viewport().width()
        expected_columns = max(
            1,
            (
                collapsed_sidebar_viewport_width
                + self.interface.contentLayout.horizontalSpacing()
            )
            // (
                188 + self.interface.contentLayout.horizontalSpacing()
            ),
        )
        self.assertGreater(
            collapsed_sidebar_viewport_width,
            expanded_sidebar_viewport_width,
        )
        self.assertEqual(expected_columns, self.interface._last_columns)

    def test_search_hover_expands_and_only_empty_search_auto_hides(self):
        self.interface.resize(1000, 700)
        self.interface.show()
        QApplication.processEvents()
        self.assertTrue(self.interface.searchPanel.isHidden())

        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Enter)
        )
        QApplication.processEvents()
        self.assertTrue(self.interface.searchPanel.isVisible())
        self.assertIsNot(QApplication.focusWidget(), self.interface.searchEdit)

        self.interface.searchEdit.setText("Manga")
        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Leave)
        )
        QTest.qWait(200)
        self.assertTrue(self.interface.searchPanel.isVisible())

        self.interface.searchEdit.clear()
        QApplication.sendEvent(
            self.interface.searchPanel, QEvent(QEvent.Leave)
        )
        QTest.qWait(200)
        self.assertTrue(self.interface.searchPanel.isHidden())

        cfg.set(cfg.mangaSearchHoverEnabled, False)
        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Enter)
        )
        QApplication.processEvents()
        self.assertTrue(self.interface.searchPanel.isHidden())

    def test_search_button_pins_hover_panel_until_clicked_again(self):
        self.interface.resize(1000, 700)
        self.interface.show()
        QApplication.processEvents()

        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Enter)
        )
        QApplication.processEvents()
        self.assertTrue(self.interface.searchPanel.isVisible())
        self.assertFalse(self.interface._search_pinned)

        self.interface.searchButton.click()
        self.assertTrue(self.interface._search_pinned)
        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Leave)
        )
        QApplication.sendEvent(
            self.interface.searchPanel, QEvent(QEvent.Leave)
        )
        QTest.qWait(200)
        self.assertTrue(self.interface.searchPanel.isVisible())

        self.interface.searchButton.click()
        self.assertFalse(self.interface._search_pinned)
        QApplication.sendEvent(
            self.interface.searchButton, QEvent(QEvent.Leave)
        )
        QApplication.sendEvent(
            self.interface.searchPanel, QEvent(QEvent.Leave)
        )
        QTest.qWait(200)
        self.assertTrue(self.interface.searchPanel.isHidden())

        cfg.set(cfg.mangaSearchHoverEnabled, False)
        self.interface.searchButton.click()
        self.assertTrue(self.interface._search_pinned)
        self.assertTrue(self.interface.searchPanel.isVisible())
        QApplication.sendEvent(
            self.interface.searchPanel, QEvent(QEvent.Leave)
        )
        QTest.qWait(200)
        self.assertTrue(self.interface.searchPanel.isVisible())
        self.interface.searchButton.click()
        self.assertTrue(self.interface.searchPanel.isHidden())

    def test_context_menu_opens_three_fixed_label_selection_dialogs(self):
        self.repository.create_playlist("播放列表 A")
        self.repository.create_taxonomy_label("全彩")
        self.interface._refreshTagData()
        requests = []
        self.interface._openLabelSelection = (
            lambda mode, gids, items: requests.append(
                (mode, tuple(gids), tuple(item.gid for item in items))
            )
        )
        menu = self.interface._buildLabelMenu(self.items[0])

        self.assertEqual(
            [
                "添加到收藏",
                "在资源管理器中打开",
                "同步在线信息",
                "搜索相似画廊",
                "选择分类…",
                "选择播放列表…",
                "选择归类…",
                "移入回收站",
            ],
            [action.text() for action in menu.menuActions() if action.text()],
        )
        self.assertEqual([], menu._subMenus)
        for action in menu.menuActions():
            if action.text().startswith("选择"):
                action.trigger()

        self.assertEqual(
            [
                (self.interface.TAG_CATEGORY, (1,), (1,)),
                (self.interface.TAG_PLAYLIST, (1,), (1,)),
                (self.interface.TAG_TAXONOMY, (1,), (1,)),
            ],
            requests,
        )

    def test_context_menu_searches_similar_chapters_and_duplicates(self):
        similar_items = [
            replace(
                self.items[0],
                english_title="[Circle (Author)] Real Work Ch. 01 [English]",
            ),
            replace(self.items[1], english_title="Real Work Chapter 2 [Digital]"),
            replace(self.items[2], english_title="Real Work 第3話"),
            make_item(self.root, 4, 40),
        ]
        similar_items[3] = replace(
            similar_items[3], english_title="A Completely Different Work"
        )
        self.interface._onLoaded(
            (similar_items, ["分类 A", "分类 B"], [], [])
        )

        menu = self.interface._buildLabelMenu(similar_items[0])
        next(
            action
            for action in menu.menuActions()
            if action.text() == "搜索相似画廊"
        ).trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual(
            [1, 2, 3], [item.gid for item in self.interface._filtered_items]
        )
        self.assertIn("找到 3 个", self.interface.resultLabel.text())
        self.assertFalse(self.interface.sortCombo.isEnabled())

        self.interface.searchEdit.setText("Different")
        QTest.qWait(220)
        QApplication.processEvents()
        self.assertIsNone(self.interface._similar_result_gids)
        self.assertTrue(self.interface.sortCombo.isEnabled())
        self.assertEqual([4], [item.gid for item in self.interface._filtered_items])

    def test_label_selection_dialog_searches_and_preserves_partial_state(self):
        category_item = replace(self.items[0], primary_label="分类 A")
        category_dialog = MangaLabelSelectionDialog(
            MangaLabelSelectionDialog.CATEGORY,
            (category_item,),
            primary_labels=("分类 A", "分类 B"),
            parent=self.interface,
        )
        self.assertEqual("分类 A", category_dialog.selectedCategory())
        category_dialog.searchEdit.setText("分类 B")
        QApplication.processEvents()
        self.assertTrue(category_dialog.tree.topLevelItem(1).isHidden())
        self.assertFalse(category_dialog.tree.topLevelItem(2).isHidden())
        category_dialog.tree.setCurrentItem(category_dialog.tree.topLevelItem(2))
        self.assertEqual("分类 B", category_dialog.selectedCategory())

        mixed_items = (
            replace(self.items[0], multiple_labels=("列表 A",)),
            self.items[1],
        )
        playlist_dialog = MangaLabelSelectionDialog(
            MangaLabelSelectionDialog.PLAYLIST,
            mixed_items,
            playlists=((7, "列表 A", 1, None),),
            parent=self.interface,
        )
        playlist_item = playlist_dialog.tree.topLevelItem(0)
        self.assertEqual(Qt.PartiallyChecked, playlist_item.checkState(0))
        self.assertEqual({}, playlist_dialog.selectionChanges())
        playlist_item.setCheckState(0, Qt.Checked)
        self.assertEqual({7: True}, playlist_dialog.selectionChanges())

        taxonomy_dialog = MangaLabelSelectionDialog(
            MangaLabelSelectionDialog.TAXONOMY,
            self.items,
            taxonomy_labels=(
                (10, None, "全彩", 0),
                (11, 10, "作者一", 0),
                (12, None, "黑白", 0),
            ),
            parent=self.interface,
        )
        taxonomy_dialog.searchEdit.setText("作者一")
        QApplication.processEvents()
        self.assertFalse(taxonomy_dialog.tree.topLevelItem(0).isHidden())
        self.assertTrue(taxonomy_dialog.tree.topLevelItem(1).isHidden())
        category_dialog.close()
        playlist_dialog.close()
        taxonomy_dialog.close()

    def test_dialog_selection_changes_apply_in_bulk(self):
        playlist_id = self.repository.create_playlist("批量播放列表")
        taxonomy_id = self.repository.create_taxonomy_label("批量归类")
        self.interface._refreshTagData()

        self.interface._applyPlaylistSelection((1, 2), {playlist_id: True})
        self.interface._applyTaxonomySelection((1, 2), {taxonomy_id: True})
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual(
            {1: ("批量播放列表",), 2: ("批量播放列表",)},
            self.repository.labels_for_manga((1, 2)),
        )
        self.assertEqual(
            {
                1: ((taxonomy_id, "批量归类"),),
                2: ((taxonomy_id, "批量归类"),),
            },
            self.repository.taxonomy_for_mangas((1, 2)),
        )

        self.interface._applyPlaylistSelection((1, 2), {playlist_id: False})
        self.interface._applyTaxonomySelection((1, 2), {taxonomy_id: False})
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertEqual({}, self.repository.labels_for_manga((1, 2)))
        self.assertEqual({}, self.repository.taxonomy_for_mangas((1, 2)))

    def test_context_menu_adds_and_removes_favorite(self):
        changes = []
        self.interface.favoriteChanged.connect(
            lambda gids, favorite: changes.append((tuple(gids), favorite))
        )
        menu = self.interface._buildLabelMenu(self.items[0])
        self.assertEqual("添加到收藏", menu.menuActions()[0].text())
        menu.menuActions()[0].trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual((1,), self.repository.favorite_gids())
        self.assertTrue(
            next(item for item in self.interface._all_items if item.gid == 1).is_favorite
        )
        self.assertEqual([((1,), True)], changes)

        refreshed = next(item for item in self.interface._all_items if item.gid == 1)
        remove_menu = self.interface._buildLabelMenu(refreshed)
        self.assertEqual("取消收藏", remove_menu.menuActions()[0].text())
        remove_menu.menuActions()[0].trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertEqual((), self.repository.favorite_gids())
        self.assertEqual([((1,), True), ((1,), False)], changes)

    def test_playlist_gallery_context_menu_removes_current_members(self):
        playlist_id = self.repository.create_playlist("待整理")
        self.repository.assign_label_to_mangas((1, 2, 3), playlist_id)
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_PLAYLIST)
        self.interface.playlistTree.setCurrentItem(
            self.interface._playlist_items[playlist_id]
        )
        QApplication.processEvents()

        menu = self.interface._buildLabelMenu(self.interface._filtered_items[0], (1, 2))
        remove_action = next(
            action
            for action in menu.menuActions()
            if action.text() == "从当前播放列表移除"
        )
        remove_action.trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual((3,), self.repository.playlist_items(playlist_id))
        self.assertEqual([3], [item.gid for item in self.interface._filtered_items])
        self.interface._setTagMode(self.interface.TAG_CATEGORY)
        self.assertNotIn(
            "从当前播放列表移除",
            [
                action.text()
                for action in self.interface._buildLabelMenu(
                    self.interface._all_items[0]
                ).menuActions()
            ],
        )

    def test_open_folder_context_action_only_targets_right_clicked_gallery(self):
        opened = []
        self.interface.folderOpenRequested.connect(
            lambda item: opened.append(item.gid)
        )
        right_clicked = self.items[2]
        menu = self.interface._buildLabelMenu(right_clicked, (1, 2))
        action = next(
            action
            for action in menu.menuActions()
            if action.text() == "在资源管理器中打开"
        )

        action.trigger()

        self.assertEqual([right_clicked.gid], opened)

    def test_collection_mode_reuses_items_and_preserves_repository_order(self):
        collection = LocalMangaInterface(
            self.source,
            self.repository,
            collection_kind="favorites",
            object_name="testFavoriteCollection",
        )
        collection._preloadCovers = lambda: None
        collection.setCollectionItems(self.items, (3, 1))

        self.assertEqual([3, 1], [item.gid for item in collection._filtered_items])
        self.assertTrue(collection.tagButton.isHidden())
        self.assertTrue(collection.sortCombo.isHidden())
        collection.close()
        collection.deleteLater()

    def test_history_page_has_local_content_and_reserved_online_tab(self):
        history = MangaHistoryInterface(self.source, self.repository)
        history.localHistoryInterface._preloadCovers = lambda: None
        history.setCollectionItems(self.items, (2, 3))

        self.assertEqual(
            [2, 3],
            [item.gid for item in history.localHistoryInterface._filtered_items],
        )
        history.modeSwitch.setCurrentItem(history.ONLINE)
        history.stack.setCurrentWidget(history.onlineHistoryInterface)
        self.assertIn("预留", history.onlineHistoryInterface.descriptionLabel.text())
        history.cancelLoad()
        history.close()
        history.deleteLater()

    def test_tag_tree_context_menus_delete_all_three_tag_types(self):
        self.assertIsNone(
            self.interface._buildTagTreeMenu(
                self.interface.TAG_CATEGORY,
                self.interface.primaryLabelTree.topLevelItem(0),
            )
        )
        self.interface._confirmDeleteTag = lambda _name, _description: False
        cancelled_menu = self.interface._buildTagTreeMenu(
            self.interface.TAG_CATEGORY,
            self.interface.primaryLabelTree.topLevelItem(1),
        )
        cancelled_menu.menuActions()[0].trigger()
        self.assertEqual([], self.source.primary_deletes)

        self.interface._confirmDeleteTag = lambda _name, _description: True
        category_menu = self.interface._buildTagTreeMenu(
            self.interface.TAG_CATEGORY,
            self.interface.primaryLabelTree.topLevelItem(1),
        )
        category_menu.menuActions()[0].trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertEqual(["分类 A"], self.source.primary_deletes)
        self.assertNotIn("分类 A", self.interface._primary_labels)

        playlist_id = self.repository.create_playlist("待删除播放列表")
        self.repository.assign_label_to_mangas((1, 2), playlist_id)
        taxonomy_id = self.repository.create_taxonomy_label("待删除归类")
        self.repository.assign_taxonomy_to_mangas((1, 2), taxonomy_id)
        self.interface._refreshTagData()

        playlist_menu = self.interface._buildTagTreeMenu(
            self.interface.TAG_PLAYLIST,
            self.interface._playlist_items[playlist_id],
        )
        playlist_menu.menuActions()[0].trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertNotIn(
            playlist_id,
            [entry[0] for entry in self.repository.list_playlists()],
        )

        taxonomy_menu = self.interface._buildTagTreeMenu(
            self.interface.TAG_TAXONOMY,
            self.interface._taxonomy_items[taxonomy_id],
        )
        taxonomy_menu.menuActions()[0].trigger()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertNotIn(
            taxonomy_id,
            [entry[0] for entry in self.repository.list_taxonomy_labels()],
        )

    def test_right_click_without_multi_select_opens_menu_not_gallery(self):
        self.interface.resize(1100, 760)
        self.interface.show()
        QApplication.processEvents()

        for mode in (self.interface.GRID_MODE, self.interface.LIST_MODE):
            with self.subTest(mode=mode):
                self.interface.setLayoutMode(mode)
                QApplication.processEvents()
                card = self.interface._cards[0]
                activated = []
                menu_requests = []
                card.openCallback = lambda item: activated.append(item.gid)
                card.labelMenuCallback = (
                    lambda item, position: menu_requests.append(item.gid)
                )

                QTest.mouseClick(card, Qt.RightButton, pos=card.rect().center())
                event = QContextMenuEvent(
                    QContextMenuEvent.Mouse,
                    QPoint(10, 10),
                    card.mapToGlobal(QPoint(10, 10)),
                )
                QApplication.sendEvent(card, event)

                self.assertFalse(self.interface._selection_mode)
                self.assertEqual([], activated)
                self.assertEqual([card.item.gid], menu_requests)

    def test_multi_select_applies_context_actions_to_all_selected_items(self):
        self.repository.create_playlist("批量列表")
        self.interface._refreshTagData()
        activated = []
        self.interface.mangaActivated.connect(lambda item: activated.append(item.gid))
        self.interface.resize(1100, 760)
        self.interface.show()
        self.interface.multiSelectCheckBox.setChecked(True)
        QApplication.processEvents()

        QTest.mouseClick(
            self.interface._cards[0],
            Qt.LeftButton,
            pos=self.interface._cards[0].rect().center(),
        )
        QTest.mouseClick(
            self.interface._cards[1],
            Qt.LeftButton,
            pos=self.interface._cards[1].rect().center(),
        )
        self.assertEqual({2, 3}, self.interface._selected_gids)
        self.assertEqual([], activated)
        self.assertEqual("已选 2 项", self.interface.selectionCountLabel.text())

        selected_item = self.interface._cards[0].item
        menu = self.interface._buildLabelMenu(
            selected_item, self.interface._selected_gids
        )
        selection_requests = []
        self.interface._openLabelSelection = (
            lambda mode, gids, items: selection_requests.append(
                (mode, tuple(gids), tuple(item.gid for item in items))
            )
        )
        for action in menu.menuActions():
            if action.text() in {"选择分类…", "选择播放列表…"}:
                action.trigger()
        self.assertEqual(
            [
                (self.interface.TAG_CATEGORY, (2, 3), (2, 3)),
                (self.interface.TAG_PLAYLIST, (2, 3), (2, 3)),
            ],
            selection_requests,
        )

        playlist_id = self.repository.list_playlists()[0][0]
        self.interface._setMangaPrimaryLabel((2, 3), "分类 B")
        self.interface._applyPlaylistSelection((2, 3), {playlist_id: True})
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertIn(((2, 3), "分类 B"), self.source.primary_updates)
        self.assertEqual(
            {2: ("批量列表",), 3: ("批量列表",)},
            self.repository.labels_for_manga([1, 2, 3]),
        )
        self.interface._setMangaPrimaryLabel((2, 3), "")
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertEqual([(2, 3)], self.source.primary_clears)
        self.assertTrue(
            all(
                not item.primary_label
                for item in self.interface._all_items
                if item.gid in (2, 3)
            )
        )
        self.interface._applyPlaylistSelection((2, 3), {playlist_id: False})
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()
        self.assertEqual({}, self.repository.labels_for_manga([2, 3]))

    def test_select_all_uses_all_pages_in_current_filter_and_syncs_batch(self):
        items = [
            replace(make_item(self.root, gid, gid), primary_label=label)
            for gid, label in (
                (10, "分类 A"),
                (11, "分类 A"),
                (12, "分类 A"),
                (13, "分类 B"),
            )
        ]
        self.interface._page_size = 2
        self.interface._onLoaded((items, ["分类 A", "分类 B"], []))
        self.interface.primaryLabelTree.setCurrentItem(
            self.interface.primaryLabelTree.topLevelItem(1)
        )
        self.interface.multiSelectCheckBox.setChecked(True)
        QApplication.processEvents()

        self.assertFalse(self.interface.selectAllButton.isHidden())
        self.interface.selectAllButton.click()

        self.assertEqual({10, 11, 12}, self.interface._selected_gids)
        self.assertEqual("已选 3 项", self.interface.selectionCountLabel.text())
        self.assertEqual("取消全选", self.interface.selectAllButton.toolTip())
        self.interface.setPage(2)
        self.assertEqual(1, len(self.interface._cards))
        self.assertTrue(self.interface._cards[0].selectionCheckBox.isChecked())

        requested = []
        self.interface.metadataSyncRequested.connect(requested.append)
        menu = self.interface._buildLabelMenu(
            self.interface._cards[0].item,
            self.interface._selected_gids,
        )
        next(
            action
            for action in menu.menuActions()
            if action.text() == "同步在线信息"
        ).trigger()
        self.assertEqual(
            [(10, 11, 12)],
            [tuple(item.gid for item in batch) for batch in requested],
        )

        self.interface.selectAllButton.click()
        self.assertEqual(set(), self.interface._selected_gids)
        self.assertEqual(
            "全选当前筛选结果", self.interface.selectAllButton.toolTip()
        )

    def test_playlist_order_and_play_actions_use_the_selected_playlist(self):
        playlist_id = self.repository.create_playlist("顺序测试")
        self.repository.assign_label_to_mangas((1, 2, 3), playlist_id)
        self.repository.set_playlist_order(playlist_id, (3, 1, 2))
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_PLAYLIST)

        self.assertEqual(
            [3, 1, 2],
            [item.gid for item in self.interface._filtered_items],
        )
        requests = []
        self.interface.playlistPlayRequested.connect(
            lambda playlist, items, position, resume: requests.append(
                (playlist, tuple(item.gid for item in items), position, resume)
            )
        )
        self.interface._requestPlaylistPlayback(False)
        self.repository.save_playlist_position(playlist_id, 1)
        self.interface._requestPlaylistPlayback(True)

        self.assertEqual(
            [
                (playlist_id, (3, 1, 2), 0, False),
                (playlist_id, (3, 1, 2), 1, True),
            ],
            requests,
        )

    def test_playlist_order_dialog_moves_items_and_returns_new_order(self):
        dialog = PlaylistOrderDialog("顺序测试", self.items, self.interface)
        dialog.listWidget.setCurrentRow(2)
        dialog.topButton.click()
        self.assertEqual((3, 1, 2), dialog.orderedGids())
        dialog.downButton.click()
        self.assertEqual((1, 3, 2), dialog.orderedGids())
        dialog.bottomButton.click()
        self.assertEqual((1, 2, 3), dialog.orderedGids())
        dialog.close()

    def test_playlist_order_action_saves_dialog_result(self):
        playlist_id = self.repository.create_playlist("保存顺序")
        self.repository.assign_label_to_mangas((1, 2, 3), playlist_id)
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_PLAYLIST)

        class AcceptedOrderDialog:
            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

            def orderedGids(self):
                return (3, 2, 1)

        with patch(
            "app.view.local_manga_interface.PlaylistOrderDialog",
            AcceptedOrderDialog,
        ):
            self.interface._editPlaylistOrder()
        QThreadPool.globalInstance().waitForDone(3000)
        QApplication.processEvents()

        self.assertEqual((3, 2, 1), self.repository.playlist_items(playlist_id))
        self.assertEqual((3, 2, 1), self.interface._playlist_order)

    def test_taxonomy_tree_filters_many_to_many_assignments(self):
        root_id = self.repository.create_taxonomy_label("全彩")
        child_id = self.repository.create_taxonomy_label("作者1", root_id)
        self.repository.assign_taxonomy_to_mangas((2, 3), child_id)
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_TAXONOMY)
        self.interface.taxonomyTree.setCurrentItem(
            self.interface._taxonomy_items[child_id]
        )

        self.assertEqual(
            {2, 3}, {item.gid for item in self.interface._filtered_items}
        )
        self.assertEqual("全彩/作者1", self.interface.titleLabel.text())
        self.assertIs(
            self.interface._taxonomy_items[child_id].parent(),
            self.interface._taxonomy_items[root_id],
        )
        self.interface.taxonomyTree.setCurrentItem(
            self.interface._taxonomy_items[root_id]
        )
        self.assertEqual(
            {2, 3}, {item.gid for item in self.interface._filtered_items}
        )
        self.assertEqual("全彩", self.interface.titleLabel.text())

    def test_local_title_tracks_category_playlist_and_show_all(self):
        self.assertEqual("未分类", self.interface.titleLabel.text())

        category_item = next(
            self.interface.primaryLabelTree.topLevelItem(index)
            for index in range(self.interface.primaryLabelTree.topLevelItemCount())
            if self.interface.primaryLabelTree.topLevelItem(index).data(
                0, Qt.UserRole
            ) == "分类 A"
        )
        self.interface.primaryLabelTree.setCurrentItem(category_item)
        self.assertEqual("分类 A", self.interface.titleLabel.text())

        playlist_id = self.repository.create_playlist("稍后阅读")
        self.interface._refreshTagData()
        self.interface._setTagMode(self.interface.TAG_PLAYLIST)
        self.interface.playlistTree.setCurrentItem(
            self.interface._playlist_items[playlist_id]
        )
        self.assertEqual("稍后阅读", self.interface.titleLabel.text())

        self.interface._showAllManga()
        self.assertEqual("本地资源", self.interface.titleLabel.text())

    def test_tag_sidebar_never_exceeds_thirty_percent(self):
        self.interface.resize(1000, 700)
        self.interface.show()
        QApplication.processEvents()
        self.interface.tagSplitter.setSizes([700, 300])
        self.interface.resizeEvent.__self__.resize(1001, 700)
        QApplication.processEvents()

        self.assertLessEqual(
            self.interface.tagSplitter.sizes()[0],
            int(self.interface.width() * 0.3),
        )


if __name__ == "__main__":
    unittest.main()
