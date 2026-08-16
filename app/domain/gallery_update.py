from dataclasses import dataclass, field
from typing import Any, Mapping


UPDATE_WAITING_DOWNLOAD = "waiting_download"
UPDATE_QUEUED = "queued"
UPDATE_RUNNING = "updating"
UPDATE_PAUSED = "paused"
UPDATE_FAILED = "failed"
UPDATE_COMPLETED = "completed"


@dataclass(frozen=True)
class GalleryUpdateRecord:
    source_gid: int
    source_token: str
    site: str
    title: str
    folder: str
    latest_url: str
    target_gid: int = 0
    target_token: str = ""
    status: int = 0
    state: str = UPDATE_QUEUED
    completed_pages: int = 0
    page_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: int = 0
    updated_at: int = 0

