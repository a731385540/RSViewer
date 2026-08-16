import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from app.domain.gallery_update import GalleryUpdateRecord, UPDATE_COMPLETED
from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
    GalleryOriginalState,
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
        self.assertEqual([(200,)], gids)
        self.assertEqual([(200,)], dirname_gid)

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
