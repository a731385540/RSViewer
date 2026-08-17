import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from app.domain.gallery_update import GalleryUpdateRecord, UPDATE_COMPLETED
from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    GalleryOriginalState,
    ORIGINAL_PAGE_MODE_BASE,
    ORIGINAL_PAGE_MODE_ORIGINAL,
    ORIGINAL_STATE_ACTIVE,
)
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreview,
)
from app.repositories.ehviewer_download_repository import EhViewerDownloadRepository
from app.repositories.gallery_update_state_repository import GalleryUpdateStateRepository
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.sources.eh_online_source import OriginalImageUnavailableError
from app.workers.gallery_update_worker import (
    GalleryUpdateWorker,
    UpdateSidecar,
    encode_update_sidecar,
    read_update_sidecar,
)


def image_bytes(color):
    image = QImage(12, 16, QImage.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class UpdateProvider:
    settings = SimpleNamespace(
        site="exhentai", base_url="https://exhentai.org/"
    )

    def __init__(self, detail, pages):
        self.detail = detail
        self.pages = pages
        self.page_calls = []
        self.original_calls = []

    def load_gallery_detail(self, _gallery):
        return self.detail

    def load_gallery_page_image(self, _gallery, preview):
        self.page_calls.append(preview.page_index)
        return self.pages[preview.page_index]

    def load_gallery_page_original(self, _gallery, preview):
        self.original_calls.append(preview.page_index)
        return self.pages[preview.page_index]

    def load_thumbnail(self, _url):
        return image_bytes("gray")

    def cancel_pending_requests(self):
        pass


class GalleryUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.download_root = self.root / "downloads"
        self.download_root.mkdir()
        self.external_db = self.root / "eh.db"
        self.user_repository = UserLibraryRepository(self.root / "rsviewer.db")
        self._create_external_database()
        self.external_repository = EhViewerDownloadRepository(
            self.external_db, self.download_root
        )

        self.old_gallery = OnlineGallery(
            100, "abcdef1234", "https://exhentai.org/g/100/abcdef1234/", "Old"
        )
        self.old_detail = OnlineGalleryDetail(
            gallery=self.old_gallery,
            title="Old",
            category="Manga",
            page_count=3,
            tags=("artist:old",),
        )
        _dirname, self.folder = self.external_repository.prepare_download(
            self.old_detail
        )
        old_tokens = ("0000000001", "0000000002", "0000000003")
        (self.folder / ".ehviewer").write_bytes(
            encode_update_sidecar(
                UpdateSidecar(0, 100, "abcdef1234", 3, old_tokens)
            )
        )
        for index, color in enumerate(("red", "green", "blue"), 1):
            (self.folder / f"{index:08d}.png").write_bytes(image_bytes(color))

        self.latest_gallery = OnlineGallery(
            200,
            "fedcba4321",
            "https://exhentai.org/g/200/fedcba4321/",
            "Latest",
            thumbnail_url="https://a.hath.network/latest.png",
        )
        latest_tokens = ("0000000002", "0000000004", "0000000001")
        previews = tuple(
            OnlineGalleryPreview(
                page_index=index,
                page_url=(
                    f"https://exhentai.org/s/{token}/200-{index + 1}"
                ),
                page_token=token,
            )
            for index, token in enumerate(latest_tokens)
        )
        self.latest_detail = OnlineGalleryDetail(
            gallery=self.latest_gallery,
            title="Latest",
            category="Manga",
            page_count=3,
            tags=("artist:new",),
            previews=previews,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _create_external_database(self):
        tag_columns = ", ".join(
            f'"{name}" TEXT'
            for name in (
                "ROWS", "ARTIST", "COSPLAYER", "CHARACTER", "FEMALE",
                "GROUP", "LANGUAGE", "MALE", "MISC", "MIXED", "OTHER",
                "PARODY", "RECLASS",
            )
        )
        with closing(sqlite3.connect(str(self.external_db))) as connection:
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
                PRAGMA user_version = 7;
                """
            )

    def test_update_reorders_archives_downloads_and_promotes_gid(self):
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            connection.execute("UPDATE DOWNLOADS SET TIME = 123 WHERE GID = 100")
            connection.commit()
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(
            self.latest_detail,
            {0: image_bytes("green"), 1: image_bytes("yellow"), 2: image_bytes("red")},
        )
        completed = []
        failed = []
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )
        worker.signals.completed.connect(lambda *values: completed.append(values))
        worker.signals.failed.connect(lambda *values: failed.append(values))

        worker.run()

        self.assertEqual(
            [(100, 200)],
            completed,
            (failed, sorted(path.name for path in self.folder.iterdir())),
        )
        self.assertEqual([1], provider.page_calls)
        self.assertEqual(
            ["00000001.png", "00000002.png", "00000003.png"],
            sorted(path.name for path in self.folder.glob("*.png")),
        )
        removed = list((self.folder / "history" / "removed").rglob("*-0000000003.png"))
        self.assertEqual(1, len(removed))
        self.assertTrue((self.folder / "history" / "100-abcdef1234.ehviewer").is_file())
        sidecar = read_update_sidecar(self.folder / ".ehviewer")
        self.assertEqual((200, "fedcba4321"), (sidecar.gid, sidecar.gallery_token))
        state = json.loads((self.folder / "new.json").read_text("utf-8"))
        self.assertEqual(6, state["200:fedcba4321"]["status"])
        task = self.user_repository.gallery_update(100)
        self.assertEqual(UPDATE_COMPLETED, task.state)
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            gids = connection.execute("SELECT GID FROM DOWNLOADS").fetchall()
            dirname_gid = connection.execute(
                "SELECT GID FROM DOWNLOAD_DIRNAME"
            ).fetchall()
            updated_time = connection.execute(
                "SELECT TIME FROM DOWNLOADS WHERE GID = 200"
            ).fetchone()[0]
        self.assertEqual([(200,)], gids)
        self.assertEqual([(200,)], dirname_gid)
        self.assertGreater(updated_time, 123)

    def test_delete_update_record_preserves_folder_checkpoint(self):
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            state="failed",
        )
        self.user_repository.save_gallery_update(record)
        checkpoint = GalleryUpdateStateRepository(self.folder)
        checkpoint.write(
            200,
            "fedcba4321",
            2,
            source_gid=100,
            source_token="abcdef1234",
        )

        self.user_repository.delete_gallery_update(100)

        self.assertIsNone(self.user_repository.gallery_update(100))
        self.assertTrue(checkpoint.path.is_file())
        self.assertEqual(2, checkpoint.record(200, "fedcba4321")["status"])

    def test_touch_download_time_updates_existing_gallery(self):
        self.external_repository.touch_download_time(100, 456)

        with closing(sqlite3.connect(str(self.external_db))) as connection:
            value = connection.execute(
                "SELECT TIME FROM DOWNLOADS WHERE GID = 100"
            ).fetchone()[0]
        self.assertEqual(456, value)

    def test_original_gallery_update_downloads_original_and_promotes_state(self):
        dirname = self.folder.name
        self.user_repository.save_gallery_original_state(
            GalleryOriginalState(
                gid=100,
                site="exhentai",
                token="abcdef1234",
                dirname=dirname,
                mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
                state=ORIGINAL_STATE_ACTIVE,
                completed_pages=3,
                page_count=3,
            )
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            metadata={"image_mode": "original"},
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(
            self.latest_detail,
            {0: image_bytes("green"), 1: image_bytes("yellow"), 2: image_bytes("red")},
        )
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )

        worker.run()

        self.assertEqual([], provider.page_calls)
        self.assertEqual([1], provider.original_calls)
        promoted = self.user_repository.gallery_original_state(200)
        self.assertIsNotNone(promoted)
        self.assertEqual(ORIGINAL_STATE_ACTIVE, promoted.state)
        self.assertEqual("fedcba4321", promoted.token)
        self.assertIsNone(self.user_repository.gallery_original_state(100))

    def test_mixed_original_gallery_update_falls_back_per_new_page(self):
        self.user_repository.save_gallery_original_state(
            GalleryOriginalState(
                gid=100,
                site="exhentai",
                token="abcdef1234",
                dirname=self.folder.name,
                mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
                state=ORIGINAL_STATE_ACTIVE,
                completed_pages=3,
                page_count=3,
                fallback_to_standard=True,
                page_modes=(
                    ORIGINAL_PAGE_MODE_ORIGINAL,
                    ORIGINAL_PAGE_MODE_BASE,
                    ORIGINAL_PAGE_MODE_ORIGINAL,
                ),
            )
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            metadata={"image_mode": "original"},
        )
        self.user_repository.save_gallery_update(record)
        class MixedUpdateProvider(UpdateProvider):
            def load_gallery_page_original(self, _gallery, preview):
                self.original_calls.append(preview.page_index)
                raise OriginalImageUnavailableError("no full image")

        provider = MixedUpdateProvider(
            self.latest_detail,
            {0: image_bytes("green"), 1: image_bytes("yellow"), 2: image_bytes("red")},
        )

        GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        ).run()

        self.assertEqual([1], provider.page_calls)
        self.assertEqual([1], provider.original_calls)
        promoted = self.user_repository.gallery_original_state(200)
        self.assertIsNotNone(promoted)
        self.assertTrue(promoted.fallback_to_standard)
        self.assertEqual(
            (
                ORIGINAL_PAGE_MODE_BASE,
                ORIGINAL_PAGE_MODE_BASE,
                ORIGINAL_PAGE_MODE_ORIGINAL,
            ),
            promoted.page_modes,
        )
        self.assertEqual("original", promoted.metadata["image_mode"])
        saved_task = self.user_repository.gallery_update(100)
        self.assertEqual("original", saved_task.metadata["image_mode"])

    def test_startup_marks_interrupted_update_as_paused(self):
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            state="updating",
        )
        self.user_repository.save_gallery_update(record)
        self.user_repository.mark_interrupted_gallery_updates()
        self.assertEqual("paused", self.user_repository.gallery_update(100).state)

        completed = replace(record, status=6, state="updating")
        self.user_repository.save_gallery_update(completed)
        self.user_repository.mark_interrupted_gallery_updates()
        self.assertEqual(
            UPDATE_COMPLETED,
            self.user_repository.gallery_update(100).state,
        )
        self.assertEqual((), self.user_repository.gallery_updates())

    def test_duplicate_page_tokens_repair_stale_remap_checkpoint(self):
        duplicate_tokens = (
            "0000000002",
            "0000000002",
            "0000000001",
        )
        duplicate_detail = replace(
            self.latest_detail,
            previews=tuple(
                OnlineGalleryPreview(
                    page_index=index,
                    page_url=(
                        f"https://exhentai.org/s/{token}/200-{index + 1}"
                    ),
                    page_token=token,
                )
                for index, token in enumerate(duplicate_tokens)
            ),
        )
        staged = UpdateSidecar(
            0,
            200,
            "fedcba4321",
            3,
            duplicate_tokens,
        )
        (self.folder / "new.ehviewer").write_bytes(
            encode_update_sidecar(staged)
        )
        source_tokens = ("0000000001", "0000000002", "0000000003")
        for index, token in enumerate(source_tokens):
            source = self.folder / f"{index + 1:08d}.png"
            source.rename(
                self.folder / f"{index + 1:08d}-{index}-{token}.png"
            )
        GalleryUpdateStateRepository(self.folder).write(
            200,
            "fedcba4321",
            2,
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            latest_url=self.latest_gallery.url,
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            target_gid=200,
            target_token="fedcba4321",
            status=2,
            state="failed",
            page_count=3,
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(
            duplicate_detail,
            {0: image_bytes("purple"), 1: image_bytes("yellow")},
        )
        completed = []
        failed = []
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )
        worker.signals.completed.connect(lambda *values: completed.append(values))
        worker.signals.failed.connect(lambda *values: failed.append(values))

        worker.run()

        self.assertEqual([(100, 200)], completed, failed)
        self.assertEqual(1, len(provider.page_calls))
        self.assertEqual(
            ["00000001.png", "00000002.png", "00000003.png"],
            sorted(path.name for path in self.folder.glob("*.png")),
        )

    def test_duplicate_source_tokens_repair_stale_tag_checkpoint(self):
        duplicate = "0000000001"
        source_tokens = (duplicate, duplicate, "0000000003")
        target_tokens = (duplicate, "0000000004", duplicate)
        (self.folder / ".ehviewer").write_bytes(
            encode_update_sidecar(
                UpdateSidecar(0, 100, "abcdef1234", 3, source_tokens)
            )
        )
        (self.folder / "new.ehviewer").write_bytes(
            encode_update_sidecar(
                UpdateSidecar(0, 200, "fedcba4321", 3, target_tokens)
            )
        )
        (self.folder / "00000001.png").rename(
            self.folder / f"00000003-2-{duplicate}.png"
        )
        (self.folder / "00000003.png").rename(
            self.folder / "00000003-2-0000000003.png"
        )
        GalleryUpdateStateRepository(self.folder).write(
            200,
            "fedcba4321",
            1,
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            latest_url=self.latest_gallery.url,
        )
        detail = replace(
            self.latest_detail,
            previews=tuple(
                OnlineGalleryPreview(
                    page_index=index,
                    page_url=f"https://exhentai.org/s/{token}/200-{index + 1}",
                    page_token=token,
                )
                for index, token in enumerate(target_tokens)
            ),
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            target_gid=200,
            target_token="fedcba4321",
            status=1,
            state="failed",
            page_count=3,
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(detail, {1: image_bytes("yellow")})
        completed = []
        failed = []
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )
        worker.signals.completed.connect(lambda *values: completed.append(values))
        worker.signals.failed.connect(lambda *values: failed.append(values))

        worker.run()

        self.assertEqual([(100, 200)], completed, failed)
        self.assertEqual([1], provider.page_calls)
        self.assertEqual(
            ["00000001.png", "00000002.png", "00000003.png"],
            sorted(path.name for path in self.folder.glob("*.png")),
        )

    def test_status_five_resumes_a_partially_stripped_directory(self):
        latest_sidecar = UpdateSidecar(
            0,
            200,
            "fedcba4321",
            3,
            ("0000000002", "0000000004", "0000000001"),
        )
        (self.folder / "new.ehviewer").write_bytes(
            encode_update_sidecar(latest_sidecar)
        )
        old_files = {
            index: self.folder / f"{index + 1:08d}.png" for index in range(3)
        }
        removed = self.folder / "history" / "removed" / "100-abcdef1234"
        removed.mkdir(parents=True)
        old_files[2].rename(removed / "00000003-2-0000000003.png")
        old_files[1].rename(self.folder / "00000001-0-0000000002.png")
        old_files[0].rename(self.folder / "00000003.png")
        GalleryUpdateStateRepository(self.folder).write(
            200,
            "fedcba4321",
            5,
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            latest_url=self.latest_gallery.url,
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            target_gid=200,
            target_token="fedcba4321",
            status=5,
            state="paused",
            page_count=3,
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(
            self.latest_detail,
            {0: image_bytes("green"), 1: image_bytes("yellow"), 2: image_bytes("red")},
        )
        completed = []
        failed = []
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )
        worker.signals.completed.connect(lambda *values: completed.append(values))
        worker.signals.failed.connect(lambda *values: failed.append(values))

        worker.run()

        self.assertEqual(
            [(100, 200)],
            completed,
            (failed, sorted(path.name for path in self.folder.iterdir())),
        )
        self.assertEqual([1], provider.page_calls)
        self.assertEqual(
            ["00000001.png", "00000002.png", "00000003.png"],
            sorted(path.name for path in self.folder.glob("*.png")),
        )

    def test_resume_after_old_sidecar_was_archived_before_new_promotion(self):
        history = self.folder / "history"
        history.mkdir()
        (self.folder / ".ehviewer").rename(
            history / "100-abcdef1234.ehviewer"
        )
        latest_sidecar = UpdateSidecar(
            0,
            200,
            "fedcba4321",
            3,
            ("0000000002", "0000000004", "0000000001"),
        )
        (self.folder / "new.ehviewer").write_bytes(
            encode_update_sidecar(latest_sidecar)
        )
        GalleryUpdateStateRepository(self.folder).write(
            200, "fedcba4321", 5, source_gid=100, source_token="abcdef1234"
        )
        record = GalleryUpdateRecord(
            source_gid=100,
            source_token="abcdef1234",
            site="exhentai",
            title="Old",
            folder=str(self.folder),
            latest_url=self.latest_gallery.url,
            target_gid=200,
            target_token="fedcba4321",
            status=5,
            state="paused",
            page_count=3,
        )
        self.user_repository.save_gallery_update(record)
        provider = UpdateProvider(self.latest_detail, {})
        completed = []
        failed = []
        worker = GalleryUpdateWorker(
            record,
            provider,
            OnlineGalleryMemoryCache(),
            self.external_repository,
            self.user_repository,
        )
        worker.signals.completed.connect(lambda *values: completed.append(values))
        worker.signals.failed.connect(lambda *values: failed.append(values))

        worker.run()

        self.assertEqual([(100, 200)], completed, failed)
        sidecar = read_update_sidecar(self.folder / ".ehviewer")
        self.assertEqual((200, "fedcba4321"), (sidecar.gid, sidecar.gallery_token))
