import os
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from qfluentwidgets import FluentWindow

from app.view.main_window import MainWindow


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


class FakeThreadPool:
    def __init__(self):
        self.started = []

    def start(self, worker):
        self.started.append(worker)


class EmptySyncRepository:
    def online_gallery_download(self, _gid):
        return None

    def gallery_sync_record(self, _gid):
        return None


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
            ["本地资源", "收藏", "在线资源", "历史记录", "正在下载"],
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

    def test_splash_matches_window_size_before_first_show(self):
        window = StartupTestWindow()
        try:
            self.assertEqual(window.size(), window.splashScreen.size())
        finally:
            window.splashScreen.finish()
            window.close()
            window.deleteLater()
            QApplication.processEvents()

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


if __name__ == "__main__":
    unittest.main()
