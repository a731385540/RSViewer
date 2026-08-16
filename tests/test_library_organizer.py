import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.domain.online_download import (
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_PAUSED,
)
from app.repositories.ehviewer_download_repository import (
    EH_STATE_FAILED,
    EH_STATE_FINISHED,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.library_organizer import (
    OrphanGalleryFolder,
    scan_orphan_gallery_folders,
    sync_orphan_gallery_folder,
)
from app.view.library_organizer_interface import LibraryOrganizerInterface
from app.workers.library_organizer_worker import LibraryOrganizerActionWorker


class LibraryOrganizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.manga_root = self.root / "downloads"
        self.manga_root.mkdir()
        self.external_db = self.root / "eh.db"
        self.user_repository = UserLibraryRepository(self.root / "rsviewer.db")
        self._create_external_database()

        registered = self.manga_root / "1-registered"
        registered.mkdir()
        self._write_sidecar(registered, 1, 1)
        orphan = self.manga_root / "2-orphan"
        orphan.mkdir()
        self._write_sidecar(orphan, 2, 2)
        (orphan / "00000001.jpg").write_bytes(b"page one")
        duplicate = self.manga_root / "duplicate-existing-gid"
        duplicate.mkdir()
        self._write_sidecar(duplicate, 1, 1)
        (self.manga_root / "unknown-folder").mkdir()

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
                    1, 'token-one', 'Registered', '', '', 4, '', '', 0,
                    NULL, 3, 0, 1, '', NULL
                );
                INSERT INTO DOWNLOAD_DIRNAME VALUES (1, '1-registered');
                """
            )

    @staticmethod
    def _write_sidecar(folder, gid, page_count):
        tokens = "".join(
            f"{index} {index + 1:010x}\n" for index in range(page_count)
        )
        (folder / ".ehviewer").write_text(
            "VERSION2\n00000000\n"
            f"{gid}\ntoken-{gid}\n1\n1\n20\n{page_count}\n{tokens}",
            encoding="ascii",
        )

    def _scan(self):
        return scan_orphan_gallery_folders(
            self.external_db,
            self.manga_root,
            self.user_repository,
            "ehentai",
        )

    def test_scan_lists_only_database_missing_directories(self):
        records = self._scan()

        self.assertEqual(
            ["2-orphan", "unknown-folder"],
            [record.dirname for record in records],
        )
        orphan, unknown = records
        self.assertTrue(orphan.syncable)
        self.assertEqual((2, 1, 2), (
            orphan.gid,
            orphan.downloaded_pages,
            orphan.page_count,
        ))
        self.assertFalse(unknown.syncable)
        self.assertIn(".ehviewer", unknown.issue)

    def test_sync_registers_exact_folder_in_both_databases(self):
        with closing(sqlite3.connect(self.external_db)) as connection:
            connection.execute(
                "INSERT INTO DOWNLOAD_DIRNAME(GID, DIRNAME) VALUES (2, '2-orphan')"
            )
            connection.commit()
        entry = next(record for record in self._scan() if record.gid == 2)
        with closing(sqlite3.connect(self.external_db)) as connection:
            schema_before = tuple(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )

        sync_orphan_gallery_folder(
            entry,
            self.external_db,
            self.manga_root,
            self.user_repository,
        )

        with closing(sqlite3.connect(self.external_db)) as connection:
            external = connection.execute(
                """
                SELECT d.GID, d.TOKEN, d.STATE, n.DIRNAME
                FROM DOWNLOADS d JOIN DOWNLOAD_DIRNAME n ON n.GID = d.GID
                WHERE d.GID = 2
                """
            ).fetchone()
            schema_after = tuple(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        self.assertEqual((2, "token-2", EH_STATE_FAILED, "2-orphan"), external)
        self.assertEqual(schema_before, schema_after)
        own_record = self.user_repository.online_gallery_download(2)
        self.assertEqual(ONLINE_DOWNLOAD_PAUSED, own_record.state)
        self.assertEqual("2-orphan", own_record.dirname)
        self.assertTrue(own_record.metadata["organized_from_local_folder"])
        self.assertEqual(
            ["unknown-folder"],
            [record.dirname for record in self._scan()],
        )

    def test_sync_rejects_nested_directory(self):
        container = self.manga_root / "container"
        container.mkdir()
        nested = container / "3-nested"
        nested.mkdir()
        self._write_sidecar(nested, 3, 1)
        entry = OrphanGalleryFolder(
            folder=nested,
            dirname=nested.name,
            title="Nested",
            gid=3,
            gallery_token="token-3",
            page_count=1,
            syncable=True,
        )

        with self.assertRaisesRegex(ValueError, "直接子目录|根目录"):
            sync_orphan_gallery_folder(
                entry,
                self.external_db,
                self.manga_root,
                self.user_repository,
            )

    def test_complete_folder_is_imported_as_finished(self):
        orphan = self.manga_root / "2-orphan"
        image = QImage(2, 2, QImage.Format_RGB32)
        image.fill(Qt.black)
        self.assertTrue(image.save(str(orphan / "00000001.png")))
        (orphan / "00000001.jpg").unlink()
        self.assertTrue(image.save(str(orphan / "00000002.png")))
        entry = next(record for record in self._scan() if record.gid == 2)

        sync_orphan_gallery_folder(
            entry,
            self.external_db,
            self.manga_root,
            self.user_repository,
        )

        with closing(sqlite3.connect(self.external_db)) as connection:
            state = connection.execute(
                "SELECT STATE FROM DOWNLOADS WHERE GID = 2"
            ).fetchone()[0]
        self.assertEqual(EH_STATE_FINISHED, state)
        self.assertEqual(
            ONLINE_DOWNLOAD_COMPLETED,
            self.user_repository.online_gallery_download(2).state,
        )

    def test_interface_supports_select_all_and_partial_selection(self):
        records = self._scan()
        interface = LibraryOrganizerInterface()
        try:
            interface.setRecords(records)
            interface._toggleSelectAll(True)
            self.assertEqual(2, len(interface.selectedEntries()))
            first = records[0]
            interface._setSelected(first.key, False)
            self.assertEqual(Qt.PartiallyChecked, interface.selectAllCheckBox.checkState())
            self.assertEqual(["unknown-folder"], [
                entry.dirname for entry in interface.selectedEntries()
            ])
        finally:
            interface.close()
            interface.deleteLater()
            QApplication.processEvents()

    def test_interface_uses_responsive_cover_card_grid(self):
        records = self._scan()
        interface = LibraryOrganizerInterface()
        try:
            interface.resize(900, 700)
            interface.show()
            interface.setRecords(records)
            QApplication.processEvents()
            interface._relayoutCards()

            self.assertGreaterEqual(interface._lastColumns, 3)
            cards = list(interface._cards.values())
            self.assertEqual(0, interface.contentLayout.indexOf(cards[0]))
            self.assertGreater(cards[0].coverLabel.height(), cards[0].coverLabel.width())

            interface.resize(360, 600)
            QApplication.processEvents()
            interface._relayoutCards()
            self.assertEqual(1, interface._lastColumns)
        finally:
            interface.close()
            interface.deleteLater()
            QApplication.processEvents()

    def test_delete_worker_uses_recycle_bin_operation(self):
        records = self._scan()
        completed = []
        worker = LibraryOrganizerActionWorker(
            LibraryOrganizerActionWorker.DELETE,
            records,
            self.external_db,
            self.manga_root,
            self.user_repository,
        )
        worker.signals.completed.connect(completed.append)

        with patch(
            "app.workers.library_organizer_worker.recycle_orphan_gallery_folder"
        ) as recycle:
            worker.run()

        self.assertEqual(2, recycle.call_count)
        self.assertEqual(2, len(completed[0].succeeded))
        self.assertEqual((), completed[0].failed)


if __name__ == "__main__":
    unittest.main()
