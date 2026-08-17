import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.repositories.ehviewer_schema import (
    EHVIEWER_TABLES,
    EHVIEWER_USER_VERSION,
    ensure_ehviewer_schema,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.ehviewer_database_transfer import (
    export_ehviewer_database,
    import_ehviewer_database,
)


class EhViewerDatabaseTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source-eh.db"
        self.repository = UserLibraryRepository(self.root / "rsviewer.db")
        with closing(sqlite3.connect(self.source)) as connection:
            ensure_ehviewer_schema(connection)
            connection.execute(
                """
                INSERT INTO DOWNLOADS(
                    GID, TOKEN, TITLE, TITLE_JPN, THUMB, CATEGORY, POSTED,
                    UPLOADER, RATING, SIMPLE_LANGUAGE, STATE, LEGACY, TIME,
                    LABEL, ARCHIVE_URI
                ) VALUES (42, 'token', 'Title', '标题', 'thumb', 1, 'posted',
                          'uploader', 4.5, 'chinese', 3, 0, 123, 'Label', NULL)
                """
            )
            connection.execute(
                "INSERT INTO DOWNLOAD_DIRNAME VALUES (42, '42-title')"
            )
            connection.execute(
                "INSERT INTO DOWNLOAD_LABELS VALUES (1, 'Label', 1)"
            )
            connection.execute(
                """
                INSERT INTO Gallery_Tags(GID, ARTIST, CREATE_TIME, UPDATE_TIME)
                VALUES (42, 'artist', 1, 2)
                """
            )
            connection.execute(
                """
                INSERT INTO LOCAL_FAVORITES VALUES (
                    42, 'token', 'Title', '标题', 'thumb', 1, 'posted',
                    'uploader', 4.5, 'chinese', 456
                )
                """
            )
            connection.execute(
                """
                INSERT INTO HISTORY VALUES (
                    42, 'token', 'Title', '标题', 'thumb', 1, 'posted',
                    'uploader', 4.5, 'chinese', 0, 789
                )
                """
            )
            connection.execute(
                """
                INSERT INTO QUICK_SEARCH VALUES (
                    1, 'recent', 0, 0, 'artist:test', 0, 0, 0, 0, 10
                )
                """
            )
            connection.execute(
                f"PRAGMA user_version = {EHVIEWER_USER_VERSION}"
            )
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_import_and_export_round_trip_without_mutating_source(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()

        result = import_ehviewer_database(self.source, self.repository)

        self.assertEqual(1, result.gallery_count)
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).digest())
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            self.assertEqual(
                UserLibraryRepository.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(
                ("Title", "Label"),
                connection.execute(
                    "SELECT TITLE, LABEL FROM DOWNLOADS WHERE GID = 42"
                ).fetchone(),
            )
            self.assertEqual(
                (456,),
                connection.execute(
                    "SELECT created_at FROM manga_favorites WHERE gid = 42"
                ).fetchone(),
            )
            self.assertEqual(
                (789,),
                connection.execute(
                    "SELECT viewed_at FROM manga_browsing_history WHERE gid = 42"
                ).fetchone(),
            )
            self.assertEqual(
                ("Black_List",),
                connection.execute(
                    """
                    SELECT tbl_name FROM sqlite_master
                    WHERE type = 'index' AND name = 'IDX_Black_List_BADGAYNAME'
                    """
                ).fetchone(),
            )

        exported = self.root / "exported" / "eh.db"
        export_result = export_ehviewer_database(self.repository, exported)

        self.assertEqual(1, export_result.gallery_count)
        with closing(sqlite3.connect(exported)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(set(EHVIEWER_TABLES).issubset(tables))
            self.assertEqual(
                EHVIEWER_USER_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(
                ("42-title",),
                connection.execute(
                    "SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = 42"
                ).fetchone(),
            )
            self.assertEqual(1, connection.execute(
                "SELECT COUNT(*) FROM LOCAL_FAVORITES"
            ).fetchone()[0])
            self.assertEqual(789, connection.execute(
                "SELECT TIME FROM HISTORY WHERE GID = 42"
            ).fetchone()[0])
            self.assertEqual("ok", connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0])
            self.assertEqual(
                ("Black_List",),
                connection.execute(
                    """
                    SELECT tbl_name FROM sqlite_master
                    WHERE type = 'index' AND name = 'IDX_Black_List_BADGAYNAME'
                    """
                ).fetchone(),
            )

    def test_v15_migration_retargets_trash_restore_to_own_database(self):
        self.repository.initialize()
        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO gallery_trash(
                    gid, title, folder, dirname, state, external_snapshot_json,
                    deleted_at, updated_at, database_path, manga_root
                ) VALUES (42, 'Title', 'trash-folder', '42-title', 'trashed',
                          '{}', 1, 1, 'old-eh.db', 'manga-root')
                """
            )
            connection.execute("PRAGMA user_version = 14")
            connection.commit()

        self.repository.initialize()

        with closing(sqlite3.connect(self.repository.database_path)) as connection:
            self.assertEqual(
                UserLibraryRepository.SCHEMA_VERSION,
                connection.execute("PRAGMA user_version").fetchone()[0],
            )
            self.assertEqual(
                (str(self.repository.database_path),),
                connection.execute(
                    "SELECT database_path FROM gallery_trash WHERE gid = 42"
                ).fetchone(),
            )


if __name__ == "__main__":
    unittest.main()
