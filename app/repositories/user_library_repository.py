import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


class UserLibraryRepository:
    """RSViewer 自有数据库；绝不在外部 EhViewer 库中建表。"""

    SCHEMA_VERSION = 4

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
            if version < 4:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS multi_labels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        created_at INTEGER NOT NULL,
                        last_gid INTEGER
                    );
                    CREATE TABLE IF NOT EXISTS manga_multi_labels (
                        gid INTEGER NOT NULL,
                        label_id INTEGER NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        added_at INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (gid, label_id),
                        FOREIGN KEY (label_id) REFERENCES multi_labels(id)
                            ON DELETE CASCADE
                    );
                    """
                )
                label_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(multi_labels)")
                }
                if "last_gid" not in label_columns:
                    connection.execute(
                        "ALTER TABLE multi_labels ADD COLUMN last_gid INTEGER"
                    )
                assignment_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(manga_multi_labels)"
                    )
                }
                if "sort_order" not in assignment_columns:
                    connection.execute(
                        "ALTER TABLE manga_multi_labels "
                        "ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
                    )
                if "added_at" not in assignment_columns:
                    connection.execute(
                        "ALTER TABLE manga_multi_labels "
                        "ADD COLUMN added_at INTEGER NOT NULL DEFAULT 0"
                    )
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS taxonomy_labels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        parent_id INTEGER,
                        name TEXT NOT NULL COLLATE NOCASE,
                        created_at INTEGER NOT NULL,
                        UNIQUE(parent_id, name),
                        FOREIGN KEY (parent_id) REFERENCES taxonomy_labels(id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS manga_taxonomy_labels (
                        gid INTEGER NOT NULL,
                        label_id INTEGER NOT NULL,
                        PRIMARY KEY (gid, label_id),
                        FOREIGN KEY (label_id) REFERENCES taxonomy_labels(id)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_manga_taxonomy_gid
                        ON manga_taxonomy_labels(gid);
                    CREATE INDEX IF NOT EXISTS idx_manga_taxonomy_label
                        ON manga_taxonomy_labels(label_id);
                    """
                )
                for label_id, in connection.execute("SELECT id FROM multi_labels"):
                    gids = [
                        row[0]
                        for row in connection.execute(
                            """
                            SELECT gid FROM manga_multi_labels
                            WHERE label_id = ? ORDER BY rowid
                            """,
                            (label_id,),
                        )
                    ]
                    connection.executemany(
                        """
                        UPDATE manga_multi_labels SET sort_order = ?, added_at = ?
                        WHERE label_id = ? AND gid = ?
                        """,
                        (
                            (position, int(time.time()), label_id, gid)
                            for position, gid in enumerate(gids)
                        ),
                    )
                connection.execute("PRAGMA user_version = 4")

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

    def list_playlists(self) -> List[Tuple[int, str, int, Optional[int]]]:
        self.initialize()
        with self._connect() as connection:
            return [
                (
                    int(row[0]),
                    str(row[1]),
                    int(row[2]),
                    int(row[3]) if row[3] is not None else None,
                )
                for row in connection.execute(
                    """
                    SELECT labels.id, labels.name, COUNT(assignments.gid),
                           labels.last_gid
                    FROM multi_labels AS labels
                    LEFT JOIN manga_multi_labels AS assignments
                        ON assignments.label_id = labels.id
                    GROUP BY labels.id, labels.name, labels.last_gid
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

    def create_playlist(self, name: str) -> int:
        return self.create_label(name)

    def delete_playlist(self, playlist_id: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM multi_labels WHERE id = ?",
                (int(playlist_id),),
            )

    def assign_label(self, gid: int, label_id: int):
        self.assign_label_to_mangas([gid], label_id)

    def assign_label_to_mangas(self, gids: Sequence[int], label_id: int):
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not target_gids:
            return
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), -1)
                FROM manga_multi_labels WHERE label_id = ?
                """,
                (int(label_id),),
            ).fetchone()
            next_order = int(row[0]) + 1
            now = int(time.time())
            connection.executemany(
                """
                INSERT OR IGNORE INTO manga_multi_labels(
                    gid, label_id, sort_order, added_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (gid, int(label_id), next_order + offset, now)
                    for offset, gid in enumerate(target_gids)
                ),
            )

    def unassign_label(self, gid: int, label_id: int):
        self.unassign_label_from_mangas([gid], label_id)

    def unassign_label_from_mangas(self, gids: Sequence[int], label_id: int):
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not target_gids:
            return
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM manga_multi_labels WHERE gid = ? AND label_id = ?",
                ((gid, int(label_id)) for gid in target_gids),
            )

    def playlist_items(self, playlist_id: int) -> Tuple[int, ...]:
        self.initialize()
        with self._connect() as connection:
            return tuple(
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT gid FROM manga_multi_labels
                    WHERE label_id = ?
                    ORDER BY sort_order, added_at, gid
                    """,
                    (int(playlist_id),),
                )
            )

    def set_playlist_order(self, playlist_id: int, gids: Sequence[int]):
        self.initialize()
        ordered_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        with self._connect() as connection:
            existing = {
                int(row[0])
                for row in connection.execute(
                    "SELECT gid FROM manga_multi_labels WHERE label_id = ?",
                    (int(playlist_id),),
                )
            }
            if existing != set(ordered_gids):
                raise ValueError("播放顺序必须包含播放列表中的全部漫画")
            connection.executemany(
                """
                UPDATE manga_multi_labels SET sort_order = ?
                WHERE label_id = ? AND gid = ?
                """,
                (
                    (position, int(playlist_id), gid)
                    for position, gid in enumerate(ordered_gids)
                ),
            )

    def save_playlist_position(self, playlist_id: int, gid: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "UPDATE multi_labels SET last_gid = ? WHERE id = ?",
                (int(gid), int(playlist_id)),
            )

    def playlist_last_gid(self, playlist_id: int) -> Optional[int]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_gid FROM multi_labels WHERE id = ?",
                (int(playlist_id),),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def list_taxonomy_labels(self) -> List[Tuple[int, Optional[int], str, int]]:
        self.initialize()
        with self._connect() as connection:
            return [
                (
                    int(row[0]),
                    int(row[1]) if row[1] is not None else None,
                    str(row[2]),
                    int(row[3]),
                )
                for row in connection.execute(
                    """
                    SELECT labels.id, labels.parent_id, labels.name,
                           COUNT(assignments.gid)
                    FROM taxonomy_labels AS labels
                    LEFT JOIN manga_taxonomy_labels AS assignments
                        ON assignments.label_id = labels.id
                    GROUP BY labels.id, labels.parent_id, labels.name
                    ORDER BY labels.name COLLATE NOCASE
                    """
                )
            ]

    def create_taxonomy_label(self, name: str, parent_id=None) -> int:
        self.initialize()
        normalized = name.strip()
        if not normalized:
            raise ValueError("归类名称不能为空")
        normalized_parent = int(parent_id) if parent_id is not None else None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM taxonomy_labels
                WHERE parent_id IS ? AND name = ? COLLATE NOCASE
                """,
                (normalized_parent, normalized),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO taxonomy_labels(parent_id, name, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_parent, normalized, int(time.time())),
                )
                return int(cursor.lastrowid)
            return int(row[0])

    def delete_taxonomy_label(self, label_id: int):
        """Delete a taxonomy node; foreign keys cascade its subtree and links."""
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM taxonomy_labels WHERE id = ?",
                (int(label_id),),
            )

    def taxonomy_for_mangas(
        self, gids: Sequence[int]
    ) -> Dict[int, Tuple[Tuple[int, str], ...]]:
        self.initialize()
        if not gids:
            return {}
        requested_gids = set(int(gid) for gid in gids)
        result: Dict[int, List[Tuple[int, str]]] = {}
        with self._connect() as connection:
            for gid, label_id, name in connection.execute(
                """
                SELECT assignments.gid, labels.id, labels.name
                FROM manga_taxonomy_labels AS assignments
                JOIN taxonomy_labels AS labels ON labels.id = assignments.label_id
                ORDER BY labels.name COLLATE NOCASE
                """
            ):
                if int(gid) in requested_gids:
                    result.setdefault(int(gid), []).append(
                        (int(label_id), str(name))
                    )
        return {gid: tuple(values) for gid, values in result.items()}

    def assign_taxonomy_to_mangas(self, gids: Sequence[int], label_id: int):
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO manga_taxonomy_labels(gid, label_id)
                VALUES (?, ?)
                """,
                ((gid, int(label_id)) for gid in target_gids),
            )

    def unassign_taxonomy_from_mangas(self, gids: Sequence[int], label_id: int):
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        with self._connect() as connection:
            connection.executemany(
                """
                DELETE FROM manga_taxonomy_labels
                WHERE gid = ? AND label_id = ?
                """,
                ((gid, int(label_id)) for gid in target_gids),
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
