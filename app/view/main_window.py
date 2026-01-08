from PySide6.QtCore import QUrl, QSize, QTimer
from PySide6.QtGui import QIcon, QDesktopServices, QColor
from PySide6.QtWidgets import QApplication

from qfluentwidgets import (NavigationAvatarWidget, NavigationItemPosition, MessageBox, FluentWindow,
                            SplashScreen, SystemThemeListener, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.common.icon import Icon
from app.common.translator import Translator
from app.view.setting_interface import SettingInterface
from app.view.main_interface import MainInterface
from app.view.basic_interface import BaseInterface
from ..common import resource


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        self.themeListener = SystemThemeListener(self)
        # 主页面
        self.MainInterface = MainInterface(self)

        # 样例
        self.BaseInterface = BaseInterface(self)
        # 设置
        self.settingInterface = SettingInterface(self)

        # 侧边栏
        self.initNavigation()
        self.splashScreen.finish()
        self.themeListener.start()

    def initWindow(self):
        self.resize(960, 780)
        self.setMinimumWidth(760)
        # 设置图标
        self.setWindowIcon(QIcon(':/gallery/images/logo.png'))
        self.setWindowTitle('资源阅读器')

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        # create splash screen
        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QApplication.screens()[0].availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)
        self.show()
        QApplication.processEvents()

    # 侧边栏
    def initNavigation(self):

        self.addSubInterface(self.MainInterface, FIF.HOME, self.tr('Home'))
        self.navigationInterface.addSeparator()
        pos = NavigationItemPosition.SCROLL

        self.addSubInterface(self.BaseInterface, FIF.SETTING, "样例", NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settingInterface, FIF.SETTING, self.tr('Settings'), NavigationItemPosition.BOTTOM)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'splashScreen'):
            self.splashScreen.resize(self.size())

    def closeEvent(self, e):
        self.themeListener.terminate()
        self.themeListener.deleteLater()
        super().closeEvent(e)

    def _onThemeChangedFinished(self):
        super()._onThemeChangedFinished()

        # retry
        if self.isMicaEffectEnabled():
            QTimer.singleShot(100, lambda: self.windowEffect.setMicaEffect(self.winId(), isDarkTheme()))
    #
    # def switchToSample(self, routeKey, index):
    #     """ switch to sample """
    #     interfaces = self.findChildren(GalleryInterface)
    #     for w in interfaces:
    #         if w.objectName() == routeKey:
    #             self.stackedWidget.setCurrentWidget(w, False)
    #             w.scrollToCard(index)
