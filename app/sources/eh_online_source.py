from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable
from urllib.parse import urlparse
from urllib.request import getproxies

from eh_tool_refactored import EhData

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryPage,
    OnlineGalleryQuery,
)


SITE_BASE_URLS = {
    "ehentai": "https://e-hentai.org/",
    "exhentai": "https://exhentai.org/",
}


class EhOnlineError(RuntimeError):
    """Safe, user-facing error raised by an online provider."""


class EhOnlineProviderNotImplemented(EhOnlineError):
    """Raised until a concrete crawler provider is registered."""


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
        return dict(getproxies())


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

    def load_thumbnail(self, url: str) -> bytes:
        """Optionally return encoded thumbnail bytes for a gallery card."""

        return b""

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

    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        if query.cursor:
            self._validate_list_url(query.cursor)
            result = self._crawler.getUrl(query.cursor)
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

    def load_thumbnail(self, url: str) -> bytes:
        if not url:
            return b""
        self._validate_thumbnail_url(url)
        response = self._crawler.req.get(url)
        if response is None or not getattr(response, "ok", False):
            return b""
        return bytes(response.content or b"")

    def set_display_mode(self, mode: str):
        result = self._crawler.setDisplayMode(mode)
        if not isinstance(result, dict):
            raise EhOnlineError("画廊爬虫返回了未知的页面模式设置结果")
        if result.get("error"):
            raise EhOnlineError(str(result["error"]))

    def _validate_list_url(self, url: str):
        parsed = urlparse(url)
        expected_host = urlparse(self.settings.base_url).hostname
        if parsed.scheme != "https" or parsed.hostname != expected_host:
            raise EhOnlineError("拒绝访问站点列表页之外的翻页地址")

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
