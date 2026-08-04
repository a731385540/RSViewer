import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


class UserLibraryRepository:
    """RSViewer 自有数据库；绝不在外部 EhViewer 库中建表。"""

    SCHEMA_VERSION = 3

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()

    def initialize(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                connection.executescript(
                    """
                CREATE TABLE IF NOT EXISTS multi_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS manga_multi_labels (
                    gid INTEGER NOT NULL,
                    label_id INTEGER NOT NULL,
                    PRIMARY KEY (gid, label_id),
                    FOREIGN KEY (label_id) REFERENCES multi_labels(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_manga_multi_labels_gid
                    ON manga_multi_labels(gid);
                    """
                )
                connection.execute("PRAGMA user_version = 1")
            if version < 2:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS manga_reading_progress (
                        gid INTEGER PRIMARY KEY,
                        page_index INTEGER NOT NULL CHECK (page_index >= 0),
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_manga_reading_progress_updated
                        ON manga_reading_progress(updated_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 2")
            if version < 3:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS manga_primary_labels (
                        gid INTEGER PRIMARY KEY,
                        label TEXT NOT NULL COLLATE NOCASE,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_manga_primary_labels_label
                        ON manga_primary_labels(label COLLATE NOCASE);
                    """
                )
                connection.execute("PRAGMA user_version = 3")

    def list_labels(self) -> List[Tuple[int, str, int]]:
        self.initialize()
        with self._connect() as connection:
            return [
                (int(row[0]), str(row[1]), int(row[2]))
                for row in connection.execute(
                    """
                    SELECT labels.id, labels.name, COUNT(assignments.gid)
                    FROM multi_labels AS labels
                    LEFT JOIN manga_multi_labels AS assignments
                        ON assignments.label_id = labels.id
                    GROUP BY labels.id, labels.name
                    ORDER BY labels.name COLLATE NOCASE
                    """
                )
            ]

    def labels_for_manga(self, gids: Sequence[int]) -> Dict[int, Tuple[str, ...]]:
        self.initialize()
        if not gids:
            return {}
        requested_gids = set(gids)
        result: Dict[int, List[str]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT assignments.gid, labels.name
                FROM manga_multi_labels AS assignments
                JOIN multi_labels AS labels ON labels.id = assignments.label_id
                ORDER BY labels.name COLLATE NOCASE
                """
            )
            for gid, name in rows:
                if gid in requested_gids:
                    result.setdefault(int(gid), []).append(str(name))
        return {gid: tuple(names) for gid, names in result.items()}

    def create_label(self, name: str) -> int:
        self.initialize()
        normalized = name.strip()
        if not normalized:
            raise ValueError("标签名称不能为空")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO multi_labels(name, created_at) VALUES (?, ?)",
                (normalized, int(time.time())),
            )
            row = connection.execute(
                "SELECT id FROM multi_labels WHERE name = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
            return int(row[0])

    def assign_label(self, gid: int, label_id: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO manga_multi_labels(gid, label_id) VALUES (?, ?)",
                (gid, label_id),
            )

    def unassign_label(self, gid: int, label_id: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM manga_multi_labels WHERE gid = ? AND label_id = ?",
                (gid, label_id),
            )

    def primary_labels_for_mangas(self, gids: Sequence[int]) -> Dict[int, str]:
        self.initialize()
        if not gids:
            return {}
        requested_gids = set(gids)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT gid, label FROM manga_primary_labels"
            )
            return {
                int(gid): str(label)
                for gid, label in rows
                if gid in requested_gids
            }

    def set_primary_label(self, gid: int, label: str):
        self.initialize()
        normalized = label.strip()
        if not normalized:
            raise ValueError("分类标签不能为空")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manga_primary_labels(gid, label, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(gid) DO UPDATE SET
                    label = excluded.label,
                    updated_at = excluded.updated_at
                """,
                (int(gid), normalized, int(time.time())),
            )

    def progress_for_manga(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT page_index FROM manga_reading_progress WHERE gid = ?",
                (gid,),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def progress_for_mangas(self, gids: Sequence[int]) -> Dict[int, int]:
        self.initialize()
        if not gids:
            return {}
        requested_gids = set(gids)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT gid, page_index FROM manga_reading_progress"
            )
            return {
                int(gid): int(page_index)
                for gid, page_index in rows
                if gid in requested_gids
            }

    def save_progress(self, gid: int, page_index: int):
        self.initialize()
        page_index = max(0, int(page_index))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manga_reading_progress(gid, page_index, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(gid) DO UPDATE SET
                    page_index = excluded.page_index,
                    updated_at = excluded.updated_at
                """,
                (int(gid), page_index, int(time.time())),
            )

    def resolve_progress(self, gid: int, ehviewer_page_index):
        """Prefer RSViewer progress, importing EhViewer only when ours is absent."""
        own_progress = self.progress_for_manga(gid)
        if own_progress is not None:
            return own_progress
        if ehviewer_page_index is None:
            return None
        imported_progress = max(0, int(ehviewer_page_index))
        self.save_progress(gid, imported_progress)
        return imported_progress

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
