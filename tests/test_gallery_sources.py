import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.domain.online_download import GallerySyncRecord, OnlineGalleryDownloadRecord
from app.repositories.user_library_repository import UserLibraryRepository


class GallerySourceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "rsviewer.db"
        self.repository = UserLibraryRepository(self.database)
        self.repository.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_download_row_defaults_to_exh_then_explicit_sync_corrects_it(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO DOWNLOADS(GID, TITLE, TITLE_JPN, CATEGORY, RATING, "
                "TIME, STATE, LEGACY) VALUES (123, 'Title', '', 4, 0, 1, 0, 0)"
            )
            connection.commit()
            self.assertEqual(
                ("exhentai", "123"),
                connection.execute(
                    "SELECT source, remote_id FROM gallery_sources WHERE local_gid = 123"
                ).fetchone(),
            )

        self.repository.save_gallery_sync(
            GallerySyncRecord(123, "ehentai", "token", {}, 1)
        )

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                ("ehentai", "123"),
                connection.execute(
                    "SELECT source, remote_id FROM gallery_sources WHERE local_gid = 123"
                ).fetchone(),
            )

    def test_task_delete_only_removes_a_source_without_a_local_gallery(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO gallery_sources(local_gid, source, remote_id) "
                "VALUES (999, 'ehentai', '999')"
            )

    def test_nh_sources_receive_stable_non_colliding_local_ids(self):
        nhc_gid = self.repository.ensure_gallery_local_gid("nhc", "123")
        nhn_gid = self.repository.ensure_gallery_local_gid("nhn", "123")

        self.assertLess(nhc_gid, 0)
        self.assertLess(nhn_gid, 0)
        self.assertNotEqual(nhc_gid, nhn_gid)
        self.assertEqual(
            nhc_gid,
            self.repository.ensure_gallery_local_gid("nhc", "123"),
        )

        self.repository.save_online_gallery_download(
            OnlineGalleryDownloadRecord(
                gid=nhc_gid,
                site="nhc",
                token="",
                title="NHC title",
                dirname="NHC-123-title",
                page_count=2,
                metadata={"source_id": "123"},
            )
        )
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                ("nhc", "123"),
                connection.execute(
                    "SELECT source, remote_id FROM gallery_sources WHERE local_gid = ?",
                    (nhc_gid,),
                ).fetchone(),
            )
            connection.commit()

        self.repository.delete_online_gallery_download(999)

        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM gallery_sources WHERE local_gid = 999"
                ).fetchone()
            )

    def test_v23_migration_prefers_sync_source_and_defaults_unknown_to_exh(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DROP TRIGGER gallery_sources_download_insert")
            connection.execute("DROP TABLE gallery_sources")
            connection.execute(
                "INSERT INTO DOWNLOADS(GID, TITLE, TITLE_JPN, CATEGORY, RATING, "
                "TIME, STATE, LEGACY) VALUES (1, 'Known', '', 4, 0, 1, 0, 0)"
            )
            connection.execute(
                "INSERT INTO DOWNLOADS(GID, TITLE, TITLE_JPN, CATEGORY, RATING, "
                "TIME, STATE, LEGACY) VALUES (2, 'Unknown', '', 4, 0, 1, 0, 0)"
            )
            connection.execute(
                "INSERT INTO gallery_sync_records(gid, site, token, metadata_json, updated_at) "
                "VALUES (1, 'ehentai', 'token', '{}', 1)"
            )
            connection.execute("PRAGMA user_version = 23")
            connection.commit()

        self.repository.initialize()

        with closing(sqlite3.connect(self.database)) as connection:
            rows = dict(
                connection.execute(
                    "SELECT local_gid, source FROM gallery_sources ORDER BY local_gid"
                )
            )
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual({1: "ehentai", 2: "exhentai"}, rows)
        self.assertEqual(UserLibraryRepository.SCHEMA_VERSION, version)


if __name__ == "__main__":
    unittest.main()
