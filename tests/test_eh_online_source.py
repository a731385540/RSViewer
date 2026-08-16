import base64
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import perf_counter
from unittest.mock import patch

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
    def test_real_requests_socket_is_interrupted_without_waiting_for_server(self):
        response_started = Event()
        release_server = Event()

        class SlowHandler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(1024 * 1024))
                self.end_headers()
                self.wfile.write(b"x" * 1024)
                self.wfile.flush()
                response_started.set()
                release_server.wait(2)

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        errors = []
        worker = Thread(
            target=lambda: self._capture_error(
                errors,
                lambda: provider._request_bytes_cancellable(
                    f"http://127.0.0.1:{server.server_port}/slow",
                    lambda: False,
                ),
            ),
            daemon=True,
        )
        try:
            worker.start()
            self.assertTrue(response_started.wait(1))
            started_at = perf_counter()
            provider.cancel_pending_requests()
            worker.join(1)
            elapsed = perf_counter() - started_at

            self.assertFalse(worker.is_alive())
            self.assertLess(elapsed, 0.5)
            self.assertEqual(1, len(errors))
        finally:
            release_server.set()
            server.shutdown()
            server.server_close()

    def test_streaming_download_caps_no_data_timeout(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct", timeout_seconds=60)
        )

        class FakeResponse:
            ok = True
            status_code = 200

            def iter_content(self, chunk_size):
                self.chunk_size = chunk_size
                yield b"image-data"

            def close(self):
                pass

        class CapturingRequest:
            def __init__(self):
                self.kwargs = None

            def get(self, _url, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        request = CapturingRequest()
        provider._crawler = type("Crawler", (), {"req": request})()

        data, status = provider._request_bytes_cancellable(
            "https://exhentai.org/fullimg/1/1/key/page.jpg",
            lambda: False,
        )

        self.assertEqual(b"image-data", data)
        self.assertEqual(200, status)
        self.assertTrue(request.kwargs["stream"])
        self.assertEqual((15, 15), request.kwargs["timeout"])

    @staticmethod
    def _capture_error(errors, operation):
        try:
            operation()
        except Exception as error:
            errors.append(error)

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

    @patch("app.sources.eh_online_source.getproxies")
    def test_system_proxy_reuses_single_windows_http_endpoint_for_https(
        self, getproxies
    ):
        getproxies.return_value = {
            "http": "http://127.0.0.1:7890",
            "https": "https://127.0.0.1:7890",
            "no": "localhost,127.0.0.1",
        }
        settings = EhOnlineSettings.create(proxy_mode="system")

        self.assertEqual(
            settings.proxy_mapping(),
            {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
                "no": "localhost,127.0.0.1",
            },
        )
        provider = RefactoredEhOnlineProvider(settings)
        self.assertTrue(provider._crawler.req.proxy)
        self.assertTrue(provider._crawler.req.session.trust_env)
        self.assertEqual(
            "http://127.0.0.1:7890",
            provider._crawler.req.proxies["https"],
        )

    @patch("app.sources.eh_online_source.getproxies")
    def test_system_proxy_preserves_distinct_protocol_endpoints(self, getproxies):
        getproxies.return_value = {
            "http": "http://proxy-http.example:8080",
            "https": "https://proxy-tls.example:8443",
        }

        self.assertEqual(
            EhOnlineSettings.create(proxy_mode="system").proxy_mapping(),
            getproxies.return_value,
        )

    @patch("app.sources.eh_online_source.getproxies")
    def test_system_http_proxy_is_used_for_https_when_no_https_entry_exists(
        self, getproxies
    ):
        getproxies.return_value = {"http": "http://proxy.example:8080"}

        self.assertEqual(
            EhOnlineSettings.create(proxy_mode="system").proxy_mapping(),
            {
                "http": "http://proxy.example:8080",
                "https": "http://proxy.example:8080",
            },
        )

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

    def test_gallery_detail_is_parsed_from_direct_html_request(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        html = b"""
        <html><body>
          <h1 id="gn">English title</h1><h1 id="gj">Original title</h1>
          <div id="gdc">Manga</div><div id="gdn">uploader-name</div>
          <div id="gd1"><div style="background:transparent url(https://ehgt.org/cover.webp) 0 0 no-repeat"></div></div>
          <div id="gdd"><table>
            <tr><td>Posted:</td><td>2026-08-15 10:20</td></tr>
            <tr><td>Parent:</td><td>None</td></tr>
            <tr><td>Visible:</td><td>Yes</td></tr>
            <tr><td>Language:</td><td>Chinese</td></tr>
            <tr><td>File Size:</td><td>12.3 MiB</td></tr>
            <tr><td>Length:</td><td>42 pages</td></tr>
            <tr><td>Favorited:</td><td>7 times</td></tr>
          </table></div>
          <div id="rating_label">Average: 4.25</div><div id="rating_count">16</div>
          <div id="gnd">
            <a href="/g/124/def123/">Version 2</a>
            <a href="https://example.com/g/125/abc123/">Foreign</a>
          </div>
          <div id="taglist"><table>
            <tr><td class="tc">artist:</td><td><div><a id="ta_artist:someone">someone</a></div></td></tr>
          </table></div>
          <div id="gdt"><a href="https://e-hentai.org/s/pagetoken/123-1"><div
            title="Page 1: 001.jpg"
            style="width:200px;height:292px;background:transparent url(https://a.hath.network/thumb.webp) -200px -292px no-repeat"
          ></div></a></div>
          <div id="cdiv">
            <div class="c1"><div class="c2">
              <div class="c3">Posted on 15 August 2026, 10:30 by: <a>reader</a></div>
              <div class="c4 nosel">[ Vote+ ]</div><div class="c5 nosel">Score +3</div>
            </div><div class="c6" id="comment_123">first line<br/>second line</div></div>
            <div class="c1"><div class="c2">
              <div class="c3">Posted on 15 August 2026, 10:20 by: <a>uploader-name</a></div>
              <div class="c4 nosel">Uploader Comment</div>
            </div><div class="c6" id="comment_0">introduction</div></div>
          </div>
        </body></html>
        """

        class FakeResponse:
            ok = True
            status_code = 200
            content = html

        class FakeRequest:
            def __init__(self):
                self.url = None

            def get(self, url):
                self.url = url
                return FakeResponse()

        class FakeCrawler:
            def __init__(self):
                self.req = FakeRequest()

        provider._crawler = FakeCrawler()
        gallery = OnlineGallery(
            123, "abc", "https://e-hentai.org/g/123/abc/", "List title"
        )

        detail = provider.load_gallery_detail(gallery)

        self.assertEqual(gallery.url, provider._crawler.req.url)
        self.assertEqual("English title", detail.title)
        self.assertEqual("Original title", detail.secondary_title)
        self.assertEqual(42, detail.page_count)
        self.assertEqual(42, detail.gallery.page_count)
        self.assertEqual(4.25, detail.rating)
        self.assertEqual(16, detail.rating_count)
        self.assertEqual(("artist:someone",), detail.tags)
        self.assertEqual(2, len(detail.comments))
        self.assertEqual(("reader", 3, "first line\nsecond line"), (
            detail.comments[0].author,
            detail.comments[0].score,
            detail.comments[0].text,
        ))
        self.assertTrue(detail.comments[1].is_uploader)
        self.assertEqual("https://ehgt.org/cover.webp", detail.cover_url)
        self.assertEqual(1, len(detail.previews))
        self.assertEqual(0, detail.previews[0].page_index)
        self.assertEqual("pagetoken", detail.previews[0].page_token)
        self.assertEqual("https://a.hath.network/thumb.webp", detail.previews[0].thumbnail_url)
        self.assertEqual((200, 292), (
            detail.previews[0].thumbnail_width,
            detail.previews[0].thumbnail_height,
        ))
        self.assertEqual((200, 292), (
            detail.previews[0].thumbnail_x,
            detail.previews[0].thumbnail_y,
        ))
        self.assertEqual(
            ("https://e-hentai.org/g/124/def123/",),
            detail.newer_gallery_urls,
        )

    def test_gallery_detail_rejects_foreign_or_mismatched_url(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        for gallery in (
            OnlineGallery(123, "abc", "https://example.com/g/123/abc/", "foreign"),
            OnlineGallery(123, "abc", "https://e-hentai.org/g/456/abc/", "gid"),
            OnlineGallery(123, "abc", "https://e-hentai.org/g/123/wrong/", "token"),
        ):
            with self.subTest(url=gallery.url), self.assertRaises(EhOnlineError):
                provider.load_gallery_detail(gallery)

    def test_preview_page_and_reader_image_use_direct_html_pages(self):
        provider = RefactoredEhOnlineProvider(
            EhOnlineSettings.create(proxy_mode="direct")
        )
        gallery = OnlineGallery(
            123,
            "abc",
            "https://e-hentai.org/g/123/abc/",
            "Gallery",
            page_count=21,
        )
        preview_html = b"""
        <div id="gdt"><a href="https://e-hentai.org/s/pagetoken/123-21"><div
          title="Page 21: 021.jpg"
          style="width:200px;height:292px;background:transparent url(https://a.hath.network/thumb.webp) -400px 0 no-repeat"
        ></div></a></div>
        """
        image_page_html = b"""
        <div id="i3"><img id="img" src="https://b.hath.network/page.png"/></div>
        <a href="https://e-hentai.org/fullimg/123/21/originalkey/021.jpg">
          Download original
        </a>
        """
        image_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

        class FakeResponse:
            def __init__(self, content):
                self.ok = True
                self.status_code = 200
                self.content = content

        class FakeRequest:
            def __init__(self):
                self.urls = []

            def get(self, url):
                self.urls.append(url)
                if url.endswith("?p=1"):
                    return FakeResponse(preview_html)
                if "/s/" in url:
                    return FakeResponse(image_page_html)
                if (
                    url.endswith("page.png")
                    or url.endswith("thumb.webp")
                    or "/fullimg/" in url
                ):
                    return FakeResponse(image_data)
                raise AssertionError(url)

        class FakeCrawler:
            def __init__(self):
                self.req = FakeRequest()

        provider._crawler = FakeCrawler()
        page = provider.load_gallery_preview_page(gallery, 2)
        thumb_data = provider.load_preview_thumbnail(page.items[0])
        page_data = provider.load_gallery_page_image(gallery, page.items[0])
        original_data = provider.load_gallery_page_original(gallery, page.items[0])

        self.assertEqual(2, page.page_number)
        self.assertEqual(2, page.page_count)
        self.assertEqual(20, page.items[0].page_index)
        self.assertEqual("pagetoken", page.items[0].page_token)
        self.assertEqual((200, 292, 400, 0), (
            page.items[0].thumbnail_width,
            page.items[0].thumbnail_height,
            page.items[0].thumbnail_x,
            page.items[0].thumbnail_y,
        ))
        self.assertEqual(image_data, thumb_data)
        self.assertEqual(image_data, page_data)
        self.assertEqual(image_data, original_data)
        self.assertEqual(
            [
                "https://e-hentai.org/g/123/abc/?p=1",
                "https://a.hath.network/thumb.webp",
                "https://e-hentai.org/s/pagetoken/123-21",
                "https://b.hath.network/page.png",
                "https://e-hentai.org/s/pagetoken/123-21",
                "https://e-hentai.org/fullimg/123/21/originalkey/021.jpg",
            ],
            provider._crawler.req.urls,
        )

        foreign_preview = replace(
            page.items[0], page_url="https://example.com/s/token/123-21"
        )
        with self.assertRaises(EhOnlineError):
            provider.load_gallery_page_image(gallery, foreign_preview)

        provider._crawler.req.get = lambda _url: FakeResponse(
            b'<a href="https://e-hentai.org/fullimg/999/21/key/021.jpg">original</a>'
        )
        with self.assertRaises(EhOnlineError):
            provider.load_gallery_page_original(gallery, page.items[0])


if __name__ == "__main__":
    unittest.main()
