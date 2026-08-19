from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
import re
import socket
import time
from threading import Lock
from typing import Dict, Iterable
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import getproxies

from bs4 import BeautifulSoup

from eh_tool_refactored import EhData

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryComment,
    OnlineGalleryDetail,
    OnlineGalleryLink,
    OnlineGalleryPage,
    OnlineGalleryPreview,
    OnlineGalleryPreviewPage,
    OnlineGalleryQuery,
)


SITE_BASE_URLS = {
    "ehentai": "https://e-hentai.org/",
    "exhentai": "https://exhentai.org/",
}

GALLERY_HOST_SITES = {
    "e-hentai.org": "ehentai",
    "exhentai.org": "exhentai",
}


@dataclass(frozen=True)
class EhGalleryAddress:
    source_site: str
    gid: int
    token: str


def parse_eh_gallery_url(value: str) -> EhGalleryAddress:
    """Parse only a canonical EH/EX gallery URL with both GID and token."""

    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError as error:
        raise EhOnlineError(
            "请输入完整的 E-Hentai 或 ExHentai 画廊地址，地址必须包含 GID 和 token"
        ) from error
    hostname = (parsed.hostname or "").casefold()
    match = re.fullmatch(r"/g/([1-9]\d*)/([0-9a-fA-F]+)/?", parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.netloc.casefold() not in GALLERY_HOST_SITES
        or hostname not in GALLERY_HOST_SITES
        or match is None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise EhOnlineError(
            "请输入完整的 E-Hentai 或 ExHentai 画廊地址，地址必须包含 GID 和 token"
        )
    return EhGalleryAddress(
        source_site=GALLERY_HOST_SITES[hostname],
        gid=int(match.group(1)),
        token=match.group(2),
    )


def build_eh_gallery_url(site: str, gid: int, token: str) -> str:
    if site not in SITE_BASE_URLS or int(gid) <= 0 or not re.fullmatch(
        r"[0-9a-fA-F]+", str(token or "")
    ):
        raise EhOnlineError("无法生成有效的 EH/EX 画廊地址")
    return f"{SITE_BASE_URLS[site]}g/{int(gid)}/{token}/"


class EhOnlineError(RuntimeError):
    """Safe, user-facing error raised by an online provider."""


class EhOnlineProviderNotImplemented(EhOnlineError):
    """Raised until a concrete crawler provider is registered."""


class OriginalImageUnavailableError(EhOnlineError):
    """The image page is valid but does not expose a full-image target."""


@dataclass(frozen=True)
class EhOnlineSettings:
    """Validated runtime configuration passed to a crawler implementation.

    Credentials are excluded from ``repr`` so an accidentally logged settings
    object cannot expose the user's cookie.
    """

    site: str
    base_url: str
    cookie: str = field(default="", repr=False)
    proxy_mode: str = "system"
    manual_proxy: str = field(default="", repr=False)
    timeout_seconds: int = 20

    @classmethod
    def create(
        cls,
        site="ehentai",
        cookie="",
        proxy_mode="system",
        manual_proxy="",
        timeout_seconds=20,
    ):
        if site not in SITE_BASE_URLS:
            raise EhOnlineError(f"不支持的在线站点：{site}")
        if proxy_mode not in {"system", "direct", "manual"}:
            raise EhOnlineError(f"不支持的代理模式：{proxy_mode}")

        normalized_proxy = (manual_proxy or "").strip()
        if proxy_mode == "manual":
            if not normalized_proxy:
                raise EhOnlineError("手动代理地址为空")
            if "://" not in normalized_proxy:
                normalized_proxy = "http://" + normalized_proxy
            parsed = urlparse(normalized_proxy)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise EhOnlineError("手动代理仅支持有效的 HTTP(S) 地址")

        return cls(
            site=site,
            base_url=SITE_BASE_URLS[site],
            cookie=cls.normalize_cookie(cookie),
            proxy_mode=proxy_mode,
            manual_proxy=normalized_proxy,
            timeout_seconds=max(3, int(timeout_seconds)),
        )

    @staticmethod
    def normalize_cookie(value: str) -> str:
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

    def proxy_mapping(self) -> Dict[str, str]:
        """Return a urllib/requests-style proxy mapping for the selected mode."""

        if self.proxy_mode == "direct":
            return {}
        if self.proxy_mode == "manual":
            return {"http": self.manual_proxy, "https": self.manual_proxy}
        return self._system_proxy_mapping(getproxies())

    @staticmethod
    def _system_proxy_mapping(proxies) -> Dict[str, str]:
        """Adapt OS proxy discovery to requests' proxy URL semantics.

        On Windows, urllib expands one scheme-less system proxy endpoint into
        ``http://host`` and ``https://host``.  The latter tells requests to use
        TLS *to the proxy*, although Windows intends the same HTTP CONNECT
        proxy for HTTPS destinations.
        """

        mapping = {
            str(key).casefold(): str(value).strip()
            for key, value in dict(proxies or {}).items()
            if value
        }
        http_proxy = mapping.get("http", "")
        https_proxy = mapping.get("https", "")

        if http_proxy and not https_proxy:
            mapping["https"] = http_proxy
            return mapping

        if not http_proxy or not https_proxy:
            return mapping

        http_url = urlparse(http_proxy)
        https_url = urlparse(https_proxy)
        try:
            same_endpoint = bool(
                http_url.hostname
                and http_url.hostname == https_url.hostname
                and http_url.port == https_url.port
                and http_url.username == https_url.username
                and http_url.password == https_url.password
            )
        except ValueError:
            same_endpoint = False
        if (
            same_endpoint
            and http_url.scheme.casefold() == "http"
            and https_url.scheme.casefold() == "https"
        ):
            mapping["https"] = http_proxy

        return mapping


class EhOnlineProvider(ABC):
    """Extension point for an EH/EX crawler.

    A concrete provider only needs to implement :meth:`fetch_page`. Override
    :meth:`filter_items` for provider-side filtering and :meth:`load_thumbnail`
    when the returned gallery model contains remote thumbnail URLs. The UI and
    workers depend only on this contract.
    """

    def __init__(self, settings: EhOnlineSettings):
        self.settings = settings

    def search(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        page = self.fetch_page(query)
        filtered_items = tuple(self.filter_items(page.items, query))
        return replace(page, items=filtered_items)

    @abstractmethod
    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        """Fetch and parse one result page without touching Qt widgets."""

        raise NotImplementedError

    def filter_items(
        self,
        items: Iterable[OnlineGallery],
        query: OnlineGalleryQuery,
    ) -> Iterable[OnlineGallery]:
        """Apply provider-specific filtering; the default keeps all items."""

        return items

    def load_thumbnail(self, url: str, should_cancel=None) -> bytes:
        """Optionally return encoded thumbnail bytes for a gallery card."""

        return b""

    def load_gallery_detail(self, gallery: OnlineGallery) -> OnlineGalleryDetail:
        """Load one gallery page without using a site API."""

        raise EhOnlineError("当前在线 provider 不支持读取画廊详情")

    def load_gallery_preview_page(
        self, gallery: OnlineGallery, page_number: int, should_cancel=None
    ) -> OnlineGalleryPreviewPage:
        raise EhOnlineError("当前在线 provider 不支持读取画廊预览")

    def load_preview_thumbnail(self, preview: OnlineGalleryPreview) -> bytes:
        return b""

    def load_gallery_page_image(
        self,
        gallery: OnlineGallery,
        preview: OnlineGalleryPreview,
        should_cancel=None,
        progress_callback=None,
    ) -> bytes:
        raise EhOnlineError("当前在线 provider 不支持在线阅读")

    def load_gallery_page_original(
        self,
        gallery: OnlineGallery,
        preview: OnlineGalleryPreview,
        should_cancel=None,
        progress_callback=None,
    ) -> bytes:
        raise EhOnlineError("当前在线 provider 不支持下载原图")

    def set_display_mode(self, mode: str):
        """Update the account/session list mode through the site's own page control."""

        raise EhOnlineError("当前在线 provider 不支持更新站点默认页面模式")


class UnimplementedEhOnlineProvider(EhOnlineProvider):
    """Safe default that performs no network access."""

    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        raise EhOnlineProviderNotImplemented(
            "在线爬虫尚未接入：请实现 EhOnlineProvider.fetch_page()，"
            "并在 create_eh_online_provider() 中返回你的 provider"
        )


class RefactoredEhOnlineProvider(EhOnlineProvider):
    """Adapter for the user-supplied ``eh_tool_refactored`` list crawler."""

    STREAM_READ_IDLE_TIMEOUT_SECONDS = 15
    SPEED_UPDATE_INTERVAL_SECONDS = 1.0

    SOURCE_NAMES = {
        "ehentai": "e-hentai",
        "exhentai": "exhentai",
    }

    def __init__(self, settings: EhOnlineSettings):
        super().__init__(settings)
        proxy_mapping = settings.proxy_mapping()
        use_proxy = settings.proxy_mode == "manual" or (
            settings.proxy_mode == "system" and bool(proxy_mapping)
        )
        self._crawler = EhData(
            settings.cookie,
            source=self.SOURCE_NAMES[settings.site],
            proxies=proxy_mapping,
            use_proxy=use_proxy,
            timeout=settings.timeout_seconds,
            trust_env=settings.proxy_mode == "system",
        )
        self._request_lock = Lock()
        self._session_configuration_lock = Lock()
        self._session_configuration_loaded = False
        self._active_responses = {}
        self._cancel_requested = False

    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        self._ensure_session_configuration()
        if query.cursor:
            self._validate_list_url(query.cursor)
            result = self._crawler.getUrl(query.cursor)
        elif query.seek_date:
            if query.keyword:
                context = self._crawler.getMain(search=query.keyword)
                if not isinstance(context, dict):
                    raise EhOnlineError("画廊爬虫返回了未知数据")
                if context.get("error"):
                    raise EhOnlineError(str(context["error"]))
            result = self._crawler.getMain(time=query.seek_date)
        else:
            result = self._crawler.getMain(search=query.keyword)
        if not isinstance(result, dict):
            raise EhOnlineError("画廊爬虫返回了未知数据")
        if result.get("error"):
            raise EhOnlineError(str(result["error"]))

        items = tuple(
            gallery
            for gallery in (
                self._to_gallery(raw) for raw in result.get("data", ())
            )
            if gallery is not None
        )
        return OnlineGalleryPage(
            items=items,
            next_cursor=str(result.get("next_url") or ""),
            previous_cursor=str(result.get("prev_url") or ""),
        )

    def load_thumbnail(self, url: str, should_cancel=None) -> bytes:
        if not url:
            return b""
        self._validate_thumbnail_url(url)
        if should_cancel is not None:
            data, _status = self._request_bytes_cancellable(url, should_cancel)
            return data
        response = self._crawler.req.get(url)
        if response is None or not getattr(response, "ok", False):
            return b""
        return bytes(response.content or b"")

    def load_gallery_detail(self, gallery: OnlineGallery) -> OnlineGalleryDetail:
        self._validate_gallery_url(gallery)
        response = self._crawler.req.get(gallery.url)
        if response is None or not getattr(response, "ok", False):
            status = getattr(response, "status_code", "未知")
            raise EhOnlineError(f"画廊详情请求失败（HTTP {status}）")
        content = getattr(response, "content", b"") or getattr(response, "text", "")
        soup = BeautifulSoup(content, "lxml")
        if soup.select_one("#gn") is None or soup.select_one("#gdd") is None:
            raise EhOnlineError("站点返回的页面不是可识别的画廊详情")
        return self._parse_gallery_detail(gallery, soup)

    def load_gallery_preview_page(self, gallery, page_number, should_cancel=None):
        self._validate_gallery_url(gallery)
        page_number = int(page_number)
        page_count = max(1, (int(gallery.page_count) + 19) // 20)
        if not 1 <= page_number <= page_count:
            raise EhOnlineError("画廊预览页码超出范围")
        url = gallery.url if page_number == 1 else f"{gallery.url}?p={page_number - 1}"
        if should_cancel is None:
            response = self._crawler.req.get(url)
            content = (
                getattr(response, "content", b"")
                or getattr(response, "text", "")
                if response is not None else b""
            )
            status = getattr(response, "status_code", "未知")
            ok = response is not None and getattr(response, "ok", False)
        else:
            content, status = self._request_bytes_cancellable(
                url, should_cancel
            )
            ok = bool(content)
        if not ok:
            raise EhOnlineError(f"画廊预览请求失败（HTTP {status}）")
        soup = BeautifulSoup(content, "lxml")
        items = self._parse_gallery_previews(gallery, soup)
        if not items and gallery.page_count:
            raise EhOnlineError("站点返回的画廊预览为空")
        return OnlineGalleryPreviewPage(
            gallery=gallery,
            page_number=page_number,
            page_count=page_count,
            items=items,
        )

    def load_preview_thumbnail(self, preview):
        if not preview.thumbnail_url:
            return b""
        self._validate_online_image_url(preview.thumbnail_url)
        return self._request_image(preview.thumbnail_url)

    def load_gallery_page_image(
        self, gallery, preview, should_cancel=None, progress_callback=None
    ):
        self._validate_gallery_page_url(gallery, preview)
        if should_cancel is None:
            response = self._crawler.req.get(preview.page_url)
            content = (
                getattr(response, "content", b"")
                or getattr(response, "text", "")
                if response is not None else b""
            )
            status = getattr(response, "status_code", "未知")
            ok = response is not None and getattr(response, "ok", False)
        else:
            content, status = self._request_bytes_cancellable(
                preview.page_url, should_cancel
            )
            ok = bool(content)
        if not ok:
            raise EhOnlineError(f"单图页面请求失败（HTTP {status}）")
        soup = BeautifulSoup(content, "lxml")
        image = soup.select_one("#img[src]")
        if image is None:
            raise EhOnlineError("站点返回的单图页面缺少图片地址")
        image_url = str(image.get("src") or "")
        self._validate_online_image_url(image_url)
        data = self._request_image(
            image_url, should_cancel, progress_callback=progress_callback
        )
        if not data:
            raise EhOnlineError("在线图片请求失败")
        return data

    def load_gallery_page_original(
        self, gallery, preview, should_cancel=None, progress_callback=None
    ):
        self._validate_gallery_page_url(gallery, preview)
        if should_cancel is None:
            response = self._crawler.req.get(preview.page_url)
            content = (
                getattr(response, "content", b"")
                or getattr(response, "text", "")
                if response is not None else b""
            )
            status = getattr(response, "status_code", "未知")
            ok = response is not None and getattr(response, "ok", False)
        else:
            content, status = self._request_bytes_cancellable(
                preview.page_url, should_cancel
            )
            ok = bool(content)
        if not ok:
            raise EhOnlineError(f"单图页面请求失败（HTTP {status}）")
        soup = BeautifulSoup(content, "lxml")
        original_url = ""
        for anchor in soup.select("a[href]"):
            candidate = urljoin(preview.page_url, str(anchor.get("href") or ""))
            path = urlparse(candidate).path.casefold()
            if path.startswith("/fullimg/") or path == "/fullimg.php":
                original_url = candidate
                break
        if not original_url:
            raise OriginalImageUnavailableError(
                "单图页面没有可用的原图下载链接"
            )
        self._validate_original_image_url(original_url, gallery, preview)
        data = self._request_image(
            original_url, should_cancel, progress_callback=progress_callback
        )
        if not data:
            raise EhOnlineError("原图请求失败")
        return data

    def _request_image(self, url, should_cancel=None, progress_callback=None):
        if should_cancel is not None:
            data, _status = self._request_bytes_cancellable(
                url, should_cancel, progress_callback=progress_callback
            )
            return data
        response = self._crawler.req.get(url)
        if response is None or not getattr(response, "ok", False):
            return b""
        return bytes(response.content or b"")

    def cancel_pending_requests(self):
        with self._request_lock:
            self._cancel_requested = True
            responses = tuple(self._active_responses.values())
        for response in responses:
            self._abort_response(response, close_response=False)

    def _request_bytes_cancellable(
        self, url, should_cancel, progress_callback=None
    ):
        self._raise_if_request_cancelled(should_cancel)
        configured_timeout = max(3, int(self.settings.timeout_seconds))
        transfer_idle_timeout = min(
            configured_timeout,
            self.STREAM_READ_IDLE_TIMEOUT_SECONDS,
        )
        try:
            response = self._crawler.req.get(
                url,
                stream=True,
                timeout=(transfer_idle_timeout, transfer_idle_timeout),
            )
        except TypeError:
            # Small provider test doubles and third-party adapters may not
            # expose requests' streaming keyword.
            response = self._crawler.req.get(url)
        if response is None:
            return b"", "未知"
        response_key = id(response)
        with self._request_lock:
            if self._cancel_requested:
                self._abort_response(response)
                raise EhOnlineError("请求已取消")
            self._active_responses[response_key] = response
        try:
            status = getattr(response, "status_code", "未知")
            if not getattr(response, "ok", False):
                return b"", status
            iterator = getattr(response, "iter_content", None)
            if iterator is None:
                self._raise_if_request_cancelled(should_cancel)
                return bytes(getattr(response, "content", b"") or b""), status
            chunks = []
            interval_bytes = 0
            last_progress_at = time.monotonic()
            for chunk in iterator(chunk_size=64 * 1024):
                self._raise_if_request_cancelled(should_cancel)
                if chunk:
                    chunk = bytes(chunk)
                    chunks.append(chunk)
                    interval_bytes += len(chunk)
                    now = time.monotonic()
                    elapsed = now - last_progress_at
                    if (
                        progress_callback is not None
                        and elapsed >= self.SPEED_UPDATE_INTERVAL_SECONDS
                    ):
                        progress_callback(interval_bytes / max(0.001, elapsed))
                        interval_bytes = 0
                        last_progress_at = now
            self._raise_if_request_cancelled(should_cancel)
            if progress_callback is not None and interval_bytes:
                elapsed = time.monotonic() - last_progress_at
                progress_callback(interval_bytes / max(0.001, elapsed))
            return b"".join(chunks), status
        finally:
            with self._request_lock:
                self._active_responses.pop(response_key, None)
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _raise_if_request_cancelled(self, should_cancel):
        if self._cancel_requested or (should_cancel and should_cancel()):
            raise EhOnlineError("请求已取消")

    @staticmethod
    def _abort_response(response, close_response=True):
        raw = getattr(response, "raw", None)
        raw_shutdown = getattr(raw, "_sock_shutdown", None)
        if raw_shutdown is not None:
            try:
                raw_shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
        sockets = []
        connection = getattr(raw, "_connection", None)
        if connection is not None:
            sockets.append(getattr(connection, "sock", None))
        original_response = getattr(raw, "_fp", None)
        stream = getattr(original_response, "fp", None)
        buffered_raw = getattr(stream, "raw", None)
        sockets.extend(
            (
                getattr(buffered_raw, "_sock", None),
                getattr(stream, "_sock", None),
            )
        )
        closed_handles = set()
        for active_socket in sockets:
            if active_socket is None:
                continue
            try:
                socket_handle = active_socket.fileno()
            except (OSError, ValueError):
                socket_handle = -1
            if socket_handle >= 0 and socket_handle not in closed_handles:
                closed_handles.add(socket_handle)
                interrupt_socket = None
                try:
                    interrupt_socket = socket.socket(fileno=socket_handle)
                    interrupt_socket.shutdown(socket.SHUT_RDWR)
                except (OSError, ValueError):
                    pass
                finally:
                    if interrupt_socket is not None:
                        try:
                            interrupt_socket.detach()
                        except (OSError, ValueError):
                            pass
                try:
                    socket.close(socket_handle)
                except (OSError, ValueError):
                    pass
        if close_response:
            close = getattr(response, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass

    def set_display_mode(self, mode: str):
        self._ensure_session_configuration()
        result = self._crawler.setDisplayMode(mode)
        if not isinstance(result, dict):
            raise EhOnlineError("画廊爬虫返回了未知的页面模式设置结果")
        if result.get("error"):
            raise EhOnlineError(str(result["error"]))

    def _ensure_session_configuration(self):
        """Load account capability cookies before the first list request."""
        if self._session_configuration_loaded:
            return
        with self._session_configuration_lock:
            if self._session_configuration_loaded:
                return
            self._session_configuration_loaded = True
            if not self.settings.cookie:
                return
            request = getattr(self._crawler, "req", None)
            session = getattr(request, "session", None)
            cookies = getattr(session, "cookies", ())
            try:
                if any(cookie.name == "hath_perks" for cookie in cookies):
                    return
            except (AttributeError, TypeError):
                pass
            getter = getattr(request, "get", None)
            if getter is None:
                return
            try:
                getter(urljoin(self.settings.base_url, "uconfig.php"))
            except Exception:
                # Account preferences improve result counts, but a failed
                # bootstrap must not make the gallery list unavailable.
                return

    def _validate_list_url(self, url: str):
        parsed = urlparse(url)
        expected_host = urlparse(self.settings.base_url).hostname
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise EhOnlineError("拒绝访问站点列表页之外的翻页地址")

    def _validate_gallery_url(self, gallery: OnlineGallery):
        parsed = urlparse(gallery.url)
        expected_host = urlparse(self.settings.base_url).hostname
        match = re.fullmatch(r"/g/(\d+)/([0-9A-Za-z]+)/?", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.query
            or parsed.fragment
            or match is None
            or int(match.group(1)) != int(gallery.gid)
            or match.group(2) != gallery.token
        ):
            raise EhOnlineError("拒绝访问当前站点画廊之外的详情地址")

    def _validate_gallery_page_url(self, gallery, preview):
        parsed = urlparse(preview.page_url)
        expected_host = urlparse(self.settings.base_url).hostname
        match = re.fullmatch(
            r"/s/([0-9A-Za-z]+)/(\d+)-(\d+)/?", parsed.path
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.query
            or parsed.fragment
            or match is None
            or (preview.page_token and match.group(1) != preview.page_token)
            or int(match.group(2)) != int(gallery.gid)
            or int(match.group(3)) != int(preview.page_index) + 1
        ):
            raise EhOnlineError("拒绝访问当前画廊之外的单图页面")

    def _validate_original_image_url(self, url, gallery, preview):
        parsed = urlparse(str(url or ""))
        expected_host = urlparse(self.settings.base_url).hostname
        valid = False
        path_match = re.fullmatch(
            r"/fullimg/(\d+)/(\d+)/[0-9A-Za-z]+/[^/]+",
            parsed.path,
        )
        if path_match is not None and not parsed.query:
            valid = (
                int(path_match.group(1)) == int(gallery.gid)
                and int(path_match.group(2)) == int(preview.page_index) + 1
            )
        elif parsed.path.casefold() == "/fullimg.php":
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                valid = (
                    set(query) == {"gid", "page", "key"}
                    and int(query["gid"][0]) == int(gallery.gid)
                    and int(query["page"][0]) == int(preview.page_index) + 1
                    and bool(re.fullmatch(r"[0-9A-Za-z]+", query["key"][0]))
                )
            except (KeyError, TypeError, ValueError):
                valid = False
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.fragment
            or not valid
        ):
            raise EhOnlineError("拒绝访问当前画廊之外的原图地址")

    @staticmethod
    def _validate_thumbnail_url(url: str):
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        allowed = (
            hostname in {"e-hentai.org", "exhentai.org", "ehgt.org"}
            or hostname.endswith(".e-hentai.org")
            or hostname.endswith(".exhentai.org")
            or hostname.endswith(".ehgt.org")
        )
        if parsed.scheme != "https" or not allowed:
            raise EhOnlineError("拒绝加载未知站点的缩略图")

    @staticmethod
    def _validate_online_image_url(url: str):
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold()
        allowed = (
            hostname in {"e-hentai.org", "exhentai.org", "ehgt.org", "hath.network"}
            or hostname.endswith(".e-hentai.org")
            or hostname.endswith(".exhentai.org")
            or hostname.endswith(".ehgt.org")
            or hostname.endswith(".hath.network")
        )
        if parsed.scheme != "https" or not allowed:
            raise EhOnlineError("拒绝加载未知站点的在线图片")

    @classmethod
    def _parse_gallery_detail(cls, gallery, soup):
        metadata = {}
        for row in soup.select("#gdd tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            key = cells[0].get_text(" ", strip=True).rstrip(":").casefold()
            metadata[key] = cells[1].get_text(" ", strip=True)

        tags = []
        for row in soup.select("#taglist tr"):
            namespace_cell = row.select_one("td.tc")
            namespace = (
                namespace_cell.get_text(" ", strip=True).rstrip(":").casefold()
                if namespace_cell is not None
                else ""
            )
            for anchor in row.select("a[id^='ta_']"):
                value = anchor.get_text(" ", strip=True)
                if not value:
                    continue
                tag = f"{namespace}:{value}" if namespace else value
                if tag not in tags:
                    tags.append(tag)

        comments = tuple(
            comment
            for comment in (
                cls._parse_comment(node, gallery.url)
                for node in soup.select("#cdiv .c1")
            )
            if comment is not None
        )
        rating = cls._first_number(cls._node_text(soup, "#rating_label"))
        rating_count = cls._first_integer(cls._node_text(soup, "#rating_count"))
        page_count = cls._first_integer(metadata.get("length", ""))

        cover_url = ""
        cover = soup.select_one("#gd1 div[style]")
        if cover is not None:
            match = re.search(r"url\((['\"]?)(https://[^)'\"]+)\1\)", cover.get("style", ""))
            if match:
                cover_url = match.group(2)

        title = cls._node_text(soup, "#gn") or gallery.title
        secondary_title = cls._node_text(soup, "#gj")
        category = cls._node_text(soup, "#gdc") or gallery.category
        posted = metadata.get("posted", gallery.posted)
        uploader = cls._node_text(soup, "#gdn") or gallery.uploader
        resolved_page_count = page_count or gallery.page_count
        resolved_rating = rating if rating is not None else gallery.rating
        resolved_tags = tuple(tags) or gallery.tags
        newer_gallery_urls = []
        for anchor in soup.select("#gnd a[href]"):
            candidate = urljoin(gallery.url, str(anchor.get("href") or ""))
            parsed = urlparse(candidate)
            if (
                parsed.scheme != "https"
                or parsed.hostname != urlparse(gallery.url).hostname
                or not re.fullmatch(r"/g/\d+/[0-9a-fA-F]+/?", parsed.path)
                or candidate.rstrip("/") == gallery.url.rstrip("/")
                or candidate in newer_gallery_urls
            ):
                continue
            newer_gallery_urls.append(candidate)
        enriched_gallery = replace(
            gallery,
            title=title,
            category=category,
            posted=posted,
            page_count=resolved_page_count,
            tags=resolved_tags,
            uploader=uploader,
            rating=resolved_rating,
        )

        return OnlineGalleryDetail(
            gallery=enriched_gallery,
            title=title,
            secondary_title=secondary_title,
            category=category,
            cover_url=cover_url or gallery.thumbnail_url,
            posted=posted,
            uploader=uploader,
            visible=metadata.get("visible", ""),
            language=metadata.get("language", ""),
            file_size=metadata.get("file size", ""),
            page_count=resolved_page_count,
            favorited=metadata.get("favorited", ""),
            parent_gallery=metadata.get("parent", ""),
            newer_gallery_urls=tuple(newer_gallery_urls),
            rating=resolved_rating,
            rating_count=rating_count,
            tags=resolved_tags,
            comments=comments,
            previews=cls._parse_gallery_previews(enriched_gallery, soup),
        )

    @classmethod
    def _parse_gallery_previews(cls, gallery, soup):
        previews = []
        for anchor in soup.select("#gdt a[href]"):
            page_url = str(anchor.get("href") or "")
            parsed = urlparse(page_url)
            match = re.fullmatch(
                r"/s/([0-9A-Za-z]+)/(\d+)-(\d+)/?", parsed.path
            )
            if match is None or int(match.group(2)) != int(gallery.gid):
                continue
            page_index = int(match.group(3)) - 1
            node = anchor.select_one("div[style]")
            style = node.get("style", "") if node is not None else ""
            image_match = re.search(
                r"url\((['\"]?)(https://[^)'\"]+)\1\)", style
            )
            width_match = re.search(r"(?:^|;)\s*width\s*:\s*(\d+)px", style)
            height_match = re.search(r"(?:^|;)\s*height\s*:\s*(\d+)px", style)
            position_match = None
            if image_match is not None:
                position_match = re.search(
                    r"(-?\d+)(?:px)?\s+(-?\d+)(?:px)?",
                    style[image_match.end():],
                )
            css_x = int(position_match.group(1)) if position_match else 0
            css_y = int(position_match.group(2)) if position_match else 0
            title = node.get("title", "") if node is not None else ""
            previews.append(
                OnlineGalleryPreview(
                    page_index=page_index,
                    page_url=page_url,
                    thumbnail_url=image_match.group(2) if image_match else "",
                    title=str(title),
                    thumbnail_width=int(width_match.group(1)) if width_match else 0,
                    thumbnail_height=int(height_match.group(1)) if height_match else 0,
                    thumbnail_x=max(0, -css_x),
                    thumbnail_y=max(0, -css_y),
                    page_token=match.group(1),
                )
            )
        return tuple(previews)

    @staticmethod
    def _parse_comment(node, gallery_url):
        body = node.select_one(".c6[id^='comment_']")
        header = node.select_one(".c3")
        if body is None or header is None:
            return None
        comment_id = body.get("id", "").removeprefix("comment_")
        author_link = header.find("a")
        author = author_link.get_text(" ", strip=True) if author_link else ""
        header_text = header.get_text(" ", strip=True)
        posted_match = re.match(r"Posted on\s+(.+?)\s+by:\s*", header_text, re.IGNORECASE)
        posted = posted_match.group(1).strip() if posted_match else header_text
        score_text = RefactoredEhOnlineProvider._node_text(node, ".c5")
        score_match = re.search(r"Score\s*([+-]?\d+)", score_text, re.IGNORECASE)
        score = int(score_match.group(1)) if score_match else None
        text = body.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        gallery_links = []
        seen_links = set()
        for anchor in body.select("a[href]"):
            try:
                target = parse_eh_gallery_url(
                    urljoin(gallery_url, str(anchor.get("href") or ""))
                )
            except EhOnlineError:
                continue
            identity = (target.gid, target.token.casefold())
            if identity in seen_links:
                continue
            seen_links.add(identity)
            gallery_links.append(
                OnlineGalleryLink(
                    gid=target.gid,
                    token=target.token,
                    text=anchor.get_text(" ", strip=True),
                )
            )
        uploader_marker = RefactoredEhOnlineProvider._node_text(node, ".c4")
        return OnlineGalleryComment(
            comment_id=comment_id,
            author=author,
            posted=posted,
            text=text,
            score=score,
            is_uploader="Uploader Comment" in uploader_marker,
            gallery_links=tuple(gallery_links),
        )

    @staticmethod
    def _node_text(node, selector):
        selected = node.select_one(selector)
        return selected.get_text(" ", strip=True) if selected is not None else ""

    @staticmethod
    def _first_integer(value):
        match = re.search(r"[\d,]+", value or "")
        return int(match.group(0).replace(",", "")) if match else 0

    @staticmethod
    def _first_number(value):
        match = re.search(r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)", value or "")
        return float(match.group(1)) if match else None

    @staticmethod
    def _to_gallery(raw):
        gid = raw.get("gid")
        token = raw.get("token")
        url = raw.get("gallery_url")
        if gid is None or not token or not url:
            return None
        tags = []
        for namespace, names in (raw.get("label") or {}).items():
            for name in names or ():
                value = f"{namespace}:{name}" if namespace else str(name)
                if value not in tags:
                    tags.append(value)
        score = raw.get("score")
        try:
            rating = float(score) if score is not None else None
        except (TypeError, ValueError):
            rating = None
        if rating is not None and not 0 <= rating <= 5:
            rating = None
        return OnlineGallery(
            gid=int(gid),
            token=str(token),
            url=str(url),
            title=str(raw.get("title") or raw.get("alt") or gid),
            category=str(raw.get("type") or ""),
            thumbnail_url=str(raw.get("thumb_url") or ""),
            posted=str(raw.get("upload") or ""),
            page_count=int(raw.get("page_num") or 0),
            tags=tuple(tags),
            uploader=str(raw.get("uploader") or ""),
            rating=rating,
            source_mode=str(raw.get("page_mode") or ""),
        )


def create_eh_online_provider(settings: EhOnlineSettings) -> EhOnlineProvider:
    """Compose the bundled adapter around ``eh_tool_refactored.py``."""

    return RefactoredEhOnlineProvider(settings)
