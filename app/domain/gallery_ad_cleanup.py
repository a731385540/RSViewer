from dataclasses import dataclass
from typing import Mapping, Tuple


AD_CLEANUP_MOVING = "moving"
AD_CLEANUP_STAGED = "staged"
AD_CLEANUP_RESTORING = "restoring"
AD_CLEANUP_DELETING = "deleting"
AD_CLEANUP_CLEANED = "cleaned"
AD_CLEANUP_FAILED = "failed"

AD_ACTION_STAGE = "stage"
AD_ACTION_RESTORE = "restore"
AD_ACTION_DELETE = "delete"


@dataclass(frozen=True)
class GalleryAdCleanupRecord:
    """Persistent, version-specific removal of a gallery's trailing ad pages."""

    gid: int
    dirname: str
    cutoff_page_index: int
    page_count: int
    state: str = AD_CLEANUP_MOVING
    pending_action: str = AD_ACTION_STAGE
    manifest: Tuple[Mapping[str, str], ...] = ()
    error: str = ""
    created_at: int = 0
    updated_at: int = 0

    @property
    def retained_page_count(self) -> int:
        return min(max(0, int(self.cutoff_page_index)), max(0, int(self.page_count)))

    @property
    def removed_page_count(self) -> int:
        return max(0, int(self.page_count) - self.retained_page_count)
