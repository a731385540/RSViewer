import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


class UserLibraryRepository:
    """RSViewer 自有数据库；绝不在外部 EhViewer 库中建表。"""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()

    def initialize(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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
        placeholders = ",".join("?" for _ in gids)
        result: Dict[int, List[str]] = {}
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT assignments.gid, labels.name
                FROM manga_multi_labels AS assignments
                JOIN multi_labels AS labels ON labels.id = assignments.label_id
                WHERE assignments.gid IN ({placeholders})
                ORDER BY labels.name COLLATE NOCASE
                """,
                tuple(gids),
            )
            for gid, name in rows:
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path))
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
