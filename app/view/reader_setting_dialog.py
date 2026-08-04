from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget
from qfluentwidgets import (
    ColorSettingCard,
    OptionsSettingCard,
    ScrollArea,
    SettingCardGroup,
    SwitchSettingCard,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.common.style_sheet import StyleSheet
from app.view.setting_interface import ShortcutSettingCard


class ReaderSettingDialog(QDialog):
    """Non-modal reader settings backed by the global QConfig instance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("readerSettingDialog")
        self.setWindowTitle(self.tr("阅读设置"))
        self.setModal(False)
        self.resize(620, 700)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setObjectName("readerSettingScrollArea")
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollWidget = QWidget(self.scrollArea)
        self.scrollWidget.setObjectName("readerSettingScrollWidget")
        self.scrollArea.setWidget(self.scrollWidget)
        self.group = SettingCardGroup(self.tr("漫画阅读器"), self.scrollWidget)

        self.backgroundCard = ColorSettingCard(
            cfg.readerBackgroundColor,
            FIF.PALETTE,
            self.tr("阅读背景颜色"),
            self.tr("设置漫画图片周围的画布颜色"),
            self.group,
        )
        self.directionCard = OptionsSettingCard(
            cfg.readerPageDirection,
            FIF.MOVE,
            self.tr("翻页方向"),
            self.tr("括号中的方向键用于翻到下一页"),
            texts=[
                self.tr("从左向右（←）"),
                self.tr("从右向左（→）"),
                self.tr("从上向下（↑）"),
                self.tr("从下向上（↓）"),
            ],
            parent=self.group,
        )
        self.imageLoadSizeCard = OptionsSettingCard(
            cfg.readerImageLoadSize,
            FIF.FIT_PAGE,
            self.tr("图片载入大小"),
            self.tr("控制图片首次显示时的缩放方式"),
            texts=[
                self.tr("适应窗口"),
                self.tr("适应宽度（长图）"),
                self.tr("原始大小"),
            ],
            parent=self.group,
        )
        self.scrollShortcutCard = ShortcutSettingCard(
            cfg.readerScrollShortcut,
            FIF.SCROLL,
            self.tr("向前滚动"),
            self.tr("滚动一屏，到底后进入下一页"),
            self.group,
        )
        self.autoPageCard = SwitchSettingCard(
            FIF.PLAY,
            self.tr("自动翻页"),
            self.tr("按设定间隔自动进入下一页"),
            cfg.readerAutoPageEnabled,
            self.group,
        )
        self.autoPageIntervalCard = OptionsSettingCard(
            cfg.readerAutoPageInterval,
            FIF.SPEED_HIGH,
            self.tr("自动翻页间隔"),
            self.tr("每次自动翻页等待的时间"),
            texts=[
                self.tr("2 秒"),
                self.tr("3 秒"),
                self.tr("5 秒"),
                self.tr("8 秒"),
                self.tr("10 秒"),
                self.tr("15 秒"),
                self.tr("30 秒"),
            ],
            parent=self.group,
        )

        for card in (
            self.backgroundCard,
            self.directionCard,
            self.imageLoadSizeCard,
            self.scrollShortcutCard,
            self.autoPageCard,
            self.autoPageIntervalCard,
        ):
            self.group.addSettingCard(card)

        contentLayout = QVBoxLayout(self.scrollWidget)
        contentLayout.setContentsMargins(18, 18, 18, 18)
        contentLayout.addWidget(self.group)
        contentLayout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scrollArea)
        StyleSheet.READER_SETTING_DIALOG.apply(self)
