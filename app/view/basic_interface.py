from qfluentwidgets import (SettingCardGroup, SwitchSettingCard, FolderListSettingCard,
                            OptionsSettingCard, PushSettingCard,
                            HyperlinkCard, PrimaryPushSettingCard, ScrollArea,
                            ComboBoxSettingCard, ExpandLayout, Theme, CustomColorSettingCard,
                            setTheme, setThemeColor, RangeSettingCard, isDarkTheme)

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar
from app.common.config import cfg
from ..common.style_sheet import StyleSheet
class BaseInterface(ScrollArea):
    """主页面 查看画廊数据"""
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.__initWidget()
    def __initWidget(self):
        self.resize(1000, 800)
        # 唯一
        self.setObjectName('BasicInterface')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 80, 0, 20)
        StyleSheet.SETTING_INTERFACE.apply(self)
        self.__initLayout()
        self.__connectSignalToSlot()
    def __showRestartTooltip(self):
        """ show restart tooltip """
        InfoBar.success(
            self.tr('Updated successfully'),
            self.tr('Configuration takes effect after restart'),
            duration=1500,
            parent=self
        )
    # 内部组件样式
    def __initLayout(self):
        pass

    def __connectSignalToSlot(self):
        """ connect signal to slot """
        cfg.appRestartSig.connect(self.__showRestartTooltip)
        cfg.themeChanged.connect(setTheme)