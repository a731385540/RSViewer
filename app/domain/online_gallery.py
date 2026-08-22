from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


DEFAULT_PREVIEW_PAGE_SIZE = 20


def gallery_preview_page_size(gallery) -> int:
    return max(
        1,
        int(
            getattr(gallery, "preview_page_size", 0)
            or DEFAULT_PREVIEW_PAGE_SIZE
        ),
    )


def gallery_preview_page_count(gallery, page_count=None) -> int:
    total = max(
        0,
        int(
            getattr(gallery, "page_count", 0)
            if page_count is None
            else page_count
        ),
    )
    page_size = gallery_preview_page_size(gallery)
    return max(1, (total + page_size - 1) // page_size)


def gallery_preview_page_number(gallery, page_index) -> int:
    return max(0, int(page_index)) // gallery_preview_page_size(gallery) + 1


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
    uploader: str = ""
    rating: Optional[float] = None
    source_mode: str = ""
    preview_page_size: int = 0


@dataclass(frozen=True)
class OnlineGalleryLink:
    """A same-service gallery reference embedded in a comment."""

    gid: int
    token: str
    text: str = ""


@dataclass(frozen=True)
class OnlineGalleryComment:
    """One read-only comment parsed from a gallery HTML page."""

    comment_id: str
    author: str
    posted: str
    text: str
    score: Optional[int] = None
    is_uploader: bool = False
    gallery_links: Tuple[OnlineGalleryLink, ...] = ()


@dataclass(frozen=True)
class OnlineGalleryPreview:
    """One thumbnail and its same-site image-page URL."""

    page_index: int
    page_url: str
    thumbnail_url: str = ""
    title: str = ""
    thumbnail_width: int = 0
    thumbnail_height: int = 0
    thumbnail_x: int = 0
    thumbnail_y: int = 0
    page_token: str = ""


@dataclass(frozen=True)
class OnlineGalleryPreviewPage:
    gallery: OnlineGallery
    page_number: int
    page_count: int
    items: Tuple[OnlineGalleryPreview, ...] = ()


@dataclass(frozen=True)
class OnlineGalleryDetail:
    """Gallery metadata and comments parsed from the site's gallery page."""

    gallery: OnlineGallery
    title: str
    secondary_title: str = ""
    category: str = ""
    cover_url: str = ""
    posted: str = ""
    uploader: str = ""
    visible: str = ""
    language: str = ""
    file_size: str = ""
    page_count: int = 0
    favorited: str = ""
    parent_gallery: str = ""
    newer_gallery_urls: Tuple[str, ...] = ()
    rating: Optional[float] = None
    rating_count: int = 0
    tags: Tuple[str, ...] = ()
    comments: Tuple[OnlineGalleryComment, ...] = ()
    previews: Tuple[OnlineGalleryPreview, ...] = ()


@dataclass(frozen=True)
class OnlineGalleryPage:
    items: Tuple[OnlineGallery, ...]
    next_cursor: str = ""
    previous_cursor: str = ""


@dataclass(frozen=True)
class OnlineGalleryQuery:
    """Provider-neutral query passed from the online resource page."""

    keyword: str = ""
    seek_date: str = ""
    cursor: str = ""
    filters: Mapping[str, Any] = field(default_factory=dict)
