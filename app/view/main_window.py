from PySide6.QtCore import QEvent, QSize, QThreadPool, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from qfluentwidgets import (
    FluentWindow,
    NavigationItemPosition,
    SplashScreen,
    SystemThemeListener,
    isDarkTheme,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import PROJECT_ROOT, cfg
from app.repositories.user_library_repository import UserLibraryRepository
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import LocalMangaInterface
from app.view.manga_detail_interface import MangaDetailInterface, PageDiscoveryWorker
from app.view.manga_reader_interface import MangaReaderInterface
from app.view.media_interface import MediaInterface
from app.view.navigation_resize_handle import NavigationResizeHandle
from app.view.setting_interface import SettingInterface
from app.workers.reading_progress_worker import (
    PlaylistPositionSaveWorker,
    ReadingProgressSaveWorker,
)


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        self.themeListener = SystemThemeListener(self)

        self.mangaSource = self._createMangaSource()
        self.userLibraryRepository = UserLibraryRepository(
            PROJECT_ROOT / "app" / "data" / "rsviewer.db"
        )
        self.localMangaInterface = LocalMangaInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
        )
        self.mangaDetailInterface = MangaDetailInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
        )
        self.mangaReaderInterface = MangaReaderInterface(self)
        self.progressThreadPool = QThreadPool(self)
        self.progressThreadPool.setMaxThreadCount(1)
        self._pendingProgress = {}
        self.progressSaveTimer = QTimer(self)
        self.progressSaveTimer.setSingleShot(True)
        self.progressSaveTimer.setInterval(180)
        self.progressSaveTimer.timeout.connect(self._flushReadingProgress)
        self._readerWasMaximized = False
        self._playlistContext = None
        self._playlistPageWorker = None
        self.localMangaInterface.mangaActivated.connect(self.openMangaDetail)
        self.localMangaInterface.playlistMangaActivated.connect(
            self.openPlaylistMangaDetail
        )
        self.localMangaInterface.playlistPlayRequested.connect(
            self.startPlaylistPlayback
        )
        self.mangaDetailInterface.backRequested.connect(self.navigateBack)
        self.mangaDetailInterface.readRequested.connect(self.openMangaReader)
        self.mangaDetailInterface.progressResolved.connect(
            self.localMangaInterface.updateReadingProgress
        )
        self.mangaReaderInterface.backRequested.connect(self.navigateBack)
        self.mangaReaderInterface.fullscreenRequested.connect(
            self.setReaderFullscreen
        )
        self.mangaReaderInterface.progressChanged.connect(
            self.updateReadingProgress
        )
        self.mangaReaderInterface.nextMangaRequested.connect(
            self._openNextPlaylistManga
        )
        self.mangaReaderInterface.previousMangaRequested.connect(
            self._openPreviousPlaylistManga
        )
        self.favoriteMangaInterface = MediaInterface(
            self.tr("收藏"),
            self.tr("已收藏的漫画将在这里显示。"),
            "favoriteMangaInterface",
            self,
        )
        self.onlineMangaInterface = MediaInterface(
            self.tr("在线资源"),
            self.tr("在线漫画数据源接口已预留，当前版本暂不提供此功能。"),
            "onlineMangaInterface",
            self,
        )
        self.mangaHistoryInterface = MediaInterface(
            self.tr("历史记录"),
            self.tr("漫画阅读历史和阅读进度将在这里显示。"),
            "mangaHistoryInterface",
            self,
        )
        self.videoInterface = MediaInterface(
            self.tr("视频"),
            self.tr("本地目录、映射盘与 NAS 视频将在这里显示。"),
            "videoInterface",
            self,
        )
        self.settingInterface = SettingInterface(self)
        self.settingInterface.dataSourceChanged.connect(self.reloadMangaSource)
        self.initNavigation()
        self.stackedWidget.addWidget(self.mangaDetailInterface)
        self.stackedWidget.addWidget(self.mangaReaderInterface)
        self.navigationResizeHandle = NavigationResizeHandle(
            self.navigationInterface,
            self,
        )
        self.searchShortcut = QShortcut(self)
        self.searchShortcut.setContext(Qt.ApplicationShortcut)
        self._updateSearchShortcut(cfg.get(cfg.searchShortcut))
        self.searchShortcut.activated.connect(self.openLocalMangaSearch)
        cfg.searchShortcut.valueChanged.connect(self._updateSearchShortcut)
        self._backKeySequence = QKeySequence()
        self._updateBackShortcut(cfg.get(cfg.backShortcut))
        cfg.backShortcut.valueChanged.connect(self._updateBackShortcut)
        QApplication.instance().installEventFilter(self)
        self.openMangaHome()
        self.splashScreen.finish()
        self.themeListener.start()

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowIcon(FIF.PHOTO.icon())
        self.setWindowTitle("RSViewer")

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop =  QApplication.screens()[0].availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        self.show()
        QApplication.processEvents()

    def initNavigation(self):
        manga_route_key = "mangaInterface"
        self.navigationInterface.addItem(
            routeKey=manga_route_key,
            icon=FIF.BOOK_SHELF,
            text=self.tr("漫画"),
            onClick=self.openMangaHome,
            tooltip=self.tr("漫画"),
        )
        self.addSubInterface(
            self.localMangaInterface,
            FIF.FOLDER,
            self.tr("本地资源"),
            parent=manga_route_key,
            isTransparent=True,
        )
        self.addSubInterface(
            self.favoriteMangaInterface,
            FIF.HEART,
            self.tr("收藏"),
            parent=manga_route_key,
            isTransparent=True,
        )
        self.addSubInterface(
            self.onlineMangaInterface,
            FIF.GLOBE,
            self.tr("在线资源"),
            parent=manga_route_key,
            isTransparent=True,
        )
        self.addSubInterface(
            self.mangaHistoryInterface,
            FIF.HISTORY,
            self.tr("历史记录"),
            parent=manga_route_key,
            isTransparent=True,
        )
        self.addSubInterface(
            self.videoInterface,
            FIF.VIDEO,
            self.tr("视频"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            self.tr("设置"),
            NavigationItemPosition.BOTTOM,
        )

    def openMangaHome(self):
        self.stackedWidget.setCurrentWidget(self.localMangaInterface, popOut=False)
        self.navigationInterface.setCurrentItem("mangaInterface")

    def _createMangaSource(self):
        return EhViewerDataSource(
            cfg.get(cfg.ehViewerDatabase),
            cfg.get(cfg.ehViewerMangaRoot),
        )

    def reloadMangaSource(self):
        self._clearPlaylistContext()
        self.mangaSource = self._createMangaSource()
        self.localMangaInterface.setSource(self.mangaSource)
        self.mangaDetailInterface.setSource(self.mangaSource)
        self.switchTo(self.localMangaInterface)

    def openLocalMangaSearch(self):
        self.switchTo(self.localMangaInterface)
        self.localMangaInterface.openSearch()

    def openMangaDetail(self, item):
        self._clearPlaylistContext()
        if self.mangaReaderInterface.isFullscreen:
            self.setReaderFullscreen(False)
        self.mangaDetailInterface.setManga(item)
        self.switchTo(self.mangaDetailInterface)

    def openPlaylistMangaDetail(self, item, playlist_id, items, position):
        self._setPlaylistContext(playlist_id, items, position)
        if self.mangaReaderInterface.isFullscreen:
            self.setReaderFullscreen(False)
        self.mangaDetailInterface.setManga(item)
        self.switchTo(self.mangaDetailInterface)

    def startPlaylistPlayback(self, playlist_id, items, position, continue_previous):
        self._setPlaylistContext(playlist_id, items, position)
        self._loadPlaylistManga(position, -1 if continue_previous else 0)

    def _setPlaylistContext(self, playlist_id, items, position):
        self._cancelPlaylistLoad()
        self._playlistContext = {
            "playlist_id": int(playlist_id),
            "items": tuple(items),
            "position": int(position),
        }

    def _clearPlaylistContext(self):
        self._cancelPlaylistLoad()
        self._playlistContext = None
        self.mangaReaderInterface.setPlaylistContinuation(False, False)

    def _cancelPlaylistLoad(self):
        if self._playlistPageWorker is not None:
            self._playlistPageWorker.cancelled = True
            self._playlistPageWorker = None

    def _loadPlaylistManga(self, position, page_index=0):
        context = self._playlistContext
        if context is None or not 0 <= position < len(context["items"]):
            return
        self._cancelPlaylistLoad()
        self.mangaReaderInterface.setPlaylistContinuation(False, False)
        item = context["items"][position]
        worker = PageDiscoveryWorker(
            self.mangaSource, self.userLibraryRepository, item
        )
        worker.signals.loaded.connect(
            lambda loaded_item: self._finishPlaylistLoad(
                worker, position, page_index, loaded_item
            )
        )
        worker.signals.failed.connect(
            lambda _message: self._finishFailedPlaylistLoad(worker)
        )
        self._playlistPageWorker = worker
        QThreadPool.globalInstance().start(worker)

    def _finishPlaylistLoad(self, worker, position, page_index, item):
        if self._playlistPageWorker is not worker or self._playlistContext is None:
            return
        self._playlistPageWorker = None
        context = self._playlistContext
        items = list(context["items"])
        items[position] = item
        context["items"] = tuple(items)
        context["position"] = position
        self.openMangaReader(item, page_index)

    def _finishFailedPlaylistLoad(self, worker):
        if self._playlistPageWorker is worker:
            self._playlistPageWorker = None

    def _openNextPlaylistManga(self):
        if self._playlistContext is None:
            return
        next_position = self._playlistContext["position"] + 1
        if next_position >= len(self._playlistContext["items"]):
            self.mangaReaderInterface.setPlaylistContinuation(False)
            return
        self._loadPlaylistManga(next_position, 0)

    def _openPreviousPlaylistManga(self):
        if self._playlistContext is None:
            return
        previous_position = self._playlistContext["position"] - 1
        if previous_position < 0:
            self.mangaReaderInterface.setPlaylistContinuation(
                bool(len(self._playlistContext["items"]) > 1), False
            )
            return
        self._loadPlaylistManga(previous_position, -2)

    def openMangaReader(self, item, page_index=-1):
        if not item.page_paths:
            return
        self.mangaDetailInterface.cancelLoads()
        if page_index == -2:
            page_index = len(item.page_paths) - 1
        elif page_index < 0:
            page_index = item.progress_page_index or 0
        context = self._playlistContext
        if context is not None:
            position = context["position"]
            if context["items"][position].gid == item.gid:
                items = list(context["items"])
                items[position] = item
                context["items"] = tuple(items)
                self.mangaReaderInterface.setPlaylistContinuation(
                    position + 1 < len(items),
                    position > 0,
                )
                self.progressThreadPool.start(
                    PlaylistPositionSaveWorker(
                        self.userLibraryRepository,
                        context["playlist_id"],
                        item.gid,
                    )
                )
            else:
                self._clearPlaylistContext()
        else:
            self.mangaReaderInterface.setPlaylistContinuation(False, False)
        self.mangaReaderInterface.setManga(item, page_index)
        self.switchTo(self.mangaReaderInterface)
        self.mangaReaderInterface.setFocus()

    def updateReadingProgress(self, gid: int, page_index: int, page_count: int):
        self.localMangaInterface.updateReadingProgress(gid, page_index, page_count)
        self.mangaDetailInterface.updateReadingProgress(gid, page_index, page_count)
        self._pendingProgress[int(gid)] = int(page_index)
        self.progressSaveTimer.start()

    def _flushReadingProgress(self):
        if not self._pendingProgress:
            return
        pending_progress = self._pendingProgress
        self._pendingProgress = {}
        for gid, page_index in pending_progress.items():
            self.progressThreadPool.start(
                ReadingProgressSaveWorker(
                    self.userLibraryRepository,
                    gid,
                    page_index,
                )
            )

    def setReaderFullscreen(self, fullscreen: bool):
        fullscreen = bool(fullscreen)
        if fullscreen == self.isFullScreen():
            self.mangaReaderInterface.setFullscreenState(fullscreen)
            return
        if fullscreen:
            self._readerWasMaximized = self.isMaximized()
            self.navigationInterface.hide()
            self.navigationResizeHandle.hide()
            if hasattr(self, "titleBar"):
                self.titleBar.hide()
            self.showFullScreen()
        else:
            if self._readerWasMaximized:
                self.showMaximized()
            else:
                self.showNormal()
            self.navigationInterface.show()
            self.navigationResizeHandle.show()
            if hasattr(self, "titleBar"):
                self.titleBar.show()
        self.mangaReaderInterface.setFullscreenState(fullscreen)
        self.mangaReaderInterface.setFocus()

    def navigateBack(self):
        current = self.stackedWidget.currentWidget()
        if current is self.mangaReaderInterface:
            if self.mangaReaderInterface.isFullscreen:
                self.setReaderFullscreen(False)
            else:
                self.mangaReaderInterface.deactivate()
                if self._playlistContext is None:
                    self.switchTo(self.mangaDetailInterface)
                else:
                    self.switchTo(self.localMangaInterface)
                    self._clearPlaylistContext()
            return True
        if current is self.mangaDetailInterface:
            self.switchTo(self.localMangaInterface)
            self._clearPlaylistContext()
            return True
        if current in {
            self.localMangaInterface,
            self.favoriteMangaInterface,
            self.onlineMangaInterface,
            self.mangaHistoryInterface,
        }:
            self.switchTo(self.localMangaInterface)
            return True
        return False

    def _updateSearchShortcut(self, shortcut: str):
        self.searchShortcut.setKey(QKeySequence(shortcut))

    def _updateBackShortcut(self, shortcut: str):
        self._backKeySequence = QKeySequence(shortcut)

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and watched.window() is not self:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.BackButton:
            if self.navigateBack():
                return True

        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)) or (
                focus is not None and focus.property("capturesShortcut")
            ):
                return super().eventFilter(watched, event)
            pressed = QKeySequence(event.keyCombination())
            if (
                not self._backKeySequence.isEmpty()
                and pressed.matches(self._backKeySequence)
                == QKeySequence.SequenceMatch.ExactMatch
                and self.navigateBack()
            ):
                return True

        return super().eventFilter(watched, event)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())
        if hasattr(self, "navigationResizeHandle"):
            self.navigationResizeHandle.syncGeometry()

    def closeEvent(self, e):
        self._cancelPlaylistLoad()
        self.localMangaInterface.cancelLoad()
        self.mangaDetailInterface.cancelLoads()
        self.mangaReaderInterface.deactivate()
        self.progressSaveTimer.stop()
        self._flushReadingProgress()
        self.progressThreadPool.waitForDone(3000)
        QApplication.instance().removeEventFilter(self)
        self.themeListener.terminate()
        self.themeListener.deleteLater()
        super().closeEvent(e)


    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

        if self.isMicaEffectEnabled():
            QTimer.singleShot(
                100,
                lambda: self.windowEffect.setMicaEffect(self.winId(), isDarkTheme()),
            )
