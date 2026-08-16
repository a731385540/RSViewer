from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


TRASH_MOVING = "moving"
TRASHED = "trashed"
TRASH_RESTORING = "restoring"
TRASH_DELETING = "deleting"
TRASH_FAILED = "failed"


@dataclass(frozen=True)
class GalleryTrashRecord:
    gid: int
    title: str
    folder: Path
    dirname: str
    cover_path: Optional[Path] = None
    page_count: int = 0
    database_path: Optional[Path] = None
    manga_root: Optional[Path] = None
    state: str = TRASHED
    external_snapshot: Mapping = field(default_factory=dict)
    error: str = ""
    deleted_at: int = 0
    updated_at: int = 0

    @property
    def key(self):
        return str(self.gid)
