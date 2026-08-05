from dataclasses import dataclass, field
from typing import Any, Mapping, Tuple


@dataclass(frozen=True)
class OnlineGallery:
    """Metadata exposed by an E-Hentai compatible gallery list."""

    gid: int
    token: str
    url: str
    title: str
    category: str = ""
    thumbnail_url: str = ""
    posted: str = ""
    page_count: int = 0
    tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OnlineGalleryPage:
    items: Tuple[OnlineGallery, ...]
    next_cursor: str = ""
    previous_cursor: str = ""


@dataclass(frozen=True)
class OnlineGalleryQuery:
    """Provider-neutral query passed from the online resource page."""

    keyword: str = ""
    cursor: str = ""
    page_number: int = 1
    filters: Mapping[str, Any] = field(default_factory=dict)
