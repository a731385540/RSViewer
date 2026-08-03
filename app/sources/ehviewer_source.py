import os
import sqlite3
from contextlib import closing
from dataclasses import replace
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
            for row in connection.execute(self._LOCAL_MANGA_QUERY):
                folder = self._resolve_folder(row, folders_by_gid, folders_by_name)
                if folder is None:
                    continue

                # 列表阶段不枚举页面。大型 NAS 库逐本扫描会产生数百万次
                # 文件处理，页面只在用户打开某一本详情时按需读取。
                thumbnail = folder / ".thumb"

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
                        cover_path=thumbnail,
                        thumbnail_path=thumbnail,
                        page_paths=(),
                        page_count=0,
                    )
                )

        return sorted(items, key=lambda item: item.display_title.casefold())

    def load_pages(self, item: MangaItem) -> MangaItem:
        """按需读取单本漫画页面；只在用户打开详情时调用。"""
        pages = tuple(
            sorted(
                (
                    path
                    for path in item.folder.iterdir()
                    if path.is_file()
                    and path.suffix.casefold() in IMAGE_SUFFIXES
                ),
                key=natural_page_key,
            )
        )
        return replace(
            item,
            cover_path=pages[0] if pages else item.cover_path,
            thumbnail_path=self._find_thumbnail(item.folder),
            page_paths=pages,
            page_count=len(pages),
        )

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

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"file:{self.database_path.as_posix()}?mode=ro&immutable=1"
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
            raise ValueError("请先在设置中选择 EhViewer 数据库")
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
