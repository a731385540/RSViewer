import json
import unittest

from app.domain.online_gallery import OnlineGallery, OnlineGalleryQuery
from app.sources.eh_online_source import EhOnlineError, EhOnlineSettings
from app.sources.nh_online_source import NhentaiOnlineProvider
from app.services.online_query_syntax import adapt_online_query, online_tag_query_token


class _Response:
    ok = True
    status_code = 200

    def __init__(self, content=b"", url="", json_data=None):
        self.content = content
        self.url = url
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("not json")
        return self._json_data


class _Session:
    def __init__(self, html, json_data=None):
        self.html = html.encode("utf-8")
        self.json_data = json_data
        self.headers = {}
        self.proxies = {}
        self.trust_env = True
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        json_data = (
            self.json_data(url, kwargs)
            if callable(self.json_data)
            else self.json_data
        )
        content = (
            json.dumps(json_data).encode("utf-8")
            if url.endswith("/images") and json_data is not None
            else self.html
        )
        return _Response(content, url, json_data)


NHC_HTML = """
<html><body>
  <div class="ssr-comic-grid">
    <a href="https://nhentai.com/en/comic/sample-title">
      <img alt="Fallback title" src="https://cdn.nhentai.com/nhentai/storage/comics/thumbs/675147.webp">
      <span>Sample NHC title</span>
    </a>
  </div>
  <a rel="next" href="/en/hentai-comics/2">Next</a>
</body></html>
"""

NHN_HTML = """
<html><body>
  <div class="gallery lang-gb">
    <a class="cover" href="/g/674728/">
      <img src="https://t2.nhentai.net/galleries/4130667/thumb.webp" alt="Fallback title">
      <div class="caption">Sample NHN title</div>
    </a>
  </div>
  <a class="previous" href="/?page=1">Previous</a>
  <a class="next" href="/?page=3">Next</a>
</body></html>
"""

NHC_SEARCH_DATA = {
    "current_page": 2,
    "last_page": 4,
    "data": [
        {
            "id": 675147,
            "title": "NHC search result",
            "slug": "nhc-search-result",
            "uploaded_at": "2026-08-22",
            "pages": 31,
            "thumb_url": "https://cdn.nhentai.com/nhentai/storage/comics/thumbs/675147.webp",
            "category": {"name": "Doujinshi"},
            "language": {"slug": "chinese"},
            "tags": [{"slug": "full-color"}],
        }
    ],
}

NHC_DETAIL_DATA = {
    **NHC_SEARCH_DATA["data"][0],
    "alternative_title": "NHC original title",
    "image_url": "https://cdn.nhentai.com/nhentai/storage/comics/675147.webp",
    "language": {"name": "Chinese", "slug": "chinese"},
    "parodies": [{"name": "Original", "slug": "original"}],
    "artists": [{"name": "Some Artist", "slug": "some-artist"}],
    "authors": [{"name": "Story Author", "slug": "story-author"}],
    "groups": [{"name": "Sample Group", "slug": "sample-group"}],
    "characters": [{"name": "Sample Character", "slug": "sample-character"}],
    "relationships": [{"name": "Sequel", "slug": "sequel"}],
}

NHN_DETAIL_HTML = """
<html><body>
  <div id="cover"><img src="https://t2.nhentai.net/galleries/4130667/cover.webp"></div>
  <div id="info">
    <h1>NHN translated title</h1><h2>NHN original title</h2>
    <h3 id="gallery_id">#674728</h3>
    <section id="tags">
      <div class="tag-container">Parodies: <a class="tagchip" href="/parody/original/"><span class="name">original</span></a></div>
      <div class="tag-container">Tags: <a class="tagchip" href="/tag/stockings/"><span class="name">stockings</span></a></div>
      <div class="tag-container">Artists: <a class="tagchip" href="/artist/sample-artist/"><span class="name">sample artist</span></a></div>
      <div class="tag-container">Groups: <a class="tagchip" href="/group/sample-group/"><span class="name">sample group</span></a></div>
      <div class="tag-container">Languages: <a class="tagchip" href="/language/chinese/"><span class="name">chinese</span></a></div>
      <div class="tag-container">Categories: <a class="tagchip" href="/category/doujinshi/"><span class="name">doujinshi</span></a></div>
      <div class="tag-container">Pages: 42</div>
      <div class="tag-container">Uploaded: <time datetime="2026-08-22T10:00:00Z">now</time></div>
    </section>
  </div>
</body></html>
"""

