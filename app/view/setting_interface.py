from qfluentwidgets import (
    CustomColorSettingCard,
    ExpandLayout,
    FolderListSettingCard,
    OptionsSettingCard,
    ScrollArea,
    SettingCardGroup,
    setTheme,
    setThemeColor,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from app.common.config import cfg
from app.common.style_sheet import StyleSheet


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.settingLabel = QLabel(self.tr("设置"), self)

        self.personalGroup = SettingCardGroup(
            self.tr("界面设置"), self.scrollWidget)
        self.themeCard = OptionsSettingCard(
            cfg.themeMode,
            FIF.BRUSH,
            self.tr("应用主题"),
            self.tr("更改应用的明暗外观"),
            texts=[
                self.tr("浅色"), self.tr("深色"),
                self.tr("跟随系统")
            ],
            parent=self.personalGroup
        )
        self.themeColorCard = CustomColorSettingCard(
            cfg.themeColor,
            FIF.PALETTE,
            self.tr("主题色"),
            self.tr("更改应用的强调色"),
            self.personalGroup
        )
        self.libraryFoldersCard = FolderListSettingCard(
            cfg.libraryFolders,
            self.tr("媒体目录"),
            self.tr("添加本地目录、映射盘或 NAS 网络共享路径"),
            parent=self.personalGroup,
        )

        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName('settingInterface')

        self.scrollWidget.setObjectName('scrollWidget')
        self.settingLabel.setObjectName('settingLabel')
        StyleSheet.SETTING_INTERFACE.apply(self)
        self.__initLayout()
        self.__connectSignalToSlot()

    # 定义布局
    def __initLayout(self):
        self.settingLabel.move(36, 30)

        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.libraryFoldersCard)
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.personalGroup)

    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr("更新成功"),
            self.tr("配置将在重启后生效"),
            duration=1500,
            parent=self
        )

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        cfg.themeChanged.connect(setTheme)
        self.themeColorCard.colorChanged.connect(lambda c: setThemeColor(c))
