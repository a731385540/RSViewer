import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.domain.manga import MangaItem


CATEGORY_NAMES = {
    1: "其他",
    2: "同人志",
    4: "漫画",
    8: "画师 CG",
    16: "游戏 CG",
    32: "图集",
    64: "Cosplay",
    128: "亚洲写真",
    256: "非 H",
    512: "西方作品",
}

TAG_COLUMNS = (
    "ARTIST",
    "COSPLAYER",
    "CHARACTER",
    "FEMALE",
    "GROUP",
    "LANGUAGE",
    "MALE",
    "MISC",
    "MIXED",
    "OTHER",
    "PARODY",
    "RECLASS",
)

IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


@dataclass(frozen=True)
class EhViewerSpiderInfo:
    start_page_index: int
    gid: int
    gallery_token: str
    preview_page_count: int
    previews_per_page: int
    page_count: int
    page_tokens: Tuple[str, ...]


def natural_page_key(path: Path) -> Tuple[int, object]:
    """让纯数字页名按数值排序，其余文件稳定排在后面。"""
    try:
        return 0, int(path.stem)
    except ValueError:
        return 1, path.name.casefold()


class EhViewerDataSource:
    """Read the EhViewer-compatible gallery index stored in RSViewer SQLite."""

    def __init__(self, database_path: Path, manga_root: Path):
        self.database_path = self._configured_path(database_path)
        self.manga_root = self._configured_path(manga_root)

    def list_local_manga(self) -> List[MangaItem]:
        self._validate_configuration()
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        if not self.manga_root.is_dir():
            raise FileNotFoundError(f"找不到漫画目录：{self.manga_root}")

        folders_by_gid, folders_by_name = self._index_download_folders()
        items = []
        with closing(self._connect_read_only()) as connection:
            connection.row_factory = sqlite3.Row
            query = self._local_manga_query(connection)
            for row in connection.execute(query):
                folder = self._resolve_folder(row, folders_by_gid, folders_by_name)
                if folder is None:
                    continue
                items.append(self._item_from_row(row, folder))

        return items

    def load_local_manga(self, gid: int, folder=None) -> Optional[MangaItem]:
        """Read one registered gallery without rescanning the whole root."""

        self._validate_configuration()
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        gid = int(gid)
        with closing(self._connect_read_only()) as connection:
            connection.row_factory = sqlite3.Row
            query = self._local_manga_query(connection)
            row = connection.execute(
                f"SELECT * FROM ({query}) WHERE GID = ?",
                (gid,),
            ).fetchone()
        if row is None:
            return None

        resolved_folder = Path(folder).resolve() if folder is not None else None
        if resolved_folder is None or not resolved_folder.is_dir():
            dirname = str(row["DIRNAME"] or "").strip()
            candidate = self.manga_root / dirname if dirname else Path()
            if dirname and candidate.is_dir():
                resolved_folder = candidate.resolve()
            else:
                folders_by_gid, folders_by_name = self._index_download_folders()
                resolved_folder = self._resolve_folder(
                    row, folders_by_gid, folders_by_name
                )
        if resolved_folder is None:
            return None
        return self._item_from_row(row, resolved_folder)

    def _item_from_row(self, row: sqlite3.Row, folder: Path) -> MangaItem:
        # List metadata is deliberately lazy; pages are read only in details.
        thumbnail = folder / ".thumb"
        category = int(row["CATEGORY"])
        row_keys = set(row.keys())
        download_complete = (
            int(row["STATE"] or 0) == 3 if "STATE" in row_keys else None
        )
        return MangaItem(
            gid=int(row["GID"]),
            english_title=(row["TITLE"] or "").strip(),
            original_title=(row["TITLE_JPN"] or "").strip(),
            category=category,
            category_name=CATEGORY_NAMES.get(category, f"分类 {category}"),
            primary_label=(row["LABEL"] or "").strip(),
            multiple_labels=(),
            tags=self._collect_tags(row),
            folder=folder,
            cover_path=thumbnail,
            thumbnail_path=thumbnail,
            page_paths=(),
            page_count=0,
            added_time=int(row["TIME"] or 0),
            download_complete=download_complete,
            source_site=(row["SOURCE"] or "exhentai").strip(),
            source_id=(row["REMOTE_ID"] or str(row["GID"])).strip(),
        )

    def load_pages(self, item: MangaItem) -> MangaItem:
        """按需读取单本漫画页面；只在用户打开详情时调用。"""
        pages = self.list_page_files(item.folder)
        spider_info = self.read_spider_info(item)
        if spider_info is None:
            downloaded_count = len(pages)
            total_count = self._registered_page_count(item.gid) or len(pages)
            downloaded_indexes = {
                int(path.stem) - 1
                for path in pages
                if path.stem.isdigit()
                and 1 <= int(path.stem) <= total_count
            }
            complete = (
                downloaded_indexes == set(range(total_count))
                if total_count and item.source_site in {"nhc", "nhn"}
                else None
            )
            gallery_token = item.gallery_token
            page_tokens = item.page_tokens
        else:
            downloaded_indexes = {
                int(path.stem) - 1
                for path in pages
                if path.stem.isdigit()
                and 1 <= int(path.stem) <= spider_info.page_count
            }
            downloaded_count = len(downloaded_indexes)
            total_count = spider_info.page_count
            complete = downloaded_indexes == set(range(total_count))
            gallery_token = spider_info.gallery_token
            page_tokens = spider_info.page_tokens
        return replace(
            item,
            cover_path=pages[0] if pages else item.cover_path,
            thumbnail_path=self._find_thumbnail(item.folder),
            page_paths=pages,
            page_count=total_count,
            downloaded_page_count=downloaded_count,
            download_complete=complete,
            gallery_token=gallery_token,
            page_tokens=page_tokens,
        )

    def _registered_page_count(self, gid):
        try:
            with closing(self._connect_read_only()) as connection:
                row = connection.execute(
                    """
                    SELECT page_count FROM online_gallery_downloads
                    WHERE gid = ? LIMIT 1
                    """,
                    (int(gid),),
                ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return max(0, int(row[0] or 0)) if row is not None else 0

    @staticmethod
    def list_page_files(folder):
        folder = Path(folder)
        if not folder.is_dir():
            return ()
        return tuple(
            sorted(
                (
                    path
                    for path in folder.iterdir()
                    if path.is_file()
                    and path.suffix.casefold() in IMAGE_SUFFIXES
                ),
                key=natural_page_key,
            )
        )

    @staticmethod
    def read_spider_info(item: MangaItem) -> Optional[EhViewerSpiderInfo]:
        sidecar = item.folder / ".ehviewer"
        try:
            lines = sidecar.read_text(encoding="ascii").splitlines()
            if len(lines) < 8 or lines[0] != "VERSION2":
                return None
            start_page_index = int(lines[1], 16)
            gid = int(lines[2])
            gallery_token = lines[3].strip()
            preview_page_count = int(lines[5])
            previews_per_page = int(lines[6])
            page_count = int(lines[7])
        except (OSError, UnicodeError, ValueError):
            return None
        if (
            gid != int(item.gid)
            or not gallery_token
            or page_count <= 0
            or preview_page_count <= 0
            or previews_per_page <= 0
        ):
            return None
        page_tokens = [""] * page_count
        try:
            for line in lines[8:]:
                index_text, token = line.split(maxsplit=1)
                index = int(index_text)
                token = token.strip()
                if not 0 <= index < page_count or not re.fullmatch(
                    r"[0-9a-fA-F]+", token
                ):
                    return None
                page_tokens[index] = token
        except (TypeError, ValueError):
            return None
        if not all(page_tokens):
            return None
        return EhViewerSpiderInfo(
            start_page_index=max(0, start_page_index),
            gid=gid,
            gallery_token=gallery_token,
            preview_page_count=preview_page_count,
            previews_per_page=previews_per_page,
            page_count=page_count,
            page_tokens=tuple(page_tokens),
        )

    @staticmethod
    def read_ehviewer_progress(item: MangaItem) -> Optional[int]:
        """Read the zero-based hexadecimal page index from line 2 of .ehviewer."""
        sidecar = item.folder / ".ehviewer"
        try:
            with sidecar.open("r", encoding="ascii") as stream:
                stream.readline()
                value = stream.readline().strip()
        except (OSError, UnicodeError):
            return None
        if not re.fullmatch(r"[0-9a-fA-F]{1,16}", value):
            return None
        return int(value, 16)

    def find_cover_path(self, item: MangaItem) -> Optional[Path]:
        """按需寻找封面，缩略图缺失时回退到自然排序后的第一页。"""
        thumbnail = self._find_thumbnail(item.folder)
        return thumbnail or self.find_first_page_path(item)

    @staticmethod
    def find_first_page_path(item: MangaItem) -> Optional[Path]:
        """返回单本漫画自然排序后的第一张图片。"""
        first_page = None
        with os.scandir(item.folder) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    continue
                candidate = Path(entry.path)
                if candidate.suffix.casefold() not in IMAGE_SUFFIXES:
                    continue
                if (
                    first_page is None
                    or natural_page_key(candidate) < natural_page_key(first_page)
                ):
                    first_page = candidate
        return first_page

    def list_primary_labels(self) -> List[str]:
        with closing(self._connect_read_only()) as connection:
            return [
                str(row[0]).strip()
                for row in connection.execute(
                    "SELECT LABEL FROM DOWNLOAD_LABELS ORDER BY TIME, _id"
                )
                if row[0] and str(row[0]).strip()
            ]

    def set_primary_label(self, gids, label: str):
        """响应用户分类操作，批量更新目标库中的 DOWNLOADS.LABEL。"""
        normalized = label.strip()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not normalized or not target_gids:
            return
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")

        with closing(sqlite3.connect(str(self.database_path), timeout=15)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM DOWNLOAD_LABELS WHERE LABEL = ? LIMIT 1",
                (normalized,),
            ).fetchone()
            if exists is None:
                raise ValueError(f"目标数据库中不存在分类标签：{normalized}")
            connection.executemany(
                "UPDATE DOWNLOADS SET LABEL = ? WHERE GID = ?",
                ((normalized, gid) for gid in target_gids),
            )
            connection.commit()

    def clear_primary_label(self, gids):
        """Move selected downloads to the unclassified state."""
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not target_gids:
            return
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        with closing(sqlite3.connect(str(self.database_path), timeout=15)) as connection:
            connection.executemany(
                "UPDATE DOWNLOADS SET LABEL = '' WHERE GID = ?",
                ((gid,) for gid in target_gids),
            )
            connection.commit()

    def create_primary_label(self, label: str):
        """在目标库既有 DOWNLOAD_LABELS 表中新增分类，不执行 DDL。"""
        normalized = label.strip()
        if not normalized:
            raise ValueError("分类名称不能为空")
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        with closing(sqlite3.connect(str(self.database_path), timeout=15)) as connection:
            existing = connection.execute(
                """
                SELECT LABEL FROM DOWNLOAD_LABELS
                WHERE LABEL = ? COLLATE NOCASE LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO DOWNLOAD_LABELS(LABEL, TIME) VALUES (?, ?)",
                    (normalized, int(time.time() * 1000)),
                )
            connection.commit()

    def delete_primary_label(self, label: str):
        """Delete one existing classification without changing external schema."""
        normalized = label.strip()
        if not normalized:
            raise ValueError("分类名称不能为空")
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        with closing(sqlite3.connect(str(self.database_path), timeout=15)) as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM DOWNLOAD_LABELS
                WHERE LABEL = ? COLLATE NOCASE LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if exists is None:
                raise ValueError(f"目标数据库中不存在分类标签：{normalized}")
            connection.execute(
                """
                UPDATE DOWNLOADS SET LABEL = ''
                WHERE LABEL = ? COLLATE NOCASE
                """,
                (normalized,),
            )
            connection.execute(
                """
                DELETE FROM DOWNLOAD_LABELS
                WHERE LABEL = ? COLLATE NOCASE
                """,
                (normalized,),
            )
            connection.commit()

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _index_download_folders(self) -> Tuple[Dict[int, Path], Dict[str, Path]]:
        """仅枚举根目录一次，避免为每个漫画目录增加 NAS 往返。"""
        by_gid = {}
        by_name = {}
        with os.scandir(self.manga_root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                folder = Path(entry.path)
                by_name[entry.name] = folder
                gid_text = entry.name.split("-", 1)[0]
                if gid_text.isdigit():
                    by_gid[int(gid_text)] = folder
        return by_gid, by_name

    def _resolve_folder(
        self,
        row: sqlite3.Row,
        folders_by_gid: Dict[int, Path],
        folders_by_name: Dict[str, Path],
    ) -> Optional[Path]:
        dirname = (row["DIRNAME"] or "").strip()
        if dirname and dirname in folders_by_name:
            return folders_by_name[dirname]
        return folders_by_gid.get(int(row["GID"]))

    @staticmethod
    def _configured_path(value) -> Path:
        text = str(value).strip()
        return Path(text).expanduser() if text else Path()

    def _validate_configuration(self):
        if self.database_path == Path():
            raise ValueError("RSViewer 自有漫画数据库路径无效")
        if self.manga_root == Path():
            raise ValueError("请先在设置中选择本地漫画根目录")

    @staticmethod
    def _find_thumbnail(folder: Path) -> Optional[Path]:
        """兼容 EhViewer 不同版本使用过的隐藏缩略图文件名。"""
        exact_names = (".thumb", ".thumd", "thumb", "thumd")
        for name in exact_names:
            candidate = folder / name
            if candidate.is_file():
                return candidate.resolve()

        for candidate in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
            if candidate.is_file() and candidate.suffix.casefold() in {".thumb", ".thumd"}:
                return candidate.resolve()
        return None

    @staticmethod
    def _collect_tags(row: sqlite3.Row) -> Tuple[str, ...]:
        tags = []
        seen = set()
        for column in TAG_COLUMNS:
            raw_value = row[column]
            if not raw_value:
                continue
            for value in str(raw_value).split(","):
                tag = value.strip()
                if not tag:
                    continue
                for searchable_tag in (tag, f"{column.casefold()}:{tag}"):
                    key = searchable_tag.casefold()
                    if key not in seen:
                        seen.add(key)
                        tags.append(searchable_tag)
        return tuple(tags)

    _LOCAL_MANGA_QUERY = """
        SELECT
            downloads.GID,
            downloads.TITLE,
            downloads.TITLE_JPN,
            downloads.CATEGORY,
            downloads.LABEL,
            downloads.TIME,
            COALESCE(source.source, 'exhentai') AS SOURCE,
            COALESCE(source.remote_id, CAST(downloads.GID AS TEXT)) AS REMOTE_ID,
            dirname.DIRNAME,
            tags.ARTIST,
            tags.COSPLAYER,
            tags.CHARACTER,
            tags.FEMALE,
            tags."GROUP" AS "GROUP",
            tags.LANGUAGE,
            tags.MALE,
            tags.MISC,
            tags.MIXED,
            tags.OTHER,
            tags.PARODY,
            tags.RECLASS
        FROM DOWNLOADS AS downloads
        LEFT JOIN DOWNLOAD_DIRNAME AS dirname ON dirname.GID = downloads.GID
        LEFT JOIN Gallery_Tags AS tags ON tags.GID = downloads.GID
        LEFT JOIN gallery_sources AS source ON source.local_gid = downloads.GID
        ORDER BY downloads.TIME DESC
    """

    @classmethod
    def _local_manga_query(cls, connection):
        source_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'gallery_sources'"
        ).fetchone()
        if source_table is not None:
            return cls._LOCAL_MANGA_QUERY
        return (
            cls._LOCAL_MANGA_QUERY.replace(
                "COALESCE(source.source, 'exhentai') AS SOURCE,",
                "'exhentai' AS SOURCE,",
            )
            .replace(
                "COALESCE(source.remote_id, CAST(downloads.GID AS TEXT)) AS REMOTE_ID,",
                "CAST(downloads.GID AS TEXT) AS REMOTE_ID,",
            )
            .replace(
                "        LEFT JOIN gallery_sources AS source ON source.local_gid = downloads.GID\n",
                "",
            )
        )