NHN_DETAIL_HTML = NHN_DETAIL_HTML.replace(
    "</body>",
    '<div id="thumbnail-container">'
    + "".join(
        '<div class="thumb-container"><a href="/g/674728/{0}/">'
        '<img src="https://t2.nhentai.net/galleries/4130667/{0}t.webp">'
        "</a></div>".format(page)
        for page in range(1, 43)
    )
    + "</div></body>",
)

NHC_IMAGES_DATA = {
    "comic": {"id": 675147},
    "images": [
        {
            "page": page,
            "source_url": (
                f"https://cdn.nhentai.com/nhentai/storage/images/675147/{page}.webp"
            ),
            "thumbnail_url": (
                f"https://cdn.nhentai.com/nhentai/storage/thumbnails/675147/{page}.webp"
            ),
        }
        for page in range(1, 32)
    ],
}


class NhentaiOnlineProviderTests(unittest.TestCase):
    def test_nhc_homepage_uses_ssr_cards_and_numeric_cursor(self):
        session = _Session(NHC_HTML)
        provider = NhentaiOnlineProvider(
            EhOnlineSettings.create(site="nhc", proxy_mode="direct"),
            session=session,
        )

        page = provider.search(OnlineGalleryQuery())

        self.assertEqual("https://nhentai.com/hentai-comics", session.calls[0][0])
        self.assertEqual("", page.previous_cursor)
        self.assertEqual("2", page.next_cursor)
        self.assertEqual(1, len(page.items))
        item = page.items[0]
        self.assertEqual(("nhc", "675147"), item.source_identity)
        self.assertEqual("Sample NHC title", item.title)
        self.assertEqual("https://nhentai.com/en/comic/sample-title", item.url)

    def test_nhn_page_uses_gallery_nodes_and_query_pagination(self):
        session = _Session(NHN_HTML)
        provider = NhentaiOnlineProvider(
            EhOnlineSettings.create(site="nhn", proxy_mode="direct"),
            session=session,
        )

        page = provider.search(OnlineGalleryQuery(cursor="2"))

        self.assertEqual("https://nhentai.net/", session.calls[0][0])
        self.assertEqual({"page": 2}, session.calls[0][1]["params"])
        self.assertEqual("1", page.previous_cursor)
        self.assertEqual("3", page.next_cursor)
        item = page.items[0]
        self.assertEqual(("nhn", "674728"), item.source_identity)
        self.assertEqual("Sample NHN title", item.title)
        self.assertEqual("https://nhentai.net/g/674728/", item.url)

    def test_nhn_search_uses_full_namespaces_and_preserves_numeric_page(self):
        session = _Session(
            NHN_HTML.replace('/?page=1', '/search/?q=x&page=1').replace(
                '/?page=3', '/search/?q=x&page=3'
            )
        )
        settings = EhOnlineSettings.create(
            site="nhn",
            cookie="Cookie: csrftoken=abc\nsessionid=secret",
            proxy_mode="direct",
        )
        provider = NhentaiOnlineProvider(settings, session=session)

        page = provider.search(
            OnlineGalleryQuery(
                keyword='a:"some artist$" l:"chinese$"', cursor="2"
            )
        )

        url, kwargs = session.calls[0]
        self.assertEqual("https://nhentai.net/search", url)
        self.assertEqual(
            {
                "q": 'artist:"some artist" language:"chinese"',
                "sort": "date",
                "page": 2,
            },
            kwargs["params"],
        )
        self.assertEqual("csrftoken=abc; sessionid=secret", session.headers["Cookie"])
        self.assertEqual(("1", "3"), (page.previous_cursor, page.next_cursor))

    def test_nhc_search_resolves_namespaced_filters_to_site_ids(self):
        def response_data(url, _kwargs):
            if url.endswith("/artists"):
                return {"data": [{"id": 17, "name": "Some Artist", "slug": "some-artist"}]}
            if url.endswith("/languages"):
                return {"data": [{"id": 1, "name": "Chinese", "slug": "chinese"}]}
            return NHC_SEARCH_DATA

        session = _Session(NHC_HTML, response_data)
        settings = EhOnlineSettings.create(
            site="nhc", cookie="session=secret", proxy_mode="direct"
        )
        provider = NhentaiOnlineProvider(settings, session=session)

        page = provider.search(
            OnlineGalleryQuery(
                keyword='artist:"some artist" language:chinese', cursor="2"
            )
        )

        url, kwargs = session.calls[-1]
        self.assertEqual("https://nhentai.com/api/comics", url)
        self.assertEqual(
            {"page": 2, "artists": [17], "languages": [1]},
            kwargs["params"],
        )
        self.assertEqual(
            ["https://nhentai.com/api/artists", "https://nhentai.com/api/languages"],
            [call[0] for call in session.calls[:-1]],
        )
        self.assertEqual("session=secret", session.headers["Cookie"])
        self.assertEqual(("1", "3"), (page.previous_cursor, page.next_cursor))
        item = page.items[0]
        self.assertEqual("NHC search result", item.title)
        self.assertEqual(31, item.page_count)
        self.assertEqual(
            ("category:Doujinshi", "language:chinese", "tag:full-color"),
            item.tags,
        )

    def test_shared_query_syntax_keeps_plain_text_and_translates_per_site(self):
        canonical = 'sample artist:"xxxx xxxx" language:chinese'
        self.assertEqual(
            'sample a:"xxxx xxxx" l:chinese',
            adapt_online_query(canonical, "ehentai"),
        )
        self.assertEqual(
            canonical,
            adapt_online_query('sample a:"xxxx xxxx" l:chinese', "nhn"),
        )
        self.assertEqual(
            'female:"full color"',
            adapt_online_query('f:"full color$"', "nhn"),
        )
        self.assertEqual(
            'tag:"full color"',
            adapt_online_query('f:"full color$"', "nhc"),
        )
        self.assertEqual(
            'female:"stockings"',
            online_tag_query_token("female", "stockings", "nhn"),
        )
        self.assertEqual(
            'tag:"stockings"',
            online_tag_query_token("female", "stockings", "nhc"),
        )

    def test_nhc_detail_maps_each_site_collection_to_namespaced_tags(self):
        session = _Session(
            NHC_HTML,
            lambda url, _kwargs: (
                NHC_IMAGES_DATA if url.endswith("/images") else NHC_DETAIL_DATA
            ),
        )
        provider = NhentaiOnlineProvider(
            EhOnlineSettings.create(site="nhc", proxy_mode="direct"),
            session=session,
        )
        gallery = OnlineGallery(
            675147,
            "",
            "https://nhentai.com/en/comic/nhc-search-result",
            "List title",
            source_site="nhc",
            source_id="675147",
        )

        detail = provider.load_gallery_detail(gallery)

        self.assertEqual("NHC original title", detail.secondary_title)
        self.assertEqual(31, detail.page_count)
        self.assertEqual(31, len(detail.previews))
        self.assertEqual(
            "https://cdn.nhentai.com/nhentai/storage/images/675147/1.webp",
            detail.previews[0].page_url,
        )
        self.assertEqual("Chinese", detail.language)
        self.assertEqual(
            {
                "category:Doujinshi",
                "language:chinese",
                "parody:Original",
                "artist:Some Artist",
                "author:Story Author",
                "group:Sample Group",
                "character:Sample Character",
                "relationship:Sequel",
                "tag:full-color",
            },
            set(detail.tags),
        )

    def test_nhn_detail_uses_tag_link_paths_as_namespaces(self):
        provider = NhentaiOnlineProvider(
            EhOnlineSettings.create(site="nhn", proxy_mode="direct"),
            session=_Session(NHN_DETAIL_HTML),
        )
        gallery = OnlineGallery(
            674728,
            "",
            "https://nhentai.net/g/674728/",
            "List title",
            source_site="nhn",
            source_id="674728",
        )

        detail = provider.load_gallery_detail(gallery)

        self.assertEqual("NHN translated title", detail.title)
        self.assertEqual("NHN original title", detail.secondary_title)
        self.assertEqual("doujinshi", detail.category)
        self.assertEqual("chinese", detail.language)
        self.assertEqual(42, detail.page_count)
        self.assertEqual(40, len(detail.previews))
        preview_page = provider.load_gallery_preview_page(detail.gallery, 2)
        self.assertEqual(2, len(preview_page.items))
        self.assertEqual(40, preview_page.items[0].page_index)
        self.assertEqual(
            {
                "parody:original",
                "tag:stockings",
                "artist:sample artist",
                "group:sample group",
                "language:chinese",
                "category:doujinshi",
            },
            set(detail.tags),
        )

    def test_rejects_invalid_cursors_and_cross_site_thumbnails(self):
        provider = NhentaiOnlineProvider(
            EhOnlineSettings.create(site="nhn", proxy_mode="direct"),
            session=_Session(NHN_HTML),
        )
        with self.assertRaises(EhOnlineError):
            provider.search(OnlineGalleryQuery(cursor="https://example.test/2"))
        with self.assertRaises(EhOnlineError):
            provider.load_thumbnail("https://example.test/thumb.webp")


if __name__ == "__main__":
    unittest.main()
