import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from app.domain.gallery_update import GalleryUpdateRecord
from app.domain.gallery_trash import GalleryTrashRecord
from app.domain.online_download import (
    DOWNLOAD_MODE_STANDARD,
    GalleryOriginalState,
    GallerySyncRecord,
    OnlineGalleryDownloadRecord,
    ORIGINAL_PAGE_MODE_BASE,
    normalize_original_page_modes,
)
from app.domain.online_gallery import OnlineGalleryComment
from app.domain.online_gallery import OnlineGalleryLink
from app.domain.similar_gallery import LatestSimilarSearch
from app.repositories.ehviewer_schema import ensure_ehviewer_schema


class UserLibraryRepository:
    """RSViewer's complete application and local-gallery database."""

    SCHEMA_VERSION = 22

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
                        gallery_links_json TEXT NOT NULL DEFAULT '[]',
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
                        gallery_links_json TEXT NOT NULL DEFAULT '[]',
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
            if version < 11:
                download_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(online_gallery_downloads)"
                    )
                }
                if "download_mode" not in download_columns:
                    connection.execute(
                        "ALTER TABLE online_gallery_downloads "
                        "ADD COLUMN download_mode TEXT NOT NULL DEFAULT 'standard'"
                    )
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS gallery_original_states (
                        gid INTEGER PRIMARY KEY,
                        site TEXT NOT NULL,
                        token TEXT NOT NULL,
                        dirname TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        completed_pages INTEGER NOT NULL DEFAULT 0
                            CHECK (completed_pages >= 0),
                        page_count INTEGER NOT NULL DEFAULT 0
                            CHECK (page_count >= 0),
                        fallback_to_standard INTEGER NOT NULL DEFAULT 0,
                        page_modes_json TEXT NOT NULL DEFAULT '[]',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        error TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_gallery_original_state
                        ON gallery_original_states(state, updated_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 11")
            if version < 12:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS gallery_trash (
                        gid INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        folder TEXT NOT NULL,
                        dirname TEXT NOT NULL COLLATE NOCASE,
                        cover_path TEXT NOT NULL DEFAULT '',
                        page_count INTEGER NOT NULL DEFAULT 0,
                        state TEXT NOT NULL,
                        external_snapshot_json TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        deleted_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS idx_gallery_trash_dirname
                        ON gallery_trash(dirname COLLATE NOCASE);
                    CREATE INDEX IF NOT EXISTS idx_gallery_trash_deleted
                        ON gallery_trash(deleted_at DESC);
                    """
                )
                connection.execute("PRAGMA user_version = 12")
            if version < 13:
                trash_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(gallery_trash)")
                }
                if "database_path" not in trash_columns:
                    connection.execute(
                        "ALTER TABLE gallery_trash "
                        "ADD COLUMN database_path TEXT NOT NULL DEFAULT ''"
                    )
                if "manga_root" not in trash_columns:
                    connection.execute(
                        "ALTER TABLE gallery_trash "
                        "ADD COLUMN manga_root TEXT NOT NULL DEFAULT ''"
                    )
                connection.execute("PRAGMA user_version = 13")
            if version < 14:
                ensure_ehviewer_schema(connection)
                connection.execute("PRAGMA user_version = 14")
            if version < 15:
                connection.execute(
                    "UPDATE gallery_trash SET database_path = ?",
                    (str(self.database_path),),
                )
                connection.execute("PRAGMA user_version = 15")
            if version < 16:
                ensure_ehviewer_schema(connection)
                connection.execute("PRAGMA user_version = 16")
            if version < 17:
                original_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(gallery_original_states)"
                    )
                }
                if "fallback_to_standard" not in original_columns:
                    connection.execute(
                        "ALTER TABLE gallery_original_states "
                        "ADD COLUMN fallback_to_standard INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute("PRAGMA user_version = 17")
            if version < 18:
                original_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(gallery_original_states)"
                    )
                }
                if "page_modes_json" not in original_columns:
                    connection.execute(
                        "ALTER TABLE gallery_original_states "
                        "ADD COLUMN page_modes_json TEXT NOT NULL DEFAULT '[]'"
                    )
                connection.execute("PRAGMA user_version = 18")
            if version < 19:
                for table in (
                    "online_gallery_comments",
                    "gallery_sync_comments",
                ):
                    columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    if "gallery_links_json" not in columns:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN "
                            "gallery_links_json TEXT NOT NULL DEFAULT '[]'"
                        )
                connection.execute("PRAGMA user_version = 19")
            if version < 20:
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
                progress_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(manga_reading_progress)"
                    )
                }
                if "completed" not in progress_columns:
                    connection.execute(
                        "ALTER TABLE manga_reading_progress "
                        "ADD COLUMN completed INTEGER NOT NULL DEFAULT 0"
                    )
                if "cleared" not in progress_columns:
                    connection.execute(
                        "ALTER TABLE manga_reading_progress "
                        "ADD COLUMN cleared INTEGER NOT NULL DEFAULT 0"
                    )
                connection.execute("PRAGMA user_version = 20")
            if version < 21:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS latest_similar_search (
                        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                        source_gid INTEGER NOT NULL,
                        selected_text TEXT NOT NULL,
                        result_gids_json TEXT NOT NULL DEFAULT '[]',
                        searched_at INTEGER NOT NULL
                    );
                    """
                )
                connection.execute("PRAGMA user_version = 21")
            if version < 22:
                progress_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(manga_reading_progress)"
                    )
                }
                if "started" not in progress_columns:
                    connection.execute(
                        "ALTER TABLE manga_reading_progress "
                        "ADD COLUMN started INTEGER NOT NULL DEFAULT 1"
                    )
                    connection.execute(
                        """
                        UPDATE manga_reading_progress
                        SET started = CASE
                            WHEN cleared = 1 THEN 0
                            WHEN completed = 1 OR page_index > 0 THEN 1
                            ELSE 0
                        END
                        """
                    )
                connection.execute("PRAGMA user_version = 22")

    def list_labels(self) -> List[Tuple[int, str, int]]:
        self.initialize()
        with self._connect() as connection:
            return [
                (int(row[0]), str(row[1]), int(row[2]))
                for row in connection.execute(
                    """
                    SELECT labels.id, labels.name,
                           COUNT(CASE WHEN trash.gid IS NULL THEN assignments.gid END)
                    FROM multi_labels AS labels
                    LEFT JOIN manga_multi_labels AS assignments
                        ON assignments.label_id = labels.id
                    LEFT JOIN gallery_trash AS trash ON trash.gid = assignments.gid
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
                    SELECT labels.id, labels.name,
                           COUNT(CASE WHEN trash.gid IS NULL THEN assignments.gid END),
                           labels.last_gid
                    FROM multi_labels AS labels
                    LEFT JOIN manga_multi_labels AS assignments
                        ON assignments.label_id = labels.id
                    LEFT JOIN gallery_trash AS trash ON trash.gid = assignments.gid
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

    def prepend_mangas_to_playlist(self, gids: Sequence[int], playlist_id: int):
        """Put unique galleries at the front while preserving both orderings."""
        self.initialize()
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if not target_gids:
            return
        playlist_id = int(playlist_id)
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM multi_labels WHERE id = ?",
                (playlist_id,),
            ).fetchone()
            if exists is None:
                raise ValueError("目标播放列表不存在")
            existing_gids = tuple(
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT gid FROM manga_multi_labels
                    WHERE label_id = ?
                    ORDER BY sort_order, added_at, gid
                    """,
                    (playlist_id,),
                )
            )
            target_set = set(target_gids)
            combined = target_gids + tuple(
                gid for gid in existing_gids if gid not in target_set
            )
            now = time.time_ns()
            connection.executemany(
                """
                INSERT OR IGNORE INTO manga_multi_labels(
                    gid, label_id, sort_order, added_at
                ) VALUES (?, ?, 0, ?)
                """,
                ((gid, playlist_id, now) for gid in target_gids),
            )
            connection.executemany(
                """
                UPDATE manga_multi_labels SET sort_order = ?
                WHERE label_id = ? AND gid = ?
                """,
                (
                    (position, playlist_id, gid)
                    for position, gid in enumerate(combined)
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
                           COUNT(CASE WHEN trash.gid IS NULL THEN assignments.gid END)
                    FROM taxonomy_labels AS labels
                    LEFT JOIN manga_taxonomy_labels AS assignments
                        ON assignments.label_id = labels.id
                    LEFT JOIN gallery_trash AS trash ON trash.gid = assignments.gid
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
        state = self.reading_state_for_manga(gid)
        return state[0] if state is not None else None

    def progress_for_mangas(self, gids: Sequence[int]) -> Dict[int, int]:
        return {
            gid: state[0]
            for gid, state in self.reading_states_for_mangas(gids).items()
        }

    def reading_state_for_manga(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT page_index, completed, cleared, started
                FROM manga_reading_progress
                WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        if row is None or bool(row[2]) or not bool(row[3]):
            return None
        return int(row[0]), bool(row[1])

    def reading_states_for_mangas(self, gids: Sequence[int]):
        self.initialize()
        if not gids:
            return {}
        requested_gids = {int(gid) for gid in gids}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, page_index, completed
                FROM manga_reading_progress
                WHERE cleared = 0 AND started = 1
                """
            )
            return {
                int(gid): (int(page_index), bool(completed))
                for gid, page_index, completed in rows
                if int(gid) in requested_gids
            }

    def save_progress(self, gid: int, page_index: int, completed=False):
        self.initialize()
        page_index = max(0, int(page_index))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manga_reading_progress(
                    gid, page_index, updated_at, completed, cleared, started
                ) VALUES (?, ?, ?, ?, 0, 1)
                ON CONFLICT(gid) DO UPDATE SET
                    page_index = excluded.page_index,
                    updated_at = excluded.updated_at,
                    completed = MAX(
                        manga_reading_progress.completed,
                        excluded.completed
                    ),
                    cleared = 0,
                    started = 1
                """,
                (int(gid), page_index, time.time_ns(), 1 if completed else 0),
            )

    def clear_progress(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO manga_reading_progress(
                    gid, page_index, updated_at, completed, cleared, started
                ) VALUES (?, 0, ?, 0, 1, 0)
                ON CONFLICT(gid) DO UPDATE SET
                    page_index = 0,
                    updated_at = excluded.updated_at,
                    completed = 0,
                    cleared = 1,
                    started = 0
                """,
                (int(gid), time.time_ns()),
            )

    def save_latest_similar_search(self, record: LatestSimilarSearch):
        self.initialize()
        result_gids = tuple(dict.fromkeys(int(gid) for gid in record.result_gids))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO latest_similar_search(
                    singleton_id, source_gid, selected_text,
                    result_gids_json, searched_at
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    source_gid = excluded.source_gid,
                    selected_text = excluded.selected_text,
                    result_gids_json = excluded.result_gids_json,
                    searched_at = excluded.searched_at
                """,
                (
                    int(record.source_gid),
                    str(record.selected_text),
                    json.dumps(result_gids, separators=(",", ":")),
                    int(record.searched_at or time.time_ns()),
                ),
            )

    def latest_similar_search(self):
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_gid, selected_text, result_gids_json, searched_at
                FROM latest_similar_search WHERE singleton_id = 1
                """
            ).fetchone()
        if row is None:
            return None
        try:
            result_gids = tuple(
                dict.fromkeys(int(gid) for gid in json.loads(row[2] or "[]"))
            )
        except (TypeError, ValueError):
            result_gids = ()
        return LatestSimilarSearch(
            source_gid=int(row[0]),
            selected_text=str(row[1]),
            result_gids=result_gids,
            searched_at=int(row[3]),
        )

    def resolve_progress(self, gid: int, ehviewer_page_index):
        """Prefer RSViewer progress, importing EhViewer only when ours is absent."""
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT page_index, cleared, started
                FROM manga_reading_progress
                WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        if row is not None:
            if bool(row[1]):
                return None
            if bool(row[2]):
                return int(row[0])
        if ehviewer_page_index is None:
            return None
        imported_progress = max(0, int(ehviewer_page_index))
        if imported_progress == 0:
            return None
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
                    created_at, updated_at, download_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    download_mode = excluded.download_mode,
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
                    str(record.download_mode or DOWNLOAD_MODE_STANDARD),
                ),
            )

            connection.execute(
                "DELETE FROM online_gallery_comments WHERE gid = ?",
                (int(record.gid),),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO online_gallery_comments(
                    gid, comment_id, author, posted, body, score, is_uploader,
                    gallery_links_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                        self._comment_gallery_links_json(comment),
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

    def save_gallery_trash(self, record: GalleryTrashRecord):
        self.initialize()
        now = time.time_ns()
        snapshot_json = json.dumps(
            dict(record.external_snapshot or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gallery_trash(
                    gid, title, folder, dirname, cover_path, page_count, state,
                    external_snapshot_json, error, deleted_at, updated_at,
                    database_path, manga_root
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gid) DO UPDATE SET
                    title = excluded.title,
                    folder = excluded.folder,
                    dirname = excluded.dirname,
                    cover_path = excluded.cover_path,
                    page_count = excluded.page_count,
                    state = excluded.state,
                    external_snapshot_json = excluded.external_snapshot_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    database_path = excluded.database_path,
                    manga_root = excluded.manga_root
                """,
                (
                    int(record.gid),
                    str(record.title),
                    str(Path(record.folder).resolve()),
                    str(record.dirname),
                    str(record.cover_path or ""),
                    max(0, int(record.page_count)),
                    str(record.state),
                    snapshot_json,
                    str(record.error or ""),
                    int(record.deleted_at or now),
                    int(record.updated_at or now),
                    str(Path(record.database_path).resolve())
                    if record.database_path
                    else "",
                    str(Path(record.manga_root).resolve())
                    if record.manga_root
                    else "",
                ),
            )

    def gallery_trash(self, gid: int) -> Optional[GalleryTrashRecord]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT gid, title, folder, dirname, cover_path, page_count,
                       state, external_snapshot_json, error, deleted_at, updated_at,
                       database_path, manga_root
                FROM gallery_trash WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        return self._gallery_trash_from_row(row) if row is not None else None

    def gallery_trash_records(self):
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, title, folder, dirname, cover_path, page_count,
                       state, external_snapshot_json, error, deleted_at, updated_at,
                       database_path, manga_root
                FROM gallery_trash ORDER BY deleted_at DESC, gid DESC
                """
            ).fetchall()
        return tuple(self._gallery_trash_from_row(row) for row in rows)

    def update_gallery_trash_state(self, gid, state, error=""):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE gallery_trash
                SET state = ?, error = ?, updated_at = ? WHERE gid = ?
                """,
                (str(state), str(error or ""), time.time_ns(), int(gid)),
            )

    def delete_gallery_trash_record(self, gid):
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM gallery_trash WHERE gid = ?", (int(gid),))

    def purge_gallery(self, gid):
        """Remove every RSViewer-owned relation after permanent file deletion."""
        self.initialize()
        gid = int(gid)
        with self._connect() as connection:
            connection.execute(
                "UPDATE multi_labels SET last_gid = NULL WHERE last_gid = ?", (gid,)
            )
            for table, column in (
                ("manga_multi_labels", "gid"),
                ("manga_taxonomy_labels", "gid"),
                ("manga_primary_labels", "gid"),
                ("manga_favorites", "gid"),
                ("manga_browsing_history", "gid"),
                ("manga_reading_progress", "gid"),
                ("gallery_update_tasks", "source_gid"),
                ("gallery_original_states", "gid"),
                ("online_gallery_downloads", "gid"),
                ("gallery_sync_records", "gid"),
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?", (gid,)
                )
            connection.execute("DELETE FROM gallery_trash WHERE gid = ?", (gid,))
            latest = connection.execute(
                "SELECT source_gid, result_gids_json FROM latest_similar_search "
                "WHERE singleton_id = 1"
            ).fetchone()
            if latest is not None:
                if int(latest[0]) == gid:
                    connection.execute(
                        "DELETE FROM latest_similar_search WHERE singleton_id = 1"
                    )
                else:
                    result_gids = tuple(
                        current
                        for current in json.loads(latest[1] or "[]")
                        if int(current) != gid
                    )
                    connection.execute(
                        "UPDATE latest_similar_search SET result_gids_json = ? "
                        "WHERE singleton_id = 1",
                        (json.dumps(result_gids, separators=(",", ":")),),
                    )

    def mark_interrupted_gallery_trash(self):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE gallery_trash
                SET state = 'failed',
                    error = CASE
                        WHEN error = '' THEN '上次回收站操作已中断，可重新还原或删除'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE state IN ('moving', 'restoring', 'deleting')
                """,
                (time.time_ns(),),
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
        conditions = ["source_gid NOT IN (SELECT gid FROM gallery_trash)"]
        if not include_completed:
            conditions.append("state != 'completed' AND status < 6")
        query += " WHERE " + " AND ".join(conditions)
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
        now = time.time_ns()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE gallery_update_tasks
                SET state = 'completed', error = '', updated_at = ?
                WHERE status >= 6
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE gallery_update_tasks
                SET state = 'paused',
                    error = CASE
                        WHEN error = '' THEN '上次更新已中断，可继续执行'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE state IN ('queued', 'updating') AND status < 6
                """,
                (now,),
            )

    def delete_gallery_update(self, source_gid):
        """Delete only the task index; folder checkpoints remain recoverable."""
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM gallery_update_tasks WHERE source_gid = ?",
                (int(source_gid),),
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
            progress_row = connection.execute(
                "SELECT page_index, completed, cleared, started "
                "FROM manga_reading_progress WHERE gid = ?",
                (source_gid,),
            ).fetchone()
            if progress_page_index is not None or progress_row is not None:
                page_index = (
                    max(0, int(progress_page_index))
                    if progress_page_index is not None
                    else max(0, int(progress_row[0]))
                )
                completed = int(progress_row[1]) if progress_row is not None else 0
                cleared = int(progress_row[2]) if progress_row is not None else 0
                started = (
                    int(progress_row[3])
                    if progress_row is not None
                    else int(page_index > 0)
                )
                if cleared:
                    started = 0
                elif progress_page_index is not None and page_index > 0:
                    started = 1
                connection.execute(
                    """
                    INSERT INTO manga_reading_progress(
                        gid, page_index, updated_at, completed, cleared, started
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(gid) DO UPDATE SET
                        page_index = excluded.page_index,
                        updated_at = excluded.updated_at,
                        completed = MAX(
                            manga_reading_progress.completed,
                            excluded.completed
                        ),
                        cleared = excluded.cleared,
                        started = excluded.started
                    """,
                    (
                        target_gid,
                        page_index,
                        time.time_ns(),
                        completed,
                        cleared,
                        started,
                    ),
                )
            connection.execute(
                "DELETE FROM manga_reading_progress WHERE gid = ?", (source_gid,)
            )
            connection.execute(
                """
                INSERT INTO gallery_original_states(
                    gid, site, token, dirname, mode, state, completed_pages,
                    page_count, fallback_to_standard, page_modes_json,
                    metadata_json, error, created_at, updated_at
                )
                SELECT ?, site, token, dirname, mode, state, completed_pages,
                       page_count, fallback_to_standard, page_modes_json,
                       metadata_json, error, created_at, updated_at
                FROM gallery_original_states WHERE gid = ?
                ON CONFLICT(gid) DO UPDATE SET
                    site = excluded.site,
                    token = excluded.token,
                    dirname = excluded.dirname,
                    mode = excluded.mode,
                    state = excluded.state,
                    completed_pages = excluded.completed_pages,
                    page_count = excluded.page_count,
                    fallback_to_standard = excluded.fallback_to_standard,
                    page_modes_json = excluded.page_modes_json,
                    metadata_json = excluded.metadata_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (target_gid, source_gid),
            )
            connection.execute(
                "DELETE FROM gallery_original_states WHERE gid = ?", (source_gid,)
            )
            connection.execute(
                "DELETE FROM online_gallery_downloads WHERE gid = ?", (source_gid,)
            )
            connection.execute(
                "DELETE FROM gallery_sync_records WHERE gid = ?", (source_gid,)
            )
            latest = connection.execute(
                "SELECT source_gid, selected_text, result_gids_json, searched_at "
                "FROM latest_similar_search WHERE singleton_id = 1"
            ).fetchone()
            if latest is not None:
                result_gids = tuple(
                    target_gid if int(gid) == source_gid else int(gid)
                    for gid in json.loads(latest[2] or "[]")
                )
                result_gids = tuple(dict.fromkeys(result_gids))
                connection.execute(
                    """
                    UPDATE latest_similar_search
                    SET source_gid = ?, result_gids_json = ?
                    WHERE singleton_id = 1
                    """,
                    (
                        target_gid if int(latest[0]) == source_gid else int(latest[0]),
                        json.dumps(result_gids, separators=(",", ":")),
                    ),
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
                       created_at, updated_at, download_mode
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
                       created_at, updated_at, download_mode
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
                       created_at, updated_at, download_mode
                FROM online_gallery_downloads
                WHERE state != 'completed'
                  AND gid NOT IN (SELECT gid FROM gallery_trash)
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
            connection.execute(
                """
                UPDATE gallery_original_states
                SET state = 'paused',
                    error = CASE
                        WHEN error = '' THEN '上次原图下载已中断，可继续下载'
                        ELSE error
                    END,
                    updated_at = ?
                WHERE state IN ('queued', 'downloading')
                """,
                (time.time_ns(),),
            )

    def save_gallery_original_state(self, record: GalleryOriginalState):
        self.initialize()
        now = time.time_ns()
        page_modes = normalize_original_page_modes(
            record.page_modes,
            record.page_count,
            record.completed_pages,
            record.fallback_to_standard,
        )
        fallback_to_standard = (
            ORIGINAL_PAGE_MODE_BASE in page_modes
            or bool(record.fallback_to_standard and not any(page_modes))
        )
        page_modes_json = json.dumps(
            list(page_modes),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        metadata_json = json.dumps(
            dict(record.metadata or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gallery_original_states(
                    gid, site, token, dirname, mode, state, completed_pages,
                    page_count, fallback_to_standard, page_modes_json,
                    metadata_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gid) DO UPDATE SET
                    site = excluded.site,
                    token = excluded.token,
                    dirname = excluded.dirname,
                    mode = excluded.mode,
                    state = excluded.state,
                    completed_pages = excluded.completed_pages,
                    page_count = excluded.page_count,
                    fallback_to_standard = excluded.fallback_to_standard,
                    page_modes_json = excluded.page_modes_json,
                    metadata_json = excluded.metadata_json,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    int(record.gid),
                    str(record.site),
                    str(record.token),
                    str(record.dirname),
                    str(record.mode),
                    str(record.state),
                    max(0, int(record.completed_pages)),
                    max(0, int(record.page_count)),
                    int(fallback_to_standard),
                    page_modes_json,
                    metadata_json,
                    str(record.error or ""),
                    int(record.created_at or now),
                    int(record.updated_at or now),
                ),
            )

    def update_gallery_original_state(
        self,
        gid,
        state,
        completed_pages=None,
        page_count=None,
        error="",
        dirname=None,
        fallback_to_standard=None,
        page_modes=None,
    ):
        self.initialize()
        assignments = ["state = ?", "error = ?", "updated_at = ?"]
        values = [str(state), str(error or ""), time.time_ns()]
        if completed_pages is not None:
            assignments.append("completed_pages = ?")
            values.append(max(0, int(completed_pages)))
        if page_count is not None:
            assignments.append("page_count = ?")
            values.append(max(0, int(page_count)))
        if dirname is not None:
            assignments.append("dirname = ?")
            values.append(str(dirname))
        if fallback_to_standard is not None:
            assignments.append("fallback_to_standard = ?")
            values.append(int(bool(fallback_to_standard)))
        if page_modes is not None:
            assignments.append("page_modes_json = ?")
            values.append(
                json.dumps(
                    list(page_modes),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )
        values.append(int(gid))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE gallery_original_states SET {', '.join(assignments)} "
                "WHERE gid = ?",
                tuple(values),
            )

    def gallery_original_state(self, gid: int) -> Optional[GalleryOriginalState]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT gid, site, token, dirname, mode, state, completed_pages,
                       page_count, fallback_to_standard, page_modes_json,
                       metadata_json, error, created_at, updated_at
                FROM gallery_original_states WHERE gid = ?
                """,
                (int(gid),),
            ).fetchone()
        return self._gallery_original_from_row(row) if row is not None else None

    def gallery_original_states_for_mangas(
        self, gids: Sequence[int]
    ) -> Dict[int, GalleryOriginalState]:
        self.initialize()
        target_gids = {int(gid) for gid in gids}
        if not target_gids:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT gid, site, token, dirname, mode, state, completed_pages,
                       page_count, fallback_to_standard, page_modes_json,
                       metadata_json, error, created_at, updated_at
                FROM gallery_original_states
                """
            ).fetchall()
        return {
            int(row[0]): self._gallery_original_from_row(row)
            for row in rows
            if int(row[0]) in target_gids
        }

    def delete_gallery_original_state(self, gid: int):
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM gallery_original_states WHERE gid = ?",
                (int(gid),),
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
                SELECT comment_id, author, posted, body, score, is_uploader,
                       gallery_links_json
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
                gallery_links=UserLibraryRepository._comment_gallery_links_from_json(
                    row[6]
                ),
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
                gid, comment_id, author, posted, body, score, is_uploader,
                gallery_links_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    UserLibraryRepository._comment_gallery_links_json(comment),
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
            download_mode=(
                str(row[12] or DOWNLOAD_MODE_STANDARD)
                if len(row) > 12
                else DOWNLOAD_MODE_STANDARD
            ),
            metadata=metadata,
            error=str(row[9] or ""),
            created_at=int(row[10]),
            updated_at=int(row[11]),
        )

    @staticmethod
    def _comment_gallery_links_json(comment):
        return json.dumps(
            [
                {
                    "gid": int(link.gid),
                    "token": str(link.token),
                    "text": str(link.text or ""),
                }
                for link in tuple(comment.gallery_links or ())
                if int(link.gid) > 0 and str(link.token or "")
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _comment_gallery_links_from_json(value):
        try:
            raw_links = json.loads(str(value or "[]"))
        except (TypeError, ValueError):
            raw_links = []
        if not isinstance(raw_links, list):
            return ()
        links = []
        seen = set()
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                continue
            try:
                gid = int(raw_link.get("gid", 0))
            except (TypeError, ValueError):
                continue
            token = str(raw_link.get("token", ""))
            identity = (gid, token.casefold())
            if gid <= 0 or not token or identity in seen:
                continue
            seen.add(identity)
            links.append(
                OnlineGalleryLink(
                    gid=gid,
                    token=token,
                    text=str(raw_link.get("text", "")),
                )
            )
        return tuple(links)

    @staticmethod
    def _gallery_original_from_row(row) -> GalleryOriginalState:
        try:
            raw_page_modes = json.loads(str(row[9] or "[]"))
        except (TypeError, ValueError):
            raw_page_modes = []
        if not isinstance(raw_page_modes, list):
            raw_page_modes = []
        page_modes = normalize_original_page_modes(
            raw_page_modes,
            int(row[7]),
            int(row[6]),
            bool(row[8]),
        )
        try:
            metadata = json.loads(str(row[10] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return GalleryOriginalState(
            gid=int(row[0]),
            site=str(row[1]),
            token=str(row[2]),
            dirname=str(row[3]),
            mode=str(row[4]),
            state=str(row[5]),
            completed_pages=int(row[6]),
            page_count=int(row[7]),
            fallback_to_standard=(
                ORIGINAL_PAGE_MODE_BASE in page_modes or bool(row[8])
            ),
            page_modes=page_modes,
            metadata=metadata,
            error=str(row[11] or ""),
            created_at=int(row[12]),
            updated_at=int(row[13]),
        )

    @staticmethod
    def _gallery_trash_from_row(row) -> GalleryTrashRecord:
        try:
            snapshot = json.loads(str(row[7] or "{}"))
        except (TypeError, ValueError):
            snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        return GalleryTrashRecord(
            gid=int(row[0]),
            title=str(row[1]),
            folder=Path(str(row[2])),
            dirname=str(row[3]),
            cover_path=Path(str(row[4])) if str(row[4] or "") else None,
            page_count=max(0, int(row[5])),
            state=str(row[6]),
            external_snapshot=snapshot,
            error=str(row[8] or ""),
            deleted_at=int(row[9]),
            updated_at=int(row[10]),
            database_path=Path(str(row[11])) if str(row[11] or "") else None,
            manga_root=Path(str(row[12])) if str(row[12] or "") else None,
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
