import math
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from app.sources.ehviewer_source import IMAGE_SUFFIXES


EH_STATE_DOWNLOADING = 2
EH_STATE_FINISHED = 3
EH_STATE_FAILED = 4

CATEGORY_VALUES = {
    "misc": 1,
    "doujinshi": 2,
    "manga": 4,
    "artist cg": 8,
    "game cg": 16,
    "image set": 32,
    "cosplay": 64,
    "asian porn": 128,
    "non-h": 256,
    "western": 512,
}

TAG_COLUMNS = {
    "artist": "ARTIST",
    "cosplayer": "COSPLAYER",
    "character": "CHARACTER",
    "female": "FEMALE",
    "group": "GROUP",
    "language": "LANGUAGE",
    "male": "MALE",
    "misc": "MISC",
    "mixed": "MIXED",
    "other": "OTHER",
    "parody": "PARODY",
    "reclass": "RECLASS",
}

_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class EhViewerDownloadRepository:
    """Write one explicit online download using EhViewer's existing schema."""

    REQUIRED_TABLES = {"DOWNLOADS", "DOWNLOAD_DIRNAME", "Gallery_Tags"}

    def __init__(self, database_path, manga_root):
        self.database_path = Path(str(database_path)).expanduser()
        self.manga_root = Path(str(manga_root)).expanduser()

    def prepare_download(self, detail):
        self._validate_targets()
        dirname = self._resolve_dirname(detail)
        folder = self._safe_folder(dirname)
        folder.mkdir(parents=False, exist_ok=True)
        now = int(time.time() * 1000)
        with closing(sqlite3.connect(str(self.database_path), timeout=30)) as connection:
            self._validate_schema(connection)
            existing = connection.execute(
                "SELECT LABEL, TIME, ARCHIVE_URI FROM DOWNLOADS WHERE GID = ?",
                (int(detail.gallery.gid),),
            ).fetchone()
            label = str(existing[0] or "") if existing else ""
            added_time = int(existing[1]) if existing else now
            archive_uri = existing[2] if existing else None
            values = self._download_values(
                detail,
                EH_STATE_DOWNLOADING,
                label,
                added_time,
                archive_uri,
            )
            if existing:
                connection.execute(
                    """
                    UPDATE DOWNLOADS SET
                        TOKEN = ?, TITLE = ?, TITLE_JPN = ?, THUMB = ?,
                        CATEGORY = ?, POSTED = ?, UPLOADER = ?, RATING = ?,
                        SIMPLE_LANGUAGE = ?, STATE = ?, LEGACY = ?, TIME = ?,
                        LABEL = ?, ARCHIVE_URI = ?
                    WHERE GID = ?
                    """,
                    values[1:] + (values[0],),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO DOWNLOADS(
                        GID, TOKEN, TITLE, TITLE_JPN, THUMB, CATEGORY,
                        POSTED, UPLOADER, RATING, SIMPLE_LANGUAGE, STATE,
                        LEGACY, TIME, LABEL, ARCHIVE_URI
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            connection.execute(
                """
                INSERT INTO DOWNLOAD_DIRNAME(GID, DIRNAME) VALUES (?, ?)
                ON CONFLICT(GID) DO UPDATE SET DIRNAME = excluded.DIRNAME
                """,
                (int(detail.gallery.gid), dirname),
            )
            self._upsert_tags(connection, detail, now)
            connection.commit()
        return dirname, folder

    def sync_metadata(self, detail):
        """Refresh an existing local row without changing its download state."""
        self._validate_targets()
        gid = int(detail.gallery.gid)
        now = int(time.time() * 1000)
        with closing(sqlite3.connect(str(self.database_path), timeout=30)) as connection:
            self._validate_schema(connection)
            existing = connection.execute(
                """
                SELECT STATE, LEGACY, TIME, LABEL, ARCHIVE_URI
                FROM DOWNLOADS WHERE GID = ?
                """,
                (gid,),
            ).fetchone()
            if existing is None:
                raise ValueError("本地 EhViewer 数据库中不存在这个画廊")
            values = list(
                self._download_values(
                    detail,
                    int(existing[0]),
                    str(existing[3] or ""),
                    int(existing[2]),
                    existing[4],
                )
            )
            values[11] = int(existing[1])
            connection.execute(
                """
                UPDATE DOWNLOADS SET
                    TOKEN = ?, TITLE = ?, TITLE_JPN = ?, THUMB = ?,
                    CATEGORY = ?, POSTED = ?, UPLOADER = ?, RATING = ?,
                    SIMPLE_LANGUAGE = ?, STATE = ?, LEGACY = ?, TIME = ?,
                    LABEL = ?, ARCHIVE_URI = ?
                WHERE GID = ?
                """,
                tuple(values[1:]) + (gid,),
            )
            self._upsert_tags(connection, detail, now)
            connection.commit()

    def mark_state(self, gid, state):
        if not self.database_path.is_file():
            return
        with closing(sqlite3.connect(str(self.database_path), timeout=30)) as connection:
            connection.execute(
                "UPDATE DOWNLOADS SET STATE = ? WHERE GID = ?",
                (int(state), int(gid)),
            )
            connection.commit()

    def write_thumbnail(self, folder, data):
        if data:
            self._atomic_write(Path(folder) / ".thumb", data)

    def write_spider_info(self, folder, detail, page_tokens):
        page_count = int(detail.page_count)
        expected = set(range(page_count))
        if set(page_tokens) != expected:
            missing = len(expected.difference(page_tokens))
            raise ValueError(f"画廊页面 ID 不完整，缺少 {missing} 页")
        start_page = self._existing_start_page(Path(folder), detail.gallery.gid)
        lines = [
            "VERSION2",
            f"{start_page:08x}",
            str(int(detail.gallery.gid)),
            str(detail.gallery.token),
            "1",
            str(max(1, math.ceil(page_count / 20))),
            "20",
            str(page_count),
        ]
        lines.extend(f"{index} {page_tokens[index]}" for index in range(page_count))
        self._atomic_write(
            Path(folder) / ".ehviewer",
            ("\n".join(lines) + "\n").encode("ascii"),
        )

    def find_page_file(self, folder, page_index):
        stem = f"{int(page_index) + 1:08d}"
        folder = Path(folder)
        for suffix in IMAGE_SUFFIXES:
            candidate = folder / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def write_page(self, folder, page_index, extension, data):
        extension = str(extension or "").casefold()
        if extension not in IMAGE_SUFFIXES:
            extension = ".jpg"
        folder = Path(folder)
        stem = f"{int(page_index) + 1:08d}"
        target = folder / f"{stem}{extension}"
        self._atomic_write(target, data)
        for suffix in IMAGE_SUFFIXES:
            candidate = folder / f"{stem}{suffix}"
            if candidate != target and candidate.is_file():
                candidate.unlink()
        return target

    def _resolve_dirname(self, detail):
        gid = int(detail.gallery.gid)
        with closing(sqlite3.connect(str(self.database_path), timeout=30)) as connection:
            row = connection.execute(
                "SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = ?",
                (gid,),
            ).fetchone()
        if row and str(row[0] or "").strip():
            return self._sanitize_dirname(str(row[0]))
        prefix = f"{gid}-"
        existing = [
            child.name
            for child in self.manga_root.iterdir()
            if child.is_dir() and child.name.startswith(prefix)
        ]
        if existing:
            return max(existing, key=len)
        title = detail.secondary_title or detail.title or str(gid)
        return self._sanitize_dirname(f"{gid}-{title}")

    def _sanitize_dirname(self, value):
        value = _INVALID_FILENAME_RE.sub("_", str(value)).strip().rstrip(". ")
        value = " ".join(value.split())
        if not value:
            value = "gallery"
        if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            value = "_" + value
        max_length = max(48, min(180, 235 - len(str(self.manga_root.resolve()))))
        return value[:max_length].rstrip(". ") or "gallery"

    def _safe_folder(self, dirname):
        root = self.manga_root.resolve()
        folder = (root / dirname).resolve()
        if os.path.commonpath((str(root), str(folder))) != str(root):
            raise ValueError("下载目录超出设置中的本地画廊根目录")
        return folder

    def _validate_targets(self):
        if not self.database_path.is_file():
            raise FileNotFoundError(f"找不到 EhViewer 数据库：{self.database_path}")
        if not self.manga_root.is_dir():
            raise FileNotFoundError(f"找不到本地画廊目录：{self.manga_root}")

    @classmethod
    def _validate_schema(cls, connection):
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = cls.REQUIRED_TABLES.difference(tables)
        if missing:
            raise ValueError(f"EhViewer 数据库缺少表：{', '.join(sorted(missing))}")
        download_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(DOWNLOADS)")
        }
        required_columns = {
            "GID", "TOKEN", "TITLE", "TITLE_JPN", "THUMB", "CATEGORY",
            "POSTED", "UPLOADER", "RATING", "SIMPLE_LANGUAGE", "STATE",
            "LEGACY", "TIME", "LABEL", "ARCHIVE_URI",
        }
        if not required_columns.issubset(download_columns):
            raise ValueError("EhViewer DOWNLOADS 表结构与当前兼容格式不一致")

    @staticmethod
    def _download_values(detail, state, label, added_time, archive_uri):
        gallery = detail.gallery
        return (
            int(gallery.gid),
            str(gallery.token),
            str(detail.title or gallery.title),
            str(detail.secondary_title or ""),
            str(detail.cover_url or gallery.thumbnail_url or ""),
            CATEGORY_VALUES.get(str(detail.category or gallery.category).casefold(), 1),
            str(detail.posted or gallery.posted or ""),
            str(detail.uploader or gallery.uploader or ""),
            float(detail.rating if detail.rating is not None else gallery.rating or 0),
            EhViewerDownloadRepository._simple_language(detail.tags),
            int(state),
            0,
            int(added_time),
            str(label or ""),
            archive_uri,
        )

    @staticmethod
    def _simple_language(tags):
        for tag in tags:
            namespace, separator, value = str(tag).partition(":")
            if separator and namespace.casefold() == "language":
                normalized = value.strip().casefold()
                if normalized not in {"translated", "rewrite"}:
                    return normalized
        return None

    @staticmethod
    def _upsert_tags(connection, detail, now):
        grouped = {column: [] for column in TAG_COLUMNS.values()}
        for raw_tag in detail.tags:
            namespace, separator, value = str(raw_tag).partition(":")
            column = TAG_COLUMNS.get(namespace.strip().casefold()) if separator else "OTHER"
            value = value.strip() if separator else str(raw_tag).strip()
            if column and value and value not in grouped[column]:
                grouped[column].append(value)
        values = {column: ",".join(items) or None for column, items in grouped.items()}
        gid = int(detail.gallery.gid)
        existing = connection.execute(
            "SELECT CREATE_TIME FROM Gallery_Tags WHERE GID = ?",
            (gid,),
        ).fetchone()
        columns = list(TAG_COLUMNS.values())
        if existing:
            assignments = ", ".join(f'"{column}" = ?' for column in columns)
            connection.execute(
                f"UPDATE Gallery_Tags SET {assignments}, UPDATE_TIME = ? WHERE GID = ?",
                tuple(values[column] for column in columns) + (now, gid),
            )
        else:
            names = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            connection.execute(
                f"""
                INSERT INTO Gallery_Tags(
                    GID, {names}, CREATE_TIME, UPDATE_TIME
                ) VALUES (?, {placeholders}, ?, ?)
                """,
                (gid,) + tuple(values[column] for column in columns) + (now, now),
            )

    @staticmethod
    def _existing_start_page(folder, gid):
        sidecar = folder / ".ehviewer"
        try:
            lines = sidecar.read_text(encoding="ascii").splitlines()
            if len(lines) >= 4 and lines[0] == "VERSION2" and int(lines[2]) == int(gid):
                return max(0, int(lines[1], 16))
        except (OSError, UnicodeError, ValueError):
            pass
        return 0

    @staticmethod
    def _atomic_write(target, data):
        target = Path(target)
        temporary = target.with_name(target.name + ".part")
        try:
            with temporary.open("wb") as stream:
                stream.write(bytes(data))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(target))
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
