from dataclasses import dataclass
from typing import Tuple


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
    next_url: str = ""
    previous_url: str = ""
