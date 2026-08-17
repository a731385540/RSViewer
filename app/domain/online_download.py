from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Tuple


ONLINE_DOWNLOAD_QUEUED = "queued"
ONLINE_DOWNLOAD_DOWNLOADING = "downloading"
ONLINE_DOWNLOAD_PAUSED = "paused"
ONLINE_DOWNLOAD_FAILED = "failed"
ONLINE_DOWNLOAD_COMPLETED = "completed"

DOWNLOAD_MODE_STANDARD = "standard"
DOWNLOAD_MODE_ORIGINAL_DIRECT = "original_direct"
DOWNLOAD_MODE_ORIGINAL_LOCAL = "original_local"

ORIGINAL_STATE_QUEUED = "queued"
ORIGINAL_STATE_DOWNLOADING = "downloading"
ORIGINAL_STATE_PAUSED = "paused"
ORIGINAL_STATE_FAILED = "failed"
ORIGINAL_STATE_STAGED = "staged"
ORIGINAL_STATE_REPLACING_BASE = "replacing_base"
ORIGINAL_STATE_REPLACING_ORIGINAL = "replacing_original"
ORIGINAL_STATE_ACTIVE = "active"
ORIGINAL_STATE_CLEANING = "cleaning"

ORIGINAL_PAGE_MODE_ORIGINAL = "original"
ORIGINAL_PAGE_MODE_BASE = "base"


def normalize_original_page_modes(
    modes: Iterable[str],
    page_count: int,
    completed_pages: int = 0,
    legacy_fallback: bool = False,
) -> Tuple[str, ...]:
    total = max(0, int(page_count))
    normalized = [
        str(mode) if str(mode) in {
            ORIGINAL_PAGE_MODE_ORIGINAL,
            ORIGINAL_PAGE_MODE_BASE,
        } else ""
        for mode in tuple(modes or ())[:total]
    ]
    normalized.extend("" for _ in range(total - len(normalized)))
    if not any(normalized):
        inferred = (
            ORIGINAL_PAGE_MODE_BASE
            if legacy_fallback
            else ORIGINAL_PAGE_MODE_ORIGINAL
        )
        for index in range(min(total, max(0, int(completed_pages)))):
            normalized[index] = inferred
    return tuple(normalized)


@dataclass(frozen=True)
class OnlineGalleryDownloadRecord:
    gid: int
    site: str
    token: str
    title: str
    dirname: str
    page_count: int
    completed_pages: int = 0
    state: str = ONLINE_DOWNLOAD_QUEUED
    download_mode: str = DOWNLOAD_MODE_STANDARD
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class GalleryOriginalState:
    gid: int
    site: str
    token: str
    dirname: str
    mode: str
    state: str = ORIGINAL_STATE_QUEUED
    completed_pages: int = 0
    page_count: int = 0
    fallback_to_standard: bool = False
    page_modes: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: int = 0
    updated_at: int = 0

    @property
    def original_page_count(self) -> int:
        return self.page_modes.count(ORIGINAL_PAGE_MODE_ORIGINAL)

    @property
    def base_page_count(self) -> int:
        return self.page_modes.count(ORIGINAL_PAGE_MODE_BASE)


@dataclass(frozen=True)
class GallerySyncRecord:
    gid: int
    site: str
    token: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    updated_at: int = 0
