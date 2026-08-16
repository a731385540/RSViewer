from collections import deque
from dataclasses import replace

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
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    SplashScreen,
    SystemThemeListener,
    isDarkTheme,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import PROJECT_ROOT, cfg
from app.domain.online_download import (
    ONLINE_DOWNLOAD_COMPLETED,
    ONLINE_DOWNLOAD_FAILED,
    ONLINE_DOWNLOAD_PAUSED,
    ONLINE_DOWNLOAD_QUEUED,
    OnlineGalleryDownloadRecord,
)
from app.domain.gallery_update import (
    GalleryUpdateRecord,
    UPDATE_COMPLETED,
    UPDATE_FAILED,
    UPDATE_PAUSED,
    UPDATE_QUEUED,
    UPDATE_RUNNING,
    UPDATE_WAITING_DOWNLOAD,
)
from app.domain.manga import local_page_slot_count
from app.repositories.ehviewer_download_repository import (
    EH_STATE_FINISHED,
    EhViewerDownloadRepository,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.services.eh_tag_search import EhTagSearchIndex
from app.services.online_download_builder import (
    build_online_gallery_from_download_record,
    build_online_gallery_from_local,
    build_online_detail_from_local,
    online_detail_metadata,
)
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.services.search_history import SearchHistoryService
from app.sources.eh_online_source import (
    EhOnlineSettings,
    create_eh_online_provider,
)
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.download_manager_interface import DownloadManagerInterface
from app.view.local_manga_interface import LocalMangaInterface
from app.view.manga_detail_interface import MangaDetailInterface, PageDiscoveryWorker
from app.view.manga_history_interface import MangaHistoryInterface
from app.view.manga_reader_interface import MangaReaderInterface
from app.view.media_interface import MediaInterface
from app.view.navigation_resize_handle import NavigationResizeHandle
from app.view.online_manga_interface import OnlineMangaInterface
from app.view.setting_interface import SettingInterface
from app.view.update_manager_interface import UpdateManagerInterface
from app.workers.reading_progress_worker import (
    BrowsingHistorySaveWorker,
    PlaylistPositionSaveWorker,
    ReadingProgressSaveWorker,
)
from app.workers.eh_online_worker import LocalGallerySyncWorker, OnlineDetailWorker
from app.workers.online_gallery_download_worker import (
    LocalGalleryPageDownloadWorker,
    OnlineGalleryDownloadWorker,
)
from app.workers.gallery_update_worker import GalleryUpdateWorker


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        self.themeListener = SystemThemeListener(self)

        self.mangaSource = self._createMangaSource()
        self.userLibraryRepository = UserLibraryRepository(
            PROJECT_ROOT / "app" / "data" / "rsviewer.db"
        )
        self.userLibraryRepository.mark_interrupted_online_downloads()
        self.userLibraryRepository.mark_interrupted_gallery_updates()
        self.ehTagSearchIndex = EhTagSearchIndex.from_repository(
            self.userLibraryRepository
        )
        self.searchHistoryService = SearchHistoryService(
            self.userLibraryRepository,
            cfg.get(cfg.searchHistoryLimit),
            self,
        )
        cfg.searchHistoryLimit.valueChanged.connect(
            self.searchHistoryService.setLimit
        )
        self.onlineGalleryCache = OnlineGalleryMemoryCache(max_galleries=20)
        self.localMangaInterface = LocalMangaInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
            tag_search_index=self.ehTagSearchIndex,
            search_history_service=self.searchHistoryService,
        )
        self.favoriteMangaInterface = LocalMangaInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
            collection_kind="favorites",
            object_name="favoriteMangaInterface",
            tag_search_index=self.ehTagSearchIndex,
            search_history_service=self.searchHistoryService,
        )
        self.mangaHistoryInterface = MangaHistoryInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
            tag_search_index=self.ehTagSearchIndex,
            search_history_service=self.searchHistoryService,
        )
        self.mangaDetailInterface = MangaDetailInterface(
            self.mangaSource,
            self.userLibraryRepository,
            self,
        )
        self.mangaReaderInterface = MangaReaderInterface(self)
        self.progressThreadPool = QThreadPool(self)
        self.progressThreadPool.setMaxThreadCount(1)
        self.onlineDetailThreadPool = QThreadPool(self)
        self.onlineDetailThreadPool.setMaxThreadCount(2)
        self.onlineDownloadThreadPool = QThreadPool(self)
        self.onlineDownloadThreadPool.setMaxThreadCount(
            cfg.get(cfg.onlineEhDownloadConcurrency)
        )
        self.galleryUpdateThreadPool = QThreadPool(self)
        self.galleryUpdateThreadPool.setMaxThreadCount(1)
        self._onlineDetailWorker = None
        self._onlineDetailProvider = None
        self._localMetadataSyncWorker = None
        self._localMetadataBatchQueue = deque()
        self._localMetadataBatchWorkers = {}
        self._localMetadataBatchTotal = 0
        self._localMetadataBatchCompleted = 0
        self._localMetadataBatchFailures = []
        self._onlineDownloadWorkers = {}
        self._onlineDownloadSpeeds = {}
        self._localPageDownloadWorkers = {}
        self._localPageDownloadSpeeds = {}
        self._pendingDownloadDeletes = set()
        self._localDownloadPrepareWorkers = {}
        self._galleryUpdateWorkers = {}
        self._galleryUpdateSpeeds = {}
        self._pendingProgress = {}
        self.progressSaveTimer = QTimer(self)
        self.progressSaveTimer.setSingleShot(True)
        self.progressSaveTimer.setInterval(180)
        self.progressSaveTimer.timeout.connect(self._flushReadingProgress)
        self._readerWasMaximized = False
        self._playlistContext = None
        self._playlistPageWorker = None
        self._libraryItems = []
        self._historyOrder = []
        self.localMangaInterface.libraryLoaded.connect(self._onLibraryLoaded)
        self.localMangaInterface.mangaActivated.connect(self.openMangaDetail)
        self.localMangaInterface.playlistMangaActivated.connect(
            self.openPlaylistMangaDetail
        )
        self.localMangaInterface.playlistPlayRequested.connect(
            self.startPlaylistPlayback
        )
        self.localMangaInterface.metadataSyncRequested.connect(
            self.syncLocalGalleryMetadataBatch
        )
        for interface in (
            self.localMangaInterface,
            self.favoriteMangaInterface,
            self.mangaHistoryInterface.localHistoryInterface,
        ):
            interface.favoriteChanged.connect(self._onFavoriteChanged)
        self.favoriteMangaInterface.mangaActivated.connect(self.openMangaDetail)
        self.mangaHistoryInterface.localHistoryInterface.mangaActivated.connect(
            self.openMangaDetail
        )
        self.mangaDetailInterface.backRequested.connect(self.navigateBack)
        self.mangaDetailInterface.readRequested.connect(self.openMangaReader)
        self.mangaDetailInterface.onlineReadRequested.connect(
            self.openOnlineMangaReader
        )
        self.mangaDetailInterface.onlineDownloadRequested.connect(
            self.startOnlineGalleryDownload
        )
        self.mangaDetailInterface.onlineDownloadCancelRequested.connect(
            self.cancelOnlineGalleryDownload
        )
        self.mangaDetailInterface.localDownloadRequested.connect(
            self.startLocalGalleryDownload
        )
        self.mangaDetailInterface.localMetadataSyncRequested.connect(
            self.syncLocalGalleryMetadata
        )
        self.mangaDetailInterface.galleryUpdateRequested.connect(
            self.requestGalleryUpdate
        )
        self.mangaDetailInterface.localMangaResolved.connect(
            self._syncLocalDownloadState
        )
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
        self.mangaReaderInterface.localPageDownloadRequested.connect(
            self.downloadLocalGalleryPage
        )
        self.onlineMangaInterface = OnlineMangaInterface(
            self,
            tag_search_index=self.ehTagSearchIndex,
            search_history_service=self.searchHistoryService,
        )
        self.onlineMangaInterface.galleryActivated.connect(
            self.openOnlineMangaDetail
        )
        self.onlineMangaInterface.galleryDownloadRequested.connect(
            self.prepareOnlineGalleryDownload
        )
        self.downloadManagerInterface = DownloadManagerInterface(self)
        self.downloadManagerInterface.startRequested.connect(
            self.startManagedGalleryDownload
        )
        self.downloadManagerInterface.pauseRequested.connect(
            self.cancelOnlineGalleryDownload
        )
        self.downloadManagerInterface.deleteRequested.connect(
            self.deleteOnlineGalleryDownload
        )
        self.downloadManagerInterface.startAllRequested.connect(
            self.startAllManagedGalleryDownloads
        )
        self.downloadManagerInterface.pauseAllRequested.connect(
            self.pauseAllOnlineGalleryDownloads
        )
        self.updateManagerInterface = UpdateManagerInterface(self)
        self.updateManagerInterface.startRequested.connect(self.startGalleryUpdate)
        self.updateManagerInterface.pauseRequested.connect(self.pauseGalleryUpdate)
        self.videoInterface = MediaInterface(
            self.tr("视频"),
            self.tr("本地目录、映射盘与 NAS 视频将在这里显示。"),
            "videoInterface",
            self,
        )
        self.settingInterface = SettingInterface(self)
        self.settingInterface.dataSourceChanged.connect(self.reloadMangaSource)
        cfg.onlineEhDownloadConcurrency.valueChanged.connect(
            self._updateOnlineDownloadConcurrency
        )
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
        self.tagSidebarShortcut = QShortcut(self)
        self.tagSidebarShortcut.setContext(Qt.ApplicationShortcut)
        self._updateTagSidebarShortcut(cfg.get(cfg.tagSidebarShortcut))
        self.tagSidebarShortcut.activated.connect(self.toggleLocalMangaTags)
        cfg.tagSidebarShortcut.valueChanged.connect(
            self._updateTagSidebarShortcut
        )
        self._backKeySequence = QKeySequence()
        self._updateBackShortcut(cfg.get(cfg.backShortcut))
        cfg.backShortcut.valueChanged.connect(self._updateBackShortcut)
        QApplication.instance().installEventFilter(self)
        self.openMangaHome()
        self.splashScreen.finish()
        self.themeListener.start()
        self._refreshDownloadManager()
        self._refreshUpdateManager()

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        self.setWindowIcon(FIF.PHOTO.icon())
        self.setWindowTitle("RSViewer")

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.resize(self.size())
        self.splashScreen.raise_()

        desktop =  QApplication.screens()[0].availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        self.show()
        QApplication.processEvents()

    def initNavigation(self):
        # Full-window slide transitions repaint every visible media card on
        # each frame and become noticeably expensive on large displays.
        self.stackedWidget.setAnimationEnabled(False)
        if not hasattr(self, "updateManagerInterface"):
            self.updateManagerInterface = UpdateManagerInterface(self)
        self.addSubInterface(
            self.localMangaInterface,
            FIF.FOLDER,
            self.tr("本地资源"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.favoriteMangaInterface,
            FIF.HEART,
            self.tr("收藏"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.onlineMangaInterface,
            FIF.GLOBE,
            self.tr("在线资源"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.mangaHistoryInterface,
            FIF.HISTORY,
            self.tr("历史记录"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.downloadManagerInterface,
            FIF.DOWNLOAD,
            self.tr("正在下载"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.updateManagerInterface,
            FIF.SYNC,
            self.tr("更新管理"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.videoInterface,
            FIF.VIDEO,
            self.tr("资源"),
            isTransparent=True,
        )
        self._navigationRoutes = {
            "manga": (
                self.localMangaInterface.objectName(),
                self.favoriteMangaInterface.objectName(),
                self.onlineMangaInterface.objectName(),
                self.mangaHistoryInterface.objectName(),
                self.downloadManagerInterface.objectName(),
                self.updateManagerInterface.objectName(),
            ),
            "video": (self.videoInterface.objectName(),),
        }
        self._navigationDefaultInterfaces = {
            "manga": self.localMangaInterface,
            "video": self.videoInterface,
        }
        self._navigationMode = None
        self.navigationInterface.addItem(
            routeKey="mangaNavigationMode",
            icon=FIF.BOOK_SHELF,
            text=self.tr("漫画"),
            onClick=lambda: self._setNavigationMode("manga"),
            position=NavigationItemPosition.BOTTOM,
            tooltip=self.tr("切换到漫画"),
        )
        self.navigationInterface.addItem(
            routeKey="videoNavigationMode",
            icon=FIF.VIDEO,
            text=self.tr("视频"),
            onClick=lambda: self._setNavigationMode("video"),
            position=NavigationItemPosition.BOTTOM,
            tooltip=self.tr("切换到视频"),
        )
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            self.tr("设置"),
            NavigationItemPosition.BOTTOM,
        )
        self._setNavigationMode("manga", switch_page=False)

    def openMangaHome(self):
        self._setNavigationMode("manga")

    def _setNavigationMode(self, mode: str, switch_page: bool = True):
        if mode not in self._navigationRoutes:
            raise ValueError(f"Unsupported navigation mode: {mode}")

        self._navigationMode = mode
        for route_mode, route_keys in self._navigationRoutes.items():
            visible = route_mode == mode
            for route_key in route_keys:
                self.navigationInterface.widget(route_key).setVisible(visible)

        if not switch_page:
            return

        target = self._navigationDefaultInterfaces[mode]
        self.switchTo(target)
        self.navigationInterface.setCurrentItem(target.objectName())

    def _createMangaSource(self):
        return EhViewerDataSource(
            cfg.get(cfg.ehViewerDatabase),
            cfg.get(cfg.ehViewerMangaRoot),
        )

    def reloadMangaSource(self):
        self._cancelLocalMetadataSync()
        self._cancelLocalMetadataBatchSync()
        self._cancelAllOnlineDownloads()
        for worker in tuple(self._galleryUpdateWorkers.values()):
            worker.cancel()
        self.galleryUpdateThreadPool.clear()
        self._clearPlaylistContext()
        self.mangaSource = self._createMangaSource()
        self._libraryItems = []
        self._historyOrder = []
        self.onlineMangaInterface.setDownloadedGids(())
        self.favoriteMangaInterface.setCollectionItems((), ())
        self.mangaHistoryInterface.setCollectionItems((), ())
        self.localMangaInterface.setSource(self.mangaSource)
        self.favoriteMangaInterface.setSource(self.mangaSource)
        self.mangaHistoryInterface.setSource(self.mangaSource)
        self.mangaDetailInterface.setSource(self.mangaSource)
        self.openMangaHome()

    def _onLibraryLoaded(self, items):
        self._libraryItems = list(items)
        self.onlineMangaInterface.setDownloadedGids(
            item.gid for item in self._libraryItems
        )
        tag_metadata = self.localMangaInterface.tagMetadata()
        self.settingInterface.setOnlineDownloadLabels(tag_metadata[0])
        self.favoriteMangaInterface.setTagMetadata(*tag_metadata)
        self.mangaHistoryInterface.localHistoryInterface.setTagMetadata(
            *tag_metadata
        )
        favorite_order = self.userLibraryRepository.favorite_gids()
        self._historyOrder = list(
            self.userLibraryRepository.browsing_history_gids()
        )
        self.favoriteMangaInterface.setCollectionItems(
            self._libraryItems, favorite_order
        )
        self.mangaHistoryInterface.setCollectionItems(
            self._libraryItems, self._historyOrder
        )
        detail = self.mangaDetailInterface.currentOnlineDetail
        if detail is not None:
            self._syncOnlineDownloadState(detail)
        self._refreshDownloadManager()
        self._refreshUpdateManager()
        current_item = self.mangaDetailInterface.currentItem
        if current_item is not None:
            self._syncCurrentGalleryUpdate(current_item.gid)

    def _onFavoriteChanged(self, gids, favorite):
        for interface in (
            self.localMangaInterface,
            self.favoriteMangaInterface,
            self.mangaHistoryInterface.localHistoryInterface,
        ):
            interface.setFavoriteState(gids, favorite)
        self._libraryItems = list(self.localMangaInterface.allItems())
        self.favoriteMangaInterface.setCollectionItems(
            self._libraryItems,
            self.userLibraryRepository.favorite_gids(),
        )
        self.mangaHistoryInterface.setCollectionItems(
            self._libraryItems, self._historyOrder
        )

    def _recordLocalHistory(self, item):
        gid = int(item.gid)
        self._historyOrder = [
            current_gid for current_gid in self._historyOrder
            if current_gid != gid
        ]
        self._historyOrder.insert(0, gid)
        items = list(self._libraryItems)
        if not any(current.gid == gid for current in items):
            items.append(item)
        self.mangaHistoryInterface.setCollectionItems(items, self._historyOrder)
        self.progressThreadPool.start(
            BrowsingHistorySaveWorker(self.userLibraryRepository, gid)
        )

    def openLocalMangaSearch(self):
        self.openMangaHome()
        self.localMangaInterface.openSearch()

    def toggleLocalMangaTags(self):
        self.openMangaHome()
        self.localMangaInterface.toggleClassification()

    def openMangaDetail(self, item):
        self._cancelOnlineDetailLoad()
        self._cancelLocalMetadataSync()
        self._clearPlaylistContext()
        self._recordLocalHistory(item)
        if self.mangaReaderInterface.isFullscreen:
            self.setReaderFullscreen(False)
        self.mangaDetailInterface.setManga(item)
        self._syncCurrentGalleryUpdate(item.gid)
        self.switchTo(self.mangaDetailInterface)

    def openPlaylistMangaDetail(self, item, playlist_id, items, position):
        self._cancelOnlineDetailLoad()
        self._cancelLocalMetadataSync()
        self._setPlaylistContext(playlist_id, items, position)
        self._recordLocalHistory(item)
        if self.mangaReaderInterface.isFullscreen:
            self.setReaderFullscreen(False)
        self.mangaDetailInterface.setManga(item)
        self._syncCurrentGalleryUpdate(item.gid)
        self.switchTo(self.mangaDetailInterface)

    def openOnlineMangaDetail(self, item, provider, cover_data=b""):
        self._cancelOnlineDetailLoad()
        self._cancelLocalMetadataSync()
        self._clearPlaylistContext()
        if self.mangaReaderInterface.isFullscreen:
            self.setReaderFullscreen(False)
        self._onlineDetailProvider = provider
        site = provider.settings.site
        self.onlineGalleryCache.touch(site, item)
        if cover_data:
            self.onlineGalleryCache.put_cover_data(site, item, cover_data)
        else:
            cover_data = self.onlineGalleryCache.cover_data(site, item)
        self.mangaDetailInterface.setOnlineLoading(
            item, provider, self.onlineGalleryCache, cover_data
        )
        self.switchTo(self.mangaDetailInterface)
        cached_detail = self.onlineGalleryCache.get_detail(site, item)
        if cached_detail is not None:
            self.mangaDetailInterface.setOnlineDetail(
                cached_detail,
                cover_data,
                provider,
                self.onlineGalleryCache,
            )
            self._syncOnlineDownloadState(cached_detail)
            return
        worker = OnlineDetailWorker(provider, item, cover_data)
        worker.signals.loaded.connect(
            lambda detail, data: self._finishOnlineDetail(worker, detail, data)
        )
        worker.signals.failed.connect(
            lambda message: self._failOnlineDetail(worker, message)
        )
        self._onlineDetailWorker = worker
        self.onlineDetailThreadPool.start(worker)

    def _finishOnlineDetail(self, worker, detail, cover_data):
        if self._onlineDetailWorker is not worker:
            return
        self._onlineDetailWorker = None
        provider = self._onlineDetailProvider
        if provider is None:
            return
        site = provider.settings.site
        self.onlineGalleryCache.put_detail(site, detail, cover_data)
        self.mangaDetailInterface.setOnlineDetail(
            detail, cover_data, provider, self.onlineGalleryCache
        )
        self._syncOnlineDownloadState(detail)

    def _failOnlineDetail(self, worker, message):
        if self._onlineDetailWorker is not worker:
            return
        self._onlineDetailWorker = None
        self.mangaDetailInterface.setOnlineError(message)

    def _cancelOnlineDetailLoad(self):
        if self._onlineDetailWorker is not None:
            self._onlineDetailWorker.cancelled = True
            self._onlineDetailWorker = None
        self._onlineDetailProvider = None

    def openOnlineMangaReader(self, detail, page_index=0):
        provider = self._onlineDetailProvider
        if provider is None or not detail.page_count:
            return
        self.mangaDetailInterface.cancelLoads()
        self.mangaReaderInterface.setPlaylistContinuation(False, False)
        self.mangaReaderInterface.setOnlineGallery(
            detail,
            provider,
            self.onlineGalleryCache,
            page_index,
        )
        self.switchTo(self.mangaReaderInterface)
        self.mangaReaderInterface.setFocus()

    def startOnlineGalleryDownload(self, detail):
        gid = int(detail.gallery.gid)
        if gid in self._onlineDownloadWorkers:
            return
        detail_provider = self._onlineDetailProvider
        if detail_provider is None:
            self.mangaDetailInterface.setOnlineDownloadState(
                "failed", 0, detail.page_count, self.tr("在线连接已失效，请重新打开画廊")
            )
            return
        try:
            provider = self._createOnlineDownloadProvider(
                detail_provider.settings.site
            )
        except Exception as error:
            self.mangaDetailInterface.setOnlineDownloadState(
                "failed", 0, detail.page_count, str(error)
            )
            return
        self._queueOnlineGalleryDownload(
            detail,
            provider,
            self.onlineGalleryCache.cover_data(
                provider.settings.site, detail.gallery
            ),
        )

    def prepareOnlineGalleryDownload(self, item, provider, cover_data=b""):
        gid = int(item.gid)
        if gid in self._onlineDownloadWorkers or gid in self._localDownloadPrepareWorkers:
            InfoBar.info(
                title=self.tr("下载任务已存在"),
                content=self.tr("这个画廊已经在准备或下载中。"),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500,
                parent=self.onlineMangaInterface,
            )
            return
        site = str(provider.settings.site)
        existing = self.userLibraryRepository.online_gallery_download(gid)
        existing_metadata = dict(existing.metadata or {}) if existing else {}
        target_label = str(
            existing_metadata.get(
                "download_label",
                cfg.get(cfg.onlineEhDownloadLabel),
            )
            or ""
        ).strip()
        metadata = dict(existing_metadata)
        metadata.update(
            {
                "url": item.url,
                "category": item.category,
                "cover_url": item.thumbnail_url,
                "posted": item.posted,
                "uploader": item.uploader,
                "rating": item.rating,
                "tags": list(item.tags),
                "download_label": target_label,
            }
        )
        record = OnlineGalleryDownloadRecord(
            gid=gid,
            site=site,
            token=item.token,
            title=item.title,
            dirname=existing.dirname if existing else "",
            page_count=max(0, int(item.page_count)),
            completed_pages=existing.completed_pages if existing else 0,
            state=ONLINE_DOWNLOAD_QUEUED,
            metadata=metadata,
            created_at=existing.created_at if existing else 0,
        )
        existing_comments = (
            self.userLibraryRepository.online_gallery_comments(gid)
            if existing is not None
            else ()
        )
        self.userLibraryRepository.save_online_gallery_download(
            record,
            existing_comments,
        )
        self._setCurrentDownloadState(
            gid,
            ONLINE_DOWNLOAD_QUEUED,
            record.completed_pages,
            record.page_count,
            self.tr("正在获取画廊信息…"),
        )

        if cover_data:
            self.onlineGalleryCache.put_cover_data(site, item, cover_data)
        cached_detail = self.onlineGalleryCache.get_detail(site, item)
        if cached_detail is not None:
            self._startPreparedOnlineGalleryDownload(
                cached_detail,
                site,
                cover_data or self.onlineGalleryCache.cover_data(site, item),
                target_label,
            )
            return

        worker = OnlineDetailWorker(provider, item, cover_data)
        worker.signals.loaded.connect(
            lambda detail, data: self._finishOnlineDownloadPreparation(
                worker,
                detail,
                data,
                site,
                target_label,
            )
        )
        worker.signals.failed.connect(
            lambda message: self._failOnlineDownloadPreparation(
                worker,
                gid,
                message,
            )
        )
        self._localDownloadPrepareWorkers[gid] = worker
        self._refreshDownloadManager()
        self.onlineDetailThreadPool.start(worker)

    def _finishOnlineDownloadPreparation(
        self, worker, detail, cover_data, site, target_label
    ):
        gid = int(detail.gallery.gid)
        if self._localDownloadPrepareWorkers.get(gid) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(gid, None)
        self.onlineGalleryCache.put_detail(site, detail, cover_data)
        self._startPreparedOnlineGalleryDownload(
            detail,
            site,
            cover_data,
            target_label,
        )

    def _startPreparedOnlineGalleryDownload(
        self, detail, site, cover_data, target_label
    ):
        gid = int(detail.gallery.gid)
        try:
            download_provider = self._createOnlineDownloadProvider(site)
            self._queueOnlineGalleryDownload(
                detail,
                download_provider,
                cover_data,
                target_label=target_label,
            )
        except Exception as error:
            self._markManagedDownloadFailed(gid, str(error))
            self._showOnlineDownloadPreparationError(str(error))
            return
        label_text = target_label or self.tr("未分类")
        InfoBar.success(
            title=self.tr("已加入下载"),
            content=self.tr("画廊将保存到分类：{}").format(label_text),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
            parent=self.onlineMangaInterface,
        )

    def _failOnlineDownloadPreparation(self, worker, gid, message):
        gid = int(gid)
        if self._localDownloadPrepareWorkers.get(gid) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(gid, None)
        self._markManagedDownloadFailed(gid, message)
        self._showOnlineDownloadPreparationError(message)

    def _showOnlineDownloadPreparationError(self, message):
        InfoBar.error(
            title=self.tr("添加下载失败"),
            content=str(message),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self.onlineMangaInterface,
        )

    def startLocalGalleryDownload(self, item):
        gid = int(item.gid)
        update_record = self.userLibraryRepository.gallery_update(gid)
        if (
            update_record is not None
            and update_record.state != UPDATE_COMPLETED
            and update_record.state != UPDATE_WAITING_DOWNLOAD
        ):
            return
        if gid in self._onlineDownloadWorkers:
            return
        record = self.userLibraryRepository.online_gallery_download(gid)
        sync_record = self.userLibraryRepository.gallery_sync_record(gid)
        comments = self.userLibraryRepository.online_gallery_comments(gid)
        try:
            detail = build_online_detail_from_local(
                item,
                record,
                comments,
                cfg.get(cfg.onlineEhSite),
                sync_record,
            )
            provider = self._createOnlineDownloadProvider(
                item.source_site
                or (sync_record.site if sync_record else "")
                or (record.site if record else "")
                or cfg.get(cfg.onlineEhSite)
            )
        except Exception as error:
            self.mangaDetailInterface.setOnlineDownloadState(
                ONLINE_DOWNLOAD_FAILED,
                item.downloaded_page_count,
                item.page_count,
                str(error),
            )
            return
        self._queueOnlineGalleryDownload(
            detail,
            provider,
            initial_completed=item.downloaded_page_count,
        )

    def _createLocalOnlineContext(self, item):
        gid = int(item.gid)
        record = self.userLibraryRepository.online_gallery_download(gid)
        sync_record = self.userLibraryRepository.gallery_sync_record(gid)
        detail = build_online_detail_from_local(
            item,
            record,
            self.userLibraryRepository.online_gallery_comments(gid),
            cfg.get(cfg.onlineEhSite),
            sync_record,
        )
        site = (
            item.source_site
            or (sync_record.site if sync_record else "")
            or (record.site if record else "")
            or cfg.get(cfg.onlineEhSite)
        )
        provider = self._createOnlineDownloadProvider(site)
        return detail, provider

    def _configureLocalOnlinePreview(self, item):
        if local_page_slot_count(item) <= len(item.page_paths):
            return
        try:
            detail, provider = self._createLocalOnlineContext(item)
        except Exception:
            return
        self.mangaDetailInterface.setLocalOnlineContext(
            detail, provider, self.onlineGalleryCache
        )

    def downloadLocalGalleryPage(self, item, page_index):
        gid = int(item.gid)
        if self._isGalleryUpdating(gid):
            return
        page_index = int(page_index)
        key = (gid, page_index)
        if key in self._localPageDownloadWorkers:
            return
        try:
            detail, provider = self._createLocalOnlineContext(item)
            repository = EhViewerDownloadRepository(
                cfg.get(cfg.ehViewerDatabase),
                cfg.get(cfg.ehViewerMangaRoot),
            )
            worker = LocalGalleryPageDownloadWorker(
                provider,
                detail,
                page_index,
                item.folder,
                self.onlineGalleryCache,
                repository,
                provider.settings.site,
            )
        except Exception as error:
            self.mangaReaderInterface.setLocalPageDownloadFailed(
                gid, page_index, str(error)
            )
            return
        worker.signals.speedChanged.connect(
            lambda speed: self._updateLocalPageDownloadSpeed(
                worker, key, item, speed
            )
        )
        worker.signals.saved.connect(
            lambda saved_gid, saved_index, path, done, total: self._finishLocalPageDownload(
                worker, key, saved_gid, saved_index, path, done, total
            )
        )
        worker.signals.failed.connect(
            lambda failed_gid, failed_index, message: self._failLocalPageDownload(
                worker, key, failed_gid, failed_index, message
            )
        )
        self._localPageDownloadWorkers[key] = worker
        self._localPageDownloadSpeeds[key] = 0.0
        self.mangaReaderInterface.setDownloadState(
            gid,
            "downloading",
            item.downloaded_page_count,
            detail.page_count,
            0.0,
            self.tr("正在下载第 {} 页…").format(page_index + 1),
        )
        self.onlineDetailThreadPool.start(worker)

    def _updateLocalPageDownloadSpeed(self, worker, key, item, speed):
        if self._localPageDownloadWorkers.get(key) is not worker:
            return
        speed = max(0.0, float(speed or 0))
        self._localPageDownloadSpeeds[key] = speed
        self.mangaReaderInterface.setDownloadState(
            item.gid,
            "downloading",
            item.downloaded_page_count,
            item.page_count,
            speed,
            self.tr("正在下载第 {} 页…").format(key[1] + 1),
        )

    def _finishLocalPageDownload(
        self, worker, key, gid, page_index, page_path, completed, total
    ):
        if self._localPageDownloadWorkers.get(key) is not worker:
            return
        self._localPageDownloadWorkers.pop(key, None)
        speed = self._localPageDownloadSpeeds.pop(key, 0.0)
        active_worker = self._onlineDownloadWorkers.get(int(gid))
        if active_worker is not None:
            active_worker.markPageAvailable(page_index)
        else:
            record = self.userLibraryRepository.online_gallery_download(gid)
            if record is not None:
                state = (
                    ONLINE_DOWNLOAD_COMPLETED
                    if int(completed) >= int(total)
                    else ONLINE_DOWNLOAD_PAUSED
                )
                self.userLibraryRepository.update_online_download(
                    gid, completed, state, ""
                )
                if state == ONLINE_DOWNLOAD_COMPLETED:
                    worker.ehviewer_repository.mark_state(gid, EH_STATE_FINISHED)
        updated = self.mangaDetailInterface.addDownloadedPage(
            gid, page_index, page_path, completed, total
        )
        reader_item = self.mangaReaderInterface.addDownloadedPage(
            gid, page_index, page_path, completed, total
        )
        self._rememberResolvedLocalItem(
            updated or reader_item or self.mangaReaderInterface.currentItem
        )
        if active_worker is not None:
            self._setCurrentDownloadState(
                gid, "downloading", completed, total
            )
            self.mangaReaderInterface.setDownloadState(
                gid, "downloading", completed, total, speed
            )
        else:
            state = (
                ONLINE_DOWNLOAD_COMPLETED
                if int(completed) >= int(total)
                else ONLINE_DOWNLOAD_PAUSED
            )
            self.mangaReaderInterface.setDownloadState(
                gid, state, completed, total, speed
            )
        self._refreshDownloadManager()

    def _failLocalPageDownload(self, worker, key, gid, page_index, message):
        if self._localPageDownloadWorkers.get(key) is not worker:
            return
        self._localPageDownloadWorkers.pop(key, None)
        self._localPageDownloadSpeeds.pop(key, None)
        self.mangaReaderInterface.setLocalPageDownloadFailed(
            gid, page_index, message
        )
        if int(gid) not in self._onlineDownloadWorkers:
            item = self.mangaReaderInterface.currentItem
            completed = item.downloaded_page_count if item is not None else 0
            total = item.page_count if item is not None else 0
            self.mangaReaderInterface.setDownloadState(
                gid, "failed", completed, total, 0.0, message
            )

    def syncLocalGalleryMetadata(self, item):
        gid = int(item.gid)
        if self._isGalleryUpdating(gid):
            return
        if (
            self._localMetadataSyncWorker is not None
            or self._localMetadataBatchWorkers
            or self._localMetadataBatchQueue
        ):
            return
        if gid in self._onlineDownloadWorkers:
            message = self.tr("画廊正在下载，请暂停或完成后再同步信息")
            self.mangaDetailInterface.setLocalSyncState(
                False,
                message,
            )
            self._showLocalMetadataSyncError(message)
            return
        download_record = self.userLibraryRepository.online_gallery_download(gid)
        sync_record = self.userLibraryRepository.gallery_sync_record(gid)
        try:
            worker = self._createLocalMetadataSyncWorker(
                item, download_record, sync_record
            )
        except Exception as error:
            self.mangaDetailInterface.setLocalSyncState(False, str(error))
            self._showLocalMetadataSyncError(str(error))
            return
        worker.signals.loaded.connect(
            lambda detail: self._finishLocalMetadataSync(worker, detail)
        )
        worker.signals.failed.connect(
            lambda message: self._failLocalMetadataSync(worker, message)
        )
        self._localMetadataSyncWorker = worker
        self.mangaDetailInterface.setLocalSyncState(
            True,
            self.tr("正在从源站同步标签、评论与版本信息"),
        )
        self.onlineDetailThreadPool.start(worker)

    def syncLocalGalleryMetadataBatch(self, items):
        items = tuple(item for item in items if not self._isGalleryUpdating(item.gid))
        if not items:
            return
        if (
            self._localMetadataSyncWorker is not None
            or self._localMetadataBatchWorkers
            or self._localMetadataBatchQueue
        ):
            InfoBar.warning(
                title=self.tr("已有同步任务"),
                content=self.tr("请等待当前画廊信息同步完成后再试。"),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
                parent=self.localMangaInterface,
            )
            return
        unique_items = []
        seen_gids = set()
        for item in items or ():
            gid = int(item.gid)
            if gid not in seen_gids:
                seen_gids.add(gid)
                unique_items.append(item)
        if not unique_items:
            return
        self._localMetadataBatchQueue.extend(unique_items)
        self._localMetadataBatchTotal = len(unique_items)
        self._localMetadataBatchCompleted = 0
        self._localMetadataBatchFailures = []
        InfoBar.info(
            title=self.tr("正在同步在线信息"),
            content=self.tr("已提交 {} 个画廊，最多同时同步 2 个。").format(
                len(unique_items)
            ),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
            parent=self.localMangaInterface,
        )
        self._pumpLocalMetadataBatch()

    def _createLocalMetadataSyncWorker(
        self, item, download_record=None, sync_record=None
    ):
        default_site = cfg.get(cfg.onlineEhSite)
        site = str(
            item.source_site
            or (sync_record.site if sync_record else "")
            or (download_record.site if download_record else "")
            or default_site
        )
        if site not in {"ehentai", "exhentai"}:
            site = default_site
        stored_token = str(
            item.gallery_token
            or (sync_record.token if sync_record else "")
            or (download_record.token if download_record else "")
        )
        gallery_loader = None
        if stored_token:
            gallery = build_online_gallery_from_local(
                item,
                download_record,
                sync_record,
                default_site,
            )
        else:
            gallery = None
            source = self.mangaSource
            missing_token_message = self.tr(
                "本地 .ehviewer 缺少 gallery token，无法从源站同步"
            )

            def gallery_loader():
                spider_info = source.read_spider_info(item)
                if spider_info is None:
                    raise ValueError(missing_token_message)
                resolved_item = replace(
                    item,
                    gallery_token=spider_info.gallery_token,
                    page_count=spider_info.page_count,
                )
                return build_online_gallery_from_local(
                    resolved_item,
                    download_record,
                    sync_record,
                    default_site,
                )

        provider = self._createOnlineDownloadProvider(site)
        return LocalGallerySyncWorker(
            provider,
            gallery,
            EhViewerDownloadRepository(
                cfg.get(cfg.ehViewerDatabase),
                cfg.get(cfg.ehViewerMangaRoot),
            ),
            self.userLibraryRepository,
            gallery_loader=gallery_loader,
        )

    def _pumpLocalMetadataBatch(self):
        while self._localMetadataBatchQueue and len(
            self._localMetadataBatchWorkers
        ) < 2:
            item = self._localMetadataBatchQueue.popleft()
            gid = int(item.gid)
            if gid in self._onlineDownloadWorkers:
                self._localMetadataBatchCompleted += 1
                self._localMetadataBatchFailures.append(
                    self.tr("GID {}：画廊正在下载").format(gid)
                )
                continue
            try:
                worker = self._createLocalMetadataSyncWorker(
                    item,
                    self.userLibraryRepository.online_gallery_download(gid),
                    self.userLibraryRepository.gallery_sync_record(gid),
                )
            except Exception as error:
                self._localMetadataBatchCompleted += 1
                self._localMetadataBatchFailures.append(
                    self.tr("GID {}：{}").format(gid, error)
                )
                continue
            worker.signals.loaded.connect(
                lambda detail, current=worker: (
                    self._finishLocalMetadataBatchItem(current, detail)
                )
            )
            worker.signals.failed.connect(
                lambda message, current=worker: (
                    self._failLocalMetadataBatchItem(current, message)
                )
            )
            self._localMetadataBatchWorkers[worker] = gid
            self.onlineDetailThreadPool.start(worker)
        if (
            not self._localMetadataBatchQueue
            and not self._localMetadataBatchWorkers
            and self._localMetadataBatchTotal
        ):
            self._finishLocalMetadataBatch()

    def _finishLocalMetadataBatchItem(self, worker, _detail):
        if worker not in self._localMetadataBatchWorkers:
            return
        self._localMetadataBatchWorkers.pop(worker)
        self._localMetadataBatchCompleted += 1
        self._pumpLocalMetadataBatch()

    def _failLocalMetadataBatchItem(self, worker, message):
        gid = self._localMetadataBatchWorkers.pop(worker, None)
        if gid is None:
            return
        self._localMetadataBatchCompleted += 1
        self._localMetadataBatchFailures.append(
            self.tr("GID {}：{}").format(gid, message)
        )
        self._pumpLocalMetadataBatch()

    def _finishLocalMetadataBatch(self):
        total = self._localMetadataBatchTotal
        failed = len(self._localMetadataBatchFailures)
        succeeded = max(0, total - failed)
        failure_preview = "；".join(self._localMetadataBatchFailures[:3])
        self._localMetadataBatchTotal = 0
        self._localMetadataBatchCompleted = 0
        self._localMetadataBatchFailures = []
        self.localMangaInterface.reload()
        if failed:
            InfoBar.warning(
                title=self.tr("在线信息同步完成"),
                content=self.tr("成功 {} 个，失败 {} 个。{}").format(
                    succeeded, failed, failure_preview
                ),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=7000,
                parent=self.localMangaInterface,
            )
        else:
            InfoBar.success(
                title=self.tr("在线信息同步完成"),
                content=self.tr("已更新 {} 个本地画廊。").format(succeeded),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
                parent=self.localMangaInterface,
            )

    def _finishLocalMetadataSync(self, worker, detail):
        if self._localMetadataSyncWorker is not worker:
            return
        self._localMetadataSyncWorker = None
        self.mangaDetailInterface.applyLocalSyncedDetail(detail)
        self.localMangaInterface.reload()

    def requestGalleryUpdate(self, item):
        """Create a durable update task and satisfy an incomplete source first."""
        gid = int(item.gid)
        existing = self.userLibraryRepository.gallery_update(gid)
        if existing is not None and existing.state != UPDATE_COMPLETED:
            self.startGalleryUpdate(gid)
            return
        if not item.gallery_token or not item.newer_gallery_urls:
            return
        state = (
            UPDATE_QUEUED
            if item.download_complete is True
            else UPDATE_WAITING_DOWNLOAD
        )
        record = GalleryUpdateRecord(
            source_gid=gid,
            source_token=str(item.gallery_token),
            site=str(item.source_site or cfg.get(cfg.onlineEhSite)),
            title=str(item.display_title),
            folder=str(item.folder),
            latest_url=str(item.newer_gallery_urls[-1]),
            state=state,
            completed_pages=int(item.downloaded_page_count),
            page_count=int(item.page_count),
        )
        self.userLibraryRepository.save_gallery_update(record)
        self._syncCurrentGalleryUpdate(gid)
        self._refreshUpdateManager()
        if state == UPDATE_WAITING_DOWNLOAD:
            self.startLocalGalleryDownload(item)
        else:
            self.startGalleryUpdate(gid)

    def startGalleryUpdate(self, source_gid):
        source_gid = int(source_gid)
        if source_gid in self._galleryUpdateWorkers:
            return
        record = self.userLibraryRepository.gallery_update(source_gid)
        if record is None or record.state == UPDATE_COMPLETED:
            return
        if record.state == UPDATE_WAITING_DOWNLOAD:
            item = next(
                (entry for entry in self._libraryItems if int(entry.gid) == source_gid),
                None,
            )
            if item is None or item.download_complete is not True:
                self.startManagedGalleryDownload(source_gid)
                return
            self.userLibraryRepository.update_gallery_update_state(
                source_gid, UPDATE_QUEUED, error=""
            )
            record = self.userLibraryRepository.gallery_update(source_gid)
        try:
            provider = self._createOnlineDownloadProvider(record.site)
            worker = GalleryUpdateWorker(
                record=record,
                provider=provider,
                gallery_cache=self.onlineGalleryCache,
                ehviewer_repository=EhViewerDownloadRepository(
                    cfg.get(cfg.ehViewerDatabase),
                    cfg.get(cfg.ehViewerMangaRoot),
                ),
                user_repository=self.userLibraryRepository,
            )
        except Exception as error:
            self.userLibraryRepository.update_gallery_update_state(
                source_gid, UPDATE_FAILED, error=str(error)
            )
            self._refreshUpdateManager()
            self._syncCurrentGalleryUpdate(source_gid)
            return
        self.userLibraryRepository.update_gallery_update_state(
            source_gid, UPDATE_RUNNING, error=""
        )
        worker.signals.stageChanged.connect(
            lambda message: self._updateGalleryUpdateStage(worker, source_gid, message)
        )
        worker.signals.checkpointChanged.connect(
            lambda _status: self._refreshUpdateManager()
        )
        worker.signals.progressChanged.connect(
            lambda done, total: self._updateGalleryUpdateProgress(
                worker, source_gid, done, total
            )
        )
        worker.signals.speedChanged.connect(
            lambda speed: self._updateGalleryUpdateSpeed(worker, source_gid, speed)
        )
        worker.signals.completed.connect(
            lambda old_gid, new_gid: self._finishGalleryUpdate(
                worker, old_gid, new_gid
            )
        )
        worker.signals.failed.connect(
            lambda gid, message: self._failGalleryUpdate(worker, gid, message)
        )
        worker.signals.paused.connect(
            lambda gid: self._pauseGalleryUpdateFinished(worker, gid)
        )
        self._galleryUpdateWorkers[source_gid] = worker
        self._galleryUpdateSpeeds[source_gid] = 0.0
        self._refreshUpdateManager()
        self._syncCurrentGalleryUpdate(source_gid)
        self.galleryUpdateThreadPool.start(worker)

    def pauseGalleryUpdate(self, source_gid):
        source_gid = int(source_gid)
        worker = self._galleryUpdateWorkers.get(source_gid)
        if worker is not None:
            worker.cancel()
            return
        record = self.userLibraryRepository.gallery_update(source_gid)
        if record is None or record.state == UPDATE_COMPLETED:
            return
        if record.state == UPDATE_WAITING_DOWNLOAD:
            self.cancelOnlineGalleryDownload(source_gid)
        self.userLibraryRepository.update_gallery_update_state(
            source_gid, UPDATE_PAUSED, error="Update paused"
        )
        self._refreshUpdateManager()
        self._syncCurrentGalleryUpdate(source_gid)

    def _updateGalleryUpdateStage(self, worker, source_gid, message):
        if self._galleryUpdateWorkers.get(int(source_gid)) is not worker:
            return
        record = self.userLibraryRepository.gallery_update(source_gid)
        if record is not None:
            self.userLibraryRepository.update_gallery_update_state(
                source_gid,
                UPDATE_RUNNING,
                status=record.status,
                completed_pages=record.completed_pages,
                page_count=record.page_count,
                error=str(message),
            )
        self._refreshUpdateManager()

    def _updateGalleryUpdateProgress(self, worker, source_gid, done, total):
        if self._galleryUpdateWorkers.get(int(source_gid)) is not worker:
            return
        record = self.userLibraryRepository.gallery_update(source_gid)
        self.userLibraryRepository.update_gallery_update_state(
            source_gid,
            UPDATE_RUNNING,
            status=record.status if record else 0,
            completed_pages=done,
            page_count=total,
            error="",
        )
        self._refreshUpdateManager()
        self._syncCurrentGalleryUpdate(source_gid)

    def _updateGalleryUpdateSpeed(self, worker, source_gid, speed):
        if self._galleryUpdateWorkers.get(int(source_gid)) is not worker:
            return
        self._galleryUpdateSpeeds[int(source_gid)] = max(0.0, float(speed))
        self._refreshUpdateManager()

    def _finishGalleryUpdate(self, worker, source_gid, target_gid):
        source_gid = int(source_gid)
        if self._galleryUpdateWorkers.get(source_gid) is not worker:
            return
        self._galleryUpdateWorkers.pop(source_gid, None)
        self._galleryUpdateSpeeds.pop(source_gid, None)
        self._refreshUpdateManager()
        current = self.mangaDetailInterface.currentItem
        if current is not None and int(current.gid) == source_gid:
            self.openMangaHome()
        self.localMangaInterface.reload(reveal_gid=int(target_gid))

    def _failGalleryUpdate(self, worker, source_gid, _message):
        source_gid = int(source_gid)
        if self._galleryUpdateWorkers.get(source_gid) is not worker:
            return
        self._galleryUpdateWorkers.pop(source_gid, None)
        self._galleryUpdateSpeeds.pop(source_gid, None)
        self._refreshUpdateManager()
        self._syncCurrentGalleryUpdate(source_gid)

    def _pauseGalleryUpdateFinished(self, worker, source_gid):
        self._failGalleryUpdate(worker, source_gid, "")

    def _refreshUpdateManager(self):
        self.updateManagerInterface.setRecords(
            self.userLibraryRepository.gallery_updates(),
            self._galleryUpdateWorkers,
            self._galleryUpdateSpeeds,
        )

    def _syncCurrentGalleryUpdate(self, gid):
        item = self.mangaDetailInterface.currentItem
        if item is None or int(item.gid) != int(gid):
            return
        record = self.userLibraryRepository.gallery_update(gid)
        self.mangaDetailInterface.setGalleryUpdateState(
            record,
            int(gid) in self._galleryUpdateWorkers,
            self._galleryUpdateSpeeds.get(int(gid), 0.0),
        )

    def _isGalleryUpdating(self, gid):
        lookup = getattr(self.userLibraryRepository, "gallery_update", None)
        if lookup is None:
            return False
        record = lookup(int(gid))
        return record is not None and record.state != UPDATE_COMPLETED

    def _failLocalMetadataSync(self, worker, message):
        if self._localMetadataSyncWorker is not worker:
            return
        self._localMetadataSyncWorker = None
        self.mangaDetailInterface.setLocalSyncState(False, message)
        self._showLocalMetadataSyncError(message)

    def _showLocalMetadataSyncError(self, message):
        InfoBar.error(
            title=self.tr("同步画廊信息失败"),
            content=str(message),
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self.mangaDetailInterface,
        )

    def _cancelLocalMetadataSync(self):
        if self._localMetadataSyncWorker is not None:
            self._localMetadataSyncWorker.cancelled = True
            self._localMetadataSyncWorker = None

    def _cancelLocalMetadataBatchSync(self):
        self._localMetadataBatchQueue.clear()
        for worker in tuple(self._localMetadataBatchWorkers):
            worker.cancelled = True
        self._localMetadataBatchWorkers.clear()
        self._localMetadataBatchTotal = 0
        self._localMetadataBatchCompleted = 0
        self._localMetadataBatchFailures = []

    def _createOnlineDownloadProvider(self, site):
        settings = EhOnlineSettings.create(
            site=site,
            cookie=cfg.get(cfg.onlineEhCookie),
            proxy_mode=cfg.get(cfg.onlineEhProxyMode),
            manual_proxy=cfg.get(cfg.onlineEhManualProxy),
            timeout_seconds=cfg.get(cfg.onlineEhRequestTimeout),
        )
        return create_eh_online_provider(settings)

    def _queueOnlineGalleryDownload(
        self,
        detail,
        provider,
        cover_data=b"",
        initial_completed=None,
        target_label=None,
    ):
        gid = int(detail.gallery.gid)
        if gid in self._onlineDownloadWorkers:
            return
        existing = self.userLibraryRepository.online_gallery_download(gid)
        existing_metadata = dict(existing.metadata or {}) if existing else {}
        if target_label is None:
            target_label = existing_metadata.get(
                "download_label",
                cfg.get(cfg.onlineEhDownloadLabel),
            )
        target_label = str(target_label or "").strip()
        completed = (
            int(initial_completed)
            if initial_completed is not None
            else int(existing.completed_pages if existing else 0)
        )
        record = OnlineGalleryDownloadRecord(
            gid=gid,
            site=provider.settings.site,
            token=detail.gallery.token,
            title=detail.title or detail.gallery.title,
            dirname=existing.dirname if existing is not None else "",
            page_count=int(detail.page_count),
            completed_pages=min(completed, int(detail.page_count)),
            state=ONLINE_DOWNLOAD_QUEUED,
            metadata=online_detail_metadata(detail, target_label),
            created_at=existing.created_at if existing is not None else 0,
        )
        self.userLibraryRepository.save_online_gallery_download(
            record,
            detail.comments,
        )
        worker = OnlineGalleryDownloadWorker(
            provider=provider,
            detail=detail,
            cover_data=cover_data,
            gallery_cache=self.onlineGalleryCache,
            ehviewer_repository=EhViewerDownloadRepository(
                cfg.get(cfg.ehViewerDatabase),
                cfg.get(cfg.ehViewerMangaRoot),
            ),
            user_repository=self.userLibraryRepository,
            site=provider.settings.site,
            target_label=target_label,
        )
        worker.signals.stageChanged.connect(
            lambda message: self._updateOnlineDownloadStage(worker, gid, message)
        )
        worker.signals.progressChanged.connect(
            lambda done, total: self._updateOnlineDownloadProgress(
                worker, gid, done, total
            )
        )
        worker.signals.speedChanged.connect(
            lambda speed: self._updateOnlineDownloadSpeed(worker, gid, speed)
        )
        worker.signals.galleryRegistered.connect(
            lambda registered_gid, folder: self._registerDownloadedGallery(
                worker, registered_gid, folder
            )
        )
        worker.signals.sidecarReady.connect(
            lambda prepared_gid, folder: self._refreshPreparedLocalGallery(
                worker, prepared_gid, folder
            )
        )
        worker.signals.pageSaved.connect(
            lambda page_gid, page_index, path, done, total: (
                self._updateDownloadedLocalPage(
                    worker,
                    page_gid,
                    page_index,
                    path,
                    done,
                    total,
                )
            )
        )
        worker.signals.completed.connect(
            lambda completed_gid, _folder: self._finishOnlineGalleryDownload(
                worker, completed_gid
            )
        )
        worker.signals.failed.connect(
            lambda failed_gid, message: self._failOnlineGalleryDownload(
                worker, failed_gid, message
            )
        )
        worker.signals.paused.connect(
            lambda paused_gid: self._pauseOnlineGalleryDownload(worker, paused_gid)
        )
        self._onlineDownloadWorkers[gid] = worker
        self._onlineDownloadSpeeds[gid] = 0.0
        self._setCurrentDownloadState(
            gid,
            ONLINE_DOWNLOAD_QUEUED,
            record.completed_pages,
            detail.page_count,
            self.tr("等待下载…"),
        )
        self._refreshDownloadManager()
        self.onlineDownloadThreadPool.start(worker)

    def startManagedGalleryDownload(self, gid):
        gid = int(gid)
        if gid in self._onlineDownloadWorkers or gid in self._localDownloadPrepareWorkers:
            return
        current = self.mangaDetailInterface.currentItem
        item = current if current is not None and int(current.gid) == gid else None
        if item is None:
            item = next((entry for entry in self._libraryItems if entry.gid == gid), None)
        if item is None:
            self._startManagedDownloadBootstrap(gid)
            return
        if item.page_tokens and item.page_count:
            self.startLocalGalleryDownload(item)
            return
        worker = PageDiscoveryWorker(
            self.mangaSource,
            self.userLibraryRepository,
            item,
        )
        worker.signals.loaded.connect(
            lambda loaded: self._finishManagedDownloadDiscovery(worker, loaded)
        )
        worker.signals.failed.connect(
            lambda message: self._failManagedDownloadDiscovery(worker, gid, message)
        )
        self._localDownloadPrepareWorkers[gid] = worker
        QThreadPool.globalInstance().start(worker)

    def startAllManagedGalleryDownloads(self):
        active_gids = set(self._onlineDownloadWorkers)
        active_gids.update(self._localDownloadPrepareWorkers)
        for record in self.userLibraryRepository.incomplete_online_gallery_downloads():
            gid = int(record.gid)
            if gid not in active_gids:
                self.startManagedGalleryDownload(gid)

    def pauseAllOnlineGalleryDownloads(self):
        active_gids = set(self._onlineDownloadWorkers)
        active_gids.update(self._localDownloadPrepareWorkers)
        for gid in tuple(sorted(active_gids)):
            self.cancelOnlineGalleryDownload(gid)

    def _finishManagedDownloadDiscovery(self, worker, item):
        gid = int(item.gid)
        if self._localDownloadPrepareWorkers.get(gid) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(gid, None)
        self._rememberResolvedLocalItem(item)
        if item.page_tokens and item.page_count:
            self.startLocalGalleryDownload(item)
        else:
            self._startManagedDownloadBootstrap(gid)

    def _failManagedDownloadDiscovery(self, worker, gid, message):
        if self._localDownloadPrepareWorkers.get(int(gid)) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(int(gid), None)
        if self.userLibraryRepository.online_gallery_download(gid) is not None:
            self._startManagedDownloadBootstrap(gid)
            return
        self._markManagedDownloadFailed(gid, message)

    def _startManagedDownloadBootstrap(self, gid):
        gid = int(gid)
        record = self.userLibraryRepository.online_gallery_download(gid)
        if record is None:
            self._refreshDownloadManager()
            return
        try:
            gallery = build_online_gallery_from_download_record(record)
            provider = self._createOnlineDownloadProvider(record.site)
        except Exception as error:
            self._markManagedDownloadFailed(gid, str(error))
            return

        worker = OnlineDetailWorker(
            provider,
            gallery,
            fetch_cover=False,
        )
        worker.signals.loaded.connect(
            lambda detail, cover_data: self._finishManagedDownloadBootstrap(
                worker, gid, detail, cover_data
            )
        )
        worker.signals.failed.connect(
            lambda message: self._failManagedDownloadBootstrap(
                worker, gid, message
            )
        )
        self._localDownloadPrepareWorkers[gid] = worker
        self.userLibraryRepository.update_online_download(
            gid,
            record.completed_pages,
            ONLINE_DOWNLOAD_QUEUED,
        )
        self._setCurrentDownloadState(
            gid,
            ONLINE_DOWNLOAD_QUEUED,
            record.completed_pages,
            record.page_count,
            self.tr("正在重新获取画廊信息…"),
        )
        self._refreshDownloadManager()
        self.onlineDetailThreadPool.start(worker)

    def _finishManagedDownloadBootstrap(
        self, worker, gid, detail, cover_data
    ):
        gid = int(gid)
        if self._localDownloadPrepareWorkers.get(gid) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(gid, None)
        try:
            self._queueOnlineGalleryDownload(
                detail,
                worker.provider,
                cover_data,
            )
        except Exception as error:
            self._markManagedDownloadFailed(gid, str(error))

    def _failManagedDownloadBootstrap(self, worker, gid, message):
        gid = int(gid)
        if self._localDownloadPrepareWorkers.get(gid) is not worker:
            return
        self._localDownloadPrepareWorkers.pop(gid, None)
        self._markManagedDownloadFailed(gid, message)

    def _markManagedDownloadFailed(self, gid, message):
        record = self.userLibraryRepository.online_gallery_download(gid)
        if record is not None:
            self.userLibraryRepository.update_online_download(
                gid,
                record.completed_pages,
                ONLINE_DOWNLOAD_FAILED,
                message,
            )
        self._setCurrentDownloadState(
            gid,
            ONLINE_DOWNLOAD_FAILED,
            record.completed_pages if record is not None else 0,
            record.page_count if record is not None else 0,
            message,
        )
        self._refreshDownloadManager()

    def cancelOnlineGalleryDownload(self, gid):
        gid = int(gid)
        prepare_worker = self._localDownloadPrepareWorkers.pop(gid, None)
        if prepare_worker is not None:
            self._cancelManagedDownloadPreparation(prepare_worker)
            record = self.userLibraryRepository.online_gallery_download(gid)
            if record is not None:
                self.userLibraryRepository.update_online_download(
                    gid,
                    record.completed_pages,
                    ONLINE_DOWNLOAD_PAUSED,
                )
            self._refreshDownloadManager()
            return
        worker = self._onlineDownloadWorkers.get(gid)
        if worker is not None:
            worker.cancel()
            record = self.userLibraryRepository.online_gallery_download(gid)
            completed = record.completed_pages if record is not None else 0
            total = record.page_count if record is not None else 0
            self._setCurrentDownloadState(
                gid,
                "downloading",
                completed,
                total,
                self.tr("正在暂停…"),
            )

    def _updateOnlineDownloadStage(self, worker, gid, message):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        record = self.userLibraryRepository.online_gallery_download(gid)
        completed = record.completed_pages if record is not None else 0
        total = record.page_count if record is not None else 0
        self._setCurrentDownloadState(
            gid, "downloading", completed, total, message
        )
        self._refreshDownloadManager()

    def _updateOnlineDownloadProgress(self, worker, gid, completed, total):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        self._setCurrentDownloadState(
            gid, "downloading", completed, total
        )
        self._refreshDownloadManager()

    def _updateOnlineDownloadSpeed(self, worker, gid, speed):
        gid = int(gid)
        if self._onlineDownloadWorkers.get(gid) is not worker:
            return
        self._onlineDownloadSpeeds[gid] = max(0.0, float(speed or 0))
        record = self.userLibraryRepository.online_gallery_download(gid)
        if record is not None:
            self.mangaReaderInterface.setDownloadState(
                gid,
                "downloading",
                record.completed_pages,
                record.page_count,
                self._onlineDownloadSpeeds[gid],
            )
        self._refreshDownloadManager()

    def _registerDownloadedGallery(self, worker, gid, _folder):
        gid = int(gid)
        if self._onlineDownloadWorkers.get(gid) is not worker:
            return
        self.onlineMangaInterface.setGalleryDownloaded(gid)
        self.localMangaInterface.reload()

    def _refreshPreparedLocalGallery(self, worker, gid, _folder):
        gid = int(gid)
        if self._onlineDownloadWorkers.get(gid) is not worker:
            return
        item = self.mangaDetailInterface.currentItem
        if item is None or int(item.gid) != gid:
            return
        self.mangaDetailInterface.setManga(item)
        self._syncCurrentDownload(gid)

    def _updateDownloadedLocalPage(
        self,
        worker,
        gid,
        page_index,
        page_path,
        completed_pages,
        page_count,
    ):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        self.mangaDetailInterface.addDownloadedPage(
            gid,
            page_index,
            page_path,
            completed_pages,
            page_count,
        )
        self.mangaReaderInterface.addDownloadedPage(
            gid,
            page_index,
            page_path,
            completed_pages,
            page_count,
        )

    def _finishOnlineGalleryDownload(self, worker, gid):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        self._onlineDownloadWorkers.pop(int(gid), None)
        self._onlineDownloadSpeeds.pop(int(gid), None)
        pending_delete = int(gid) in self._pendingDownloadDeletes
        self._pendingDownloadDeletes.discard(int(gid))
        record = self.userLibraryRepository.online_gallery_download(gid)
        total = record.page_count if record is not None else 0
        if pending_delete:
            self.userLibraryRepository.delete_online_gallery_download(gid)
        self._setCurrentDownloadState(
            gid, ONLINE_DOWNLOAD_COMPLETED, total, total
        )
        self._refreshDownloadManager()
        self.localMangaInterface.reload(reveal_gid=gid)
        update_record = self.userLibraryRepository.gallery_update(gid)
        if (
            update_record is not None
            and update_record.state == UPDATE_WAITING_DOWNLOAD
        ):
            self.userLibraryRepository.update_gallery_update_state(
                gid, UPDATE_QUEUED, error=""
            )
            QTimer.singleShot(
                0, lambda target_gid=int(gid): self.startGalleryUpdate(target_gid)
            )
        self._refreshUpdateManager()

    def _failOnlineGalleryDownload(self, worker, gid, message):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        self._onlineDownloadWorkers.pop(int(gid), None)
        self._onlineDownloadSpeeds.pop(int(gid), None)
        if int(gid) in self._pendingDownloadDeletes:
            self._pendingDownloadDeletes.discard(int(gid))
            self.userLibraryRepository.delete_online_gallery_download(gid)
        else:
            self._syncCurrentDownload(gid, message)
        self._refreshDownloadManager()
        self.localMangaInterface.reload()

    def _pauseOnlineGalleryDownload(self, worker, gid):
        if self._onlineDownloadWorkers.get(int(gid)) is not worker:
            return
        self._onlineDownloadWorkers.pop(int(gid), None)
        self._onlineDownloadSpeeds.pop(int(gid), None)
        if int(gid) in self._pendingDownloadDeletes:
            self._pendingDownloadDeletes.discard(int(gid))
            self.userLibraryRepository.delete_online_gallery_download(gid)
        else:
            self._syncCurrentDownload(gid)
        self._refreshDownloadManager()
        self.localMangaInterface.reload()

    def _syncOnlineDownloadState(self, detail):
        gid = int(detail.gallery.gid)
        if gid in self._onlineDownloadWorkers:
            record = self.userLibraryRepository.online_gallery_download(gid)
            completed = record.completed_pages if record is not None else 0
            self.mangaDetailInterface.setOnlineDownloadState(
                "downloading", completed, detail.page_count
            )
            return
        record = self.userLibraryRepository.online_gallery_download(gid)
        if record is not None:
            state = record.state
            if state in {"queued", "downloading"}:
                state = ONLINE_DOWNLOAD_PAUSED
            self.mangaDetailInterface.setOnlineDownloadState(
                state,
                record.completed_pages,
                detail.page_count,
                record.error,
            )
            return
        if any(item.gid == gid for item in self._libraryItems):
            self.mangaDetailInterface.setOnlineDownloadState(
                "completed", detail.page_count, detail.page_count
            )
        else:
            self.mangaDetailInterface.setOnlineDownloadState(
                "idle", 0, detail.page_count
            )

    def _syncCurrentDownload(self, gid, fallback_message=""):
        item = self.mangaDetailInterface.currentItem
        detail = self.mangaDetailInterface.currentOnlineDetail
        online_matches = detail is not None and int(detail.gallery.gid) == int(gid)
        local_matches = item is not None and int(item.gid) == int(gid)
        if not online_matches and not local_matches:
            return
        record = self.userLibraryRepository.online_gallery_download(gid)
        if record is None:
            self.mangaDetailInterface.setOnlineDownloadState(
                "failed",
                item.downloaded_page_count if local_matches else 0,
                item.page_count if local_matches else detail.page_count,
                fallback_message,
            )
            return
        self.mangaDetailInterface.setOnlineDownloadState(
            record.state,
            record.completed_pages,
            item.page_count if local_matches else detail.page_count,
            record.error or fallback_message,
        )

    def _setCurrentDownloadState(
        self, gid, state, completed_pages=0, page_count=0, message=""
    ):
        item = self.mangaDetailInterface.currentItem
        detail = self.mangaDetailInterface.currentOnlineDetail
        if (
            (item is not None and int(item.gid) == int(gid))
            or (detail is not None and int(detail.gallery.gid) == int(gid))
        ):
            self.mangaDetailInterface.setOnlineDownloadState(
                state, completed_pages, page_count, message
            )
        self.mangaReaderInterface.setDownloadState(
            gid,
            state,
            completed_pages,
            page_count,
            self._onlineDownloadSpeeds.get(int(gid), 0.0),
            message,
        )

    def _syncLocalDownloadState(self, item):
        self._rememberResolvedLocalItem(item)
        self._configureLocalOnlinePreview(item)
        gid = int(item.gid)
        record = self.userLibraryRepository.online_gallery_download(gid)
        if gid in self._onlineDownloadWorkers:
            completed = record.completed_pages if record is not None else item.downloaded_page_count
            self._setCurrentDownloadState(
                gid, "downloading", completed, item.page_count
            )
            return
        actual = int(item.downloaded_page_count)
        if record is not None and item.download_complete is not None:
            expected_state = (
                ONLINE_DOWNLOAD_COMPLETED
                if item.download_complete
                else ONLINE_DOWNLOAD_PAUSED
            )
            if record.state != expected_state or record.completed_pages != actual:
                error = "" if item.download_complete else self.tr("本地文件不完整，可继续补齐")
                self.userLibraryRepository.update_online_download(
                    gid, actual, expected_state, error
                )
                record = self.userLibraryRepository.online_gallery_download(gid)
        if record is not None:
            self._setCurrentDownloadState(
                gid,
                record.state,
                actual,
                item.page_count,
                record.error,
            )
        else:
            state = ONLINE_DOWNLOAD_COMPLETED if item.download_complete else "idle"
            self._setCurrentDownloadState(
                gid, state, actual, item.page_count
            )
        self._refreshDownloadManager()
        self._syncCurrentGalleryUpdate(gid)

    def _rememberResolvedLocalItem(self, item):
        if item is None:
            return
        for index, current in enumerate(self._libraryItems):
            if int(current.gid) == int(item.gid):
                self._libraryItems[index] = item
                break

    def deleteOnlineGalleryDownload(self, gid):
        gid = int(gid)
        if gid in self._onlineDownloadWorkers:
            self._pendingDownloadDeletes.add(gid)
            self.cancelOnlineGalleryDownload(gid)
            return
        prepare_worker = self._localDownloadPrepareWorkers.pop(gid, None)
        if prepare_worker is not None:
            self._cancelManagedDownloadPreparation(prepare_worker)
        self.userLibraryRepository.delete_online_gallery_download(gid)
        self._onlineDownloadSpeeds.pop(gid, None)
        self._refreshDownloadManager()

    def _refreshDownloadManager(self):
        active_gids = set(self._onlineDownloadWorkers)
        active_gids.update(self._localDownloadPrepareWorkers)
        self.downloadManagerInterface.setRecords(
            self.userLibraryRepository.incomplete_online_gallery_downloads(),
            active_gids,
            self._onlineDownloadSpeeds,
        )

    def _updateOnlineDownloadConcurrency(self, value):
        self.onlineDownloadThreadPool.setMaxThreadCount(
            min(6, max(1, int(value)))
        )

    def _cancelAllOnlineDownloads(self):
        for gid, worker in tuple(self._onlineDownloadWorkers.items()):
            worker.cancel()
            record = self.userLibraryRepository.online_gallery_download(gid)
            if record is not None:
                self.userLibraryRepository.update_online_download(
                    gid,
                    record.completed_pages,
                    ONLINE_DOWNLOAD_PAUSED,
                )
        self.onlineDownloadThreadPool.clear()
        self._onlineDownloadSpeeds.clear()
        for worker in self._localDownloadPrepareWorkers.values():
            self._cancelManagedDownloadPreparation(worker)
        self._localDownloadPrepareWorkers.clear()

    @staticmethod
    def _cancelManagedDownloadPreparation(worker):
        worker.cancelled = True
        provider = getattr(worker, "provider", None)
        cancel_requests = getattr(provider, "cancel_pending_requests", None)
        if cancel_requests is not None:
            cancel_requests()

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
        direction = -1 if page_index == -2 else 1
        while (
            0 <= position < len(context["items"])
            and self._isGalleryUpdating(context["items"][position].gid)
        ):
            position += direction
        if not 0 <= position < len(context["items"]):
            self.mangaReaderInterface.setPlaylistContinuation(False, False)
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
        if self._isGalleryUpdating(item.gid):
            return
        if not item.page_paths:
            return
        self._recordLocalHistory(item)
        self.mangaDetailInterface.cancelLoads()
        if page_index == -2:
            page_index = local_page_slot_count(item) - 1
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
        self._syncCurrentDownload(item.gid)
        self.mangaReaderInterface.setFocus()

    def updateReadingProgress(self, gid: int, page_index: int, page_count: int):
        self.localMangaInterface.updateReadingProgress(gid, page_index, page_count)
        self.favoriteMangaInterface.updateReadingProgress(gid, page_index, page_count)
        self.mangaHistoryInterface.localHistoryInterface.updateReadingProgress(
            gid, page_index, page_count
        )
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
                    self.openMangaHome()
                    self._clearPlaylistContext()
            return True
        if current is self.mangaDetailInterface:
            if self.mangaDetailInterface.isOnlineGallery:
                self._cancelOnlineDetailLoad()
                self._setNavigationMode("manga", switch_page=False)
                self.switchTo(self.onlineMangaInterface)
                self.navigationInterface.setCurrentItem(
                    self.onlineMangaInterface.objectName()
                )
                return True
            self.openMangaHome()
            self._clearPlaylistContext()
            return True
        if current in {
            self.localMangaInterface,
            self.favoriteMangaInterface,
            self.onlineMangaInterface,
            self.mangaHistoryInterface,
        }:
            self.openMangaHome()
            return True
        return False

    def _updateSearchShortcut(self, shortcut: str):
        self.searchShortcut.setKey(QKeySequence(shortcut))

    def _updateTagSidebarShortcut(self, shortcut: str):
        self.tagSidebarShortcut.setKey(QKeySequence(shortcut))

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
        self.hide()
        self._cancelPlaylistLoad()
        self._cancelOnlineDetailLoad()
        self._cancelLocalMetadataSync()
        self._cancelLocalMetadataBatchSync()
        self._cancelAllOnlineDownloads()
        for worker in tuple(self._galleryUpdateWorkers.values()):
            worker.cancel()
        self.galleryUpdateThreadPool.clear()
        for worker in tuple(self._localPageDownloadWorkers.values()):
            worker.cancel()
        self._localPageDownloadWorkers.clear()
        self._localPageDownloadSpeeds.clear()
        self.localMangaInterface.cancelLoad()
        self.favoriteMangaInterface.cancelLoad()
        self.mangaHistoryInterface.cancelLoad()
        self.onlineMangaInterface.cancelLoad()
        self.mangaDetailInterface.cancelLoads()
        self.mangaReaderInterface.deactivate()
        self.progressSaveTimer.stop()
        self._flushReadingProgress()
        self.progressThreadPool.waitForDone(3000)
        self.onlineDetailThreadPool.waitForDone(3000)
        self.onlineDownloadThreadPool.waitForDone(1000)
        self.galleryUpdateThreadPool.waitForDone(1000)
        self.mangaDetailInterface.waitForOnlineLoads(3000)
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
