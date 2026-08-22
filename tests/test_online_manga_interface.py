import base64
import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QDate, QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import Theme, qconfig, setTheme

from app.common.config import cfg
from app.domain.online_gallery import OnlineGallery, OnlineGalleryPage
from app.services.gallery_marker import gallery_matches_marker
from app.view.online_manga_interface import (
    OnlineGalleryCard,
    OnlineGalleryExtendedCard,
    OnlineGalleryListCard,
    OnlineMangaInterface,
)
from app.view.setting_interface import GalleryMarkerRulesDialog, SettingInterface


class _FakeOnlineProvider:
    instances = []
    queries = []
    display_modes = []

    def __init__(self, settings):
        self.settings = settings
        self.__class__.instances.append(self)

    def search(self, query):
        self.__class__.queries.append((self.settings.site, query))
        if query.cursor == "cursor-2":
            next_cursor = ""
            previous_cursor = "cursor-latest"
        elif query.cursor == "cursor-newer":
            next_cursor = "cursor-date"
            previous_cursor = ""
        elif getattr(query, "seek_date", "") and not query.cursor:
            next_cursor = "cursor-date"
            previous_cursor = "cursor-newer"
        else:
            next_cursor = "cursor-2"
            previous_cursor = ""
        return OnlineGalleryPage(
            (
                OnlineGallery(
                    123,
                    "abcdef0123",
                    "https://e-hentai.org/g/123/abcdef0123/",
                    f"result:{query.keyword}",
                    "Manga",
                    posted="2026-08-15 12:30",
                    page_count=12,
                    tags=("artist:tester", "language:chinese"),
                    uploader="tester",
                    rating=4.5,
                    source_mode="Compact",
                ),
            ),
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
        )

    def load_thumbnail(self, _url):
        return b""

    def set_display_mode(self, mode):
        self.__class__.display_modes.append((self.settings.site, mode))


class _CoverOnlineProvider(_FakeOnlineProvider):
    cover_calls = 0
    cover_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def search(self, query):
        self.__class__.queries.append((self.settings.site, query))
        return OnlineGalleryPage(
            (
                OnlineGallery(
                    123,
                    "token",
                    "https://e-hentai.org/g/123/token/",
                    "Cover memory",
                    "Manga",
                    "https://ehgt.org/cover-memory.png",
                    tags=("artist:tester",),
                    source_mode="Extended",
                ),
            )
        )

    def load_thumbnail(self, _url):
        self.__class__.cover_calls += 1
        return self.cover_data


class _MemoryThumbnailCache:
    def __init__(self):
        self.data = {}
        self.get_calls = 0

    def get(self, site, url, _max_age_hours):
        self.get_calls += 1
        return self.data.get((site, url))

    def put(self, site, url, data):
        self.data[(site, url)] = data
        return True

    def discard(self, site, url):
        self.data.pop((site, url), None)


class OnlineMangaInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.old_site = cfg.get(cfg.onlineEhSite)
        self.old_cookie = cfg.get(cfg.onlineEhCookie)
        self.old_nhc_cookie = cfg.get(cfg.onlineNhcCookie)
        self.old_nhn_cookie = cfg.get(cfg.onlineNhnCookie)
        self.old_proxy_mode = cfg.get(cfg.onlineEhProxyMode)
        self.old_manual_proxy = cfg.get(cfg.onlineEhManualProxy)
        self.old_timeout = cfg.get(cfg.onlineEhRequestTimeout)
        self.old_view_mode = cfg.get(cfg.onlineEhViewMode)
        self.old_cover_concurrency = cfg.get(cfg.onlineEhThumbnailConcurrency)
        self.old_download_label = cfg.get(cfg.onlineEhDownloadLabel)
        self.old_marker_title_rules = cfg.get(cfg.onlineEhMarkerTitleRules)
        self.old_marker_tag_rules = cfg.get(cfg.onlineEhMarkerTagRules)
        self.old_cache_hours = cfg.get(cfg.onlineEhThumbnailCacheHours)
        cfg.set(cfg.onlineEhSite, "ehentai")
        cfg.set(cfg.onlineEhCookie, "token")
        cfg.set(cfg.onlineNhcCookie, "nhc_session=one")
        cfg.set(cfg.onlineNhnCookie, "nhn_session=two")
        cfg.set(cfg.onlineEhProxyMode, "direct")
        cfg.set(cfg.onlineEhManualProxy, "")
        cfg.set(cfg.onlineEhRequestTimeout, 30)
        cfg.set(cfg.onlineEhViewMode, "card")
        cfg.set(cfg.onlineEhThumbnailConcurrency, 6)
        cfg.set(cfg.onlineEhDownloadLabel, "")
        cfg.set(cfg.onlineEhMarkerTitleRules, [])
        cfg.set(cfg.onlineEhMarkerTagRules, [])
        cfg.set(cfg.onlineEhThumbnailCacheHours, 168)
        _FakeOnlineProvider.instances.clear()
        _FakeOnlineProvider.queries.clear()
        _FakeOnlineProvider.display_modes.clear()

    def tearDown(self):
        cfg.set(cfg.onlineEhSite, self.old_site)
        cfg.set(cfg.onlineEhCookie, self.old_cookie)
        cfg.set(cfg.onlineNhcCookie, self.old_nhc_cookie)
        cfg.set(cfg.onlineNhnCookie, self.old_nhn_cookie)
        cfg.set(cfg.onlineEhProxyMode, self.old_proxy_mode)
        cfg.set(cfg.onlineEhManualProxy, self.old_manual_proxy)
        cfg.set(cfg.onlineEhRequestTimeout, self.old_timeout)
        cfg.set(cfg.onlineEhViewMode, self.old_view_mode)
        cfg.set(cfg.onlineEhThumbnailConcurrency, self.old_cover_concurrency)
        cfg.set(cfg.onlineEhDownloadLabel, self.old_download_label)
        cfg.set(cfg.onlineEhMarkerTitleRules, self.old_marker_title_rules)
        cfg.set(cfg.onlineEhMarkerTagRules, self.old_marker_tag_rules)
        cfg.set(cfg.onlineEhThumbnailCacheHours, self.old_cache_hours)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def _wait_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_search_uses_current_settings_and_builds_result_cards(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.setFilters({"language": "chinese"})
        interface.searchEdit.setText("artist:test")
        interface.search()

        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual(len(interface._cards), 1)
        self.assertIsInstance(interface._cards[0], OnlineGalleryCard)
        self.assertEqual(229, interface._cards[0].width())
        self.assertEqual(367, interface._cards[0].minimumHeight())
        self.assertEqual(241, interface._cards[0].coverLabel.height())
        self.assertEqual(interface._cards[0].item.title, "result:artist:test")
        self.assertEqual("★ 4.5", interface._cards[0].ratingLabel.text())
        self.assertNotIn("background-position", interface._cards[0].ratingLabel.text())
        self.assertEqual("ct3", interface._cards[0].categoryLabel.property("categoryStyle"))
        self.assertEqual("tester", interface._cards[0].uploaderLabel.text())
        self.assertNotIn("上传者", interface._cards[0].uploaderLabel.text())
        self.assertTrue(interface.nextButton.isEnabled())
        settings = _FakeOnlineProvider.instances[0].settings
        self.assertEqual(settings.site, "ehentai")
        self.assertEqual(settings.cookie, "igneous=token")
        self.assertEqual(settings.timeout_seconds, 30)
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.coverThreadPool.waitForDone(1000)
        interface.deleteLater()

    def test_category_badges_use_eh_category_palette(self):
        original_theme = qconfig.theme
        setTheme(Theme.LIGHT)
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.resize(900, 650)
        interface._setItems(
            (
                OnlineGallery(1, "a", "https://e-hentai.org/g/1/a/", "Misc", "Misc"),
                OnlineGallery(
                    2,
                    "b",
                    "https://e-hentai.org/g/2/b/",
                    "Doujinshi",
                    "Doujinshi",
                ),
            )
        )
        interface.show()
        for _ in range(5):
            self.app.processEvents()
        try:
            misc = interface._cards[0].categoryLabel
            doujinshi = interface._cards[1].categoryLabel
            misc_color = misc.grab().toImage().pixelColor(1, 1)
            doujinshi_color = doujinshi.grab().toImage().pixelColor(1, 1)

            self.assertEqual("ct1", misc.property("categoryStyle"))
            self.assertEqual("ct2", doujinshi.property("categoryStyle"))
            self.assertLess(abs(misc_color.red() - misc_color.green()), 25)
            self.assertGreater(doujinshi_color.red(), doujinshi_color.green() + 35)
        finally:
            setTheme(original_theme)
            interface.close()
            interface.deleteLater()

    def test_downloaded_badge_uses_local_gids_in_all_view_modes(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        items = (
            OnlineGallery(
                1,
                "one",
                "https://e-hentai.org/g/1/one/",
                "Downloaded",
            ),
            OnlineGallery(
                2,
                "two",
                "https://e-hentai.org/g/2/two/",
                "Online only",
            ),
        )
        interface.setDownloadedGids({1})
        interface._setItems(items)

        self.assertIsInstance(interface._cards[0], OnlineGalleryCard)
        self.assertFalse(interface._cards[0].downloadedBadge.isHidden())
        self.assertTrue(interface._cards[1].downloadedBadge.isHidden())
        self.assertEqual((16, 16), (
            interface._cards[0].downloadedBadge.x(),
            interface._cards[0].downloadedBadge.y(),
        ))
        icon = interface._cards[0].downloadedBadge.pixmap().toImage()
        self.assertTrue(
            any(
                icon.pixelColor(x, y).alpha()
                and icon.pixelColor(x, y).green()
                > icon.pixelColor(x, y).red() + 20
                for y in range(icon.height())
                for x in range(icon.width())
            )
        )

        cfg.set(cfg.onlineEhViewMode, "list")
        interface._setItems(items)
        self.assertIsInstance(interface._cards[0], OnlineGalleryListCard)
        self.assertFalse(interface._cards[0].downloadedBadge.isHidden())
        self.assertTrue(interface._cards[1].downloadedBadge.isHidden())

        cfg.set(cfg.onlineEhViewMode, "extended")
        interface._setItems(items)
        self.assertIsInstance(interface._cards[0], OnlineGalleryExtendedCard)
        self.assertFalse(interface._cards[0].downloadedBadge.isHidden())
        self.assertTrue(interface._cards[1].downloadedBadge.isHidden())

        cards = tuple(interface._cards)
        interface.setDownloadedGids({2})
        self.assertEqual(cards, tuple(interface._cards))
        self.assertTrue(interface._cards[0].downloadedBadge.isHidden())
        self.assertFalse(interface._cards[1].downloadedBadge.isHidden())
        interface.setGalleryDownloaded(1)
        self.assertFalse(interface._cards[0].downloadedBadge.isHidden())
        interface.setGalleryDownloaded(2, False)
        self.assertTrue(interface._cards[1].downloadedBadge.isHidden())
        interface.setGalleryStates({1: ("none", "partial")})
        self.assertTrue(interface._cards[0].downloadedBadge.isHidden())

        first, second = interface._cards
        first.setDownloaded = MagicMock()
        first.setGalleryStates = MagicMock()
        second.setDownloaded = MagicMock()
        second.setGalleryStates = MagicMock()
        interface.setGalleryState(1, "complete", "partial")
        first.setDownloaded.assert_called_once_with(True)
        first.setGalleryStates.assert_called_once_with("complete", "partial")
        second.setDownloaded.assert_not_called()
        second.setGalleryStates.assert_not_called()
        interface.deleteLater()

    def test_four_sources_are_single_select_and_nh_search_is_enabled(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        self.assertEqual(
            ["EH", "EXH", "NHC", "NHN"],
            [
                interface.siteSwitch.items[key].text()
                for key in ("ehentai", "exhentai", "nhc", "nhn")
            ],
        )

        interface.setSite("nhc")

        self.assertEqual("nhc", interface._current_site)
        self.assertEqual("nhc", interface.siteSwitch.currentRouteKey())
        self.assertTrue(interface.searchEdit.isEnabled())
        self.assertTrue(interface.searchButton.isEnabled())
        self.assertFalse(interface.timeSearchToggleButton.isEnabled())
        self.assertFalse(interface.galleryUrlToggleButton.isEnabled())
        self.assertTrue(interface.refreshButton.isEnabled())
        self.assertEqual("nhc_session=one", interface._makeProvider().settings.cookie)
        interface.setSite("nhn")
        self.assertEqual("nhn_session=two", interface._makeProvider().settings.cookie)
        interface.deleteLater()

    def test_nh_card_opens_metadata_detail_with_current_provider(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.setSite("nhc")
        gallery = OnlineGallery(
            10,
            "",
            "https://nhentai.com/en/comic/sample",
            "NHC sample",
            source_site="nhc",
            source_id="10",
        )
        activated = []
        interface.galleryActivated.connect(
            lambda item, provider, cover: activated.append((item, provider, cover))
        )

        interface._openGallery(gallery)

        self.assertEqual(1, len(activated))
        self.assertIs(gallery, activated[0][0])
        self.assertEqual("nhc", activated[0][1].settings.site)
        interface.deleteLater()

    def test_nh_card_download_uses_the_current_source_provider(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.setSite("nhn")
        gallery = OnlineGallery(
            20,
            "",
            "https://nhentai.net/g/20/",
            "NHN sample",
            source_site="nhn",
            source_id="20",
        )
        requested = []
        interface.galleryDownloadRequested.connect(
            lambda item, provider, cover: requested.append(
                (item, provider, cover)
            )
        )

        interface._downloadGallery(gallery)

        self.assertEqual(1, len(requested))
        self.assertIs(gallery, requested[0][0])
        self.assertEqual("nhn", requested[0][1].settings.site)
        interface.deleteLater()

    def test_online_cards_show_the_declared_source_badge(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface._setItems(
            (
                OnlineGallery(
                    10,
                    "",
                    "https://nhentai.com/en/comic/sample",
                    "NHC sample",
                    source_site="nhc",
                    source_id="10",
                ),
            )
        )

        self.assertEqual("nhc", interface._cards[0].sourceBadge.source)
        interface.deleteLater()

    def test_card_click_opens_internal_detail_with_current_provider(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        activated = []
        interface.galleryActivated.connect(
            lambda item, provider, data: activated.append((item, provider, data))
        )
        interface.search()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))

        interface._cards[0].clicked.emit()

        self.assertEqual(1, len(activated))
        item, provider, data = activated[0]
        self.assertEqual(123, item.gid)
        self.assertIs(provider, interface._site_providers["ehentai"])
        self.assertEqual(b"", data)
        self.assertIn("点击卡片查看详情", interface.resultLabel.text())
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.deleteLater()

    def test_site_switch_uses_independent_memory_state_and_refreshes_current_page(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.show()
        interface.search()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        interface.nextPage()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("cursor-2", interface.currentState.current_cursor)
        self.assertTrue(interface.previousButton.isEnabled())
        self.assertFalse(interface.nextButton.isEnabled())
        eh_state = interface.currentState

        interface.setSite("exhentai")
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("exhentai", interface._current_site)
        self.assertEqual("", interface.currentState.current_cursor)
        self.assertIsNot(eh_state, interface.currentState)
        request_count = len(_FakeOnlineProvider.queries)

        interface.setSite("ehentai")
        self.app.processEvents()
        self.assertIs(eh_state, interface.currentState)
        self.assertEqual("cursor-2", interface.currentState.current_cursor)
        self.assertFalse(hasattr(interface, "pageLabel"))
        self.assertEqual(request_count, len(_FakeOnlineProvider.queries))

        interface.refresh()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual(request_count + 1, len(_FakeOnlineProvider.queries))
        self.assertEqual("cursor-2", _FakeOnlineProvider.queries[-1][1].cursor)
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.coverThreadPool.waitForDone(1000)
        interface.close()
        interface.deleteLater()

    def test_first_show_requests_empty_site_homepage(self):
        interface = OnlineMangaInterface(provider_factory=_FakeOnlineProvider)
        interface.show()
        self.assertTrue(
            self._wait_until(
                lambda: bool(_FakeOnlineProvider.queries)
                and interface._search_worker is None
            )
        )
        site, query = _FakeOnlineProvider.queries[0]
        self.assertEqual("ehentai", site)
        self.assertEqual("", query.keyword)
        self.assertEqual("", query.cursor)
        self.assertEqual([("ehentai", "compact")], _FakeOnlineProvider.display_modes)
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.coverThreadPool.waitForDone(1000)
        interface.close()
        interface.deleteLater()

    def test_proxy_input_only_enables_for_manual_mode(self):
        settings = SettingInterface()
        self.assertFalse(settings.onlineManualProxyCard.isEnabled())
        cfg.set(cfg.onlineEhProxyMode, "manual")
        self.app.processEvents()
        self.assertTrue(settings.onlineManualProxyCard.isEnabled())
        self.assertIs(
            cfg.onlineEhViewMode,
            settings.onlineViewModeCard.configItem,
        )
        self.assertIs(
            cfg.onlineEhThumbnailConcurrency,
            settings.onlineThumbnailConcurrencyCard.configItem,
        )
        self.assertIs(
            cfg.onlineEhThumbnailCacheHours,
            settings.onlineThumbnailCacheHoursCard.configItem,
        )
        settings.setOnlineDownloadLabels(("稍后阅读", "自动下载"))
        self.assertEqual(0, settings.onlineDownloadLabelCard.comboBox.currentIndex())
        settings.onlineDownloadLabelCard.comboBox.setCurrentIndex(2)
        self.assertEqual("自动下载", cfg.get(cfg.onlineEhDownloadLabel))
        settings.setOnlineDownloadLabels(("稍后阅读",))
        self.assertEqual("", cfg.get(cfg.onlineEhDownloadLabel))
        settings.deleteLater()

    def test_online_card_context_menu_requests_download_without_opening_detail(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        provider = interface._makeProvider("ehentai")
        interface._site_providers["ehentai"] = provider
        interface._rendered_site = "ehentai"
        item = provider.search(
            type("Query", (), {"keyword": "", "cursor": ""})()
        ).items[0]
        interface._setItems((item,))
        requested = []
        interface.galleryDownloadRequested.connect(
            lambda gallery, current_provider, cover: requested.append(
                (gallery, current_provider, cover)
            )
        )
        opened = []
        interface.galleryActivated.connect(
            lambda gallery, _provider, _cover: opened.append(gallery)
        )
        interface.resize(1100, 760)
        interface.show()
        QApplication.processEvents()

        for view_mode in ("card", "list", "extended"):
            with self.subTest(view_mode=view_mode):
                cfg.set(cfg.onlineEhViewMode, view_mode)
                interface._setItems((item,))
                QApplication.processEvents()
                card = interface._cards[0]

                QTest.mouseClick(card, Qt.RightButton, pos=card.rect().center())
                self.assertEqual([], opened)

                event = QContextMenuEvent(
                    QContextMenuEvent.Mouse,
                    QPoint(10, 10),
                    card.mapToGlobal(QPoint(10, 10)),
                )
                with patch(
                    "app.view.online_manga_interface.RoundMenu.exec",
                    lambda menu, _position: menu.actions()[0].trigger(),
                ):
                    QApplication.sendEvent(card, event)

        self.assertEqual(
            [(item, provider, b""), (item, provider, b""), (item, provider, b"")],
            requested,
        )
        interface.deleteLater()

    def test_selected_title_search_runs_exact_query_and_shows_return_button(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        returned = []
        interface.detailReturnRequested.connect(lambda: returned.append(True))
        interface.setDetailReturnAvailable(True)

        self.assertTrue(interface.searchForText('"Selected title"'))
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual('"Selected title"', interface.searchEdit.text())
        self.assertEqual(
            '"Selected title"', _FakeOnlineProvider.queries[-1][1].keyword
        )
        self.assertFalse(interface.detailReturnButton.isHidden())
        interface.detailReturnButton.click()
        self.assertEqual([True], returned)

        interface.setDetailReturnAvailable(False)
        self.assertTrue(interface.detailReturnButton.isHidden())
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.coverThreadPool.waitForDone(1000)
        interface.deleteLater()

    def test_gallery_marker_matches_title_and_exact_full_or_bare_tags(self):
        gallery = OnlineGallery(
            1,
            "token",
            "https://e-hentai.org/g/1/token/",
            "[Circle] Warning chapter",
            tags=("artist:Tester", "female:glasses"),
        )

        self.assertTrue(gallery_matches_marker(gallery, ("WARNING",), ()))
        self.assertTrue(gallery_matches_marker(gallery, (), ("artist:tester",)))
        self.assertTrue(gallery_matches_marker(gallery, (), ("TESTER",)))
        self.assertFalse(gallery_matches_marker(gallery, (), ("test",)))
        self.assertFalse(gallery_matches_marker(gallery, (), ("male:tester",)))

    def test_gallery_marker_dialog_adds_deduplicates_and_removes_rules(self):
        parent = QWidget()
        dialog = GalleryMarkerRulesDialog(
            ("Warning", "warning", ""),
            ("artist:tester",),
            parent,
        )
        try:
            self.assertEqual(("Warning",), dialog.titleRules())
            self.assertEqual(("artist:tester",), dialog.tagRules())

            dialog.titleSection.inputEdit.setText("Another title")
            QTest.mouseClick(dialog.titleSection.addButton, Qt.LeftButton)
            self.assertEqual(("Warning", "Another title"), dialog.titleRules())

            first_item = dialog.titleSection.listWidget.item(0)
            first_row = dialog.titleSection.listWidget.itemWidget(first_item)
            QTest.mouseClick(first_row.removeButton, Qt.LeftButton)
            self.assertEqual(("Another title",), dialog.titleRules())

            dialog.tagSection.inputEdit.setText("language:chinese")
            dialog.tagSection.inputEdit.returnPressed.emit()
            self.assertEqual(
                ("artist:tester", "language:chinese"),
                dialog.tagRules(),
            )
        finally:
            dialog.close()
            dialog.deleteLater()
            parent.deleteLater()

    def test_gallery_marker_setting_card_saves_dialog_rules(self):
        class AcceptedMarkerDialog:
            def __init__(self, title_rules, tag_rules, parent):
                self.initial = (tuple(title_rules), tuple(tag_rules), parent)

            def exec(self):
                return True

            def titleRules(self):
                return ("warning",)

            def tagRules(self):
                return ("artist:tester", "chinese")

        settings = SettingInterface()
        try:
            with patch(
                "app.view.setting_interface.GalleryMarkerRulesDialog",
                AcceptedMarkerDialog,
            ):
                settings.onlineGalleryMarkerCard.configureButton.click()
            self.assertEqual(
                ["warning"], cfg.get(cfg.onlineEhMarkerTitleRules)
            )
            self.assertEqual(
                ["artist:tester", "chinese"],
                cfg.get(cfg.onlineEhMarkerTagRules),
            )
            self.assertEqual(
                "标题 1 项 · Tag 2 项",
                settings.onlineGalleryMarkerCard.contentLabel.text(),
            )
            self.assertEqual(70, settings.onlineGalleryMarkerCard.height())
        finally:
            settings.deleteLater()

    def test_gallery_markers_update_existing_cards_in_all_view_modes(self):
        cfg.set(cfg.onlineEhMarkerTitleRules, ["warning"])
        cfg.set(cfg.onlineEhMarkerTagRules, ["artist:tester"])
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        items = (
            OnlineGallery(
                1,
                "one",
                "https://e-hentai.org/g/1/one/",
                "Warning title",
            ),
            OnlineGallery(
                2,
                "two",
                "https://e-hentai.org/g/2/two/",
                "Tag match",
                tags=("artist:Tester",),
            ),
            OnlineGallery(
                3,
                "three",
                "https://e-hentai.org/g/3/three/",
                "Ordinary gallery",
                tags=("artist:someone",),
            ),
        )

        for view_mode, card_type in (
            ("card", OnlineGalleryCard),
            ("list", OnlineGalleryListCard),
            ("extended", OnlineGalleryExtendedCard),
        ):
            with self.subTest(view_mode=view_mode):
                cfg.set(cfg.onlineEhViewMode, view_mode)
                interface._setItems(items)
                self.assertTrue(all(isinstance(card, card_type) for card in interface._cards))
                self.assertEqual(
                    [True, True, False],
                    [bool(card.property("galleryMarked")) for card in interface._cards],
                )

        cards = tuple(interface._cards)
        cfg.set(cfg.onlineEhMarkerTitleRules, ["ordinary"])
        cfg.set(cfg.onlineEhMarkerTagRules, [])
        self.assertEqual(cards, tuple(interface._cards))
        self.assertEqual(
            [False, False, True],
            [bool(card.property("galleryMarked")) for card in interface._cards],
        )
        interface.deleteLater()

    def test_gallery_marker_border_is_drawn_deep_red_in_both_themes(self):
        original_theme = qconfig.theme
        cfg.set(cfg.onlineEhMarkerTitleRules, ["marked"])
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.resize(700, 600)
        interface._setItems(
            (
                OnlineGallery(
                    1,
                    "token",
                    "https://e-hentai.org/g/1/token/",
                    "Marked gallery",
                ),
            )
        )
        interface.show()
        try:
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.subTest(theme=theme):
                    setTheme(theme)
                    for _ in range(3):
                        self.app.processEvents()
                    card = interface._cards[0]
                    pixel = card.grab().toImage().pixelColor(card.width() // 2, 1)
                    self.assertGreater(pixel.red(), pixel.green() + 60)
                    self.assertGreater(pixel.red(), pixel.blue() + 60)
        finally:
            setTheme(original_theme)
            interface.close()
            interface.deleteLater()

    def test_gallery_url_panel_validates_and_opens_with_current_site(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        activated = []
        interface.galleryActivated.connect(
            lambda item, provider, data: activated.append((item, provider, data))
        )

        self.assertTrue(interface.galleryUrlPanel.isHidden())
        interface.galleryUrlToggleButton.click()
        self.assertFalse(interface.galleryUrlPanel.isHidden())

        interface.galleryUrlEdit.setText(
            "https://e-hentai.org/s/pagetoken/123-1"
        )
        with patch("app.view.online_manga_interface.InfoBar.error") as error_bar:
            interface.galleryUrlOpenButton.click()
        error_bar.assert_called_once()
        self.assertEqual([], activated)
        self.assertIn("地址无效", interface.resultLabel.text())

        interface.galleryUrlEdit.setText(
            "https://exhentai.org/g/987/deadbeef01/"
        )
        interface.galleryUrlOpenButton.click()

        self.assertEqual(1, len(activated))
        gallery, provider, cover_data = activated[0]
        self.assertEqual((987, "deadbeef01"), (gallery.gid, gallery.token))
        self.assertEqual(
            "https://e-hentai.org/g/987/deadbeef01/",
            gallery.url,
        )
        self.assertEqual("ehentai", provider.settings.site)
        self.assertEqual(b"", cover_data)
        interface.deleteLater()

    def test_downloaded_card_context_menu_opens_only_its_local_folder(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        provider = interface._makeProvider("ehentai")
        item = provider.search(
            type("Query", (), {"keyword": "", "cursor": ""})()
        ).items[0]
        opened = []
        interface.localFolderOpenRequested.connect(opened.append)
        interface.setDownloadedGids((item.gid,))
        interface._setItems((item,))
        card = interface._cards[0]
        event = QContextMenuEvent(
            QContextMenuEvent.Mouse,
            QPoint(10, 10),
            card.mapToGlobal(QPoint(10, 10)),
        )

        with patch(
            "app.view.online_manga_interface.RoundMenu.exec",
            lambda menu, _position: next(
                action
                for action in menu.actions()
                if action.text() == "在资源管理器中打开"
            ).trigger(),
        ):
            QApplication.sendEvent(card, event)

        self.assertEqual([item.gid], opened)
        interface.setDownloadedGids(())
        menu_actions = []
        with patch(
            "app.view.online_manga_interface.RoundMenu.exec",
            lambda menu, _position: menu_actions.extend(
                action.text() for action in menu.actions()
            ),
        ):
            QApplication.sendEvent(card, event)
        self.assertNotIn("在资源管理器中打开", menu_actions)
        interface.deleteLater()

    def test_cover_pool_concurrency_updates_immediately(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        self.assertEqual(6, interface.coverThreadPool.maxThreadCount())
        cfg.set(cfg.onlineEhThumbnailConcurrency, 2)
        self.app.processEvents()
        self.assertEqual(2, interface.coverThreadPool.maxThreadCount())
        interface.deleteLater()

    def test_view_button_reuses_page_and_persists_default_mode(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.search()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        request_count = len(_FakeOnlineProvider.queries)
        card_icon = interface.viewModeButton.icon().pixmap(16, 16).toImage()
        self.assertEqual(
            "list", interface.viewModeButton.property("targetViewMode")
        )

        interface.viewModeButton.click()
        self.app.processEvents()

        self.assertEqual("list", cfg.get(cfg.onlineEhViewMode))
        self.assertEqual("extended", interface.viewModeButton.property("targetViewMode"))
        self.assertIsInstance(interface._cards[0], OnlineGalleryListCard)
        self.assertEqual(116, interface._cards[0].height())
        self.assertEqual("tester", interface._cards[0].uploaderLabel.text())
        self.assertEqual(request_count, len(_FakeOnlineProvider.queries))
        self.assertEqual([], _FakeOnlineProvider.display_modes)

        interface.viewModeButton.click()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))

        self.assertEqual("extended", cfg.get(cfg.onlineEhViewMode))
        self.assertEqual("card", interface.viewModeButton.property("targetViewMode"))
        self.assertNotEqual(
            card_icon,
            interface.viewModeButton.icon().pixmap(16, 16).toImage(),
        )
        self.assertIsInstance(interface._cards[0], OnlineGalleryExtendedCard)
        self.assertEqual(2, len(interface._cards[0].tagLabels))
        self.assertEqual(request_count + 1, len(_FakeOnlineProvider.queries))
        self.assertEqual([("ehentai", "extended")], _FakeOnlineProvider.display_modes)

        cfg.set(cfg.onlineEhViewMode, "card")
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual(
            "list", interface.viewModeButton.property("targetViewMode")
        )
        self.assertEqual(
            card_icon,
            interface.viewModeButton.icon().pixmap(16, 16).toImage(),
        )
        self.assertIsInstance(interface._cards[0], OnlineGalleryCard)
        self.assertEqual(request_count + 2, len(_FakeOnlineProvider.queries))
        self.assertEqual(
            [("ehentai", "extended"), ("ehentai", "compact")],
            _FakeOnlineProvider.display_modes,
        )
        interface.cancelLoad()
        interface.coverThreadPool.waitForDone(1000)
        interface.deleteLater()

    def test_view_button_reuses_loaded_cover_without_another_cover_task(self):
        cache = _MemoryThumbnailCache()
        _CoverOnlineProvider.cover_calls = 0
        interface = OnlineMangaInterface(
            provider_factory=_CoverOnlineProvider,
            thumbnail_cache=cache,
            auto_load_on_show=False,
        )
        interface.resize(900, 700)
        interface.show()
        self.app.processEvents()
        interface.search()
        self.assertTrue(
            self._wait_until(
                lambda: interface._search_worker is None
                and not interface._cover_workers
                and interface._cards
                and not interface._cards[0].coverLabel.pixmap().isNull()
            )
        )
        self.assertEqual(1, cache.get_calls)
        self.assertEqual(1, _CoverOnlineProvider.cover_calls)
        card = interface._cards[0]
        self.assertEqual(
            min(card.coverLabel.width(), card.coverLabel.height()),
            card.coverLabel.pixmap().width(),
        )

        interface.viewModeButton.click()
        self.app.processEvents()

        self.assertIsInstance(interface._cards[0], OnlineGalleryListCard)
        self.assertFalse(hasattr(interface._cards[0], "coverLabel"))
        self.assertEqual(1, cache.get_calls)
        self.assertEqual(1, _CoverOnlineProvider.cover_calls)

        interface.viewModeButton.click()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))

        self.assertIsInstance(interface._cards[0], OnlineGalleryExtendedCard)
        self.assertFalse(interface._cards[0].coverLabel.pixmap().isNull())
        extended_card = interface._cards[0]
        self.assertEqual(
            min(
                extended_card.coverLabel.width(),
                extended_card.coverLabel.height(),
            ),
            extended_card.coverLabel.pixmap().width(),
        )
        self.assertEqual(1, cache.get_calls)
        self.assertEqual(1, _CoverOnlineProvider.cover_calls)
        self.assertFalse(interface._cover_workers)

        interface.viewModeButton.click()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertIsInstance(interface._cards[0], OnlineGalleryCard)
        card = interface._cards[0]
        self.assertEqual(
            min(card.coverLabel.width(), card.coverLabel.height()),
            card.coverLabel.pixmap().width(),
        )
        self.assertEqual(1, cache.get_calls)
        self.assertEqual(1, _CoverOnlineProvider.cover_calls)
        interface.cancelLoad()
        interface.searchThreadPool.waitForDone(1000)
        interface.close()
        interface.deleteLater()

    def test_previous_and_next_use_only_response_cursors(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.search()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertFalse(interface.previousButton.isEnabled())
        self.assertTrue(interface.nextButton.isEnabled())

        interface.nextPage()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("cursor-2", _FakeOnlineProvider.queries[-1][1].cursor)
        self.assertTrue(interface.previousButton.isEnabled())
        self.assertFalse(interface.nextButton.isEnabled())

        interface.previousPage()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("cursor-latest", _FakeOnlineProvider.queries[-1][1].cursor)
        self.assertFalse(interface.previousButton.isEnabled())
        self.assertTrue(interface.nextButton.isEnabled())
        interface.deleteLater()

    def test_date_search_preserves_keyword_and_passes_seek_date(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.searchEdit.setText("artist:tester")
        interface.timeSearchPicker.setDate(QDate(2026, 8, 1))

        interface.seekDate()

        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        _site, query = _FakeOnlineProvider.queries[-1]
        self.assertEqual("artist:tester", query.keyword)
        self.assertEqual("2026-08-01", query.seek_date)
        self.assertEqual("2026-08-01", interface.currentState.seek_date)
        self.assertIn("定位 2026-08-01", interface.resultLabel.text())
        self.assertTrue(interface.previousButton.isEnabled())
        self.assertTrue(interface.nextButton.isEnabled())
        interface.deleteLater()

    def test_extended_view_explains_missing_minimal_labels(self):
        cfg.set(cfg.onlineEhViewMode, "extended")
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface._setItems(
            (
                OnlineGallery(
                    1,
                    "token",
                    "https://e-hentai.org/g/1/token/",
                    "Minimal item",
                    source_mode="Minimal",
                ),
            )
        )
        card = interface._cards[0]
        self.assertIsInstance(card, OnlineGalleryExtendedCard)
        self.assertEqual("Minimal 源页面未提供标签", card.tagsPlaceholder.text())
        interface.deleteLater()

    def test_online_page_and_cards_follow_light_dark_theme(self):
        original_theme = qconfig.theme
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.resize(900, 700)
        interface.setDownloadedGids({1})
        interface._setItems(
            (
                OnlineGallery(
                    1,
                    "token",
                    "https://e-hentai.org/g/1/token/",
                    "Theme test",
                ),
            )
        )
        interface.show()
        try:
            setTheme(Theme.DARK)
            self.app.processEvents()
            dark_color = interface._cards[0].coverLabel.grab().toImage().pixelColor(5, 5)
            dark_badge = (
                interface._cards[0]
                .downloadedBadge.grab()
                .toImage()
                .pixelColor(2, 2)
            )

            setTheme(Theme.LIGHT)
            self.app.processEvents()
            light_color = interface._cards[0].coverLabel.grab().toImage().pixelColor(5, 5)
            light_badge = (
                interface._cards[0]
                .downloadedBadge.grab()
                .toImage()
                .pixelColor(2, 2)
            )

            self.assertEqual("onlineMangaScrollArea", interface.scrollArea.objectName())
            self.assertEqual("onlineMangaScrollWidget", interface.scrollWidget.objectName())
            self.assertLess(dark_color.lightness(), light_color.lightness())
            self.assertLess(dark_badge.lightness(), light_badge.lightness())
        finally:
            setTheme(original_theme)
            interface.close()
            interface.deleteLater()


if __name__ == "__main__":
    unittest.main()
