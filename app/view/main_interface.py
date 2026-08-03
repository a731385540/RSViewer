from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)
from qfluentwidgets import FluentIcon as FIF
from PySide6.QtCore import Qt, Signal, QUrl, QStandardPaths
from qfluentwidgets import InfoBar
from PySide6.QtWidgets import QWidget, QLabel, QFileDialog
from app.common.config import cfg,isWin11
from ..common.style_sheet import StyleSheet
class MainInterface(ScrollArea):
    """主页面 查看画廊数据"""
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # self.scrollWidget = QWidget()
        # self.expandLayout = ExpandLayout(self.scrollWidget)

        # self.MainViewLabel = QLabel(self.tr("1111"), self)
        self.__initWidget()

    def __initWidget(self):
        self.resize(1000, 800)
        # 唯一
        self.setObjectName('MainInterface')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        # self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        # self.scrollWidget.setObjectName('scrollWidget')

        # 引出样式
        StyleSheet.SETTING_INTERFACE.apply(self)
        # self.MainViewLabel.setObjectName('settingLabel')
        self.__initLayout()
        self.__connectSignalToSlot()
    def __initLayout(self):
        pass


    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('Updated successfully'),
            self.tr('Configuration takes effect after restart'),
            duration=1500,
            parent=self
        )

    def __onDownloadFolderCardClicked(self):
        """ download folder card clicked slot """
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Choose folder"), "./")
        if not folder or cfg.get(cfg.downloadFolder) == folder:
            return

        cfg.set(cfg.downloadFolder, folder)
        self.downloadFolderCard.setContent(folder)

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)

        # music in the pc
        # self.downloadFolderCard.clicked.connect(
        #     self.__onDownloadFolderCardClicked)

        # personalization
        cfg.themeChanged.connect(setTheme)

        # self.micaCard.checkedChanged.connect(signalBus.micaEnableChanged)

        # about
        # self.feedbackCard.clicked.connect(
        #     lambda: QDesktopServices.openUrl(QUrl(FEEDBACK_URL)))