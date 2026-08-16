import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from app.domain.gallery_trash import (
    GalleryTrashRecord,
    TRASH_DELETING,
    TRASH_FAILED,
    TRASH_MOVING,
    TRASH_RESTORING,
    TRASHED,
)


@dataclass(frozen=True)
class GalleryTrashActionResult:
    succeeded: Tuple[object, ...] = ()
    failed: Tuple[Tuple[object, str], ...] = ()


def trash_local_gallery(item, external_repository, user_repository):
    gid = int(item.gid)
    existing = user_repository.gallery_trash(gid)
    if existing is None:
        snapshot = external_repository.capture_gallery_snapshot(gid, item.folder)
        now = time.time_ns()
        record = GalleryTrashRecord(
            gid=gid,
            title=str(item.display_title),
            folder=Path(item.folder).resolve(),
            dirname=Path(item.folder).name,
            cover_path=Path(item.cover_image_path),
            page_count=max(0, int(item.page_count or 0)),
            database_path=Path(external_repository.database_path).resolve(),
            manga_root=Path(external_repository.manga_root).resolve(),
            state=TRASH_MOVING,
            external_snapshot=snapshot,
            deleted_at=now,
            updated_at=now,
        )
        user_repository.save_gallery_trash(record)
    else:
        record = existing
        user_repository.update_gallery_trash_state(gid, TRASH_MOVING, "")
    try:
        external_repository.remove_gallery_to_trash(
            gid, record.folder, record.external_snapshot
        )
        user_repository.update_gallery_trash_state(gid, TRASHED, "")
    except Exception as error:
        user_repository.update_gallery_trash_state(gid, TRASH_FAILED, str(error))
        raise
    return user_repository.gallery_trash(gid)


def restore_trashed_gallery(record, external_repository, user_repository):
    gid = int(record.gid)
    user_repository.update_gallery_trash_state(gid, TRASH_RESTORING, "")
    try:
        external_repository.restore_gallery_from_trash(
            gid, record.folder, record.external_snapshot
        )
        user_repository.delete_gallery_trash_record(gid)
    except Exception as error:
        user_repository.update_gallery_trash_state(gid, TRASH_FAILED, str(error))
        raise


def permanently_delete_trashed_gallery(
    record,
    external_repository,
    user_repository,
    manga_root,
):
    gid = int(record.gid)
    user_repository.update_gallery_trash_state(gid, TRASH_DELETING, "")
    try:
        external_repository.remove_gallery_to_trash(
            gid, record.folder, record.external_snapshot
        )
        folder = _validated_trash_folder(record.folder, manga_root)
        if folder.exists():
            shutil.rmtree(str(folder))
        user_repository.purge_gallery(gid)
    except Exception as error:
        if user_repository.gallery_trash(gid) is not None:
            user_repository.update_gallery_trash_state(gid, TRASH_FAILED, str(error))
        raise


def _validated_trash_folder(folder, manga_root):
    root = Path(str(manga_root)).expanduser().resolve()
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError("回收站目录不能是符号链接")
    folder = folder.resolve()
    if folder == root or folder.parent != root:
        raise ValueError("回收站目录不在漫画根目录的直接子目录中")
    if folder.exists() and not folder.is_dir():
        raise ValueError("回收站目标不是目录")
    return folder
