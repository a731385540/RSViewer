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
    PushButton,
)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import InfoBar
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QWidget

from app.common.config import cfg
from app.common.style_sheet import StyleSheet


class DataPathSettingCard(SettingCard):
    """为单个数据库文件或目录提供明确的路径选择。"""

    pathChanged = Signal(str)

    def __init__(self, config_item, icon, title, content, select_file, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        self.selectFile = select_file
        self.chooseButton = PushButton(self.tr("选择…"), self)
        self.hBoxLayout.addWidget(self.chooseButton)
        self.hBoxLayout.addSpacing(16)
        self.chooseButton.clicked.connect(self._choosePath)
        self._updateContent(cfg.get(config_item))

    def _choosePath(self):
        current = cfg.get(self.configItem)
        if self.selectFile:
            path, _selected_filter = QFileDialog.getOpenFileName(
                self,
                self.tr("选择 EhViewer 数据库"),
                current,
                self.tr("SQLite 数据库 (*.db *.sqlite *.sqlite3);;所有文件 (*)"),
            )
        else:
            path = QFileDialog.getExistingDirectory(
                self,
                self.tr("选择本地漫画根目录"),
                current,
            )
        if not path:
            return
        cfg.set(self.configItem, path)
        self._updateContent(path)
        self.pathChanged.emit(path)

    def _updateContent(self, path: str):
        self.setContent(path or self.tr("尚未配置"))


class ShortcutCaptureButton(PushButton):
    """点击后捕获一次键盘输入，组合键按下即确认。"""

    sequenceCaptured = Signal(str)

    def __init__(self, sequence: str, parent=None):
        self._sequence = sequence
        self._capturing = False
        super().__init__(parent)
        self.setProperty("capturesShortcut", True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(150)
        self.setMaximumWidth(190)
        self.clicked.connect(self.beginCapture)
        self._updateText()

    @property
    def sequence(self) -> str:
        return self._sequence

    def beginCapture(self):
        self._capturing = True
        self.setText(self.tr("请按下快捷键…"))
        self.setFocus(Qt.MouseFocusReason)
        self.grabKeyboard()

    def cancelCapture(self):
        if not self._capturing:
            return
        self._capturing = False
        self.releaseKeyboard()
        self._updateText()

    def event(self, event):
        if self._capturing and event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelCapture()
            event.accept()
            return
        if event.key() in {
            Qt.Key_Control,
            Qt.Key_Shift,
            Qt.Key_Alt,
            Qt.Key_Meta,
        }:
            event.accept()
            return

        sequence = QKeySequence(event.keyCombination()).toString(
            QKeySequence.PortableText
        )
        if sequence:
            self._sequence = sequence
            self._capturing = False
            self.releaseKeyboard()
            self._updateText()
            self.sequenceCaptured.emit(sequence)
        event.accept()

    def focusOutEvent(self, event):
        self.cancelCapture()
        super().focusOutEvent(event)

    def _updateText(self):
        self.setText(self._sequence or self.tr("点击后按键"))


class ShortcutSettingCard(SettingCard):
    """使用按键捕获按钮绑定 QFluentWidgets 配置项。"""

    def __init__(self, config_item, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = config_item
        self.captureButton = ShortcutCaptureButton(cfg.get(config_item), self)
        self.hBoxLayout.addWidget(self.captureButton)
        self.hBoxLayout.addSpacing(16)
        self.captureButton.sequenceCaptured.connect(self._saveShortcut)

    def _saveShortcut(self, shortcut: str):
        cfg.set(self.configItem, shortcut)


class SettingInterface(ScrollArea):
    """ Setting interface """

    dataSourceChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.settingLabel = QLabel(self.tr("设置"), self)

        self.dataSourceGroup = SettingCardGroup(
            self.tr("本地漫画数据源"), self.scrollWidget)
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
        self.ehViewerDatabaseCard = DataPathSettingCard(
            cfg.ehViewerDatabase,
            FIF.DOCUMENT,
            self.tr("EhViewer 数据库"),
            self.tr("只读加载外部 eh.db，不会修改原数据库"),
            True,
            self.dataSourceGroup,
        )
        self.ehViewerMangaRootCard = DataPathSettingCard(
            cfg.ehViewerMangaRoot,
            FIF.FOLDER,
            self.tr("本地漫画根目录"),
            self.tr("EhViewer 下载目录，可选择本地盘、映射盘或 UNC 路径"),
            False,
            self.dataSourceGroup,
        )
        self.libraryFoldersCard = FolderListSettingCard(
            cfg.libraryFolders,
            self.tr("其他媒体目录"),
            self.tr("添加图片或视频使用的本地、映射盘或 NAS 路径"),
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

        self.dataSourceGroup.addSettingCard(self.ehViewerDatabaseCard)
        self.dataSourceGroup.addSettingCard(self.ehViewerMangaRootCard)
        self.personalGroup.addSettingCard(self.themeCard)
        self.personalGroup.addSettingCard(self.themeColorCard)
        self.personalGroup.addSettingCard(self.libraryFoldersCard)
        self.shortcutGroup.addSettingCard(self.searchShortcutCard)
        self.shortcutGroup.addSettingCard(self.backShortcutCard)
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(36, 10, 36, 0)
        self.expandLayout.addWidget(self.dataSourceGroup)
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
        self.ehViewerDatabaseCard.pathChanged.connect(self.dataSourceChanged)
        self.ehViewerMangaRootCard.pathChanged.connect(self.dataSourceChanged)
