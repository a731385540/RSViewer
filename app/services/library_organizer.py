import ctypes
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtGui import QImageReader

from app.domain.online_download import (
    DOWNLOAD_MODE_STANDARD,
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_PAUSED,
    OnlineGalleryDownloadRecord,
)
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreview,
)
from app.repositories.ehviewer_download_repository import (
    EH_STATE_FAILED,
    EH_STATE_FINISHED,
    EhViewerDownloadRepository,
)
from app.services.online_download_builder import online_detail_metadata
from app.sources.eh_online_source import SITE_BASE_URLS
from app.sources.ehviewer_source import IMAGE_SUFFIXES


_GID_PREFIX_RE = re.compile(r"^(\d+)(?:-|$)")
_TOKEN_RE = re.compile(r"[0-9a-fA-F]+")


@dataclass(frozen=True)
class OrganizerSidecar:
    gid: int
    gallery_token: str
    page_count: int
    page_tokens: Tuple[str, ...]


@dataclass(frozen=True)
class OrphanGalleryFolder:
    folder: Path
    dirname: str
    title: str
    gid: int = 0
    gallery_token: str = ""
    site: str = "ehentai"
    page_count: int = 0
    downloaded_pages: int = 0
    cover_path: Optional[Path] = None
    syncable: bool = False
    issue: str = ""

    @property
    def key(self):
        return str(self.folder)

    @property
    def complete(self):
        return self.page_count > 0 and self.downloaded_pages >= self.page_count


@dataclass(frozen=True)
class OrganizerActionResult:
    succeeded: Tuple[OrphanGalleryFolder, ...] = ()
    failed: Tuple[Tuple[OrphanGalleryFolder, str], ...] = ()


