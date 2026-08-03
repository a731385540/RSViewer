from pathlib import Path

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (
    FluentWindow,
    NavigationItemPosition,
    SplashScreen,
    SystemThemeListener,
    isDarkTheme,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import LocalMangaInterface
from app.view.media_interface import MediaInterface
from app.view.navigation_resize_handle import NavigationResizeHandle
from app.view.setting_interface import SettingInterface


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        self.themeListener = SystemThemeListener(self)

        self.mangaInterface = MediaInterface(
            self.tr("漫画"),
            self.tr("浏览和管理漫画资源。"),
            "mangaInterface",
            self,
        )
        project_root = Path(__file__).resolve().parents[2]
        self.localMangaInterface = LocalMangaInterface(
            EhViewerDataSource(
                project_root / "testData" / "db" / "eh.db",
                project_root / "testData" / "manga",
            ),
            self,
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
        self.initNavigation()
        self.navigationResizeHandle = NavigationResizeHandle(
            self.navigationInterface,
            self,
        )
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
        self.addSubInterface(
            self.mangaInterface,
            FIF.BOOK_SHELF,
            self.tr("漫画"),
            isTransparent=True,
        )
        self.addSubInterface(
            self.localMangaInterface,
            FIF.FOLDER,
            self.tr("本地资源"),
            parent=self.mangaInterface,
            isTransparent=True,
        )
        self.addSubInterface(
            self.favoriteMangaInterface,
            FIF.HEART,
            self.tr("收藏"),
            parent=self.mangaInterface,
            isTransparent=True,
        )
        self.addSubInterface(
            self.onlineMangaInterface,
            FIF.GLOBE,
            self.tr("在线资源"),
            parent=self.mangaInterface,
            isTransparent=True,
        )
        self.addSubInterface(
            self.mangaHistoryInterface,
            FIF.HISTORY,
            self.tr("历史记录"),
            parent=self.mangaInterface,
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

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())
        if hasattr(self, "navigationResizeHandle"):
            self.navigationResizeHandle.syncGeometry()

    def closeEvent(self, e):
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
