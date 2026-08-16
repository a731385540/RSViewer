import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_DIRECT,
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    GalleryOriginalState,
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_FAILED,
    ONLINE_DOWNLOAD_PAUSED,
    ONLINE_DOWNLOAD_QUEUED,
    OnlineGalleryDownloadRecord,
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_REPLACING_ORIGINAL,
    ORIGINAL_STATE_STAGED,
)
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryComment,
    OnlineGalleryDetail,
    OnlineGalleryPreview,
)
from app.repositories.ehviewer_download_repository import EhViewerDownloadRepository
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.online_download_builder import (
    build_online_detail_from_local,
    build_online_gallery_from_download_record,
)
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.sources.ehviewer_source import EhViewerDataSource
from app.workers.online_gallery_download_worker import (
    LocalGalleryPageDownloadWorker,
    OnlineGalleryDownloadWorker,
)
from app.workers.original_gallery_worker import OriginalGalleryFileWorker
from app.workers.eh_online_worker import LocalGallerySyncWorker


def image_bytes(color):
    image = QImage(12, 16, QImage.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class FakeDownloadProvider:
    settings = SimpleNamespace(site="exhentai")

    def __init__(self, pages, fail_index=None, before_image=None):
        self.pages = pages
        self.fail_index = fail_index
        self.before_image = before_image
        self.page_calls = []
        self.original_calls = []
        self.cancel_calls = 0

    def cancel_pending_requests(self):
        self.cancel_calls += 1

    def load_thumbnail(self, _url):
        return image_bytes("gray")

    def load_gallery_page_image(self, _gallery, preview):
        if self.before_image is not None:
            self.before_image()
            self.before_image = None
        self.page_calls.append(preview.page_index)
        if preview.page_index == self.fail_index:
            raise ConnectionError("network disconnected")
        return self.pages[preview.page_index]

    def load_gallery_page_original(self, _gallery, preview):
        self.original_calls.append(preview.page_index)
        return self.pages[preview.page_index]

    def load_gallery_preview_page(self, _gallery, _page_number):
        raise AssertionError("first preview page is already in detail")


class FakeSyncProvider:
    settings = SimpleNamespace(site="exhentai")

    def __init__(self, detail):
        self.detail = detail

    def load_gallery_detail(self, _gallery):
        return self.detail


class FailingDownloadRepository:
    def __init__(self):
        self.states = []

    def prepare_download(self, _detail, _default_label=""):
        raise OSError("cannot create local gallery")

    def mark_state(self, gid, state):
        self.states.append((gid, state))


class OnlineGalleryDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.download_root = self.root / "downloads"
        self.download_root.mkdir()
        self.external_db = self.root / "eh.db"
        self.user_repository = UserLibraryRepository(self.root / "rsviewer.db")
        self._create_external_database()
        gallery = OnlineGallery(
            4120989,
            "gallerytoken",
            "https://exhentai.org/g/4120989/gallerytoken/",
            "Download title",
            "Manga",
            "https://a.hath.network/cover.webp",
            "2026-08-15 12:00",
            3,
            ("artist:someone", "language:chinese", "female:tag one"),
            "uploader",
            4.5,
        )
        previews = tuple(
            OnlineGalleryPreview(
                page_index=index,
                page_url=(
                    f"https://exhentai.org/s/{index + 1:010x}/4120989-{index + 1}"
                ),
                page_token=f"{index + 1:010x}",
            )
            for index in range(3)
        )
        self.detail = OnlineGalleryDetail(
            gallery=gallery,
            title="Download title",
            secondary_title="Original title",
            category="Manga",
            cover_url=gallery.thumbnail_url,
            posted=gallery.posted,
            uploader=gallery.uploader,
            visible="Yes",
            language="Chinese",
            file_size="3 MiB",
            page_count=3,
            rating=4.5,
            rating_count=12,
            tags=gallery.tags,
            comments=(
                OnlineGalleryComment(
                    "15", "reader", "today", "saved comment", 2, False
                ),
            ),
            previews=previews,
        )
        self.cache = OnlineGalleryMemoryCache()
        self.external_repository = EhViewerDownloadRepository(
            self.external_db, self.download_root
        )
        self.pages = {
            0: image_bytes("red"),
            1: image_bytes("green"),
            2: image_bytes("blue"),
        }

    def tearDown(self):
        self.temp_directory.cleanup()

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
                    LABEL TEXT NOT NULL,
                    TIME INTEGER NOT NULL
                );
                INSERT INTO DOWNLOAD_LABELS(LABEL, TIME)
                VALUES ('自动下载', 1), ('其他分类', 2);
                CREATE TABLE Gallery_Tags (
                    GID INTEGER PRIMARY KEY NOT NULL, {tag_columns},
                    CREATE_TIME INTEGER, UPDATE_TIME INTEGER
                );
                PRAGMA user_version = 7;
                """
            )

    def test_new_download_uses_default_label_and_existing_row_keeps_its_label(self):
        self.external_repository.prepare_download(self.detail, "自动下载")
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            label = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = ?",
                (self.detail.gallery.gid,),
            ).fetchone()[0]
        self.assertEqual("自动下载", label)

        self.external_repository.prepare_download(self.detail, "其他分类")
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            label = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = ?",
                (self.detail.gallery.gid,),
            ).fetchone()[0]
        self.assertEqual("自动下载", label)

        with self.assertRaisesRegex(ValueError, "默认下载分类不存在"):
            replacement_detail = replace(
                self.detail,
                gallery=replace(self.detail.gallery, gid=999999),
            )
            self.external_repository.prepare_download(replacement_detail, "不存在")

    def test_single_page_worker_only_downloads_requested_missing_page(self):
        _dirname, folder = self.external_repository.prepare_download(self.detail)
        provider = FakeDownloadProvider(self.pages)
        saved = []
        speeds = []
        worker = LocalGalleryPageDownloadWorker(
            provider,
            self.detail,
            1,
            folder,
            self.cache,
            self.external_repository,
            provider.settings.site,
        )
        worker.signals.saved.connect(lambda *values: saved.append(values))
        worker.signals.speedChanged.connect(speeds.append)

        worker.run()

        self.assertEqual([1], provider.page_calls)
        self.assertEqual(1, len(saved))
        self.assertEqual((self.detail.gallery.gid, 1), saved[0][:2])
        self.assertEqual(1, saved[0][3])
        self.assertEqual(3, saved[0][4])
        self.assertTrue(Path(saved[0][2]).is_file())
        self.assertTrue(speeds and speeds[0] > 0)

    def test_single_page_repair_uses_original_endpoint_for_original_gallery(self):
        _dirname, folder = self.external_repository.prepare_download(self.detail)
        provider = FakeDownloadProvider(self.pages)
        worker = LocalGalleryPageDownloadWorker(
            provider,
            self.detail,
            1,
            folder,
            self.cache,
            self.external_repository,
            provider.settings.site,
            original=True,
        )

        worker.run()

        self.assertEqual([1], provider.original_calls)
        self.assertEqual([], provider.page_calls)
        self.assertTrue((folder / "00000002.png").is_file())

    def _worker(self, provider):
        return OnlineGalleryDownloadWorker(
            provider=provider,
            detail=self.detail,
            cover_data=image_bytes("gray"),
            gallery_cache=self.cache,
            ehviewer_repository=self.external_repository,
            user_repository=self.user_repository,
            site="exhentai",
            retry_count=1,
        )

    def test_direct_original_download_marks_gallery_and_writes_root_pages(self):
        provider = FakeDownloadProvider(self.pages)
        worker = self._worker(provider)
        worker.download_mode = DOWNLOAD_MODE_ORIGINAL_DIRECT

        worker.run()

        record = self.user_repository.online_gallery_download(4120989)
        original = self.user_repository.gallery_original_state(4120989)
        folder = self.download_root / record.dirname
        self.assertEqual(DOWNLOAD_MODE_ORIGINAL_DIRECT, record.download_mode)
        self.assertEqual(ORIGINAL_STATE_ACTIVE, original.state)
        self.assertEqual([0, 1, 2], provider.original_calls)
        self.assertEqual([], provider.page_calls)
        self.assertFalse((folder / "original").exists())
        self.assertEqual(3, len(tuple(folder.glob("*.png"))))

    def test_stalled_original_request_retries_and_clears_stale_speed(self):
        class FlakyOriginalProvider(FakeDownloadProvider):
            def __init__(self, pages):
                super().__init__(pages)
                self.failures_remaining = 2

            def load_gallery_page_original(self, _gallery, preview):
                self.original_calls.append(preview.page_index)
                if preview.page_index == 0 and self.failures_remaining:
                    self.failures_remaining -= 1
                    raise TimeoutError("response body idle")
                return self.pages[preview.page_index]

        provider = FlakyOriginalProvider(self.pages)
        worker = self._worker(provider)
        worker.download_mode = DOWNLOAD_MODE_ORIGINAL_DIRECT
        worker.retry_count = 3
        speeds = []
        stages = []
        worker.signals.speedChanged.connect(speeds.append)
        worker.signals.stageChanged.connect(stages.append)

        worker.run()

        self.assertEqual([0, 0, 0, 1, 2], provider.original_calls)
        self.assertGreaterEqual(speeds.count(0.0), 2)
        self.assertTrue(any("重试" in stage for stage in stages))
        self.assertEqual(
            ONLINE_DOWNLOAD_COMPLETED,
            self.user_repository.online_gallery_download(4120989).state,
        )

    def test_local_original_download_stages_then_replaces_and_cleans_backup(self):
        dirname, folder = self.external_repository.prepare_download(self.detail)
        base_pages = {}
        for index in range(3):
            path = folder / f"{index + 1:08d}.jpg"
            path.write_bytes(image_bytes("black"))
            base_pages[index] = path.read_bytes()
        sidecar = folder / ".ehviewer"
        sidecar.write_text("keep-sidecar", encoding="ascii")
        provider = FakeDownloadProvider(self.pages)
        worker = OnlineGalleryDownloadWorker(
            provider=provider,
            detail=self.detail,
            cover_data=b"",
            gallery_cache=self.cache,
            ehviewer_repository=self.external_repository,
            user_repository=self.user_repository,
            site="exhentai",
            download_mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            existing_folder=folder,
            retry_count=1,
        )

        worker.run()

        original = self.user_repository.gallery_original_state(4120989)
        self.assertEqual(dirname, original.dirname)
        self.assertEqual(ORIGINAL_STATE_STAGED, original.state)
        self.assertEqual([0, 1, 2], provider.original_calls)
        self.assertEqual("keep-sidecar", sidecar.read_text(encoding="ascii"))
        self.assertEqual(base_pages[0], (folder / "00000001.jpg").read_bytes())
        self.assertEqual(3, len(tuple((folder / "original").glob("*.png"))))

        replacement = OriginalGalleryFileWorker(
            original,
            self.download_root,
            self.user_repository,
            OriginalGalleryFileWorker.REPLACE,
        )
        replacement.run()
        promoted = self.user_repository.gallery_original_state(4120989)
        self.assertEqual(ORIGINAL_STATE_ACTIVE, promoted.state)
        self.assertEqual(3, len(tuple((folder / "history" / "del").glob("*.jpg"))))
        self.assertEqual(3, len(tuple(folder.glob("*.png"))))
        self.assertEqual("keep-sidecar", sidecar.read_text(encoding="ascii"))

        cleanup = OriginalGalleryFileWorker(
            promoted,
            self.download_root,
            self.user_repository,
            OriginalGalleryFileWorker.CLEANUP,
        )
        cleanup.run()
        self.assertFalse((folder / "history" / "del").exists())

    def test_original_replacement_resumes_after_partial_promotion(self):
        dirname, folder = self.external_repository.prepare_download(self.detail)
        original_folder = folder / "original"
        backup = folder / "history" / "del"
        original_folder.mkdir()
        backup.mkdir(parents=True)
        for index in range(3):
            (backup / f"{index + 1:08d}.jpg").write_bytes(image_bytes("black"))
            (original_folder / f"{index + 1:08d}.png").write_bytes(
                self.pages[index]
            )
        (original_folder / "00000001.png").rename(folder / "00000001.png")
        state = GalleryOriginalState(
            gid=4120989,
            site="exhentai",
            token="gallerytoken",
            dirname=dirname,
            mode=DOWNLOAD_MODE_ORIGINAL_LOCAL,
            state=ORIGINAL_STATE_REPLACING_ORIGINAL,
            completed_pages=3,
            page_count=3,
        )
        self.user_repository.save_gallery_original_state(state)

        OriginalGalleryFileWorker(
            state,
            self.download_root,
            self.user_repository,
            OriginalGalleryFileWorker.REPLACE,
        ).run()

        self.assertEqual(
            ORIGINAL_STATE_ACTIVE,
            self.user_repository.gallery_original_state(4120989).state,
        )
        self.assertEqual(3, len(tuple(folder.glob("*.png"))))
        self.assertFalse(original_folder.exists())

    def test_failed_download_resumes_missing_pages_and_keeps_ehviewer_format(self):
        schema_before = self._external_schema()

        def assert_metadata_precedes_images():
            with closing(sqlite3.connect(str(self.external_db))) as connection:
                state = connection.execute(
                    "SELECT STATE FROM DOWNLOADS WHERE GID = 4120989"
                ).fetchone()[0]
            comments = self.user_repository.online_gallery_comments(4120989)
            self.assertEqual(2, state)
            self.assertEqual("saved comment", comments[0].text)

        first_provider = FakeDownloadProvider(
            self.pages,
            fail_index=1,
            before_image=assert_metadata_precedes_images,
        )
        failures = []
        first_worker = self._worker(first_provider)
        first_worker.signals.failed.connect(lambda _gid, message: failures.append(message))
        first_worker.run()

        record = self.user_repository.online_gallery_download(4120989)
        self.assertEqual(ONLINE_DOWNLOAD_FAILED, record.state)
        self.assertEqual(1, record.completed_pages)
        self.assertEqual([0, 1], first_provider.page_calls)
        self.assertIn("network disconnected", failures[0])
        folder = self.download_root / record.dirname
        self.assertTrue((folder / "00000001.png").is_file())
        self.assertFalse((folder / "00000002.png").exists())
        self.assertTrue((folder / ".thumb").is_file())
        (folder / "00000002.jpg").write_bytes(b"broken")
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            connection.execute(
                """
                UPDATE DOWNLOADS
                SET LABEL = 'keep-label', TIME = 123, ARCHIVE_URI = 'keep-uri'
                WHERE GID = 4120989
                """
            )
            connection.commit()

        second_provider = FakeDownloadProvider(self.pages)
        completed = []
        saved_pages = []
        speeds = []
        second_worker = self._worker(second_provider)
        second_worker.signals.completed.connect(
            lambda gid, path: completed.append((gid, path))
        )
        second_worker.signals.pageSaved.connect(
            lambda gid, index, path, done, total: saved_pages.append(
                (gid, index, Path(path).name, done, total)
            )
        )
        second_worker.signals.speedChanged.connect(speeds.append)
        second_worker.run()

        record = self.user_repository.online_gallery_download(4120989)
        self.assertEqual(ONLINE_DOWNLOAD_COMPLETED, record.state)
        self.assertEqual(3, record.completed_pages)
        self.assertEqual([1, 2], second_provider.page_calls)
        self.assertEqual(
            [
                (4120989, 1, "00000002.png", 2, 3),
                (4120989, 2, "00000003.png", 3, 3),
            ],
            saved_pages,
        )
        self.assertEqual(1, len(completed))
        self.assertTrue(speeds)
        self.assertTrue(all(speed > 0 for speed in speeds))
        self.assertFalse((folder / "00000002.jpg").exists())
        self.assertTrue((folder / "00000002.png").is_file())
        self.assertTrue((folder / "00000003.png").is_file())
        self.assertFalse(any(folder.glob("*.part")))

        sidecar = (folder / ".ehviewer").read_text(encoding="ascii").splitlines()
        self.assertEqual(
            [
                "VERSION2", "00000000", "4120989", "gallerytoken",
                "1", "1", "20", "3", "0 0000000001",
                "1 0000000002", "2 0000000003",
            ],
            sidecar,
        )
        comments = self.user_repository.online_gallery_comments(4120989)
        self.assertEqual(1, len(comments))
        self.assertEqual("saved comment", comments[0].text)
        self.assertEqual("3 MiB", record.metadata["file_size"])

        with closing(sqlite3.connect(str(self.external_db))) as connection:
            download = connection.execute(
                """
                SELECT TOKEN, TITLE, TITLE_JPN, CATEGORY, UPLOADER, RATING,
                       SIMPLE_LANGUAGE, STATE, LEGACY, LABEL, TIME, ARCHIVE_URI
                FROM DOWNLOADS WHERE GID = 4120989
                """
            ).fetchone()
            dirname = connection.execute(
                "SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = 4120989"
            ).fetchone()[0]
            tags = connection.execute(
                """
                SELECT ARTIST, FEMALE, LANGUAGE
                FROM Gallery_Tags WHERE GID = 4120989
                """
            ).fetchone()
            count = connection.execute(
                "SELECT COUNT(*) FROM DOWNLOADS WHERE GID = 4120989"
            ).fetchone()[0]
        self.assertEqual(
            (
                "gallerytoken", "Download title", "Original title", 4,
                "uploader", 4.5, "chinese", 3, 0,
                "keep-label", 123, "keep-uri",
            ),
            download,
        )
        self.assertEqual(record.dirname, dirname)
        self.assertEqual(("someone", "tag one", "chinese"), tags)
        self.assertEqual(1, count)
        self.assertEqual(schema_before, self._external_schema())
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            self.assertEqual(7, connection.execute("PRAGMA user_version").fetchone()[0])

    def test_startup_marks_interrupted_download_as_paused(self):
        provider = FakeDownloadProvider(self.pages, fail_index=0)
        worker = self._worker(provider)
        worker.run()
        with closing(
            sqlite3.connect(str(self.user_repository.database_path))
        ) as connection:
            connection.execute(
                """
                UPDATE online_gallery_downloads
                SET state = 'downloading', error = '' WHERE gid = 4120989
                """
            )
            connection.commit()
        self.user_repository.mark_interrupted_online_downloads()
        record = self.user_repository.online_gallery_download(4120989)
        self.assertEqual("paused", record.state)
        self.assertIn("中断", record.error)

    def test_worker_cancel_notifies_provider_to_abort_active_response(self):
        provider = FakeDownloadProvider(self.pages)
        worker = self._worker(provider)

        worker.cancel()

        self.assertTrue(worker.cancelled)
        self.assertEqual(1, provider.cancel_calls)

    def test_worker_reports_local_registration_and_sidecar_readiness(self):
        events = []
        worker = self._worker(FakeDownloadProvider(self.pages))
        worker.signals.galleryRegistered.connect(
            lambda gid, folder: events.append(
                (
                    "registered",
                    gid,
                    (Path(folder) / ".thumb").is_file(),
                    (Path(folder) / ".ehviewer").is_file(),
                )
            )
        )
        worker.signals.sidecarReady.connect(
            lambda gid, folder: events.append(
                (
                    "sidecar",
                    gid,
                    (Path(folder) / ".thumb").is_file(),
                    (Path(folder) / ".ehviewer").is_file(),
                )
            )
        )

        worker.run()

        self.assertEqual(
            [
                ("registered", 4120989, True, False),
                ("sidecar", 4120989, True, True),
            ],
            events,
        )

    def test_early_pause_and_failure_persist_terminal_task_state(self):
        queued = OnlineGalleryDownloadRecord(
            gid=4120989,
            site="exhentai",
            token="gallerytoken",
            title="Download title",
            dirname="",
            page_count=3,
            completed_pages=2,
            state=ONLINE_DOWNLOAD_QUEUED,
        )
        self.user_repository.save_online_gallery_download(queued)

        paused_worker = self._worker(FakeDownloadProvider(self.pages))
        paused_worker.cancel()
        paused_worker.run()
        paused = self.user_repository.online_gallery_download(4120989)
        self.assertEqual(ONLINE_DOWNLOAD_PAUSED, paused.state)
        self.assertEqual(2, paused.completed_pages)

        self.user_repository.save_online_gallery_download(queued)
        failed_repository = FailingDownloadRepository()
        failed_worker = self._worker(FakeDownloadProvider(self.pages))
        failed_worker.ehviewer_repository = failed_repository
        failed_worker.run()
        failed = self.user_repository.online_gallery_download(4120989)
        self.assertEqual(ONLINE_DOWNLOAD_FAILED, failed.state)
        self.assertEqual(2, failed.completed_pages)
        self.assertIn("cannot create local gallery", failed.error)
        self.assertTrue(failed_repository.states)

    def test_metadata_sync_preserves_download_state_and_saves_version_info(self):
        self.external_repository.prepare_download(self.detail)
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            connection.execute(
                "UPDATE DOWNLOADS SET STATE = 3, LEGACY = 7 WHERE GID = 4120989"
            )
            connection.commit()
        synced_detail = OnlineGalleryDetail(
            **{
                **self.detail.__dict__,
                "title": "Synced title",
                "tags": ("artist:updated", "language:japanese"),
                "newer_gallery_urls": (
                    "https://exhentai.org/g/4120990/newtoken/",
                ),
            }
        )
        worker = LocalGallerySyncWorker(
            FakeSyncProvider(synced_detail),
            synced_detail.gallery,
            self.external_repository,
            self.user_repository,
        )
        loaded = []
        worker.signals.loaded.connect(loaded.append)

        worker.run()

        self.assertEqual([synced_detail], loaded)
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            row = connection.execute(
                "SELECT TITLE, STATE, LEGACY FROM DOWNLOADS WHERE GID = 4120989"
            ).fetchone()
            tags = connection.execute(
                "SELECT ARTIST, LANGUAGE FROM Gallery_Tags WHERE GID = 4120989"
            ).fetchone()
        self.assertEqual(("Synced title", 3, 7), row)
        self.assertEqual(("updated", "japanese"), tags)
        sync_record = self.user_repository.gallery_sync_record(4120989)
        self.assertEqual("exhentai", sync_record.site)
        self.assertEqual(
            ["https://exhentai.org/g/4120990/newtoken/"],
            sync_record.metadata["newer_gallery_urls"],
        )
        self.assertEqual(
            "saved comment",
            self.user_repository.online_gallery_comments(4120989)[0].text,
        )

    def test_metadata_sync_resolves_gallery_in_worker_before_request(self):
        self.external_repository.prepare_download(self.detail)
        resolved = []
        worker = LocalGallerySyncWorker(
            FakeSyncProvider(self.detail),
            None,
            self.external_repository,
            self.user_repository,
            gallery_loader=lambda: resolved.append(True) or self.detail.gallery,
        )
        loaded = []
        worker.signals.loaded.connect(loaded.append)

        worker.run()

        self.assertEqual([True], resolved)
        self.assertEqual([self.detail], loaded)
        self.assertEqual(self.detail.gallery, worker.gallery)

    def test_interrupted_task_rebuilds_canonical_gallery_without_local_files(self):
        record = OnlineGalleryDownloadRecord(
            gid=4120989,
            site="exhentai",
            token="gallerytoken",
            title="Saved task title",
            dirname="",
            page_count=3,
            metadata={
                "url": "https://example.invalid/not-used",
                "category": "Manga",
                "cover_url": "https://a.hath.network/cover.webp",
                "posted": "2026-08-15 12:00",
                "uploader": "uploader",
                "rating": "4.5",
                "tags": ["artist:someone", "language:chinese"],
            },
        )

        gallery = build_online_gallery_from_download_record(record)

        self.assertEqual(4120989, gallery.gid)
        self.assertEqual("gallerytoken", gallery.token)
        self.assertEqual(
            "https://exhentai.org/g/4120989/gallerytoken/",
            gallery.url,
        )
        self.assertEqual(3, gallery.page_count)
        self.assertEqual(4.5, gallery.rating)
        self.assertEqual(
            ("artist:someone", "language:chinese"),
            gallery.tags,
        )

    def test_local_repair_ignores_finished_database_state_and_fills_missing_page(self):
        first_provider = FakeDownloadProvider(self.pages)
        self._worker(first_provider).run()
        record = self.user_repository.online_gallery_download(4120989)
        folder = self.download_root / record.dirname
        (folder / "00000002.png").unlink()

        source = EhViewerDataSource(self.external_db, self.download_root)
        item = source.load_pages(source.list_local_manga()[0])
        self.assertEqual(3, item.page_count)
        self.assertEqual(2, item.downloaded_page_count)
        self.assertFalse(item.download_complete)
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            self.assertEqual(
                3,
                connection.execute(
                    "SELECT STATE FROM DOWNLOADS WHERE GID = 4120989"
                ).fetchone()[0],
            )

        local_detail = build_online_detail_from_local(
            item,
            record,
            self.user_repository.online_gallery_comments(4120989),
        )
        repair_provider = FakeDownloadProvider(self.pages)
        worker = OnlineGalleryDownloadWorker(
            provider=repair_provider,
            detail=local_detail,
            cover_data=b"",
            gallery_cache=self.cache,
            ehviewer_repository=self.external_repository,
            user_repository=self.user_repository,
            site="exhentai",
            retry_count=1,
        )
        worker.run()

        self.assertEqual([1], repair_provider.page_calls)
        self.assertTrue((folder / "00000002.png").is_file())
        repaired = source.load_pages(source.list_local_manga()[0])
        self.assertTrue(repaired.download_complete)
        self.assertEqual(3, repaired.downloaded_page_count)

    def _external_schema(self):
        with closing(sqlite3.connect(str(self.external_db))) as connection:
            return tuple(
                connection.execute(
                    """
                    SELECT type, name, sql FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                )
            )


if __name__ == "__main__":
    unittest.main()
