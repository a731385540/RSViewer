import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.domain.gallery_ad_cleanup import (
    AD_ACTION_STAGE,
    AD_CLEANUP_CLEANED,
    AD_CLEANUP_FAILED,
    AD_CLEANUP_MOVING,
    AD_CLEANUP_STAGED,
    GalleryAdCleanupRecord,
)
from app.domain.manga import MangaItem, local_page_slot_count
from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    GalleryOriginalState,
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_STAGED,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.workers.gallery_ad_cleanup_worker import GalleryAdCleanupWorker


class GalleryAdCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "downloads"
        self.root.mkdir()
        self.folder = self.root / "gallery"
        self.folder.mkdir()
        self.repository = UserLibraryRepository(
            Path(self.temp.name) / "rsviewer.db"
        )
        self.repository.initialize()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _write_pages(folder, total, prefix=b"page"):
        folder.mkdir(parents=True, exist_ok=True)
        for index in range(total):
            (folder / f"{index + 1:08d}.jpg").write_bytes(
                prefix + str(index).encode("ascii")
            )

    def _worker(self, action, cutoff=None, total=6, original_state=None):
        return GalleryAdCleanupWorker(
            2841576,
            self.folder,
            self.root,
            self.repository,
            action,
            page_count=total,
            cutoff_page_index=cutoff,
            original_state=original_state,
        )

    def test_stage_and_restore_move_standard_and_original_tail(self):
        self._write_pages(self.folder, 6, b"standard")
        self._write_pages(self.folder / "original", 6, b"original")
        original = GalleryOriginalState(
            gid=2841576,
            site="exhentai",
            token="token",
            dirname=self.folder.name,
            mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            state=ORIGINAL_STATE_STAGED,
            completed_pages=6,
            page_count=6,
        )

        self._worker(
            GalleryAdCleanupWorker.STAGE,
            cutoff=3,
            original_state=original,
        ).run()

        record = self.repository.gallery_ad_cleanup(2841576)
        self.assertEqual(AD_CLEANUP_STAGED, record.state)
        self.assertEqual(3, record.retained_page_count)
        self.assertEqual(6, len(record.manifest))
        self.assertEqual(3, len(tuple(self.folder.glob("*.jpg"))))
        self.assertEqual(3, len(tuple((self.folder / "original").glob("*.jpg"))))
        self.assertEqual(
            3, len(tuple((self.folder / "delete" / "standard").glob("*.jpg")))
        )
        self.assertEqual(
            3, len(tuple((self.folder / "delete" / "original").glob("*.jpg")))
        )

        item = MangaItem(
            gid=2841576,
            english_title="Gallery",
            original_title="",
            category=0,
            category_name="Manga",
            primary_label="",
            multiple_labels=(),
            tags=(),
            folder=self.folder,
            cover_path=self.folder / "00000001.jpg",
            thumbnail_path=None,
            page_paths=tuple(sorted(self.folder.glob("*.jpg"))),
            page_count=6,
            page_tokens=tuple(f"{index:010x}" for index in range(6)),
            ad_cleanup_state=record.state,
            ad_cleanup_cutoff_page_index=record.cutoff_page_index,
        )
        self.assertEqual(3, local_page_slot_count(item))

        self._worker(GalleryAdCleanupWorker.RESTORE).run()

        self.assertIsNone(self.repository.gallery_ad_cleanup(2841576))
        self.assertFalse((self.folder / "delete").exists())
        self.assertEqual(6, len(tuple(self.folder.glob("*.jpg"))))
        self.assertEqual(6, len(tuple((self.folder / "original").glob("*.jpg"))))

    def test_permanent_cleanup_keeps_cutoff_but_removes_backup(self):
        self._write_pages(self.folder, 6)
        self._worker(GalleryAdCleanupWorker.STAGE, cutoff=3).run()
        self._worker(GalleryAdCleanupWorker.DELETE).run()

        record = self.repository.gallery_ad_cleanup(2841576)
        self.assertEqual(AD_CLEANUP_CLEANED, record.state)
        self.assertEqual(3, record.retained_page_count)
        self.assertFalse((self.folder / "delete").exists())
        self.assertEqual(3, len(tuple(self.folder.glob("*.jpg"))))

    def test_active_original_cleanup_also_stages_compressed_history_tail(self):
        self._write_pages(self.folder, 6, b"active-original")
        self._write_pages(self.folder / "history" / "del", 6, b"base")
        original = GalleryOriginalState(
            gid=2841576,
            site="exhentai",
            token="token",
            dirname=self.folder.name,
            mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            state=ORIGINAL_STATE_ACTIVE,
            completed_pages=6,
            page_count=6,
        )

        self._worker(
            GalleryAdCleanupWorker.STAGE,
            cutoff=3,
            original_state=original,
        ).run()

        self.assertEqual(
            3, len(tuple((self.folder / "delete" / "original").glob("*.jpg")))
        )
        self.assertEqual(
            3, len(tuple((self.folder / "delete" / "standard").glob("*.jpg")))
        )
        self.assertEqual(
            3, len(tuple((self.folder / "history" / "del").glob("*.jpg")))
        )

    def test_interrupted_operation_becomes_retryable_failed_state(self):
        self.repository.save_gallery_ad_cleanup(
            GalleryAdCleanupRecord(
                gid=2841576,
                dirname=self.folder.name,
                cutoff_page_index=3,
                page_count=6,
                state=AD_CLEANUP_MOVING,
                pending_action=AD_ACTION_STAGE,
                manifest=(
                    {
                        "source": "00000004.jpg",
                        "target": "delete/standard/00000004.jpg",
                    },
                ),
            )
        )

        self.repository.mark_interrupted_gallery_ad_cleanups()

        record = self.repository.gallery_ad_cleanup(2841576)
        self.assertEqual(AD_CLEANUP_FAILED, record.state)
        self.assertEqual(AD_ACTION_STAGE, record.pending_action)
        self.assertIn("中断", record.error)

    def test_schema_is_migrated_to_ad_cleanup_version(self):
        with closing(sqlite3.connect(str(self.repository.database_path))) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'gallery_ad_cleanup_states'"
            ).fetchone()
        self.assertEqual(UserLibraryRepository.SCHEMA_VERSION, version)
        self.assertEqual(("gallery_ad_cleanup_states",), table)


if __name__ == "__main__":
    unittest.main()
