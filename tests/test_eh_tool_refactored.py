import unittest

from eh_tool_refactored import EhBase, EhData, ehCookies, proxies


LIST_HTML = """
<html><body>
<select><option selected="selected">Compact</option></select>
<a id="unext" href="/?next=100">next</a>
<a id="uprev" href="/?prev=200">prev</a>
<table><tr>
  <td class="glcat">Manga</td>
  <td>
    <a href="/g/123/AbCd/"><div class="glink">Example Gallery</div>
      <img data-src="https://ehgt.org/example.jpg" alt="Example" />
    </a>
    <div class="ir" title="Average: 4.50"></div>
    <a href="/?f_uploader=tester">tester</a>
    <div class="gt" title="artist:someone"></div>
    <div class="gtl" title="language:english"></div>
    2026-08-14 12:34 42 pages
  </td>
</tr></table>
</body></html>
"""


class FakeResponse:
    def __init__(self, url, html=LIST_HTML):
        self.url = url
        self.text = html
        self.ok = True
        self.content = html.encode("utf-8")


class FakeRequest:
    def __init__(self, html=LIST_HTML):
        self.urls = []
        self.html = html

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return FakeResponse(url, self.html)


class RefactoredCrawlerTests(unittest.TestCase):
    def test_source_defaults_do_not_embed_credentials_or_proxy(self):
        self.assertEqual("", ehCookies)
        self.assertEqual({}, proxies)

    def test_list_search_uses_original_html_page_parser(self):
        crawler = EhData("", source="e-hentai")
        request = FakeRequest()
        crawler.req = request

        result = crawler.getMain(search="artist:someone")

        self.assertIn("f_search=artist%3Asomeone", request.urls[0])
        self.assertEqual(1, len(result["data"]))
        item = result["data"][0]
        self.assertEqual(123, item["gid"])
        self.assertEqual("AbCd", item["token"])
        self.assertEqual("Example Gallery", item["title"])
        self.assertEqual("Manga", item["type"])
        self.assertEqual(42, item["page_num"])
        self.assertEqual("tester", item["uploader"])
        self.assertEqual(4.5, item["score"])
        self.assertEqual(["someone"], item["label"]["artist"])
        self.assertEqual(["english"], item["label"]["language"])
        self.assertEqual("https://e-hentai.org/?next=100", result["next_url"])

    def test_display_mode_uses_the_original_inline_page_setting(self):
        crawler = EhData("", source="e-hentai")
        request = FakeRequest()
        crawler.req = request

        result = crawler.setDisplayMode("extended")

        self.assertEqual("e", result["mode"])
        self.assertEqual(
            "https://e-hentai.org/?inline_set=dm_e",
            request.urls[-1],
        )
        self.assertIn("error", crawler.setDisplayMode("unknown"))

    def test_rating_sprite_style_is_converted_to_numeric_score(self):
        self.assertEqual(
            2.0,
            EhBase._extract_score("", "background-position:-48px -1px;opacity:0.6"),
        )
        self.assertEqual(
            2.5,
            EhBase._extract_score(
                "",
                "background-position:-32px -21px;opacity:0.86666666666667",
            ),
        )
        self.assertEqual(
            5.0,
            EhBase._extract_score("", "background-position:0px -1px;opacity:1"),
        )
        self.assertIsNone(EhBase._extract_score("", "opacity:0.6"))

    def test_minimal_mode_without_label_nodes_returns_empty_labels(self):
        minimal_html = LIST_HTML.replace(
            '<option selected="selected">Compact</option>',
            '<option selected="selected">Minimal</option>',
        ).replace('<div class="gt" title="artist:someone"></div>', "").replace(
            '<div class="gtl" title="language:english"></div>', ""
        )
        crawler = EhData("", source="e-hentai")
        request = FakeRequest(minimal_html)
        crawler.req = request

        result = crawler.getMain()

        self.assertEqual("Minimal", result["data"][0]["page_mode"])
        self.assertEqual({}, result["data"][0]["label"])


if __name__ == "__main__":
    unittest.main()
