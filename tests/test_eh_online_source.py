import unittest

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryPage,
    OnlineGalleryQuery,
)
from app.sources.eh_online_source import (
    EhOnlineError,
    EhOnlineProvider,
    EhOnlineSettings,
    RefactoredEhOnlineProvider,
    create_eh_online_provider,
)


class _FilteringProvider(EhOnlineProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.last_query = None

    def fetch_page(self, query):
        self.last_query = query
        return OnlineGalleryPage(
            (
                OnlineGallery(1, "one", "https://example.test/1", "keep"),
                OnlineGallery(2, "two", "https://example.test/2", "drop"),
            ),
            next_cursor="cursor-2",
        )

    def filter_items(self, items, query):
        blocked = set(query.filters.get("blocked_titles", ()))
        return (item for item in items if item.title not in blocked)


class EhOnlineProviderContractTests(unittest.TestCase):
    def test_settings_normalize_credentials_and_manual_proxy(self):
        settings = EhOnlineSettings.create(
            site="exhentai",
            cookie="Cookie: ipb_member_id=1\nipb_pass_hash=two",
            proxy_mode="manual",
            manual_proxy="127.0.0.1:7890",
            timeout_seconds=30,
        )

        self.assertEqual(settings.base_url, "https://exhentai.org/")
        self.assertEqual(settings.cookie, "ipb_member_id=1; ipb_pass_hash=two")
        self.assertEqual(settings.manual_proxy, "http://127.0.0.1:7890")
        self.assertEqual(
            settings.proxy_mapping(),
            {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            },
        )
        self.assertEqual(settings.timeout_seconds, 30)

    def test_bare_token_is_compatible_and_hidden_from_repr(self):
        settings = EhOnlineSettings.create(cookie="secret-token", proxy_mode="direct")
        self.assertEqual(settings.cookie, "igneous=secret-token")
        self.assertNotIn("secret-token", repr(settings))
        self.assertEqual(settings.proxy_mapping(), {})

    def test_invalid_manual_proxy_is_rejected_before_worker_start(self):
        with self.assertRaises(EhOnlineError):
            EhOnlineSettings.create(
                proxy_mode="manual", manual_proxy="socks5://127.0.0.1:1080"
            )

    def test_provider_fetch_and_filter_hooks_are_composed(self):
        provider = _FilteringProvider(EhOnlineSettings.create(proxy_mode="direct"))
        query = OnlineGalleryQuery(
            keyword="test",
            cursor="cursor-1",
            page_number=2,
            filters={"blocked_titles": ("drop",)},
        )

        page = provider.search(query)

        self.assertIs(provider.last_query, query)
        self.assertEqual([item.title for item in page.items], ["keep"])
        self.assertEqual(page.next_cursor, "cursor-2")

    def test_stock_provider_composes_user_refactored_crawler_without_request(self):
        provider = create_eh_online_provider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        self.assertIsInstance(provider, RefactoredEhOnlineProvider)
        self.assertFalse(provider._crawler.req.proxy)
        self.assertFalse(provider._crawler.req.session.trust_env)

    def test_refactored_provider_maps_crawler_result_and_pagination(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )

        class FakeCrawler:
            def __init__(self):
                self.search = None
                self.url = None
                self.mode = None

            def getMain(self, search=None):
                self.search = search
                return {
                    "data": [
                        {
                            "gid": 123,
                            "token": "abc",
                            "gallery_url": "https://e-hentai.org/g/123/abc/",
                            "title": "Example",
                            "type": "Manga",
                            "thumb_url": "https://ehgt.org/example.jpg",
                            "upload": "2026-08-14 12:00",
                            "page_num": 42,
                            "uploader": "tester",
                            "score": 4.5,
                            "page_mode": "Extended",
                            "label": {"artist": ["someone"]},
                        }
                    ],
                    "next_url": "https://e-hentai.org/?next=100",
                    "prev_url": "https://e-hentai.org/?prev=200",
                }

            def getUrl(self, url):
                self.url = url
                return self.getMain()

            def setDisplayMode(self, mode):
                self.mode = mode
                return {"mode": mode}

        crawler = FakeCrawler()
        provider._crawler = crawler
        page = provider.search(OnlineGalleryQuery(keyword="artist:someone"))

        self.assertEqual("artist:someone", crawler.search)
        self.assertEqual(1, len(page.items))
        self.assertEqual("Example", page.items[0].title)
        self.assertEqual(("artist:someone",), page.items[0].tags)
        self.assertEqual("tester", page.items[0].uploader)
        self.assertEqual(4.5, page.items[0].rating)
        self.assertEqual("Extended", page.items[0].source_mode)
        self.assertEqual("https://e-hentai.org/?next=100", page.next_cursor)

        provider.search(
            OnlineGalleryQuery(cursor="https://e-hentai.org/?next=100")
        )
        self.assertEqual("https://e-hentai.org/?next=100", crawler.url)
        provider.set_display_mode("extended")
        self.assertEqual("extended", crawler.mode)

    def test_refactored_provider_rejects_foreign_cursor(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        with self.assertRaises(EhOnlineError):
            provider.search(
                OnlineGalleryQuery(cursor="https://example.com/?next=1")
            )


if __name__ == "__main__":
    unittest.main()
