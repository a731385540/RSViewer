import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.domain.gallery_trash import GalleryTrashRecord, TRASHED
from app.repositories.ehviewer_download_repository import EhViewerDownloadRepository
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.gallery_trash import (
    permanently_delete_trashed_gallery,
    restore_trashed_gallery,
    trash_local_gallery,
)
from app.services.library_organizer import scan_orphan_gallery_folders
from app.view.recycle_bin_interface import RecycleBinInterface
from app.workers.gallery_trash_worker import GalleryTrashWorker


class GalleryTrashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manga_root = self.root / "downloads"
        self.manga_root.mkdir()
        self.folder = self.manga_root / "42-gallery"
        self.folder.mkdir()
        (self.folder / ".thumb").write_bytes(b"cover")
        (self.folder / "00000001.jpg").write_bytes(b"page")
        self.external_db = self.root / "eh.db"
        self._create_external_database()
        self.external_repository = EhViewerDownloadRepository(
            self.external_db, self.manga_root
        )
        self.user_repository = UserLibraryRepository(self.root / "rsviewer.db")
        self.item = SimpleNamespace(
            gid=42,
            folder=self.folder,
            display_title="Trash test gallery",
            cover_image_path=self.folder / ".thumb",
            page_count=2,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_external_database(self):
        tag_columns = ", ".join(
            f'"{name}" TEXT'
            for name in (
                "ROWS", "ARTIST", "COSPLAYER", "CHARACTER", "FEMALE",
                "GROUP", "LANGUAGE", "MALE", "MISC", "MIXED", "OTHER",
                "PARODY", "RECLASS",
            )
        )
        with closing(sqlite3.connect(self.external_db)) as connection:
            connection.executescript(
                f"""
                CREATE TABLE DOWNLOADS (
                    GID INTEGER PRIMARY KEY NOT NULL,
                    TOKEN TEXT, TITLE TEXT, TITLE_JPN TEXT, THUMB TEXT,
                    CATEGORY INTEGER NOT NULL, POSTED TEXT, UPLOADER TEXT,
                    RATING REAL NOT NULL, SIMPLE_LANGUAGE TEXT,
                    STATE INTEGER NOT NULL, LEGACY INTEGER NOT NULL,
                    TIME INTEGER NOT NULL, LABEL TEXT, ARCHIVE_URI TEXT
                );
                CREATE TABLE DOWNLOAD_DIRNAME (
                    GID INTEGER PRIMARY KEY NOT NULL, DIRNAME TEXT
                );
                CREATE TABLE DOWNLOAD_LABELS (
                    _id INTEGER PRIMARY KEY AUTOINCREMENT,
                    LABEL TEXT NOT NULL, TIME INTEGER NOT NULL
                );
                CREATE TABLE Gallery_Tags (
                    GID INTEGER PRIMARY KEY NOT NULL, {tag_columns},
                    CREATE_TIME INTEGER, UPDATE_TIME INTEGER
                );
                INSERT INTO DOWNLOADS VALUES (
                    42, 'gallery-token', 'English title', 'Original title',
                    'https://example/thumb.jpg', 4, '2026-01-02', 'uploader',
                    4.5, 'chinese', 3, 7, 123456, 'Keep Label', 'archive://42'
                );
                INSERT INTO DOWNLOAD_DIRNAME VALUES (42, '42-gallery');
                INSERT INTO Gallery_Tags(
                    GID, ARTIST, LANGUAGE, CREATE_TIME, UPDATE_TIME
                ) VALUES (42, 'artist name', 'chinese', 100, 200);
                """
            )

    def _external_rows(self):
        with closing(sqlite3.connect(self.external_db)) as connection:
            return (
                connection.execute("SELECT * FROM DOWNLOADS WHERE GID = 42").fetchone(),
                connection.execute(
                    "SELECT * FROM DOWNLOAD_DIRNAME WHERE GID = 42"
                ).fetchone(),
                connection.execute(
                    "SELECT * FROM Gallery_Tags WHERE GID = 42"
                ).fetchone(),
            )

    def test_trash_and_restore_preserve_external_rows_and_own_relations(self):
        before = self._external_rows()
        playlist = self.user_repository.create_playlist("Keep playlist")
        self.user_repository.assign_label(42, playlist)
        self.user_repository.set_favorite((42,), True)
        self.user_repository.save_progress(42, 9)

        record = trash_local_gallery(
            self.item, self.external_repository, self.user_repository
        )

        self.assertEqual((None, None, None), self._external_rows())
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(TRASHED, record.state)
        self.assertEqual(self.external_db.resolve(), record.database_path)
        self.assertEqual(self.manga_root.resolve(), record.manga_root)
        self.assertEqual("Keep Label", record.external_snapshot["DOWNLOADS"]["values"][13])
        self.assertEqual(0, self.user_repository.list_playlists()[0][2])
        self.assertEqual(
            (),
            scan_orphan_gallery_folders(
                self.external_db,
                self.manga_root,
                self.user_repository,
                "ehentai",
            ),
        )

        restore_trashed_gallery(
            record, self.external_repository, self.user_repository
        )

        self.assertEqual(before, self._external_rows())
        self.assertIsNone(self.user_repository.gallery_trash(42))
        self.assertEqual((42,), self.user_repository.playlist_items(playlist))
        self.assertEqual((42,), self.user_repository.favorite_gids())
        self.assertEqual(9, self.user_repository.progress_for_manga(42))
        self.assertEqual(1, self.user_repository.list_playlists()[0][2])

    def test_restore_uses_the_data_source_saved_with_the_trash_record(self):
        before = self._external_rows()
        record = trash_local_gallery(
            self.item, self.external_repository, self.user_repository
        )
        other_root = self.root / "other-downloads"
        other_root.mkdir()
        wrong_repository = EhViewerDownloadRepository(
            self.root / "other.db", other_root
        )
        results = []
        worker = GalleryTrashWorker(
            GalleryTrashWorker.RESTORE,
            (record,),
            wrong_repository,
            self.user_repository,
            other_root,
        )
        worker.signals.completed.connect(results.append)

        worker.run()

        self.assertEqual(before, self._external_rows())
        self.assertIsNone(self.user_repository.gallery_trash(42))
        self.assertEqual(1, len(results[0].succeeded))
        self.assertEqual((), results[0].failed)

    def test_restore_keeps_newer_rows_already_restored_by_another_process(self):
        record = trash_local_gallery(
            self.item, self.external_repository, self.user_repository
        )
        self.external_repository.restore_gallery_from_trash(
            record.gid, record.folder, record.external_snapshot
        )
        with closing(sqlite3.connect(self.external_db)) as connection:
            connection.execute(
                "UPDATE DOWNLOADS SET LABEL = 'Newer Label' WHERE GID = 42"
            )
            connection.commit()

        restore_trashed_gallery(
            record, self.external_repository, self.user_repository
        )

        with closing(sqlite3.connect(self.external_db)) as connection:
            label = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = 42"
            ).fetchone()[0]
        self.assertEqual("Newer Label", label)
        self.assertIsNone(self.user_repository.gallery_trash(42))

    def test_permanent_delete_removes_folder_and_all_own_relations(self):
        playlist = self.user_repository.create_playlist("Delete playlist")
        self.user_repository.assign_label(42, playlist)
        self.user_repository.set_favorite((42,), True)
        self.user_repository.save_progress(42, 3)
        record = trash_local_gallery(
            self.item, self.external_repository, self.user_repository
        )

        permanently_delete_trashed_gallery(
            record,
            self.external_repository,
            self.user_repository,
            self.manga_root,
        )

        self.assertFalse(self.folder.exists())
        self.assertIsNone(self.user_repository.gallery_trash(42))
        self.assertEqual((), self.user_repository.playlist_items(playlist))
        self.assertEqual((), self.user_repository.favorite_gids())
        self.assertIsNone(self.user_repository.progress_for_manga(42))

    def test_recycle_bin_supports_select_all_and_toolbar_actions(self):
        now = time.time_ns()
        records = (
            GalleryTrashRecord(
                gid=1,
                title="One",
                folder=self.manga_root / "one",
                dirname="one",
                state=TRASHED,
                external_snapshot={},
                deleted_at=now,
            ),
            GalleryTrashRecord(
                gid=2,
                title="Two",
                folder=self.manga_root / "two",
                dirname="two",
                state=TRASHED,
                external_snapshot={},
                deleted_at=now - 1,
            ),
        )
        interface = RecycleBinInterface()
        restored = []
        deleted = []
        interface.restoreRequested.connect(restored.append)
        interface.deleteRequested.connect(deleted.append)
        try:
            interface.setRecords(records)
            interface._toggleSelectAll(True)
            interface.restoreButton.click()
            interface.deleteButton.click()
            self.assertEqual((1, 2), tuple(record.gid for record in restored[0]))
            self.assertEqual((1, 2), tuple(record.gid for record in deleted[0]))
            interface._setSelected(1, False)
            self.assertEqual(Qt.PartiallyChecked, interface.selectAllCheckBox.checkState())
        finally:
            interface.close()
            interface.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
