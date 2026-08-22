import json
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import replace
from threading import Lock
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPage,
    OnlineGalleryPreview,
    OnlineGalleryPreviewPage,
    gallery_preview_page_count,
    gallery_preview_page_size,
)
from app.sources.eh_online_source import EhOnlineError, EhOnlineProvider
from app.services.online_query_syntax import adapt_online_query, split_structured_query


class NhentaiOnlineProvider(EhOnlineProvider):
    """List and search provider for nhentai.com and nhentai.net."""

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0 Safari/537.36"
    )
    NHC_FILTER_COLLECTIONS = {
        "artist": "artists",
        "author": "authors",
        "category": "categories",
        "character": "characters",
        "group": "groups",
        "language": "languages",
        "parody": "parodies",
        "relationship": "relationships",
        "tag": "tags",
        "attribute": "attributes",
    }
    NHC_DETAIL_COLLECTIONS = (
        ("parodies", "parody"),
        ("artists", "artist"),
        ("authors", "author"),
        ("groups", "group"),
        ("characters", "character"),
        ("relationships", "relationship"),
        ("tags", "tag"),
        ("attributes", "attribute"),
    )

    def __init__(self, settings, session=None):
        super().__init__(settings)
        if settings.site not in {"nhc", "nhn"}:
            raise EhOnlineError(f"不支持的 NH 站点：{settings.site}")
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            }
        )
        if settings.cookie:
            self._session.headers["Cookie"] = settings.cookie
        self._nhc_filter_ids = {}
        self._request_lock = Lock()
        self._active_responses = {}
        self._cancel_requested = False
        self._session.trust_env = settings.proxy_mode == "system"
        proxies = settings.proxy_mapping()
        if settings.proxy_mode in {"manual", "direct"}:
            self._session.proxies.clear()
            self._session.proxies.update(proxies)

    def fetch_page(self, query):
        if query.seek_date:
            raise EhOnlineError("NHC / NHN 暂不支持日期定位")
        page_number = self._page_number(query.cursor)
        keyword = adapt_online_query(query.keyword, self.settings.site)
        if self.settings.site == "nhc" and keyword:
            return self._fetch_nhc_search(keyword, page_number)
        url, params = self._list_request(page_number, keyword)
        response = self._session.get(
            url,
            params=params or None,
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"在线列表请求失败（HTTP {status}）")
        soup = BeautifulSoup(response.content, "lxml")
        if self.settings.site == "nhc":
            items = self._parse_nhc_items(soup)
        else:
            items = self._parse_nhn_items(soup)
        if not items and not keyword:
            raise EhOnlineError("站点返回的页面不是可识别的画廊列表")
        previous_cursor, next_cursor = self._parse_pagination(soup)
        return OnlineGalleryPage(
            items=items,
            previous_cursor=previous_cursor,
            next_cursor=next_cursor,
        )

    def load_thumbnail(self, url, should_cancel=None):
        if not url or (should_cancel is not None and should_cancel()):
            return b""
        self._validate_thumbnail_url(url)
        response = self._session.get(
            url,
            headers={"Cookie": None},
            allow_redirects=False,
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            return b""
        return bytes(response.content or b"")

    def load_gallery_detail(self, gallery):
        if self.settings.site == "nhc":
            return self._load_nhc_detail(gallery)
        return self._load_nhn_detail(gallery)

    def load_gallery_preview_page(
        self, gallery, page_number, should_cancel=None
    ):
        page_number = int(page_number)
        page_count = gallery_preview_page_count(gallery)
        if not 1 <= page_number <= page_count:
            raise EhOnlineError("画廊预览页码超出范围")
        previews = (
            self._load_nhc_previews(gallery, should_cancel)
            if self.settings.site == "nhc"
            else self._load_nhn_previews(gallery, should_cancel)
        )
        page_size = gallery_preview_page_size(gallery)
        start = (page_number - 1) * page_size
        return OnlineGalleryPreviewPage(
            gallery=gallery,
            page_number=page_number,
            page_count=page_count,
            items=previews[start:start + page_size],
        )

    def load_preview_thumbnail(self, preview):
        if not preview.thumbnail_url:
            return b""
        self._validate_page_asset_url(preview.thumbnail_url, preview, thumbnail=True)
        return self._request_image(preview.thumbnail_url)

    def load_gallery_page_image(
        self,
        gallery,
        preview,
        should_cancel=None,
        progress_callback=None,
    ):
        self._validate_preview_identity(gallery, preview)
        if self.settings.site == "nhc":
            image_url = preview.page_url
            self._validate_page_asset_url(image_url, preview, thumbnail=False)
        else:
            content = self._request_page(preview.page_url, should_cancel)
            soup = BeautifulSoup(content, "lxml")
            image = soup.select_one("#image-container img[src]")
            if image is None:
                raise EhOnlineError("NHN 单图页面缺少图片地址")
            image_url = urljoin(preview.page_url, str(image.get("src") or ""))
            self._validate_page_asset_url(image_url, preview, thumbnail=False)
        data = self._request_image(
            image_url,
            should_cancel,
            progress_callback=progress_callback,
        )
        if not data:
            raise EhOnlineError("在线图片请求失败")
        return data

    def cancel_pending_requests(self):
        with self._request_lock:
            self._cancel_requested = True
            responses = tuple(self._active_responses.values())
        for response in responses:
            self._abort_response(response)

    def set_display_mode(self, mode):
        return None

    @staticmethod
    def _page_number(cursor):
        value = str(cursor or "1").strip()
        if not re.fullmatch(r"[1-9]\d*", value):
            raise EhOnlineError("无效的数字页码")
        return int(value)

    def _list_request(self, page_number, keyword=""):
        if self.settings.site == "nhc":
            if page_number == 1:
                return "https://nhentai.com/hentai-comics", {}
            return f"https://nhentai.com/en/hentai-comics/{page_number}", {}
        if keyword:
            return "https://nhentai.net/search", {
                "q": keyword,
                "sort": "date",
                "page": page_number,
            }
        if page_number == 1:
            return "https://nhentai.net/", {}
        return "https://nhentai.net/", {"page": page_number}

    def _fetch_nhc_search(self, keyword, page_number):
        free_text, structured_tokens = split_structured_query(keyword)
        params = {"page": page_number}
        if free_text:
            params["q"] = free_text
        for namespace, value in structured_tokens:
            collection = self.NHC_FILTER_COLLECTIONS.get(namespace)
            if collection is None:
                raise EhOnlineError(f"NHC 不支持搜索命名空间：{namespace}")
            filter_id = self._resolve_nhc_filter_id(collection, value)
            params.setdefault(collection, []).append(filter_id)
        response = self._session.get(
            "https://nhentai.com/api/comics",
            params=params,
            headers=self._nhc_json_headers(),
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"在线搜索请求失败（HTTP {status}）")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise EhOnlineError("NHC 返回了无法识别的搜索数据") from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
            raise EhOnlineError("NHC 返回了无法识别的搜索数据")
        items = tuple(
            item
            for item in (self._nhc_api_gallery(raw) for raw in payload["data"])
            if item is not None
        )
        current = self._positive_int(payload.get("current_page")) or page_number
        last = self._positive_int(payload.get("last_page")) or current
        return OnlineGalleryPage(
            items=items,
            previous_cursor=str(current - 1) if current > 1 else "",
            next_cursor=str(current + 1) if current < last else "",
        )

    def _resolve_nhc_filter_id(self, collection, value):
        normalized = self._normalized_filter_name(value)
        if not normalized:
            raise EhOnlineError("NHC 标签搜索值为空")
        cache_key = collection, normalized
        if cache_key in self._nhc_filter_ids:
            return self._nhc_filter_ids[cache_key]
        response = self._session.get(
            f"https://nhentai.com/api/{collection}",
            params={"q": value},
            headers=self._nhc_json_headers(),
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"NHC 标签解析失败（HTTP {status}）")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise EhOnlineError("NHC 返回了无法识别的标签数据") from None
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise EhOnlineError("NHC 返回了无法识别的标签数据")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            candidates = (row.get("name"), row.get("slug"))
            if normalized not in {
                self._normalized_filter_name(candidate)
                for candidate in candidates
                if candidate
            }:
                continue
            filter_id = self._positive_int(row.get("id"))
            if filter_id:
                self._nhc_filter_ids[cache_key] = filter_id
                return filter_id
        raise EhOnlineError(f"NHC 未找到 {collection} 标签：{value}")

    def _load_nhc_detail(self, gallery):
        parsed = urlparse(str(gallery.url or ""))
        match = re.fullmatch(r"/en/comic/([^/?#]+)", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != "nhentai.com" or match is None:
            raise EhOnlineError("NHC 画廊地址无效")
        response = self._session.get(
            f"https://nhentai.com/api/comics/{match.group(1)}",
            headers=self._nhc_json_headers(),
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"NHC 画廊详情请求失败（HTTP {status}）")
        try:
            raw = response.json()
        except (TypeError, ValueError):
            raise EhOnlineError("NHC 返回了无法识别的画廊详情") from None
        if (
            not isinstance(raw, Mapping)
            or self._positive_int(raw.get("id")) != self._remote_gallery_id(gallery)
        ):
            raise EhOnlineError("NHC 画廊详情与请求目标不一致")
        enriched = self._nhc_api_gallery(raw)
        if enriched is None:
            raise EhOnlineError("NHC 画廊详情缺少必要字段")
        language = raw.get("language") if isinstance(raw.get("language"), Mapping) else {}
        enriched = replace(enriched, preview_page_size=40)
        previews = self._load_nhc_previews(enriched)
        return OnlineGalleryDetail(
            gallery=enriched,
            title=enriched.title,
            secondary_title=str(raw.get("alternative_title") or "").strip(),
            category=enriched.category,
            cover_url=str(raw.get("image_url") or enriched.thumbnail_url),
            posted=enriched.posted,
            language=str(language.get("name") or language.get("slug") or ""),
            page_count=enriched.page_count,
            tags=enriched.tags,
            previews=previews[:gallery_preview_page_size(enriched)],
        )

    def _load_nhn_detail(self, gallery):
        parsed = urlparse(str(gallery.url or ""))
        match = re.fullmatch(r"/g/([1-9]\d*)/", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "nhentai.net"
            or match is None
            or int(match.group(1)) != self._remote_gallery_id(gallery)
        ):
            raise EhOnlineError("NHN 画廊地址无效")
        response = self._session.get(
            gallery.url,
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"NHN 画廊详情请求失败（HTTP {status}）")
        final_url = urlparse(str(getattr(response, "url", "") or gallery.url))
        if (
            final_url.scheme != "https"
            or final_url.hostname != "nhentai.net"
            or final_url.path != parsed.path
        ):
            raise EhOnlineError("NHN 画廊详情发生了无效跳转")
        try:
            html = bytes(response.content or b"").decode("utf-8")
        except UnicodeDecodeError:
            raise EhOnlineError("NHN 画廊详情编码无效") from None
        soup = BeautifulSoup(html, "lxml")
        id_node = soup.select_one("#gallery_id")
        returned_gid = re.sub(r"\D", "", id_node.get_text()) if id_node else ""
        title_node = soup.select_one("#info h1")
        if returned_gid != str(self._remote_gallery_id(gallery)) or title_node is None:
            raise EhOnlineError("NHN 画廊详情与请求目标不一致")
        secondary_node = soup.select_one("#info h2")
        cover_node = soup.select_one("#cover img")
        tags = []
        field_values = {}
        for container in soup.select("#tags .tag-container"):
            text = container.get_text(" ", strip=True)
            field_name = text.split(":", 1)[0].casefold()
            field_values[field_name] = text.split(":", 1)[-1].strip()
            for anchor in container.select("a.tagchip[href]"):
                target = urlparse(urljoin("https://nhentai.net/", anchor.get("href", "")))
                tag_match = re.fullmatch(
                    r"/(parody|character|tag|artist|group|language|category)/([^/?#]+)/",
                    target.path,
                )
                name_node = anchor.select_one(".name")
                if tag_match is not None and name_node is not None:
                    tags.append(
                        f"{tag_match.group(1)}:{name_node.get_text(' ', strip=True)}"
                    )
        page_match = re.search(r"\d+", field_values.get("pages", ""))
        page_count = int(page_match.group()) if page_match else 0
        posted_node = soup.select_one("#info time[datetime]")
        category = next(
            (tag.split(":", 1)[1] for tag in tags if tag.startswith("category:")),
            "",
        )
        languages = tuple(
            tag.split(":", 1)[1] for tag in tags if tag.startswith("language:")
        )
        enriched = replace(
            gallery,
            title=title_node.get_text(" ", strip=True),
            category=category,
            thumbnail_url=(
                urljoin("https://nhentai.net/", cover_node.get("src", ""))
                if cover_node is not None
                else gallery.thumbnail_url
            ),
            posted=str(posted_node.get("datetime") or "") if posted_node else "",
            page_count=page_count,
            tags=tuple(dict.fromkeys(tags)),
            source_mode="NHN",
            source_site="nhn",
            source_id=str(self._remote_gallery_id(gallery)),
            preview_page_size=40,
        )
        previews = self._parse_nhn_previews(enriched, soup)
        if page_count and len(previews) != page_count:
            raise EhOnlineError("NHN 画廊预览数量与页数不一致")
        return OnlineGalleryDetail(
            gallery=enriched,
            title=enriched.title,
            secondary_title=(
                secondary_node.get_text(" ", strip=True)
                if secondary_node is not None
                else ""
            ),
            category=category,
            cover_url=enriched.thumbnail_url,
            posted=enriched.posted,
            language=", ".join(languages),
            page_count=page_count,
            tags=enriched.tags,
            previews=previews[:gallery_preview_page_size(enriched)],
        )

    def _load_nhc_previews(self, gallery, should_cancel=None):
        slug = self._nhc_gallery_slug(gallery)
        content = self._request_page(
            f"https://nhentai.com/api/comics/{slug}/images",
            should_cancel,
            headers=self._nhc_json_headers(),
        )
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError):
            raise EhOnlineError("NHC 返回了无法识别的页面清单") from None
        rows = payload.get("images") if isinstance(payload, Mapping) else None
        comic = payload.get("comic") if isinstance(payload, Mapping) else None
        remote_id = self._remote_gallery_id(gallery)
        if (
            not isinstance(rows, list)
            or not isinstance(comic, Mapping)
            or self._positive_int(comic.get("id")) != remote_id
        ):
            raise EhOnlineError("NHC 页面清单与请求目标不一致")
        previews = []
        for expected_page, row in enumerate(rows, 1):
            if not isinstance(row, Mapping) or self._positive_int(row.get("page")) != expected_page:
                raise EhOnlineError("NHC 页面清单顺序无效")
            source_url = str(row.get("source_url") or "")
            thumbnail_url = str(row.get("thumbnail_url") or "")
            preview = OnlineGalleryPreview(
                page_index=expected_page - 1,
                page_url=source_url,
                thumbnail_url=thumbnail_url,
                title=f"第 {expected_page} 页",
                page_token=str(expected_page),
            )
            self._validate_page_asset_url(source_url, preview, thumbnail=False)
            self._validate_page_asset_url(thumbnail_url, preview, thumbnail=True)
            expected_suffix = f"/{remote_id}/{expected_page}."
            if expected_suffix not in urlparse(source_url).path or expected_suffix not in urlparse(thumbnail_url).path:
                raise EhOnlineError("NHC 页面图片与请求目标不一致")
            previews.append(preview)
        if gallery.page_count and len(previews) != int(gallery.page_count):
            raise EhOnlineError("NHC 画廊预览数量与页数不一致")
        return tuple(previews)

    def _load_nhn_previews(self, gallery, should_cancel=None):
        content = self._request_page(gallery.url, should_cancel)
        return self._parse_nhn_previews(gallery, BeautifulSoup(content, "lxml"))

    def _parse_nhn_previews(self, gallery, soup):
        remote_id = self._remote_gallery_id(gallery)
        previews = []
        for expected_page, anchor in enumerate(
            soup.select("#thumbnail-container .thumb-container a[href]"), 1
        ):
            target = urlparse(urljoin("https://nhentai.net/", anchor.get("href", "")))
            match = re.fullmatch(
                rf"/g/{remote_id}/([1-9]\d*)/", target.path
            )
            image = anchor.select_one("img[src]")
            if (
                target.scheme != "https"
                or target.hostname != "nhentai.net"
                or match is None
                or int(match.group(1)) != expected_page
                or image is None
            ):
                raise EhOnlineError("NHN 页面预览节点无效")
            preview = OnlineGalleryPreview(
                page_index=expected_page - 1,
                page_url=target.geturl(),
                thumbnail_url=urljoin(target.geturl(), str(image.get("src") or "")),
                title=f"第 {expected_page} 页",
                page_token=str(expected_page),
            )
            self._validate_page_asset_url(
                preview.thumbnail_url, preview, thumbnail=True
            )
            previews.append(preview)
        if not previews and gallery.page_count:
            raise EhOnlineError("NHN 站点返回的页面预览为空")
        return tuple(previews)

    def _request_page(self, url, should_cancel=None, headers=None):
        if should_cancel is not None and should_cancel():
            raise EhOnlineError("请求已取消")
        response = self._session.get(
            url,
            headers=headers,
            allow_redirects=False,
            timeout=self.settings.timeout_seconds,
        )
        if not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"页面请求失败（HTTP {status}）")
        if should_cancel is not None and should_cancel():
            raise EhOnlineError("请求已取消")
        return bytes(response.content or b"")

    def _request_image(self, url, should_cancel=None, progress_callback=None):
        if should_cancel is None:
            response = self._session.get(
                url,
                headers={"Cookie": None},
                allow_redirects=False,
                timeout=self.settings.timeout_seconds,
            )
            return bytes(response.content or b"") if getattr(response, "ok", False) else b""
        if should_cancel() or self._cancel_requested:
            raise EhOnlineError("请求已取消")
        try:
            response = self._session.get(
                url,
                headers={"Cookie": None},
                allow_redirects=False,
                stream=True,
                timeout=(min(15, self.settings.timeout_seconds),) * 2,
            )
        except TypeError:
            response = self._session.get(
                url,
                headers={"Cookie": None},
                allow_redirects=False,
                timeout=self.settings.timeout_seconds,
            )
        key = id(response)
        with self._request_lock:
            self._active_responses[key] = response
        try:
            if not getattr(response, "ok", False):
                return b""
            chunks = []
            interval_bytes = 0
            last_update = time.monotonic()
            iterator = getattr(response, "iter_content", None)
            if iterator is None:
                return bytes(response.content or b"")
            for chunk in iterator(chunk_size=64 * 1024):
                if should_cancel() or self._cancel_requested:
                    raise EhOnlineError("请求已取消")
                if not chunk:
                    continue
                chunks.append(bytes(chunk))
                interval_bytes += len(chunk)
                now = time.monotonic()
                if progress_callback is not None and now - last_update >= 1.0:
                    progress_callback(interval_bytes / max(0.001, now - last_update))
                    interval_bytes = 0
                    last_update = now
            if progress_callback is not None and interval_bytes:
                progress_callback(
                    interval_bytes / max(0.001, time.monotonic() - last_update)
                )
            return b"".join(chunks)
        finally:
            with self._request_lock:
                self._active_responses.pop(key, None)
            close = getattr(response, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _abort_response(response):
        raw = getattr(response, "raw", None)
        shutdown = getattr(raw, "_sock_shutdown", None)
        if shutdown is not None:
            try:
                shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
        close = getattr(response, "close", None)
        if close is not None:
            close()

    def _validate_preview_identity(self, gallery, preview):
        page_number = int(preview.page_index) + 1
        if not 1 <= page_number <= int(gallery.page_count):
            raise EhOnlineError("画廊单页页码超出范围")
        if self.settings.site == "nhn":
            parsed = urlparse(preview.page_url)
            remote_id = self._remote_gallery_id(gallery)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "nhentai.net"
                or parsed.query
                or parsed.fragment
                or parsed.path != f"/g/{remote_id}/{page_number}/"
            ):
                raise EhOnlineError("NHN 单图页地址无效")
        else:
            parsed = urlparse(preview.page_url)
            remote_id = self._remote_gallery_id(gallery)
            if not re.fullmatch(
                rf"/nhentai/storage/images/{remote_id}/{page_number}\.(?:avif|jpe?g|png|webp)",
                parsed.path,
                re.IGNORECASE,
            ):
                raise EhOnlineError("NHC 单图地址与画廊不一致")

    def _validate_page_asset_url(self, value, preview, thumbnail):
        parsed = urlparse(str(value or ""))
        page_number = int(preview.page_index) + 1
        if self.settings.site == "nhc":
            kind = "thumbnails" if thumbnail else "images"
            pattern = rf"/nhentai/storage/{kind}/[1-9]\d*/{page_number}\.(?:avif|jpe?g|png|webp)"
            valid = parsed.hostname == "cdn.nhentai.com" and re.fullmatch(
                pattern, parsed.path, re.IGNORECASE
            )
        else:
            host = (parsed.hostname or "").casefold()
            suffix = rf"{page_number}t\.(?:avif|jpe?g|png|webp)(?:\.(?:avif|jpe?g|png|webp))?" if thumbnail else rf"{page_number}\.(?:avif|jpe?g|png|webp)"
            expected_prefix = "t" if thumbnail else "i"
            valid = (
                re.fullmatch(rf"{expected_prefix}\d+\.nhentai\.net", host)
                and re.fullmatch(
                    rf"/galleries/[1-9]\d*/{suffix}", parsed.path, re.IGNORECASE
                )
            )
        if parsed.scheme != "https" or not valid or parsed.query or parsed.fragment:
            raise EhOnlineError("页面图片地址不属于当前在线站点")

    @staticmethod
    def _remote_gallery_id(gallery):
        try:
            value = int(str(gallery.source_id or gallery.gid))
        except (TypeError, ValueError):
            raise EhOnlineError("画廊来源编号无效") from None
        if value <= 0:
            raise EhOnlineError("画廊来源编号无效")
        return value

    @staticmethod
    def _nhc_gallery_slug(gallery):
        parsed = urlparse(str(gallery.url or ""))
        match = re.fullmatch(r"/en/comic/([^/?#]+)", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != "nhentai.com" or match is None:
            raise EhOnlineError("NHC 画廊地址无效")
        return match.group(1)

    @staticmethod
    def _nhc_json_headers():
        return {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://nhentai.com/en/latest",
        }

    @staticmethod
    def _normalized_filter_name(value):
        return " ".join(
            str(value or "").casefold().replace("-", " ").replace("_", " ").split()
        )

    @staticmethod
    def _nhc_api_gallery(raw):
        if not isinstance(raw, Mapping):
            return None
        gid = NhentaiOnlineProvider._positive_int(raw.get("id"))
        slug = str(raw.get("slug") or "").strip().strip("/")
        title = str(raw.get("title") or raw.get("alternative_title") or "").strip()
        thumbnail_url = str(raw.get("thumb_url") or "").strip()
        if not gid or not slug or not title or not thumbnail_url:
            return None
        category = raw.get("category") if isinstance(raw.get("category"), Mapping) else {}
        language = raw.get("language") if isinstance(raw.get("language"), Mapping) else {}
        tags = []
        category_name = str(category.get("name") or category.get("slug") or "").strip()
        if category_name:
            tags.append(f"category:{category_name}")
        language_slug = str(language.get("slug") or language.get("name") or "").strip()
        if language_slug:
            tags.append(f"language:{language_slug}")
        for field, namespace in NhentaiOnlineProvider.NHC_DETAIL_COLLECTIONS:
            for tag in raw.get(field) or ():
                if isinstance(tag, Mapping):
                    name = str(tag.get("name") or tag.get("slug") or "").strip()
                    if name:
                        tags.append(f"{namespace}:{name}")
        return OnlineGallery(
            gid=gid,
            token="",
            url=f"https://nhentai.com/en/comic/{slug}",
            title=title,
            category=str(category.get("name") or "漫画"),
            thumbnail_url=thumbnail_url,
            posted=str(raw.get("uploaded_at") or ""),
            page_count=NhentaiOnlineProvider._positive_int(raw.get("pages")) or 0,
            tags=tuple(dict.fromkeys(tags)),
            source_mode="NHC",
            source_site="nhc",
            source_id=str(gid),
        )

    @staticmethod
    def _positive_int(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    @staticmethod
    def _parse_nhc_items(soup):
        items = []
        seen = set()
        for anchor in soup.select("a[href]"):
            target = urlparse(urljoin("https://nhentai.com/", anchor.get("href", "")))
            match = re.fullmatch(r"/en/comic/([^/?#]+)", target.path)
            image = anchor.find("img")
            if target.scheme != "https" or target.hostname != "nhentai.com":
                continue
            if match is None or image is None:
                continue
            thumbnail_url = urljoin(
                "https://nhentai.com/", str(image.get("src") or "")
            )
            image_match = re.search(
                r"/storage/comics/thumbs/([1-9]\d*)\.(?:avif|jpe?g|png|webp)$",
                urlparse(thumbnail_url).path,
                re.IGNORECASE,
            )
            if image_match is None:
                continue
            gid = int(image_match.group(1))
            if gid in seen:
                continue
            seen.add(gid)
            title_node = anchor.find("span")
            title = (
                title_node.get_text(" ", strip=True)
                if title_node is not None
                else str(image.get("alt") or "").strip()
            )
            items.append(
                OnlineGallery(
                    gid=gid,
                    token="",
                    url=target.geturl(),
                    title=title or str(gid),
                    category="漫画",
                    thumbnail_url=thumbnail_url,
                    source_mode="NHC",
                    source_site="nhc",
                    source_id=str(gid),
                )
            )
        return tuple(items)

    @staticmethod
    def _parse_nhn_items(soup):
        items = []
        seen = set()
        for anchor in soup.select(".gallery a.cover[href], a.cover[href]"):
            target = urlparse(urljoin("https://nhentai.net/", anchor.get("href", "")))
            match = re.fullmatch(r"/g/([1-9]\d*)/", target.path)
            image = anchor.find("img")
            if target.scheme != "https" or target.hostname != "nhentai.net":
                continue
            if match is None or image is None:
                continue
            gid = int(match.group(1))
            if gid in seen:
                continue
            seen.add(gid)
            thumbnail_url = urljoin(
                "https://nhentai.net/", str(image.get("src") or "")
            )
            caption = anchor.select_one(".caption")
            title = (
                caption.get_text(" ", strip=True)
                if caption is not None
                else str(image.get("alt") or "").strip()
            )
            items.append(
                OnlineGallery(
                    gid=gid,
                    token="",
                    url=target.geturl(),
                    title=title or str(gid),
                    category="漫画",
                    thumbnail_url=thumbnail_url,
                    source_mode="NHN",
                    source_site="nhn",
                    source_id=str(gid),
                )
            )
        return tuple(items)

    def _parse_pagination(self, soup):
        previous = self._cursor_from_link(soup.select_one("a[rel~=prev], a.previous"))
        next_cursor = self._cursor_from_link(soup.select_one("a[rel~=next], a.next"))
        return previous, next_cursor

    def _cursor_from_link(self, anchor):
        if anchor is None:
            return ""
        parsed = urlparse(urljoin(self.settings.base_url, anchor.get("href", "")))
        expected_host = urlparse(self.settings.base_url).hostname
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            return ""
        if self.settings.site == "nhc":
            if parsed.path in {"/hentai-comics", "/en/hentai-comics"}:
                return "1"
            match = re.fullmatch(r"/en/hentai-comics/([1-9]\d*)/?", parsed.path)
            return match.group(1) if match is not None else ""
        if parsed.path.rstrip("/") not in {"", "/search"}:
            return ""
        values = parse_qs(parsed.query).get("page", ())
        return values[0] if values and re.fullmatch(r"[1-9]\d*", values[0]) else ""

    def _validate_thumbnail_url(self, value):
        parsed = urlparse(str(value or ""))
        host = (parsed.hostname or "").casefold()
        if self.settings.site == "nhc":
            valid_host = host == "cdn.nhentai.com"
        else:
            valid_host = host.endswith(".nhentai.net") and host != "nhentai.net"
        if parsed.scheme != "https" or not valid_host or parsed.query or parsed.fragment:
            raise EhOnlineError("缩略图地址不属于当前在线站点")
