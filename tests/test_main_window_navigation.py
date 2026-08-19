import os
import sqlite3
import tempfile
import unittest
from collections import deque
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from qfluentwidgets import FluentWindow

from app.common.config import cfg
from app.domain.gallery_update import GalleryUpdateRecord, UPDATE_QUEUED
from app.domain.online_download import OnlineGalleryDownloadRecord
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryLink,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.main_window import MAX_ONLINE_DOWNLOAD_CONCURRENCY, MainWindow
from app.workers.gallery_trash_worker import GalleryTrashWorker


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in tuple(self.callbacks):
            callback(*args)


def image_bytes(color):
    image = QImage(12, 16, QImage.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class FakeSyncWorker:
    def __init__(self, gid):
        self.gid = gid
        self.cancelled = False
        self.signals = SimpleNamespace(loaded=FakeSignal(), failed=FakeSignal())


class FakeBootstrapWorker:
    def __init__(self, provider, item, cover_data=b"", fetch_cover=True):
        self.provider = provider
        self.item = item
        self.cover_data = cover_data
        self.fetch_cover = fetch_cover
        self.cancelled = False
        self.signals = SimpleNamespace(loaded=FakeSignal(), failed=FakeSignal())


class FakeThreadPool:
    def __init__(self):
        self.started = []
        self.max_thread_counts = []

    def start(self, worker):
        self.started.append(worker)

    def setMaxThreadCount(self, value):
        self.max_thread_counts.append(int(value))


class EmptySyncRepository:
    def online_gallery_download(self, _gid):
        return None

    def gallery_sync_record(self, _gid):
        return None


class BootstrapRepository:
    def __init__(self, record):
        self.record = record
        self.updates = []

    def online_gallery_download(self, gid):
        return self.record if int(gid) == self.record.gid else None

    def update_online_download(self, *args):
        self.updates.append(args)


class DownloadPreparationRepository:
    def __init__(self):
        self.records = {}

    def online_gallery_download(self, gid):
        return self.records.get(int(gid))

    def gallery_original_state(self, _gid):
        return None

    def save_online_gallery_download(self, record, _comments=()):
        self.records[int(record.gid)] = record

    def online_gallery_comments(self, _gid):
        return ()


class NavigationTestWindow(MainWindow):
    def __init__(self):
        FluentWindow.__init__(self)
        self.localMangaInterface = self._make_interface("localMangaInterface")
        self.favoriteMangaInterface = self._make_interface("favoriteMangaInterface")
        self.onlineMangaInterface = self._make_interface("onlineMangaInterface")
        self.mangaHistoryInterface = self._make_interface("mangaHistoryInterface")
        self.downloadManagerInterface = self._make_interface("downloadManagerInterface")
        self.videoInterface = self._make_interface("videoInterface")
        self.settingInterface = self._make_interface("settingInterface")
        self.initNavigation()

    def _make_interface(self, object_name):
        interface = QWidget(self)
        interface.setObjectName(object_name)
        return interface

    def closeEvent(self, event):
        FluentWindow.closeEvent(self, event)


class StartupTestWindow(MainWindow):
    def __init__(self):
        FluentWindow.__init__(self)
        self.initWindow()

    def closeEvent(self, event):
        FluentWindow.closeEvent(self, event)


class MainWindowNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = NavigationTestWindow()
        self.window.show()
        QApplication.processEvents()

    def tearDown(self):
        QTest.qWait(250)
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_bottom_mode_buttons_switch_flat_top_navigation(self):
        navigation = self.window.navigationInterface
        manga_routes = self.window._navigationRoutes["manga"]
        video_route = self.window._navigationRoutes["video"][0]

        self.assertFalse(self.window.stackedWidget.isAnimationEnabled())
        self.assertEqual(
            [
                "本地资源",
                "收藏",
                "在线资源",
                "历史记录",
                "正在下载",
                "更新管理",
                "整理",
                "回收站",
            ],
            [navigation.widget(route).text() for route in manga_routes],
        )
        self.assertTrue(
            all(
                navigation.widget(route).property("parentRouteKey") is None
                for route in (*manga_routes, video_route)
            )
        )
        self.assertTrue(all(not navigation.widget(route).isHidden() for route in manga_routes))
        self.assertTrue(navigation.widget(video_route).isHidden())

        bottom_layout = navigation.panel.bottomLayout
        bottom_routes = (
            "mangaNavigationMode",
            "videoNavigationMode",
            "openAdditionalWindow",
            self.window.settingInterface.objectName(),
        )
        self.assertEqual(
            sorted(bottom_layout.indexOf(navigation.widget(route)) for route in bottom_routes),
            [bottom_layout.indexOf(navigation.widget(route)) for route in bottom_routes],
        )

        navigation.widget("videoNavigationMode").click()
        QApplication.processEvents()
        self.assertEqual("video", self.window._navigationMode)
        self.assertEqual(self.window.videoInterface, self.window.stackedWidget.currentWidget())
        self.assertEqual("资源", navigation.widget(video_route).text())
        self.assertTrue(all(navigation.widget(route).isHidden() for route in manga_routes))
        self.assertFalse(navigation.widget(video_route).isHidden())

        navigation.widget("mangaNavigationMode").click()
        QApplication.processEvents()
        self.assertEqual("manga", self.window._navigationMode)
        self.assertEqual(
            self.window.localMangaInterface,
            self.window.stackedWidget.currentWidget(),
        )
        self.assertTrue(all(not navigation.widget(route).isHidden() for route in manga_routes))
        self.assertTrue(navigation.widget(video_route).isHidden())

    def test_permanent_delete_requires_confirmation_but_restore_does_not(self):
        record = SimpleNamespace(gid=42, dirname="42-gallery")
        self.window._startGalleryTrashAction = MagicMock()

        button = SimpleNamespace(setText=lambda _text: None)
        rejected = SimpleNamespace(
            yesButton=button,
            cancelButton=button,
            exec=lambda: False,
        )
        with patch("app.view.main_window.MessageBox", return_value=rejected):
            self.window.permanentlyDeleteTrashedGalleries((record,))
        self.window._startGalleryTrashAction.assert_not_called()

        self.window.restoreTrashedGalleries((record,))
        self.window._startGalleryTrashAction.assert_called_once_with(
            GalleryTrashWorker.RESTORE, (record,)
        )

        self.window._startGalleryTrashAction.reset_mock()
        accepted = SimpleNamespace(
            yesButton=button,
            cancelButton=button,
            exec=lambda: True,
        )
        with patch("app.view.main_window.MessageBox", return_value=accepted):
            self.window.permanentlyDeleteTrashedGalleries((record,))
        self.window._startGalleryTrashAction.assert_called_once_with(
            GalleryTrashWorker.DELETE, (record,)
        )

    def test_splash_matches_window_size_before_first_show(self):
        window = StartupTestWindow()
        try:
            self.assertEqual(window.size(), window.splashScreen.size())
        finally:
            window.splashScreen.finish()
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    def test_open_additional_window_reuses_process_coordinator(self):
        created = []

        class AdditionalWindow:
            def __init__(self, coordinator):
                self.coordinator = coordinator
                self.shown = False
                self.raised = False
                self.activated = False
                created.append(self)

            def show(self):
                self.shown = True

            def raise_(self):
                self.raised = True

            def activateWindow(self):
                self.activated = True

        coordinator = object()
        host = SimpleNamespace(windowCoordinator=coordinator)
        with patch("app.view.main_window.MainWindow", AdditionalWindow):
            MainWindow.openAdditionalWindow(host)

        self.assertEqual(1, len(created))
        self.assertIs(coordinator, created[0].coordinator)
        self.assertTrue(created[0].shown)
        self.assertTrue(created[0].raised)
        self.assertTrue(created[0].activated)

    def test_back_from_online_detail_returns_to_online_resources(self):
        detail = QWidget(self.window)
        detail.setObjectName("sharedDetailInterface")
        detail.isOnlineGallery = True
        self.window.mangaDetailInterface = detail
        self.window.mangaReaderInterface = QWidget(self.window)
        self.window._onlineDetailWorker = None
        self.window.stackedWidget.addWidget(detail)
        self.window.switchTo(detail)

        self.assertTrue(self.window.navigateBack())

        self.assertIs(
            self.window.onlineMangaInterface,
            self.window.stackedWidget.currentWidget(),
        )

    def test_comment_gallery_link_pushes_and_restores_detail_history(self):
        window = self.window
        detail_widget = QWidget(window)
        detail_widget.setObjectName("sharedDetailInterface")
        detail_widget.isOnlineGallery = True
        window.mangaDetailInterface = detail_widget
        window.mangaReaderInterface = QWidget(window)
        window.stackedWidget.addWidget(detail_widget)
        window.switchTo(detail_widget)
        previous_gallery = OnlineGallery(
            123,
            "abcdef0123",
            "https://e-hentai.org/g/123/abcdef0123/",
            "Previous",
        )
        target_gallery = OnlineGallery(
            456,
            "deadbeef01",
            "https://e-hentai.org/g/456/deadbeef01/",
            "Target",
        )
        provider = SimpleNamespace(settings=SimpleNamespace(site="ehentai"))
        window._detailNavigationHistory = deque()
        window._currentDetailNavigationEntry = lambda: (
            "online",
            previous_gallery,
            provider,
            b"previous-cover",
        )
        window.onlineMangaInterface.galleryTarget = (
            lambda gid, token: (target_gallery, provider)
        )
        window.openOnlineMangaDetail = MagicMock()

        window.openLinkedOnlineMangaDetail(
            OnlineGalleryLink(456, "deadbeef01", "Sequel")
        )

        self.assertEqual(1, len(window._detailNavigationHistory))
        window.openOnlineMangaDetail.assert_called_once_with(
            target_gallery,
            provider,
        )
        window.openOnlineMangaDetail.reset_mock()

        self.assertTrue(window.navigateBack())
        window.openOnlineMangaDetail.assert_called_once_with(
            previous_gallery,
            provider,
            b"previous-cover",
        )
        self.assertEqual(0, len(window._detailNavigationHistory))

    def test_selected_title_online_search_restores_local_detail(self):
        window = self.window
        detail_widget = QWidget(window)
        detail_widget.setObjectName("sharedDetailInterface")
        window.mangaDetailInterface = detail_widget
        window.mangaReaderInterface = QWidget(window)
        window.stackedWidget.addWidget(detail_widget)
        window.switchTo(detail_widget)
        source_item = SimpleNamespace(gid=123)
        window._onlineSearchReturnState = None
        window._detailNavigationHistory = deque()
        window._readingSequenceContext = None
        window._currentDetailNavigationEntry = lambda: ("local", source_item)
        window.onlineMangaInterface.searchForText = MagicMock()
        window.onlineMangaInterface.setDetailReturnAvailable = MagicMock()

        window.searchSelectedTitleOnline('  Part "One"  ')

        self.assertIs(
            window.onlineMangaInterface,
            window.stackedWidget.currentWidget(),
        )
        window.onlineMangaInterface.searchForText.assert_called_once_with(
            '"Part One"'
        )
        window.onlineMangaInterface.setDetailReturnAvailable.assert_called_with(
            True
        )

        window.openMangaDetail = MagicMock()
        self.assertTrue(window.navigateBack())
        window.openMangaDetail.assert_called_once_with(source_item)
        self.assertIsNone(window._onlineSearchReturnState)

    def test_selected_title_online_search_restores_online_detail_history(self):
        window = self.window
        detail_widget = QWidget(window)
        detail_widget.setObjectName("sharedDetailInterface")
        window.mangaDetailInterface = detail_widget
        window.mangaReaderInterface = QWidget(window)
        window.stackedWidget.addWidget(detail_widget)
        window.switchTo(detail_widget)
        earlier = ("local", SimpleNamespace(gid=100))
        source_gallery = OnlineGallery(
            123,
            "abcdef0123",
            "https://e-hentai.org/g/123/abcdef0123/",
            "Source",
        )
        provider = SimpleNamespace(settings=SimpleNamespace(site="ehentai"))
        source_entry = ("online", source_gallery, provider, b"cover")
        window._onlineSearchReturnState = None
        window._detailNavigationHistory = deque((earlier,))
        window._readingSequenceContext = None
        window._currentDetailNavigationEntry = lambda: source_entry
        window.onlineMangaInterface.searchForText = MagicMock()
        window.onlineMangaInterface.setDetailReturnAvailable = MagicMock()

        window.searchSelectedTitleOnline("Source title")
        window.openOnlineMangaDetail = MagicMock()

        self.assertTrue(window.navigateBack())
        window.openOnlineMangaDetail.assert_called_once_with(
            source_gallery, provider, b"cover"
        )
        self.assertEqual([earlier], list(window._detailNavigationHistory))

    def test_selected_title_online_search_return_expires_on_unrelated_page(self):
        window = self.window
        window._onlineSearchReturnState = {"entry": ("local", object())}
        window.onlineMangaInterface.setDetailReturnAvailable = MagicMock()
        window.switchTo(window.downloadManagerInterface)

        window._onOnlineSearchPageChanged(0)

        self.assertIsNone(window._onlineSearchReturnState)
        window.onlineMangaInterface.setDetailReturnAvailable.assert_called_once_with(
            False
        )

    def test_batch_metadata_sync_runs_two_at_once_and_reloads_once(self):
        window = self.window
        window._localMetadataSyncWorker = None
        window._localMetadataBatchQueue = deque()
        window._localMetadataBatchWorkers = {}
        window._localMetadataBatchTotal = 0
        window._localMetadataBatchCompleted = 0
        window._localMetadataBatchFailures = []
        window._onlineDownloadWorkers = {}
        window.userLibraryRepository = EmptySyncRepository()
        window.onlineDetailThreadPool = FakeThreadPool()
        reloads = []
        window.localMangaInterface.reload = lambda: reloads.append(True)
        window._createLocalMetadataSyncWorker = (
            lambda item, _download=None, _sync=None: FakeSyncWorker(item.gid)
        )
        items = tuple(SimpleNamespace(gid=gid) for gid in (10, 11, 12))

        with (
            patch("app.view.main_window.InfoBar.info"),
            patch("app.view.main_window.InfoBar.success") as success,
        ):
            window.syncLocalGalleryMetadataBatch(items)
            self.assertEqual(
                [10, 11],
                [worker.gid for worker in window.onlineDetailThreadPool.started],
            )
            first, second = window.onlineDetailThreadPool.started
            first.signals.loaded.emit(SimpleNamespace())
            self.assertEqual(
                [10, 11, 12],
                [worker.gid for worker in window.onlineDetailThreadPool.started],
            )
            third = window.onlineDetailThreadPool.started[-1]
            second.signals.loaded.emit(SimpleNamespace())
            self.assertEqual([], reloads)
            third.signals.loaded.emit(SimpleNamespace())

        self.assertEqual([True], reloads)
        success.assert_called_once()

    def test_resume_without_local_gallery_refetches_detail_before_download(self):
        window = self.window
        record = OnlineGalleryDownloadRecord(
            gid=4120989,
            site="exhentai",
            token="gallerytoken",
            title="Interrupted download",
            dirname="",
            page_count=28,
            completed_pages=0,
            metadata={"category": "Manga"},
        )
        repository = BootstrapRepository(record)
        provider = SimpleNamespace(settings=SimpleNamespace(site="exhentai"))
        pool = FakeThreadPool()
        queued = []
        states = []
        window.userLibraryRepository = repository
        window.mangaDetailInterface = SimpleNamespace(currentItem=None)
        window._libraryItems = []
        window._onlineDownloadWorkers = {}
        window._localDownloadPrepareWorkers = {}
        window.onlineDetailThreadPool = pool
        window._createOnlineDownloadProvider = lambda site: provider
        window._refreshDownloadManager = lambda: None
        window._setCurrentDownloadState = lambda *args: states.append(args)
        window._queueOnlineGalleryDownload = lambda *args: queued.append(args)

        with patch("app.view.main_window.OnlineDetailWorker", FakeBootstrapWorker):
            window.startManagedGalleryDownload(record.gid)

        self.assertEqual(1, len(pool.started))
        worker = pool.started[0]
        self.assertFalse(worker.fetch_cover)
        self.assertEqual(
            "https://exhentai.org/g/4120989/gallerytoken/",
            worker.item.url,
        )
        self.assertIs(worker, window._localDownloadPrepareWorkers[record.gid])
        self.assertEqual("queued", repository.updates[-1][2])
        self.assertIn("重新获取画廊信息", states[-1][-1])

        detail = SimpleNamespace(gallery=SimpleNamespace(gid=record.gid))
        worker.signals.loaded.emit(detail, b"")

        self.assertNotIn(record.gid, window._localDownloadPrepareWorkers)
        self.assertEqual([(detail, provider, b"")], queued)

    def test_online_list_download_registers_task_before_fetching_detail(self):
        window = self.window
        repository = DownloadPreparationRepository()
        provider = SimpleNamespace(settings=SimpleNamespace(site="ehentai"))
        registration_pool = FakeThreadPool()
        detail_pool = FakeThreadPool()
        states = []
        refreshed = []
        cached = []
        started = []
        markers = []
        item = SimpleNamespace(
            gid=321,
            token="gallery-token",
            title="List download",
            url="https://e-hentai.org/g/321/gallery-token/",
            category="Manga",
            thumbnail_url="https://ehgt.org/thumb.jpg",
            posted="today",
            uploader="tester",
            rating=4.0,
            tags=("artist:tester",),
            page_count=12,
        )
        window.userLibraryRepository = repository
        window._onlineDownloadWorkers = {}
        window._localDownloadPrepareWorkers = {}
        window.onlineDownloadRegistrationThreadPool = registration_pool
        window.onlineDetailThreadPool = detail_pool
        window.onlineMangaInterface.setGalleryDownloaded = markers.append
        window.onlineGalleryCache = SimpleNamespace(
            put_cover_data=lambda *_args: None,
            get_detail=lambda *_args: None,
            put_detail=lambda *args: cached.append(args),
        )
        window._setCurrentDownloadState = lambda *args: states.append(args)
        window._refreshDownloadManager = lambda: refreshed.append(True)
        window._startPreparedOnlineGalleryDownload = lambda *args: started.append(args)
        window._prepareOnlineDownloadTarget = (
            lambda record, *_args: (record, Path("download"), True)
        )
        window._announceOnlineDownloadRegistration = lambda *_args: None
        old_label = cfg.get(cfg.onlineEhDownloadLabel)
        cfg.set(cfg.onlineEhDownloadLabel, "自动下载")
        try:
            with patch("app.view.main_window.OnlineDetailWorker", FakeBootstrapWorker):
                window.prepareOnlineGalleryDownload(item, provider, b"cover")

            self.assertNotIn(item.gid, repository.records)
            self.assertEqual([item.gid], markers)
            self.assertEqual(1, len(registration_pool.started))
            registration_pool.started[0].run()
            record = repository.records[item.gid]
            self.assertEqual("queued", record.state)
            self.assertEqual("自动下载", record.metadata["download_label"])
            self.assertIn("正在获取画廊信息", states[-1][-1])
            self.assertIs(
                detail_pool.started[0],
                window._localDownloadPrepareWorkers[item.gid],
            )

            detail = SimpleNamespace(gallery=SimpleNamespace(gid=item.gid))
            detail_pool.started[0].signals.loaded.emit(detail, b"full-cover")
            self.assertNotIn(item.gid, window._localDownloadPrepareWorkers)
            self.assertEqual([("ehentai", detail, b"full-cover")], cached)
            self.assertEqual(
                [(detail, "ehentai", b"full-cover", "自动下载")],
                started,
            )
        finally:
            cfg.set(cfg.onlineEhDownloadLabel, old_label)

    def test_list_download_creates_local_target_before_detail_worker_runs(self):
        window = self.window
        markers = []
        reloads = []
        published = []
        registration_pool = FakeThreadPool()
        detail_pool = FakeThreadPool()
        cover = image_bytes("green")
        gallery = OnlineGallery(
            gid=654321,
            token="deadbeef01",
            url="https://e-hentai.org/g/654321/deadbeef01/",
            title="Queued from list",
            category="Manga",
            thumbnail_url="https://ehgt.org/thumb.jpg",
            posted="today",
            page_count=24,
            tags=("artist:tester",),
            uploader="tester",
            rating=4.0,
        )
        provider = SimpleNamespace(settings=SimpleNamespace(site="ehentai"))
        old_root = cfg.get(cfg.ehViewerMangaRoot)
        old_label = cfg.get(cfg.onlineEhDownloadLabel)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manga_root = root / "downloads"
            manga_root.mkdir()
            repository = UserLibraryRepository(root / "rsviewer.db")
            window.userLibraryRepository = repository
            window.windowCoordinator = None
            window._onlineDownloadWorkers = {}
            window._localDownloadPrepareWorkers = {}
            window._libraryItems = []
            window.onlineDownloadRegistrationThreadPool = registration_pool
            window.onlineDetailThreadPool = detail_pool
            window.onlineGalleryCache = SimpleNamespace(
                put_cover_data=lambda *_args: None,
                get_detail=lambda *_args: None,
            )
            window.onlineMangaInterface.setGalleryDownloaded = markers.append
            window.localMangaInterface.reload = lambda: reloads.append(True)
            window._publishSharedState = lambda *args: published.append(args)
            window._setCurrentDownloadState = MagicMock()
            window._refreshDownloadManager = MagicMock()
            cfg.set(cfg.ehViewerMangaRoot, str(manga_root))
            cfg.set(cfg.onlineEhDownloadLabel, "")
            try:
                with patch(
                    "app.view.main_window.OnlineDetailWorker", FakeBootstrapWorker
                ), patch("app.view.main_window.InfoBar.success"):
                    window.prepareOnlineGalleryDownload(gallery, provider, cover)

                self.assertIsNone(repository.online_gallery_download(gallery.gid))
                self.assertEqual([gallery.gid], markers)
                registration_pool.started[0].run()
                record = repository.online_gallery_download(gallery.gid)
                folder = manga_root / record.dirname
                self.assertEqual("queued", record.state)
                self.assertEqual(gallery.title, record.title)
                self.assertTrue(folder.is_dir())
                self.assertEqual(cover, (folder / ".thumb").read_bytes())
                self.assertFalse((folder / ".ehviewer").exists())
                with closing(sqlite3.connect(str(repository.database_path))) as connection:
                    row = connection.execute(
                        "SELECT TOKEN, TITLE FROM DOWNLOADS WHERE GID = ?",
                        (gallery.gid,),
                    ).fetchone()
                    dirname = connection.execute(
                        "SELECT DIRNAME FROM DOWNLOAD_DIRNAME WHERE GID = ?",
                        (gallery.gid,),
                    ).fetchone()
                self.assertEqual((gallery.token, gallery.title), row)
                self.assertEqual(record.dirname, dirname[0])
                listed = EhViewerDataSource(
                    repository.database_path,
                    manga_root,
                ).list_local_manga()
                self.assertEqual([gallery.gid], [item.gid for item in listed])
                self.assertEqual([gallery.gid, gallery.gid], markers)
                self.assertEqual([True], reloads)
                self.assertIn(("library_refresh",), published)
                self.assertEqual(1, len(detail_pool.started))
            finally:
                cfg.set(cfg.ehViewerMangaRoot, old_root)
                cfg.set(cfg.onlineEhDownloadLabel, old_label)

    def test_online_markers_include_queued_tasks_before_library_reload(self):
        marked = []
        queued = OnlineGalleryDownloadRecord(
            gid=200,
            site="ehentai",
            token="token",
            title="Queued",
            dirname="200-Queued",
            page_count=10,
        )
        window = SimpleNamespace(
            _libraryItems=(SimpleNamespace(gid=100),),
            userLibraryRepository=SimpleNamespace(
                incomplete_online_gallery_downloads=lambda: (queued,)
            ),
            onlineMangaInterface=SimpleNamespace(
                setDownloadedGids=lambda gids: marked.append(set(gids))
            ),
        )

        MainWindow._syncOnlineGalleryDownloadMarkers(window)

        self.assertEqual([{100, 200}], marked)

    def test_detail_download_preregisters_before_download_pool_starts(self):
        window = self.window
        markers = []
        reloads = []
        pool = FakeThreadPool()
        cover = image_bytes("blue")
        gallery = OnlineGallery(
            gid=765432,
            token="abcdef0123",
            url="https://exhentai.org/g/765432/abcdef0123/",
            title="Detail title",
            category="Image Set",
            thumbnail_url="https://ehgt.org/detail.jpg",
            page_count=8,
        )
        detail = OnlineGalleryDetail(
            gallery=gallery,
            title=gallery.title,
            category=gallery.category,
            cover_url=gallery.thumbnail_url,
            page_count=gallery.page_count,
        )
        provider = SimpleNamespace(settings=SimpleNamespace(site="exhentai"))
        old_root = cfg.get(cfg.ehViewerMangaRoot)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manga_root = root / "downloads"
            manga_root.mkdir()
            repository = UserLibraryRepository(root / "rsviewer.db")
            window.userLibraryRepository = repository
            window.windowCoordinator = None
            window._onlineDownloadWorkers = {}
            window._localDownloadPrepareWorkers = {}
            window._onlineDownloadSpeeds = {}
            window._libraryItems = []
            window.onlineDownloadThreadPool = pool
            window.onlineGalleryCache = OnlineGalleryMemoryCache()
            window.onlineMangaInterface.setGalleryDownloaded = markers.append
            window.localMangaInterface.reload = lambda: reloads.append(True)
            window._publishSharedState = lambda *_args: None
            window._setCurrentDownloadState = MagicMock()
            window._refreshDownloadManager = MagicMock()
            cfg.set(cfg.ehViewerMangaRoot, str(manga_root))
            try:
                window._queueOnlineGalleryDownload(detail, provider, cover)

                record = repository.online_gallery_download(gallery.gid)
                folder = manga_root / record.dirname
                self.assertTrue(folder.is_dir())
                self.assertEqual(cover, (folder / ".thumb").read_bytes())
                self.assertFalse((folder / ".ehviewer").exists())
                self.assertEqual([gallery.gid], markers)
                self.assertEqual([True], reloads)
                self.assertEqual(1, len(pool.started))
                self.assertIs(
                    window._onlineDownloadWorkers[gallery.gid],
                    pool.started[0],
                )
            finally:
                cfg.set(cfg.ehViewerMangaRoot, old_root)

    def test_missing_sidecar_falls_back_to_online_bootstrap(self):
        window = self.window
        worker = object()
        item = SimpleNamespace(gid=77, page_tokens=(), page_count=0)
        bootstrapped = []
        window._localDownloadPrepareWorkers = {77: worker}
        window._rememberResolvedLocalItem = lambda _item: None
        window._startManagedDownloadBootstrap = bootstrapped.append

        window._finishManagedDownloadDiscovery(worker, item)

        self.assertEqual([77], bootstrapped)

    def test_bulk_download_controls_only_target_matching_task_states(self):
        window = self.window
        records = (
            OnlineGalleryDownloadRecord(
                gid=1,
                site="ehentai",
                token="one",
                title="one",
                dirname="",
                page_count=10,
            ),
            OnlineGalleryDownloadRecord(
                gid=2,
                site="ehentai",
                token="two",
                title="two",
                dirname="",
                page_count=10,
            ),
            OnlineGalleryDownloadRecord(
                gid=3,
                site="ehentai",
                token="three",
                title="three",
                dirname="",
                page_count=10,
            ),
        )
        starts = []
        pauses = []
        window.userLibraryRepository = SimpleNamespace(
            incomplete_online_gallery_downloads=lambda: records
        )
        window._onlineDownloadWorkers = {1: object()}
        window._localDownloadPrepareWorkers = {2: object()}
        window.startManagedGalleryDownload = starts.append
        window.cancelOnlineGalleryDownload = pauses.append

        window.startAllManagedGalleryDownloads()
        window.pauseAllOnlineGalleryDownloads()

        self.assertEqual([3], starts)
        self.assertEqual([1, 2], pauses)

    def test_download_registration_refreshes_library_badge_and_open_detail(self):
        window = self.window
        gid = 4120989
        worker = object()
        reloads = []
        downloaded = []
        refreshed_items = []
        synced = []
        item = SimpleNamespace(gid=gid)
        window._onlineDownloadWorkers = {gid: worker}
        window.localMangaInterface = SimpleNamespace(
            reload=lambda: reloads.append(True)
        )
        window.onlineMangaInterface = SimpleNamespace(
            setGalleryDownloaded=lambda value: downloaded.append(value)
        )
        window.mangaDetailInterface = SimpleNamespace(
            currentItem=item,
            setManga=refreshed_items.append,
        )
        window._syncCurrentDownload = synced.append

        window._registerDownloadedGallery(worker, gid, "folder")
        window._refreshPreparedLocalGallery(worker, gid, "folder")

        self.assertEqual([gid], downloaded)
        self.assertEqual([True], reloads)
        self.assertEqual([item], refreshed_items)
        self.assertEqual([gid], synced)

        window._registerDownloadedGallery(object(), gid, "folder")
        self.assertEqual([True], reloads)

    def test_download_completion_updates_one_gallery_without_revealing_or_reloading(self):
        gid = 4120990
        worker = object()
        record = OnlineGalleryDownloadRecord(
            gid=gid,
            site="ehentai",
            token="token",
            title="Completed",
            dirname="4120990-Completed",
            page_count=18,
            completed_pages=18,
            state="completed",
        )
        refreshed = []
        reloads = []
        window = SimpleNamespace(
            _onlineDownloadWorkers={gid: worker},
            _onlineDownloadSpeeds={gid: 1.0},
            _pendingDownloadDeletes=set(),
            userLibraryRepository=SimpleNamespace(
                online_gallery_download=lambda _gid: record,
                gallery_update=lambda _gid: None,
            ),
            localMangaInterface=SimpleNamespace(
                reload=lambda *args, **kwargs: reloads.append((args, kwargs))
            ),
            mangaDetailInterface=SimpleNamespace(
                reloadCurrentMangaPages=lambda: None
            ),
            _setCurrentDownloadState=lambda *args: None,
            _refreshDownloadManager=lambda: None,
            _refreshLocalGalleryItem=lambda target_gid, folder=None: (
                refreshed.append((target_gid, folder)) or True
            ),
            _syncCurrentDownload=lambda _gid: None,
            _refreshUpdateManager=lambda: None,
        )

        MainWindow._finishOnlineGalleryDownload(
            window, worker, gid, Path("download-folder")
        )

        self.assertEqual([(gid, Path("download-folder"))], refreshed)
        self.assertEqual([], reloads)

    def test_second_gallery_update_stays_queued_while_one_is_running(self):
        record = GalleryUpdateRecord(
            source_gid=2,
            source_token="token",
            site="ehentai",
            title="Queued",
            folder="queued",
            latest_url="https://e-hentai.org/g/2/token/",
        )
        state_updates = []
        repository = SimpleNamespace(
            gallery_update=lambda _gid: record,
            update_gallery_update_state=lambda *args, **kwargs: state_updates.append(
                (args, kwargs)
            ),
        )
        window = SimpleNamespace(
            _galleryUpdateWorkers={1: object()},
            _libraryItems=[],
            userLibraryRepository=repository,
            _refreshUpdateManager=lambda: None,
            _syncCurrentGalleryUpdate=lambda _gid: None,
        )

        MainWindow.startGalleryUpdate(window, 2)

        self.assertEqual(((2, UPDATE_QUEUED), {"error": ""}), state_updates[-1])

    def test_gallery_update_queue_starts_oldest_entry_next(self):
        records = (
            GalleryUpdateRecord(
                source_gid=2,
                source_token="two",
                site="ehentai",
                title="Second",
                folder="second",
                latest_url="https://e-hentai.org/g/2/two/",
                updated_at=20,
            ),
            GalleryUpdateRecord(
                source_gid=1,
                source_token="one",
                site="ehentai",
                title="First",
                folder="first",
                latest_url="https://e-hentai.org/g/1/one/",
                updated_at=10,
            ),
        )
        started = []
        window = SimpleNamespace(
            _closing=False,
            _galleryUpdateWorkers={},
            userLibraryRepository=SimpleNamespace(
                gallery_updates=lambda: records
            ),
            startGalleryUpdate=started.append,
        )

        with patch(
            "app.view.main_window.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            MainWindow._startNextGalleryUpdate(window)

        self.assertEqual([1], started)

    def test_delete_gallery_update_routes_to_owner_or_removes_idle_record(self):
        owner = SimpleNamespace(
            _pendingUpdateDeletes=set(),
            pauseGalleryUpdate=MagicMock(),
        )
        routed = SimpleNamespace(_updateOwner=lambda _gid: owner)

        MainWindow.deleteGalleryUpdate(routed, 42)

        self.assertEqual({42}, owner._pendingUpdateDeletes)
        owner.pauseGalleryUpdate.assert_called_once_with(42)

        repository = SimpleNamespace(delete_gallery_update=MagicMock())
        idle = SimpleNamespace(
            _updateOwner=lambda _gid: None,
            _pendingUpdateDeletes=set(),
            userLibraryRepository=repository,
            _refreshUpdateManager=MagicMock(),
            _syncCurrentGalleryUpdate=MagicMock(),
            _startNextGalleryUpdate=MagicMock(),
        )

        MainWindow.deleteGalleryUpdate(idle, 43)

        repository.delete_gallery_update.assert_called_once_with(43)
        idle._refreshUpdateManager.assert_called_once_with()
        idle._syncCurrentGalleryUpdate.assert_called_once_with(43)
        idle._startNextGalleryUpdate.assert_called_once_with()

    def test_completed_update_progress_cannot_restore_running_state(self):
        record = GalleryUpdateRecord(
            source_gid=42,
            source_token="source",
            site="ehentai",
            title="Completed",
            folder="folder",
            latest_url="https://e-hentai.org/g/43/target/",
            status=6,
            state="completed",
            completed_pages=10,
            page_count=10,
        )
        repository = SimpleNamespace(
            gallery_update=lambda _gid: record,
            update_gallery_update_state=MagicMock(),
        )
        worker = object()
        window = SimpleNamespace(
            _galleryUpdateWorkers={42: worker},
            userLibraryRepository=repository,
            _refreshUpdateManager=MagicMock(),
            _syncCurrentGalleryUpdate=MagicMock(),
        )

        MainWindow._updateGalleryUpdateProgress(window, worker, 42, 10, 10)

        repository.update_gallery_update_state.assert_not_called()

    def test_open_gallery_folder_uses_system_file_manager(self):
        folder = Path(__file__).parent.resolve()
        window = SimpleNamespace(
            _libraryItems=[SimpleNamespace(gid=42, folder=folder)],
            _showGalleryFolderError=MagicMock(),
        )

        with patch(
            "app.view.main_window.QDesktopServices.openUrl",
            return_value=True,
        ) as open_url:
            MainWindow.openGalleryFolder(window, 42)

        opened_url = open_url.call_args.args[0]
        self.assertTrue(opened_url.isLocalFile())
        self.assertEqual(folder.resolve(), Path(opened_url.toLocalFile()))
        window._showGalleryFolderError.assert_not_called()

    def test_download_concurrency_is_hard_capped_at_three(self):
        pool = FakeThreadPool()
        window = SimpleNamespace(onlineDownloadThreadPool=pool)

        MainWindow._updateOnlineDownloadConcurrency(window, 6)

        self.assertEqual(3, MAX_ONLINE_DOWNLOAD_CONCURRENCY)
        self.assertEqual([3], pool.max_thread_counts)

    def test_page_download_threads_are_hard_capped_at_six(self):
        scheduler = MagicMock()
        window = SimpleNamespace(galleryPageDownloadScheduler=scheduler)

        MainWindow._updateOnlinePageDownloadThreads(window, 20)

        scheduler.setThreadCount.assert_called_once_with(6)

    def test_zero_speed_keeps_last_measured_download_speed(self):
        worker = object()
        repository = MagicMock()
        repository.online_gallery_download.return_value = None
        window = SimpleNamespace(
            _onlineDownloadWorkers={42: worker},
            _onlineDownloadSpeeds={42: 2 * 1024 * 1024},
            userLibraryRepository=repository,
            _refreshDownloadManager=MagicMock(),
        )

        MainWindow._updateOnlineDownloadSpeed(window, worker, 42, 0)

        self.assertEqual(2 * 1024 * 1024, window._onlineDownloadSpeeds[42])
        repository.online_gallery_download.assert_not_called()
        window._refreshDownloadManager.assert_not_called()


if __name__ == "__main__":
    unittest.main()
