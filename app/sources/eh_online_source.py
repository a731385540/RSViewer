from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable
from urllib.parse import urlparse
from urllib.request import getproxies
import requests
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


class UnimplementedEhOnlineProvider(EhOnlineProvider):
    """Safe default that performs no network access."""

    def fetch_page(self, query: OnlineGalleryQuery) -> OnlineGalleryPage:
        raise EhOnlineProviderNotImplemented(
            "在线爬虫尚未接入：请实现 EhOnlineProvider.fetch_page()，"
            "并在 create_eh_online_provider() 中返回你的 provider"
        )


def create_eh_online_provider(settings: EhOnlineSettings) -> EhOnlineProvider:
    """Application composition hook for a user-supplied crawler.

    Replace the returned class here, or inject another factory into
    ``OnlineMangaInterface``. Keeping this function network-free ensures the
    stock application never accesses EH/EX before a crawler is supplied.
    """

    return UnimplementedEhOnlineProvider(settings)


if __name__ == '__main__':
    print(1)