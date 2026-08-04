import re
import socket
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from app.domain.online_gallery import OnlineGallery, OnlineGalleryPage


SITE_BASE_URLS = {
    "ehentai": "https://e-hentai.org/",
    "exhentai": "https://exhentai.org/",
}
_GALLERY_URL_RE = re.compile(
    r"https://(?:e-hentai|exhentai)\.org/g/(\d+)/([0-9a-f]+)/?",
    re.IGNORECASE,
)
_PAGE_COUNT_RE = re.compile(r"(\d[\d,]*)\s+pages?", re.IGNORECASE)
_ALLOWED_PAGE_HOSTS = {"e-hentai.org", "exhentai.org"}
_ALLOWED_IMAGE_HOSTS = {
    "e-hentai.org",
    "exhentai.org",
    "ehgt.org",
    "ul.ehgt.org",
}


class EhOnlineError(RuntimeError):
    pass


class _GalleryListParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: List[OnlineGallery] = []
        self.next_url = ""
        self.previous_url = ""
        self._row: Optional[Dict] = None
        self._capture = ""
        self._capture_depth = 0

    @staticmethod
    def _attrs(attrs):
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag, attrs):
        attributes = self._attrs(attrs)
        if tag == "a":
            if attributes.get("id") == "unext":
                self.next_url = attributes.get("href", "")
            elif attributes.get("id") == "uprev":
                self.previous_url = attributes.get("href", "")

        if tag == "tr":
            self._row = {
                "gid": 0,
                "token": "",
                "url": "",
                "title": "",
                "category": "",
                "thumbnail_url": "",
                "posted": "",
                "page_count": 0,
                "tags": [],
            }
            return
        if self._row is None:
            return

        if tag == "a":
            href = attributes.get("href", "")
            match = _GALLERY_URL_RE.match(href)
            if match:
                self._row["gid"] = int(match.group(1))
                self._row["token"] = match.group(2)
                self._row["url"] = href
        elif tag == "img":
            deferred_source = attributes.get("data-src", "")
            source = deferred_source or attributes.get("src", "")
            if source and not source.startswith("data:") and (
                deferred_source or not self._row["thumbnail_url"]
            ):
                self._row["thumbnail_url"] = source
            if not self._row["title"]:
                self._row["title"] = attributes.get("alt", "")

        classes = set(attributes.get("class", "").split())
        if "glink" in classes:
            self._begin_capture("title")
        elif "cn" in classes and not self._row["category"]:
            self._begin_capture("category")
        elif "gt" in classes:
            tag_name = attributes.get("title", "")
            if tag_name and tag_name not in self._row["tags"]:
                self._row["tags"].append(tag_name)
        elif attributes.get("id", "").startswith("posted_"):
            self._begin_capture("posted")

        if self._capture:
            self._capture_depth += 1

    def _begin_capture(self, field):
        self._capture = field
        self._capture_depth = 0
        self._row[field] = ""

    def handle_data(self, data):
        if self._row is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._capture:
            current = self._row[self._capture]
            self._row[self._capture] = f"{current} {text}".strip()
        if not self._row["page_count"]:
            match = _PAGE_COUNT_RE.search(text)
            if match:
                self._row["page_count"] = int(match.group(1).replace(",", ""))

    def handle_endtag(self, tag):
        if self._row is None:
            return
        if self._capture:
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                self._capture = ""
        if tag != "tr":
            return
        row = self._row
        self._row = None
        self._capture = ""
        self._capture_depth = 0
        if row["gid"] and row["url"]:
            self.items.append(
                OnlineGallery(
                    gid=row["gid"],
                    token=row["token"],
                    url=row["url"],
                    title=row["title"] or f"Gallery {row['gid']}",
                    category=row["category"],
                    thumbnail_url=row["thumbnail_url"],
                    posted=row["posted"],
                    page_count=row["page_count"],
                    tags=tuple(row["tags"]),
                )
            )


def parse_gallery_list(html: str) -> OnlineGalleryPage:
    parser = _GalleryListParser()
    parser.feed(html)
    parser.close()
    return OnlineGalleryPage(
        tuple(parser.items),
        next_url=parser.next_url,
        previous_url=parser.previous_url,
    )


class EhOnlineSource:
    """Small, synchronous EH/EX client intended to run only in workers."""

    USER_AGENT = "RSViewer/0.1 (+personal desktop gallery viewer)"

    def __init__(
        self,
        site="ehentai",
        cookie="",
        proxy_mode="system",
        manual_proxy="",
        timeout=20,
    ):
        if site not in SITE_BASE_URLS:
            raise ValueError(f"Unsupported site: {site}")
        self.site = site
        self.base_url = SITE_BASE_URLS[site]
        self.cookie = self._normalize_cookie(cookie)
        self.proxy_mode = proxy_mode
        self.manual_proxy = manual_proxy.strip()
        self.timeout = max(3, int(timeout))
        self._opener = self._build_opener()

    @staticmethod
    def _normalize_cookie(value: str) -> str:
        value = (value or "").strip()
        if value.lower().startswith("cookie:"):
            value = value[7:].strip()
        value = "; ".join(
            part.strip()
            for part in value.replace("\r", "\n").split("\n")
            if part.strip()
        )
        if value and "=" not in value:
            value = f"igneous={value}"
        return value

    def _build_opener(self):
        if self.proxy_mode == "direct":
            handler = ProxyHandler({})
        elif self.proxy_mode == "manual":
            proxy = self.manual_proxy
            if not proxy:
                raise EhOnlineError("手动代理地址为空")
            if "://" not in proxy:
                proxy = "http://" + proxy
            parsed = urlparse(proxy)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise EhOnlineError("手动代理仅支持有效的 HTTP(S) 地址")
            handler = ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = ProxyHandler()
        return build_opener(handler)

    def _request(self, url: str, allowed_hosts) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            raise EhOnlineError("站点返回了不受信任的链接")
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            with self._opener.open(Request(url, headers=headers), timeout=self.timeout) as response:
                return response.read()
        except HTTPError as error:
            if error.code in {401, 403}:
                raise EhOnlineError("访问被拒绝，请检查 EH Cookie/Token 与代理设置") from None
            raise EhOnlineError(f"站点返回 HTTP {error.code}") from None
        except (URLError, socket.timeout, TimeoutError):
            raise EhOnlineError("网络连接失败，请检查网络与代理设置") from None

    def search_url(self, query="") -> str:
        parameters = {"f_search": query.strip(), "inline_set": "dm_l"}
        return self.base_url + "?" + urlencode(parameters)

    def search(self, query="", page_url="") -> OnlineGalleryPage:
        url = page_url or self.search_url(query)
        parsed_url = urlparse(url)
        if parsed_url.hostname not in _ALLOWED_PAGE_HOSTS:
            raise EhOnlineError("无效的翻页地址")
        # Keep navigation on the selected site even if a stale cursor was supplied.
        if parsed_url.hostname != urlparse(self.base_url).hostname:
            raise EhOnlineError("翻页地址与当前站点不一致")
        html = self._request(url, _ALLOWED_PAGE_HOSTS).decode("utf-8", "replace")
        page = parse_gallery_list(html)
        if self.site == "exhentai" and not page.items:
            raise EhOnlineError("ExHentai 未返回画廊，请检查完整 Cookie 是否有效且账号具有访问权限")
        return page

    def load_thumbnail(self, url: str) -> bytes:
        if not url:
            return b""
        return self._request(url, _ALLOWED_IMAGE_HOSTS)
