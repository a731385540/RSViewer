from dataclasses import dataclass, field
from typing import Any, Mapping


ONLINE_DOWNLOAD_QUEUED = "queued"
ONLINE_DOWNLOAD_DOWNLOADING = "downloading"
ONLINE_DOWNLOAD_PAUSED = "paused"
ONLINE_DOWNLOAD_FAILED = "failed"
ONLINE_DOWNLOAD_COMPLETED = "completed"


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
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: int = 0
    updated_at: int = 0


@dataclass(frozen=True)
class GallerySyncRecord:
    gid: int
    site: str
    token: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    updated_at: int = 0
