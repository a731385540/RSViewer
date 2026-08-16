import os
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from qfluentwidgets import FluentWindow

from app.common.config import cfg
from app.domain.gallery_update import GalleryUpdateRecord, UPDATE_QUEUED
from app.domain.online_download import OnlineGalleryDownloadRecord
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
        pool = FakeThreadPool()
        states = []
        refreshed = []
        cached = []
        started = []
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
        window.onlineDetailThreadPool = pool
        window.onlineGalleryCache = SimpleNamespace(
            put_cover_data=lambda *_args: None,
            get_detail=lambda *_args: None,
            put_detail=lambda *args: cached.append(args),
        )
        window._setCurrentDownloadState = lambda *args: states.append(args)
        window._refreshDownloadManager = lambda: refreshed.append(True)
        window._startPreparedOnlineGalleryDownload = lambda *args: started.append(args)
        old_label = cfg.get(cfg.onlineEhDownloadLabel)
        cfg.set(cfg.onlineEhDownloadLabel, "自动下载")
        try:
            with patch("app.view.main_window.OnlineDetailWorker", FakeBootstrapWorker):
                window.prepareOnlineGalleryDownload(item, provider, b"cover")

            record = repository.records[item.gid]
            self.assertEqual("queued", record.state)
            self.assertEqual("自动下载", record.metadata["download_label"])
            self.assertIn("正在获取画廊信息", states[-1][-1])
            self.assertIs(pool.started[0], window._localDownloadPrepareWorkers[item.gid])

            detail = SimpleNamespace(gallery=SimpleNamespace(gid=item.gid))
            pool.started[0].signals.loaded.emit(detail, b"full-cover")
            self.assertNotIn(item.gid, window._localDownloadPrepareWorkers)
            self.assertEqual([("ehentai", detail, b"full-cover")], cached)
            self.assertEqual(
                [(detail, "ehentai", b"full-cover", "自动下载")],
                started,
            )
        finally:
            cfg.set(cfg.onlineEhDownloadLabel, old_label)

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

    def test_download_concurrency_is_hard_capped_at_three(self):
        pool = FakeThreadPool()
        window = SimpleNamespace(onlineDownloadThreadPool=pool)

        MainWindow._updateOnlineDownloadConcurrency(window, 6)

        self.assertEqual(3, MAX_ONLINE_DOWNLOAD_CONCURRENCY)
        self.assertEqual([3], pool.max_thread_counts)


if __name__ == "__main__":
    unittest.main()
