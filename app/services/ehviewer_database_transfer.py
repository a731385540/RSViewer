import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.repositories.ehviewer_schema import (
    EHVIEWER_TABLES,
    EHVIEWER_USER_VERSION,
    REQUIRED_LIBRARY_TABLES,
    ensure_ehviewer_schema,
    table_columns,
    table_names,
)


@dataclass(frozen=True)
class EhViewerTransferResult:
    path: Path
    table_counts: dict

    @property
    def gallery_count(self):
        return int(self.table_counts.get("DOWNLOADS", 0))


def import_ehviewer_database(source_path, destination_repository, replace=False):
    """Merge an existing eh.db into RSViewer's own SQLite database."""
    source_path = Path(source_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到 EhViewer 数据库：{source_path}")
    destination_repository.initialize()
    destination_path = Path(destination_repository.database_path).resolve()
    if source_path == destination_path:
        raise ValueError("导入源不能是 RSViewer 自有数据库")

    source_uri = f"file:{source_path.as_posix()}?mode=ro"
    counts = {}
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source,
        closing(sqlite3.connect(str(destination_path), timeout=30)) as destination,
    ):
        source.execute("BEGIN")
        available = table_names(source)
        missing = REQUIRED_LIBRARY_TABLES.difference(available)
        if missing:
            raise ValueError(
                "EhViewer 数据库缺少表：" + ", ".join(sorted(missing))
            )
        ensure_ehviewer_schema(destination)
        destination.execute("BEGIN IMMEDIATE")
        try:
            if replace:
                for table in EHVIEWER_TABLES:
                    destination.execute(f'DELETE FROM "{table}"')
            for table in EHVIEWER_TABLES:
                if table not in available:
                    counts[table] = 0
                    continue
                source_columns = set(table_columns(source, table))
                columns = tuple(
                    column
                    for column in table_columns(destination, table)
                    if column in source_columns
                )
                if not columns:
                    counts[table] = 0
                    continue
                quoted = ", ".join(f'"{column}"' for column in columns)
                rows = source.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
                placeholders = ", ".join("?" for _ in columns)
                destination.executemany(
                    f'INSERT OR REPLACE INTO "{table}"({quoted}) '
                    f"VALUES ({placeholders})",
                    rows,
                )
                counts[table] = len(rows)
            _merge_user_relations(destination)
            destination.commit()
        except Exception:
            destination.rollback()
            raise
    return EhViewerTransferResult(source_path, counts)


def export_ehviewer_database(source_repository, destination_path):
    """Create a standalone EhViewer-compatible database from RSViewer data."""
    source_repository.initialize()
    source_path = Path(source_repository.database_path).resolve()
    destination_path = Path(destination_path).expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("不能把导出目标设为 RSViewer 自有数据库")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(
        f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    )
    counts = {}
    source_uri = f"file:{source_path.as_posix()}?mode=ro"
    try:
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source,
            closing(sqlite3.connect(str(temporary))) as destination,
        ):
            source.execute("BEGIN")
            ensure_ehviewer_schema(destination)
            available = table_names(source)
            for table in EHVIEWER_TABLES:
                if table not in available:
                    counts[table] = 0
                    continue
                columns = table_columns(destination, table)
                quoted = ", ".join(f'"{column}"' for column in columns)
                rows = source.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
                placeholders = ", ".join("?" for _ in columns)
                destination.executemany(
                    f'INSERT INTO "{table}"({quoted}) VALUES ({placeholders})',
                    rows,
                )
                counts[table] = len(rows)
            relation_counts = _export_user_relations(source, destination)
            counts.update(relation_counts)
            locale = destination.execute(
                "SELECT locale FROM android_metadata LIMIT 1"
            ).fetchone()
            if locale is None:
                destination.execute(
                    "INSERT INTO android_metadata(locale) VALUES ('zh_CN')"
                )
            destination.execute(f"PRAGMA user_version = {EHVIEWER_USER_VERSION}")
            destination.commit()
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise ValueError("导出的 EhViewer 数据库完整性校验失败")
        os.replace(str(temporary), str(destination_path))
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return EhViewerTransferResult(destination_path, counts)


def _merge_user_relations(connection):
    now_rows = connection.execute("SELECT GID, TIME FROM LOCAL_FAVORITES").fetchall()
    connection.executemany(
        "INSERT OR IGNORE INTO manga_favorites(gid, created_at) VALUES (?, ?)",
        ((int(gid), int(saved_at or 0)) for gid, saved_at in now_rows),
    )
    history_rows = connection.execute("SELECT GID, TIME FROM HISTORY").fetchall()
    connection.executemany(
        """
        INSERT INTO manga_browsing_history(gid, viewed_at) VALUES (?, ?)
        ON CONFLICT(gid) DO UPDATE SET viewed_at = MAX(viewed_at, excluded.viewed_at)
        """,
        ((int(gid), int(viewed_at or 0)) for gid, viewed_at in history_rows),
    )


def _export_user_relations(source, destination):
    destination.execute(
        "DELETE FROM LOCAL_FAVORITES WHERE GID IN (SELECT GID FROM DOWNLOADS)"
    )
    favorites = source.execute(
        """
        SELECT d.GID, d.TOKEN, d.TITLE, d.TITLE_JPN, d.THUMB, d.CATEGORY,
               d.POSTED, d.UPLOADER, d.RATING, d.SIMPLE_LANGUAGE, f.created_at
        FROM manga_favorites AS f
        JOIN DOWNLOADS AS d ON d.GID = f.gid
        ORDER BY f.created_at
        """
    ).fetchall()
    destination.executemany(
        """
        INSERT OR REPLACE INTO LOCAL_FAVORITES(
            GID, TOKEN, TITLE, TITLE_JPN, THUMB, CATEGORY, POSTED,
            UPLOADER, RATING, SIMPLE_LANGUAGE, TIME
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        favorites,
    )
    destination.execute(
        "DELETE FROM HISTORY WHERE GID IN (SELECT GID FROM DOWNLOADS)"
    )
    history = source.execute(
        """
        SELECT d.GID, d.TOKEN, d.TITLE, d.TITLE_JPN, d.THUMB, d.CATEGORY,
               d.POSTED, d.UPLOADER, d.RATING, d.SIMPLE_LANGUAGE,
               0, h.viewed_at
        FROM manga_browsing_history AS h
        JOIN DOWNLOADS AS d ON d.GID = h.gid
        ORDER BY h.viewed_at
        """
    ).fetchall()
    destination.executemany(
        """
        INSERT OR REPLACE INTO HISTORY(
            GID, TOKEN, TITLE, TITLE_JPN, THUMB, CATEGORY, POSTED,
            UPLOADER, RATING, SIMPLE_LANGUAGE, MODE, TIME
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        history,
    )
    counts = {
        "LOCAL_FAVORITES": destination.execute(
            "SELECT COUNT(*) FROM LOCAL_FAVORITES"
        ).fetchone()[0],
        "HISTORY": destination.execute(
            "SELECT COUNT(*) FROM HISTORY"
        ).fetchone()[0],
    }
    return counts
