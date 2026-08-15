import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, qconfig, setTheme

from app.common.config import cfg
from app.domain.online_gallery import OnlineGallery, OnlineGalleryPage
from app.view.online_manga_interface import (
    OnlineGalleryCard,
    OnlineGalleryExtendedCard,
    OnlineMangaInterface,
)
from app.view.setting_interface import SettingInterface


class _FakeOnlineProvider:
    instances = []
    queries = []
    display_modes = []

    def __init__(self, settings):
        self.settings = settings
        self.__class__.instances.append(self)

    def search(self, query):
        self.__class__.queries.append((self.settings.site, query))
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
            next_cursor="cursor-2" if not query.cursor else "",
        )

    def load_thumbnail(self, _url):
        return b""

    def set_display_mode(self, mode):
        self.__class__.display_modes.append((self.settings.site, mode))


class OnlineMangaInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.old_site = cfg.get(cfg.onlineEhSite)
        self.old_cookie = cfg.get(cfg.onlineEhCookie)
        self.old_proxy_mode = cfg.get(cfg.onlineEhProxyMode)
        self.old_manual_proxy = cfg.get(cfg.onlineEhManualProxy)
        self.old_timeout = cfg.get(cfg.onlineEhRequestTimeout)
        self.old_view_mode = cfg.get(cfg.onlineEhViewMode)
        self.old_cover_concurrency = cfg.get(cfg.onlineEhThumbnailConcurrency)
        self.old_cache_hours = cfg.get(cfg.onlineEhThumbnailCacheHours)
        cfg.set(cfg.onlineEhSite, "ehentai")
        cfg.set(cfg.onlineEhCookie, "token")
        cfg.set(cfg.onlineEhProxyMode, "direct")
        cfg.set(cfg.onlineEhManualProxy, "")
        cfg.set(cfg.onlineEhRequestTimeout, 30)
        cfg.set(cfg.onlineEhViewMode, "card")
        cfg.set(cfg.onlineEhThumbnailConcurrency, 6)
        cfg.set(cfg.onlineEhThumbnailCacheHours, 168)
        _FakeOnlineProvider.instances.clear()
        _FakeOnlineProvider.queries.clear()
        _FakeOnlineProvider.display_modes.clear()

    def tearDown(self):
        cfg.set(cfg.onlineEhSite, self.old_site)
        cfg.set(cfg.onlineEhCookie, self.old_cookie)
        cfg.set(cfg.onlineEhProxyMode, self.old_proxy_mode)
        cfg.set(cfg.onlineEhManualProxy, self.old_manual_proxy)
        cfg.set(cfg.onlineEhRequestTimeout, self.old_timeout)
        cfg.set(cfg.onlineEhViewMode, self.old_view_mode)
        cfg.set(cfg.onlineEhThumbnailConcurrency, self.old_cover_concurrency)
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
        self.assertEqual(2, interface.currentState.page_number)
        eh_state = interface.currentState

        interface.setSite("exhentai")
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("exhentai", interface._current_site)
        self.assertEqual(1, interface.currentState.page_number)
        self.assertIsNot(eh_state, interface.currentState)
        request_count = len(_FakeOnlineProvider.queries)

        interface.setSite("ehentai")
        self.app.processEvents()
        self.assertIs(eh_state, interface.currentState)
        self.assertEqual(2, interface.currentState.page_number)
        self.assertEqual("第 2 页", interface.pageLabel.text())
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
        settings.deleteLater()

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

    def test_view_switch_reuses_page_and_persists_default_mode(self):
        interface = OnlineMangaInterface(
            provider_factory=_FakeOnlineProvider,
            auto_load_on_show=False,
        )
        interface.search()
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        request_count = len(_FakeOnlineProvider.queries)

        interface.setViewMode("extended")
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))

        self.assertEqual("extended", cfg.get(cfg.onlineEhViewMode))
        self.assertEqual("extended", interface.viewSwitch.currentRouteKey())
        self.assertIsInstance(interface._cards[0], OnlineGalleryExtendedCard)
        self.assertEqual(2, len(interface._cards[0].tagLabels))
        self.assertEqual(request_count + 1, len(_FakeOnlineProvider.queries))
        self.assertEqual([("ehentai", "extended")], _FakeOnlineProvider.display_modes)

        cfg.set(cfg.onlineEhViewMode, "card")
        self.assertTrue(self._wait_until(lambda: interface._search_worker is None))
        self.assertEqual("card", interface.viewSwitch.currentRouteKey())
        self.assertIsInstance(interface._cards[0], OnlineGalleryCard)
        self.assertEqual(request_count + 2, len(_FakeOnlineProvider.queries))
        self.assertEqual(
            [("ehentai", "extended"), ("ehentai", "compact")],
            _FakeOnlineProvider.display_modes,
        )
        interface.cancelLoad()
        interface.coverThreadPool.waitForDone(1000)
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

            setTheme(Theme.LIGHT)
            self.app.processEvents()
            light_color = interface._cards[0].coverLabel.grab().toImage().pixelColor(5, 5)

            self.assertEqual("onlineMangaScrollArea", interface.scrollArea.objectName())
            self.assertEqual("onlineMangaScrollWidget", interface.scrollWidget.objectName())
            self.assertLess(dark_color.lightness(), light_color.lightness())
        finally:
            setTheme(original_theme)
            interface.close()
            interface.deleteLater()


if __name__ == "__main__":
    unittest.main()
