import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.domain.gallery_update import GalleryUpdateRecord
from app.domain.online_download import GallerySyncRecord, OnlineGalleryDownloadRecord
from app.domain.online_gallery import OnlineGalleryComment


class UserLibraryRepository:
    """RSViewer 自有数据库；绝不在外部 EhViewer 库中建表。"""

    SCHEMA_VERSION = 10

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
            if version < 5:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS manga_favorites (
                        gid INTEGER PRIMARY KEY,
                        created_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_manga_favorites_created
                        ON manga_favorites(created_at DESC);

                    CREATE TABLE IF NOT EXISTS manga_browsing_history (
                        gid INTEGER PRIMARY KEY,
                        viewed_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_manga_history_viewed
                        ON manga_browsing_history(viewed_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 5")
            if version < 6:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS eh_tag_namespaces (
                        namespace TEXT PRIMARY KEY COLLATE NOCASE,
                        display_name TEXT NOT NULL,
                        abbreviation TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        aliases_json TEXT NOT NULL DEFAULT '[]',
                        source_file TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS eh_tags (
                        namespace TEXT NOT NULL COLLATE NOCASE,
                        raw_tag TEXT NOT NULL COLLATE NOCASE,
                        translated_name TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        external_links TEXT NOT NULL DEFAULT '',
                        source_file TEXT NOT NULL,
                        PRIMARY KEY (namespace, raw_tag),
                        FOREIGN KEY (namespace) REFERENCES eh_tag_namespaces(namespace)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_eh_tags_raw_tag
                        ON eh_tags(raw_tag COLLATE NOCASE);
                    CREATE INDEX IF NOT EXISTS idx_eh_tags_translated_name
                        ON eh_tags(translated_name COLLATE NOCASE);
                    """
                )
                connection.execute("PRAGMA user_version = 6")
            if version < 7:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS search_history (
                        query TEXT PRIMARY KEY COLLATE NOCASE,
                        searched_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_search_history_recent
                        ON search_history(searched_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 7")
            if version < 8:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS online_gallery_downloads (
                        gid INTEGER PRIMARY KEY,
                        site TEXT NOT NULL,
                        token TEXT NOT NULL,
                        title TEXT NOT NULL,
                        dirname TEXT NOT NULL,
                        page_count INTEGER NOT NULL CHECK (page_count >= 0),
                        completed_pages INTEGER NOT NULL DEFAULT 0
                            CHECK (completed_pages >= 0),
                        state TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        error TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_online_downloads_state
                        ON online_gallery_downloads(state, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS online_gallery_comments (
                        gid INTEGER NOT NULL,
                        comment_id TEXT NOT NULL,
                        author TEXT NOT NULL DEFAULT '',
                        posted TEXT NOT NULL DEFAULT '',
                        body TEXT NOT NULL DEFAULT '',
                        score INTEGER,
                        is_uploader INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (gid, comment_id),
                        FOREIGN KEY (gid) REFERENCES online_gallery_downloads(gid)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_online_comments_gid
                        ON online_gallery_comments(gid);
                    """
                )
                connection.execute("PRAGMA user_version = 8")
            if version < 9:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS gallery_sync_records (
                        gid INTEGER PRIMARY KEY,
                        site TEXT NOT NULL,
                        token TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS gallery_sync_comments (
                        gid INTEGER NOT NULL,
                        comment_id TEXT NOT NULL,
                        author TEXT NOT NULL DEFAULT '',
                        posted TEXT NOT NULL DEFAULT '',
                        body TEXT NOT NULL DEFAULT '',
                        score INTEGER,
                        is_uploader INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (gid, comment_id),
                        FOREIGN KEY (gid) REFERENCES gallery_sync_records(gid)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_gallery_sync_updated
                        ON gallery_sync_records(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_gallery_sync_comments_gid
                        ON gallery_sync_comments(gid);
                    """
                )
                connection.execute("PRAGMA user_version = 9")
            if version < 10:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS gallery_update_tasks (
                        source_gid INTEGER PRIMARY KEY,
                        source_token TEXT NOT NULL,
                        site TEXT NOT NULL,
                        title TEXT NOT NULL,
                        folder TEXT NOT NULL,
                        latest_url TEXT NOT NULL,
                        target_gid INTEGER NOT NULL DEFAULT 0,
                        target_token TEXT NOT NULL DEFAULT '',
                        status INTEGER NOT NULL DEFAULT 0,
                        state TEXT NOT NULL,
                        completed_pages INTEGER NOT NULL DEFAULT 0,
                        page_count INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        error TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_gallery_updates_state
                        ON gallery_update_tasks(state, updated_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 10")

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

    def favorite_gids(self, gids: Sequence[int] = ()) -> Tuple[int, ...]:
        self.initialize()
        requested = set(int(gid) for gid in gids)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT gid FROM manga_favorites ORDER BY created_at DESC, gid DESC"
            )
            return tuple(
                int(row[0]) for row in rows
                if not requested or int(row[0]) in requested
            )

    def set_favorite(self, gids: Sequence[int], favorite: bool):
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not target_gids:
            return
        with self._connect() as connection:
            if favorite:
                now = time.time_ns()
                connection.executemany(
                    """
                    INSERT INTO manga_favorites(gid, created_at)
                    VALUES (?, ?)
                    ON CONFLICT(gid) DO UPDATE SET created_at = excluded.created_at
                    """,
                    ((gid, now + offset) for offset, gid in enumerate(target_gids)),
                )
            else:
                connection.executemany(
                    "DELETE FROM manga_favorites WHERE gid = ?",
                    ((gid,) for gid in target_gids),
                )

    def record_browsing_history(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manga_browsing_history(gid, viewed_at)
                VALUES (?, ?)
                ON CONFLICT(gid) DO UPDATE SET viewed_at = excluded.viewed_at
                """,
                (int(gid), time.time_ns()),
            )

    def browsing_history_gids(self) -> Tuple[int, ...]:
        self.initialize()
        with self._connect() as connection:
            return tuple(
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT gid FROM manga_browsing_history
                    ORDER BY viewed_at DESC, gid DESC
                    """
                )
            )

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

    def replace_eh_tags(
        self,
        namespaces: Sequence[Tuple[str, str, str, str, str]],
        tags: Sequence[Tuple[str, str, str, str, str, str]],
    ):
        """Atomically replace the imported EH tag snapshot in RSViewer's DB.

        Namespace tuples contain ``(key, display_name, abbreviation,
        aliases_json, source_file)``. Tag tuples contain ``(namespace,
        raw_tag, translated_name, description, external_links, source_file)``.
        """

        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM eh_tags")
            connection.execute("DELETE FROM eh_tag_namespaces")
            connection.executemany(
                """
                INSERT INTO eh_tag_namespaces(
                    namespace, display_name, abbreviation, aliases_json,
                    source_file
                ) VALUES (?, ?, ?, ?, ?)
                """,
                namespaces,
            )
            connection.executemany(
                """
                INSERT INTO eh_tags(
                    namespace, raw_tag, translated_name, description,
                    external_links, source_file
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                tags,
            )

    def load_eh_tags(self) -> List[Tuple[str, str, str, str, str, str]]:
        """Load the compact tag fields needed by the in-memory search index."""

        self.initialize()
        with self._connect() as connection:
            return [
                tuple(str(value or "") for value in row)
                for row in connection.execute(
                    """
                    SELECT namespaces.namespace,
                           namespaces.abbreviation,
                           namespaces.aliases_json,
                           tags.raw_tag,
                           tags.translated_name,
                           namespaces.display_name
                    FROM eh_tags AS tags
                    JOIN eh_tag_namespaces AS namespaces
                      ON namespaces.namespace = tags.namespace
                    ORDER BY namespaces.namespace COLLATE NOCASE,
                             tags.raw_tag COLLATE NOCASE
                    """
                )
            ]

    def eh_tag_count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM eh_tags").fetchone()[0])

    def list_search_history(self, limit: int = 20) -> List[str]:
        self.initialize()
        limit = max(1, min(20, int(limit)))
        with self._connect() as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT query FROM search_history
                    ORDER BY searched_at DESC, query COLLATE NOCASE
                    LIMIT ?
                    """,
                    (limit,),
                )
            ]

    def save_search_history(self, query: str, limit: int = 20):
        self.initialize()
        query = " ".join(str(query or "").strip().split())
        if not query:
            return
        limit = max(1, min(20, int(limit)))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO search_history(query, searched_at)
                VALUES (?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    query = excluded.query,
                    searched_at = excluded.searched_at
                """,
                (query, time.time_ns()),
            )
            self._trim_search_history(connection, limit)

    def trim_search_history(self, limit: int):
        self.initialize()
        limit = max(1, min(20, int(limit)))
        with self._connect() as connection:
            self._trim_search_history(connection, limit)

    @staticmethod
    def _trim_search_history(connection, limit: int):
        connection.execute(
            """
            DELETE FROM search_history
            WHERE query NOT IN (
                SELECT query FROM search_history
                ORDER BY searched_at DESC, query COLLATE NOCASE
                LIMIT ?
            )
            """,
            (limit,),
        )

    def save_gallery_sync(
        self,
        record: GallerySyncRecord,
        comments: Sequence[OnlineGalleryComment] = (),
    ):
        self.initialize()
        metadata_json = json.dumps(
            dict(record.metadata or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            self._write_gallery_sync(
                connection,
                record.gid,
                record.site,
                record.token,
                metadata_json,
                int(record.updated_at or time.time_ns()),
                comments,
            )

    def gallery_sync_record(self, gid: int) -> Optional[GallerySyncRecord]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT gid, site, token, metadata_json, updated_at
                FROM gallery_sync_records WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        return self._gallery_sync_from_row(row) if row is not None else None

    def gallery_sync_records_for_mangas(
        self, gids: Sequence[int]
    ) -> Dict[int, GallerySyncRecord]:
        self.initialize()
        target_gids = {int(gid) for gid in gids}
        if not target_gids:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, site, token, metadata_json, updated_at
                FROM gallery_sync_records
                """
            ).fetchall()
        return {
            int(row[0]): self._gallery_sync_from_row(row)
            for row in rows
            if int(row[0]) in target_gids
        }

    def save_online_gallery_download(
        self,
        record: OnlineGalleryDownloadRecord,
        comments: Sequence[OnlineGalleryComment] = (),
    ):
        self.initialize()
        now = time.time_ns()
        metadata_json = json.dumps(
            dict(record.metadata or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO online_gallery_downloads(
                    gid, site, token, title, dirname, page_count,
                    completed_pages, state, metadata_json, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gid) DO UPDATE SET
                    site = excluded.site,
                    token = excluded.token,
                    title = excluded.title,
                    dirname = excluded.dirname,
                    page_count = excluded.page_count,
                    completed_pages = excluded.completed_pages,
                    state = excluded.state,
                    metadata_json = excluded.metadata_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    int(record.gid),
                    str(record.site),
                    str(record.token),
                    str(record.title),
                    str(record.dirname),
                    max(0, int(record.page_count)),
                    max(0, int(record.completed_pages)),
                    str(record.state),
                    metadata_json,
                    str(record.error or ""),
                    int(record.created_at or now),
                    int(record.updated_at or now),
                ),
            )
            connection.execute(
                "DELETE FROM online_gallery_comments WHERE gid = ?",
                (int(record.gid),),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO online_gallery_comments(
                    gid, comment_id, author, posted, body, score, is_uploader
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        int(record.gid),
                        str(comment.comment_id),
                        str(comment.author or ""),
                        str(comment.posted or ""),
                        str(comment.text or ""),
                        int(comment.score) if comment.score is not None else None,
                        1 if comment.is_uploader else 0,
                    )
                    for comment in comments
                ),
            )
            self._write_gallery_sync(
                connection,
                record.gid,
                record.site,
                record.token,
                metadata_json,
                int(record.updated_at or now),
                comments,
            )

    def save_gallery_update(self, record: GalleryUpdateRecord):
        self.initialize()
        now = time.time_ns()
        metadata_json = json.dumps(
            dict(record.metadata or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gallery_update_tasks(
                    source_gid, source_token, site, title, folder, latest_url,
                    target_gid, target_token, status, state, completed_pages,
                    page_count, metadata_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_gid) DO UPDATE SET
                    source_token = excluded.source_token,
                    site = excluded.site,
                    title = excluded.title,
                    folder = excluded.folder,
                    latest_url = excluded.latest_url,
                    target_gid = excluded.target_gid,
                    target_token = excluded.target_token,
                    status = excluded.status,
                    state = excluded.state,
                    completed_pages = excluded.completed_pages,
                    page_count = excluded.page_count,
                    metadata_json = excluded.metadata_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    int(record.source_gid),
                    str(record.source_token),
                    str(record.site),
                    str(record.title),
                    str(record.folder),
                    str(record.latest_url),
                    max(0, int(record.target_gid)),
                    str(record.target_token),
                    max(0, min(6, int(record.status))),
                    str(record.state),
                    max(0, int(record.completed_pages)),
                    max(0, int(record.page_count)),
                    metadata_json,
                    str(record.error or ""),
                    int(record.created_at or now),
                    int(record.updated_at or now),
                ),
            )

    def gallery_update(self, source_gid: int) -> Optional[GalleryUpdateRecord]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_gid, source_token, site, title, folder, latest_url,
                       target_gid, target_token, status, state, completed_pages,
                       page_count, metadata_json, error, created_at, updated_at
                FROM gallery_update_tasks WHERE source_gid = ?
                """,
                (int(source_gid),),
            ).fetchone()
        return self._gallery_update_from_row(row) if row is not None else None

    def gallery_updates(self, include_completed=False):
        self.initialize()
        query = """
            SELECT source_gid, source_token, site, title, folder, latest_url,
                   target_gid, target_token, status, state, completed_pages,
                   page_count, metadata_json, error, created_at, updated_at
            FROM gallery_update_tasks
        """
        if not include_completed:
            query += " WHERE state != 'completed'"
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return tuple(self._gallery_update_from_row(row) for row in rows)

    def update_gallery_update_state(
        self,
        source_gid,
        state,
        status=None,
        completed_pages=None,
        page_count=None,
        error="",
    ):
        self.initialize()
        assignments = ["state = ?", "error = ?", "updated_at = ?"]
        values = [str(state), str(error or ""), time.time_ns()]
        if status is not None:
            assignments.append("status = ?")
            values.append(max(0, min(6, int(status))))
        if completed_pages is not None:
            assignments.append("completed_pages = ?")
            values.append(max(0, int(completed_pages)))
        if page_count is not None:
            assignments.append("page_count = ?")
            values.append(max(0, int(page_count)))
        values.append(int(source_gid))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE gallery_update_tasks SET {', '.join(assignments)} "
                "WHERE source_gid = ?",
                tuple(values),
            )

    def mark_interrupted_gallery_updates(self):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE gallery_update_tasks
                SET state = 'paused',
                    error = CASE
                        WHEN error = '' THEN '上次更新已中断，可继续执行'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE state IN ('queued', 'updating')
                """,
                (time.time_ns(),),
            )

    def promote_gallery_gid(self, source_gid, target_gid, progress_page_index=None):
        """Move user-owned associations after the external GID is promoted."""
        self.initialize()
        source_gid = int(source_gid)
        target_gid = int(target_gid)
        if source_gid == target_gid:
            return
        with self._connect() as connection:
            for table, extra_columns in (
                ("manga_multi_labels", "label_id, sort_order, added_at"),
                ("manga_taxonomy_labels", "label_id"),
            ):
                columns = "gid, " + extra_columns
                connection.execute(
                    f"INSERT OR IGNORE INTO {table}({columns}) "
                    f"SELECT ?, {extra_columns} FROM {table} WHERE gid = ?",
                    (target_gid, source_gid),
                )
                connection.execute(
                    f"DELETE FROM {table} WHERE gid = ?", (source_gid,)
                )
            connection.execute(
                "UPDATE multi_labels SET last_gid = ? WHERE last_gid = ?",
                (target_gid, source_gid),
            )
            for table, value_column, combine in (
                ("manga_primary_labels", "label, updated_at", "REPLACE"),
                ("manga_favorites", "created_at", "IGNORE"),
                ("manga_browsing_history", "viewed_at", "REPLACE"),
            ):
                columns = "gid, " + value_column
                connection.execute(
                    f"INSERT OR {combine} INTO {table}({columns}) "
                    f"SELECT ?, {value_column} FROM {table} WHERE gid = ?",
                    (target_gid, source_gid),
                )
                connection.execute(
                    f"DELETE FROM {table} WHERE gid = ?", (source_gid,)
                )
            if progress_page_index is None:
                row = connection.execute(
                    "SELECT page_index FROM manga_reading_progress WHERE gid = ?",
                    (source_gid,),
                ).fetchone()
                progress_page_index = int(row[0]) if row is not None else None
            if progress_page_index is not None:
                connection.execute(
                    """
                    INSERT INTO manga_reading_progress(gid, page_index, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(gid) DO UPDATE SET
                        page_index = excluded.page_index,
                        updated_at = excluded.updated_at
                    """,
                    (target_gid, max(0, int(progress_page_index)), time.time_ns()),
                )
            connection.execute(
                "DELETE FROM manga_reading_progress WHERE gid = ?", (source_gid,)
            )
            connection.execute(
                "DELETE FROM online_gallery_downloads WHERE gid = ?", (source_gid,)
            )
            connection.execute(
                "DELETE FROM gallery_sync_records WHERE gid = ?", (source_gid,)
            )

    def update_online_download(
        self,
        gid: int,
        completed_pages: int,
        state: str,
        error: str = "",
    ):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE online_gallery_downloads
                SET completed_pages = ?, state = ?, error = ?, updated_at = ?
                WHERE gid = ?
                """,
                (
                    max(0, int(completed_pages)),
                    str(state),
                    str(error or ""),
                    time.time_ns(),
                    int(gid),
                ),
            )

    def online_gallery_download(self, gid: int) -> Optional[OnlineGalleryDownloadRecord]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT gid, site, token, title, dirname, page_count,
                       completed_pages, state, metadata_json, error,
                       created_at, updated_at
                FROM online_gallery_downloads WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        return self._online_download_from_row(row) if row is not None else None

    def online_gallery_downloads_for_mangas(
        self, gids: Sequence[int]
    ) -> Dict[int, OnlineGalleryDownloadRecord]:
        self.initialize()
        target_gids = {int(gid) for gid in gids}
        if not target_gids:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, site, token, title, dirname, page_count,
                       completed_pages, state, metadata_json, error,
                       created_at, updated_at
                FROM online_gallery_downloads
                """
            ).fetchall()
        return {
            int(row[0]): self._online_download_from_row(row)
            for row in rows
            if int(row[0]) in target_gids
        }

    def incomplete_online_gallery_downloads(self) -> Tuple[OnlineGalleryDownloadRecord, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, site, token, title, dirname, page_count,
                       completed_pages, state, metadata_json, error,
                       created_at, updated_at
                FROM online_gallery_downloads
                WHERE state != 'completed'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return tuple(self._online_download_from_row(row) for row in rows)

    def mark_interrupted_online_downloads(self):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE online_gallery_downloads
                SET state = 'paused',
                    error = CASE
                        WHEN error = '' THEN '上次下载已中断，可继续下载'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE state IN ('queued', 'downloading')
                """,
                (time.time_ns(),),
            )

    def delete_online_gallery_download(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM online_gallery_downloads WHERE gid = ?",
                (int(gid),),
            )

    def online_gallery_comments(self, gid: int) -> Tuple[OnlineGalleryComment, ...]:
        self.initialize()
        with self._connect() as connection:
            synced = connection.execute(
                "SELECT 1 FROM gallery_sync_records WHERE gid = ?",
                (int(gid),),
            ).fetchone()
            table = (
                "gallery_sync_comments"
                if synced is not None
                else "online_gallery_comments"
            )
            rows = connection.execute(
                f"""
                SELECT comment_id, author, posted, body, score, is_uploader
                FROM {table}
                WHERE gid = ? ORDER BY rowid
                """,
                (int(gid),),
            ).fetchall()
        return tuple(
            OnlineGalleryComment(
                comment_id=str(row[0]),
                author=str(row[1]),
                posted=str(row[2]),
                text=str(row[3]),
                score=int(row[4]) if row[4] is not None else None,
                is_uploader=bool(row[5]),
            )
            for row in rows
        )

    @staticmethod
    def _write_gallery_sync(
        connection,
        gid,
        site,
        token,
        metadata_json,
        updated_at,
        comments,
    ):
        connection.execute(
            """
            INSERT INTO gallery_sync_records(
                gid, site, token, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(gid) DO UPDATE SET
                site = excluded.site,
                token = excluded.token,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                int(gid),
                str(site),
                str(token),
                str(metadata_json),
                int(updated_at),
            ),
        )
        connection.execute(
            "DELETE FROM gallery_sync_comments WHERE gid = ?",
            (int(gid),),
        )
        connection.executemany(
            """
            INSERT OR REPLACE INTO gallery_sync_comments(
                gid, comment_id, author, posted, body, score, is_uploader
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    int(gid),
                    str(comment.comment_id),
                    str(comment.author or ""),
                    str(comment.posted or ""),
                    str(comment.text or ""),
                    int(comment.score) if comment.score is not None else None,
                    1 if comment.is_uploader else 0,
                )
                for comment in comments
            ),
        )

    @staticmethod
    def _gallery_sync_from_row(row) -> GallerySyncRecord:
        try:
            metadata = json.loads(str(row[3] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return GallerySyncRecord(
            gid=int(row[0]),
            site=str(row[1]),
            token=str(row[2]),
            metadata=metadata,
            updated_at=int(row[4]),
        )

    @staticmethod
    def _online_download_from_row(row) -> OnlineGalleryDownloadRecord:
        try:
            metadata = json.loads(str(row[8] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return OnlineGalleryDownloadRecord(
            gid=int(row[0]),
            site=str(row[1]),
            token=str(row[2]),
            title=str(row[3]),
            dirname=str(row[4]),
            page_count=int(row[5]),
            completed_pages=int(row[6]),
            state=str(row[7]),
            metadata=metadata,
            error=str(row[9] or ""),
            created_at=int(row[10]),
            updated_at=int(row[11]),
        )

    @staticmethod
    def _gallery_update_from_row(row) -> GalleryUpdateRecord:
        try:
            metadata = json.loads(str(row[12] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return GalleryUpdateRecord(
            source_gid=int(row[0]),
            source_token=str(row[1]),
            site=str(row[2]),
            title=str(row[3]),
            folder=str(row[4]),
            latest_url=str(row[5]),
            target_gid=int(row[6]),
            target_token=str(row[7]),
            status=int(row[8]),
            state=str(row[9]),
            completed_pages=int(row[10]),
            page_count=int(row[11]),
            metadata=metadata,
            error=str(row[13] or ""),
            created_at=int(row[14]),
            updated_at=int(row[15]),
        )

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
