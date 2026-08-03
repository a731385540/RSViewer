import sqlite3
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


def natural_page_key(path: Path) -> Tuple[int, object]:
    """让纯数字页名按数值排序，其余文件稳定排在后面。"""
    try:
        return 0, int(path.stem)
    except ValueError:
        return 1, path.name.casefold()


class EhViewerDataSource:
    """以只读方式读取 EhViewer 数据库及其下载目录。"""

    def __init__(self, database_path: Path, manga_root: Path):
        self.database_path = Path(database_path).resolve()
        self.manga_root = Path(manga_root).resolve()

    def list_local_manga(self) -> List[MangaItem]:
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到漫画数据库：{self.database_path}")
        if not self.manga_root.is_dir():
            raise FileNotFoundError(f"找不到漫画目录：{self.manga_root}")

        folder_index = self._index_download_folders()
        items = []
        with self._connect_read_only() as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute(self._LOCAL_MANGA_QUERY):
                folder = self._resolve_folder(row, folder_index)
                if folder is None:
                    continue

                pages = sorted(
                    (
                        path
                        for path in folder.iterdir()
                        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
                    ),
                    key=natural_page_key,
                )
                if not pages:
                    continue

                items.append(
                    MangaItem(
                        gid=int(row["GID"]),
                        english_title=(row["TITLE"] or "").strip(),
                        original_title=(row["TITLE_JPN"] or "").strip(),
                        category=int(row["CATEGORY"]),
                        category_name=CATEGORY_NAMES.get(
                            int(row["CATEGORY"]), f"分类 {row['CATEGORY']}"
                        ),
                        primary_label=(row["LABEL"] or "").strip(),
                        multiple_labels=(),
                        tags=self._collect_tags(row),
                        folder=folder,
                        cover_path=pages[0],
                        thumbnail_path=self._find_thumbnail(folder),
                        page_paths=tuple(pages),
                        page_count=len(pages),
                    )
                )

        return sorted(items, key=lambda item: item.display_title.casefold())

    def list_primary_labels(self) -> List[str]:
        with self._connect_read_only() as connection:
            return [
                str(row[0]).strip()
                for row in connection.execute(
                    "SELECT LABEL FROM DOWNLOAD_LABELS ORDER BY TIME, _id"
                )
                if row[0] and str(row[0]).strip()
            ]

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"file:{self.database_path.as_posix()}?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True)

    def _index_download_folders(self) -> Dict[int, Path]:
        index = {}
        for folder in self.manga_root.iterdir():
            if not folder.is_dir():
                continue
            gid_text = folder.name.split("-", 1)[0]
            if gid_text.isdigit():
                index[int(gid_text)] = folder.resolve()
        return index

    def _resolve_folder(
        self,
        row: sqlite3.Row,
        folder_index: Dict[int, Path],
    ) -> Optional[Path]:
        dirname = (row["DIRNAME"] or "").strip()
        if dirname:
            exact_path = self.manga_root / dirname
            if exact_path.is_dir():
                return exact_path.resolve()
        return folder_index.get(int(row["GID"]))

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
        ORDER BY downloads.TIME DESC
    """