def read_organizer_sidecar(folder) -> OrganizerSidecar:
    sidecar = Path(folder) / ".ehviewer"
    try:
        lines = sidecar.read_text(encoding="ascii").splitlines()
    except FileNotFoundError as error:
        raise ValueError("缺少 .ehviewer") from error
    except (OSError, UnicodeError) as error:
        raise ValueError("无法读取 .ehviewer") from error
    try:
        if len(lines) < 8 or lines[0] != "VERSION2":
            raise ValueError(".ehviewer 不是完整的 VERSION2 格式")
        gid = int(lines[2])
        gallery_token = lines[3].strip()
        page_count = int(lines[7])
    except (TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith(".ehviewer"):
            raise
        raise ValueError(".ehviewer 基础字段无效") from error
    if gid <= 0 or page_count <= 0 or not gallery_token:
        raise ValueError(".ehviewer 的 GID、token 或页数无效")
    page_tokens = [""] * page_count
    try:
        for line in lines[8:]:
            index_text, page_token = line.split(maxsplit=1)
            index = int(index_text)
            page_token = page_token.strip()
            if (
                not 0 <= index < page_count
                or page_tokens[index]
                or not _TOKEN_RE.fullmatch(page_token)
            ):
                raise ValueError
            page_tokens[index] = page_token
    except (TypeError, ValueError) as error:
        raise ValueError(".ehviewer 的页面 token 无效") from error
    if not all(page_tokens):
        raise ValueError(".ehviewer 的页面 token 不完整")
    return OrganizerSidecar(
        gid=gid,
        gallery_token=gallery_token,
        page_count=page_count,
        page_tokens=tuple(page_tokens),
    )


def scan_orphan_gallery_folders(
    database_path,
    manga_root,
    user_repository,
    default_site="ehentai",
):
    database_path = Path(str(database_path)).expanduser()
    manga_root = Path(str(manga_root)).expanduser()
    if not database_path.is_file():
        raise FileNotFoundError(f"找不到漫画数据库：{database_path}")
    if not manga_root.is_dir():
        raise FileNotFoundError(f"找不到漫画目录：{manga_root}")
    registered_gids, registered_dirnames = _registered_downloads(database_path)
    results = []
    with os.scandir(manga_root) as entries:
        folders = sorted(
            (entry for entry in entries if entry.is_dir(follow_symlinks=False)),
            key=lambda entry: entry.name.casefold(),
        )
    for entry in folders:
        if entry.name.casefold() in registered_dirnames:
            continue
        folder = Path(entry.path)
        prefix = _folder_gid(entry.name)
        try:
            sidecar = read_organizer_sidecar(folder)
            issue = ""
        except ValueError as error:
            sidecar = None
            issue = str(error)
        gid = sidecar.gid if sidecar is not None else prefix
        if gid and gid in registered_gids:
            continue
        download_record = (
            user_repository.online_gallery_download(gid) if gid else None
        )
        sync_record = user_repository.gallery_sync_record(gid) if gid else None
        metadata = {}
        if download_record is not None:
            metadata.update(dict(download_record.metadata or {}))
        if sync_record is not None:
            metadata.update(dict(sync_record.metadata or {}))
        site = str(
            (sync_record.site if sync_record is not None else "")
            or (download_record.site if download_record is not None else "")
            or default_site
        )
        if site not in SITE_BASE_URLS:
            site = default_site if default_site in SITE_BASE_URLS else "ehentai"
        title = str(
            (download_record.title if download_record is not None else "")
            or metadata.get("secondary_title")
            or _folder_title(entry.name, gid)
        )
        page_count = sidecar.page_count if sidecar is not None else 0
        downloaded_pages, cover_path = _local_page_summary(folder, page_count)
        results.append(
            OrphanGalleryFolder(
                folder=folder,
                dirname=entry.name,
                title=title,
                gid=int(gid or 0),
                gallery_token=(sidecar.gallery_token if sidecar is not None else ""),
                site=site,
                page_count=page_count,
                downloaded_pages=downloaded_pages,
                cover_path=cover_path,
                syncable=sidecar is not None,
                issue=issue,
            )
        )
    return tuple(results)


def sync_orphan_gallery_folder(
    entry,
    database_path,
    manga_root,
    user_repository,
):
    folder = _validated_root_child(entry.folder, manga_root)
    sidecar = read_organizer_sidecar(folder)
    completed_pages, _cover = _local_page_summary(
        folder,
        sidecar.page_count,
        validate_images=True,
    )
    existing = user_repository.online_gallery_download(sidecar.gid)
    sync_record = user_repository.gallery_sync_record(sidecar.gid)
    detail = _build_local_detail(
        entry,
        sidecar,
        existing,
        sync_record,
    )
    complete = completed_pages >= sidecar.page_count
    external_repository = EhViewerDownloadRepository(database_path, manga_root)
    external_repository.import_existing_folder(
        detail,
        folder,
        EH_STATE_FINISHED if complete else EH_STATE_FAILED,
    )
    metadata = online_detail_metadata(detail)
    if existing is not None:
        metadata.update(dict(existing.metadata or {}))
    if sync_record is not None:
        metadata.update(dict(sync_record.metadata or {}))
    metadata["organized_from_local_folder"] = True
    original = user_repository.gallery_original_state(sidecar.gid)
    download_mode = (
        existing.download_mode
        if existing is not None
        else (original.mode if original is not None else DOWNLOAD_MODE_STANDARD)
    )
    comments = user_repository.online_gallery_comments(sidecar.gid)
    try:
        user_repository.save_online_gallery_download(
            OnlineGalleryDownloadRecord(
                gid=sidecar.gid,
                site=(entry.site if entry.site in SITE_BASE_URLS else "ehentai"),
                token=sidecar.gallery_token,
                title=detail.title,
                dirname=folder.name,
                page_count=sidecar.page_count,
                completed_pages=completed_pages,
                state=(
                    ONLINE_DOWNLOAD_COMPLETED if complete else ONLINE_DOWNLOAD_PAUSED
                ),
                download_mode=download_mode,
                metadata=metadata,
                error="" if complete else "本地文件不完整，可继续补齐",
                created_at=(existing.created_at if existing is not None else 0),
            ),
            comments,
        )
    except Exception:
        external_repository.remove_imported_folder(sidecar.gid, folder)
        raise


def recycle_orphan_gallery_folder(folder, manga_root):
    folder = _validated_root_child(folder, manga_root)
    if os.name != "nt":
        raise RuntimeError("当前平台不支持 Windows 回收站")
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.wFunc = 3
    operation.pFrom = str(folder) + "\0\0"
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(int(result), "无法把目录移入 Windows 回收站")


def _registered_downloads(database_path):
    uri = f"file:{Path(database_path).resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"DOWNLOADS", "DOWNLOAD_DIRNAME"}.issubset(tables):
            raise ValueError("EhViewer 数据库缺少 DOWNLOADS 或 DOWNLOAD_DIRNAME")
        gids = {int(row[0]) for row in connection.execute("SELECT GID FROM DOWNLOADS")}
        dirnames = {
            str(row[0]).casefold()
            for row in connection.execute(
                """
                SELECT d.DIRNAME FROM DOWNLOAD_DIRNAME d
                INNER JOIN DOWNLOADS g ON g.GID = d.GID
                WHERE d.DIRNAME IS NOT NULL AND d.DIRNAME != ''
                """
            )
        }
    return gids, dirnames


def _local_page_summary(folder, page_count, validate_images=False):
    indexes = set()
    cover = None
    first_page_index = None
    for name in (".thumb", ".thumd", "thumb", "thumd"):
        candidate = folder / name
        if candidate.is_file():
            cover = candidate
            break
    with os.scandir(folder) as entries:
        for item in entries:
            if not item.is_file(follow_symlinks=False):
                continue
            path = Path(item.path)
            if path.suffix.casefold() not in IMAGE_SUFFIXES or not path.stem.isdigit():
                continue
            index = int(path.stem)
            if index > 0 and (not page_count or index <= page_count):
                if validate_images:
                    reader = QImageReader(str(path))
                    if (
                        path.stat().st_size <= 0
                        or not reader.canRead()
                        or not reader.size().isValid()
                    ):
                        continue
                indexes.add(index)
                if cover is None or (
                    first_page_index is not None and index < first_page_index
                ):
                    cover = path
                    first_page_index = index
    return len(indexes), cover


def _build_local_detail(entry, sidecar, existing, sync_record):
    metadata = {}
    if existing is not None:
        metadata.update(dict(existing.metadata or {}))
    if sync_record is not None:
        metadata.update(dict(sync_record.metadata or {}))
    site = str(entry.site or "ehentai")
    if site not in SITE_BASE_URLS:
        site = "ehentai"
    raw_tags = metadata.get("tags") or ()
    if isinstance(raw_tags, str):
        raw_tags = (raw_tags,)
    rating = metadata.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    gallery = OnlineGallery(
        gid=sidecar.gid,
        token=sidecar.gallery_token,
        url=f"{SITE_BASE_URLS[site]}g/{sidecar.gid}/{sidecar.gallery_token}/",
        title=entry.title,
        category=str(metadata.get("category") or "Misc"),
        thumbnail_url=str(metadata.get("cover_url") or ""),
        posted=str(metadata.get("posted") or ""),
        page_count=sidecar.page_count,
        tags=tuple(str(tag) for tag in raw_tags if str(tag)),
        uploader=str(metadata.get("uploader") or ""),
        rating=rating,
    )
    previews = tuple(
        OnlineGalleryPreview(
            page_index=index,
            page_url=(
                f"{SITE_BASE_URLS[site]}s/{page_token}/"
                f"{sidecar.gid}-{index + 1}"
            ),
            page_token=page_token,
        )
        for index, page_token in enumerate(sidecar.page_tokens)
    )
    return OnlineGalleryDetail(
        gallery=gallery,
        title=entry.title,
        secondary_title=str(metadata.get("secondary_title") or ""),
        category=gallery.category,
        cover_url=gallery.thumbnail_url,
        posted=gallery.posted,
        uploader=gallery.uploader,
        visible=str(metadata.get("visible") or ""),
        language=str(metadata.get("language") or ""),
        file_size=str(metadata.get("file_size") or ""),
        page_count=sidecar.page_count,
        favorited=str(metadata.get("favorited") or ""),
        parent_gallery=str(metadata.get("parent_gallery") or ""),
        newer_gallery_urls=tuple(metadata.get("newer_gallery_urls") or ()),
        rating=gallery.rating,
        rating_count=max(0, int(metadata.get("rating_count") or 0)),
        tags=gallery.tags,
        previews=previews,
    )


def _validated_root_child(folder, manga_root):
    root = Path(str(manga_root)).expanduser().resolve()
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError("目标不能是符号链接目录")
    folder = folder.resolve()
    if folder == root or folder.parent != root:
        raise ValueError("目标不是漫画根目录下的实体子目录")
    if not folder.is_dir():
        raise FileNotFoundError(f"资源目录不存在：{folder}")
    return folder


def _folder_gid(dirname):
    match = _GID_PREFIX_RE.match(dirname)
    return int(match.group(1)) if match else 0


def _folder_title(dirname, gid):
    prefix = f"{gid}-" if gid else ""
    return dirname[len(prefix):] if prefix and dirname.startswith(prefix) else dirname
