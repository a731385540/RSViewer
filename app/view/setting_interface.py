from qfluentwidgets import (
    CustomColorSettingCard,
    ExpandLayout,
    FolderListSettingCard,
    OptionsSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    setTheme,
    setThemeColor,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit, QLabel, QWidget

from app.common.config import cfg
from app.common.style_sheet import StyleSheet


class ShortcutSettingCard(SettingCard):
    """将 Qt 快捷键编辑器绑定到 QFluentWidgets 配置项。"""

    def __init__(self, config_item, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        self.sequenceEdit = QKeySequenceEdit(self)
        self.sequenceEdit.setKeySequence(QKeySequence(cfg.get(config_item)))
        self.sequenceEdit.setMaximumWidth(180)
        self.hBoxLayout.addWidget(self.sequenceEdit)
        self.hBoxLayout.addSpacing(16)
        self.sequenceEdit.editingFinished.connect(self._saveShortcut)

    def _saveShortcut(self):
        shortcut = self.sequenceEdit.keySequence().toString(QKeySequence.PortableText)
        cfg.set(self.configItem, shortcut)


class SettingInterface(ScrollArea):
    """ Setting interface """

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.settingLabel = QLabel(self.tr("设置"), self)

        self.personalGroup = SettingCardGroup(
            self.tr("界面设置"), self.scrollWidget)
        self.shortcutGroup = SettingCardGroup(
            self.tr("快捷键"), self.scrollWidget)
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
        self.searchShortcutCard = ShortcutSettingCard(
            cfg.searchShortcut,
            FIF.SEARCH,
            self.tr("打开漫画搜索"),
            self.tr("切换到本地漫画并展开搜索栏"),
            self.shortcutGroup,
        )
        self.backShortcutCard = ShortcutSettingCard(
            cfg.backShortcut,
            FIF.RETURN,
            self.tr("返回上一级"),
            self.tr("详情页等下一级页面的通用返回快捷键"),
            self.shortcutGroup,
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
        self.shortcutGroup.addSettingCard(self.searchShortcutCard)
        self.shortcutGroup.addSettingCard(self.backShortcutCard)
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.personalGroup)
        self.expandLayout.addWidget(self.shortcutGroup)

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
