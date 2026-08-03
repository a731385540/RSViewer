from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    ScrollArea,
    SearchLineEdit,
    SegmentedToolWidget,
    TitleLabel,
    isDarkTheme,
)
from qfluentwidgets import FluentIcon as FIF

from app.domain.manga import MangaItem
from app.sources.ehviewer_source import EhViewerDataSource


class FadeTextLabel(QLabel):
    """在宽度不足时让文本末端渐隐，完整内容保留在工具提示中。"""

    def __init__(self, text="", muted=False, parent=None):
        super().__init__(text, parent)
        self.muted = muted
        self.setFixedHeight(22 if not muted else 18)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setToolTip(text)

    def setText(self, text: str):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        text = self.text()
        if not text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font())
        rect = self.contentsRect()
        color = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
        if self.muted:
            color.setAlpha(155)

        text_width = QFontMetrics(self.font()).horizontalAdvance(text)
        if text_width <= rect.width():
            painter.setPen(color)
        else:
            fade_width = min(52, max(24, rect.width() // 4))
            fade_start = max(0.0, (rect.width() - fade_width) / max(1, rect.width()))
            transparent = QColor(color)
            transparent.setAlpha(0)
            gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(fade_start, color)
            gradient.setColorAt(1.0, transparent)
            painter.setPen(QPen(QBrush(gradient), 1))

        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, text)


class CoverLabel(QWidget):
    """按封面比例裁切图片并绘制圆角。"""

    def __init__(self, image_path: Path, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap(str(image_path))
        self.setMinimumSize(72, 96)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(self.rect(), 8, 8)
        painter.setClipPath(path)

        if self._pixmap.isNull():
            placeholder = self.palette().color(QPalette.AlternateBase)
            painter.fillRect(self.rect(), placeholder)
            painter.setPen(self.palette().color(QPalette.PlaceholderText))
            painter.drawText(self.rect(), Qt.AlignCenter, self.tr("无封面"))
            return

        scaled = self._pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        source_x = max(0, (scaled.width() - self.width()) // 2)
        source_y = max(0, (scaled.height() - self.height()) // 2)
        painter.drawPixmap(0, 0, scaled, source_x, source_y, self.width(), self.height())


def visible_tags(item: MangaItem) -> str:
    plain_tags = [tag for tag in item.tags if ":" not in tag]
    return " · ".join(plain_tags[:4])


class MangaGridCard(CardWidget):
    """大封面漫画卡片。"""

    def __init__(self, item: MangaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.coverLabel = CoverLabel(item.cover_path, self)
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            self.tr("{} · {} 页").format(item.category_name, item.page_count),
            self,
        )

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(5)
        self.layout.addWidget(self.coverLabel)
        self.layout.addWidget(self.titleLabel)
        self.layout.addWidget(self.englishTitleLabel)
        self.layout.addWidget(self.metaLabel)

    def setCardWidth(self, width: int):
        width = max(150, width)
        cover_width = width - 20
        cover_height = round(cover_width * 1.36)
        self.setFixedWidth(width)
        self.coverLabel.setFixedSize(cover_width, cover_height)
        self.setFixedHeight(cover_height + 92)


class MangaListCard(CardWidget):
    """一行一个条目的标题布局卡片。"""

    def __init__(self, item: MangaItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedHeight(116)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.coverLabel = CoverLabel(item.cover_path, self)
        self.coverLabel.setFixedSize(72, 96)
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            self.tr("{} · {} 页").format(item.category_name, item.page_count),
            self,
        )
        self.tagsLabel = FadeTextLabel(visible_tags(item), muted=True, parent=self)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 4, 4, 4)
        text_layout.setSpacing(3)
        text_layout.addWidget(self.titleLabel)
        text_layout.addWidget(self.englishTitleLabel)
        text_layout.addWidget(self.metaLabel)
        text_layout.addWidget(self.tagsLabel)
        text_layout.addStretch(1)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(14)
        self.layout.addWidget(self.coverLabel)
        self.layout.addLayout(text_layout, 1)


class MangaLoadSignals(QObject):
    loaded = Signal(list)
    failed = Signal(str)


class MangaLoadWorker(QRunnable):
    def __init__(self, source: EhViewerDataSource):
        super().__init__()
        self.source = source
        self.signals = MangaLoadSignals()

    def run(self):
        try:
            self.signals.loaded.emit(self.source.list_local_manga())
        except Exception as error:
            self.signals.failed.emit(str(error))


class LocalMangaInterface(QWidget):
    """EhViewer 本地下载漫画的搜索、分类与布局视图。"""

    GRID_MODE = "grid"
    LIST_MODE = "list"

    def __init__(self, source: EhViewerDataSource, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("localMangaInterface")
        self.source = source
        self._all_items: List[MangaItem] = []
        self._filtered_items: List[MangaItem] = []
        self._cards: List[QWidget] = []
        self._empty_label: Optional[BodyLabel] = None
        self._layout_mode = self.GRID_MODE
        self._last_columns = 0
        self._load_worker = None

        self.titleLabel = TitleLabel(self.tr("本地资源"), self)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText(self.tr("搜索英语标题、原标题或标签"))
        self.searchEdit.setMinimumWidth(280)

        self.categoryCombo = ComboBox(self)
        self.categoryCombo.setMinimumWidth(140)
        self.categoryCombo.addItem(self.tr("全部分类"), userData=None)

        self.layoutSwitch = SegmentedToolWidget(self)
        self.layoutSwitch.addItem(
            self.GRID_MODE,
            FIF.TILES,
            lambda: self.setLayoutMode(self.GRID_MODE),
        )
        self.layoutSwitch.addItem(
            self.LIST_MODE,
            FIF.MENU,
            lambda: self.setLayoutMode(self.LIST_MODE),
        )
        self.layoutSwitch.setCurrentItem(self.GRID_MODE)
        self.layoutSwitch.setToolTip(self.tr("切换封面或标题布局"))

        self.resultLabel = BodyLabel(self.tr("正在读取本地漫画…"), self)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(12)
        toolbar_layout.addWidget(self.searchEdit, 1)
        toolbar_layout.addWidget(self.categoryCombo)
        toolbar_layout.addWidget(self.layoutSwitch)

        self.scrollWidget = QWidget()
        self.scrollWidget.setObjectName("localMangaScrollWidget")
        self.contentLayout = QGridLayout(self.scrollWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 24)
        self.contentLayout.setHorizontalSpacing(16)
        self.contentLayout.setVerticalSpacing(16)
        self.contentLayout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#localMangaScrollWidget { background: transparent; }"
        )

        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 32, 36, 24)
        self.mainLayout.setSpacing(16)
        self.mainLayout.addWidget(self.titleLabel)
        self.mainLayout.addLayout(toolbar_layout)
        self.mainLayout.addWidget(self.resultLabel)
        self.mainLayout.addWidget(self.scrollArea, 1)

        self.searchTimer = QTimer(self)
        self.searchTimer.setSingleShot(True)
        self.searchTimer.setInterval(180)
        self.searchTimer.timeout.connect(self.applyFilters)
        self.searchEdit.textChanged.connect(self._scheduleSearch)
        self.categoryCombo.currentIndexChanged.connect(self.applyFilters)

        self.reload()

    def reload(self):
        self.resultLabel.setText(self.tr("正在读取本地漫画…"))
        worker = MangaLoadWorker(self.source)
        worker.signals.loaded.connect(self._onLoaded)
        worker.signals.failed.connect(self._onLoadFailed)
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onLoaded(self, items: List[MangaItem]):
        self._load_worker = None
        self._all_items = items
        self._populateCategories()
        self.applyFilters()

    def _onLoadFailed(self, message: str):
        self._load_worker = None
        self._all_items = []
        self._filtered_items = []
        self.resultLabel.setText(self.tr("读取失败：{}").format(message))
        self._renderCards()

    def _populateCategories(self):
        selected = self.categoryCombo.currentData()
        self.categoryCombo.clear()
        self.categoryCombo.addItem(self.tr("全部分类"), userData=None)
        categories = sorted(
            {(item.category, item.category_name) for item in self._all_items},
            key=lambda value: value[1],
        )
        selected_index = 0
        for category, name in categories:
            self.categoryCombo.addItem(name, userData=category)
            if category == selected:
                selected_index = self.categoryCombo.count() - 1
        self.categoryCombo.setCurrentIndex(selected_index)

    def _scheduleSearch(self):
        self.searchTimer.start()

    def applyFilters(self):
        query = self.searchEdit.text().strip()
        category = self.categoryCombo.currentData()
        self._filtered_items = [
            item
            for item in self._all_items
            if (category is None or item.category == category) and item.matches(query)
        ]
        self.resultLabel.setText(
            self.tr("显示 {} / {} 部漫画").format(
                len(self._filtered_items),
                len(self._all_items),
            )
        )
        self._renderCards()

    def setLayoutMode(self, mode: str):
        if mode not in (self.GRID_MODE, self.LIST_MODE) or mode == self._layout_mode:
            return
        self._layout_mode = mode
        self.layoutSwitch.setCurrentItem(mode)
        self._renderCards()

    def _renderCards(self):
        self._clearContentLayout()
        self._cards = []
        self._empty_label = None

        if not self._filtered_items:
            message = self.tr("没有找到符合条件的本地漫画")
            if not self._all_items and self._load_worker is None:
                message = self.tr("当前数据源中没有可用的本地漫画")
            self._empty_label = BodyLabel(message, self.scrollWidget)
            self.contentLayout.addWidget(self._empty_label, 0, 0)
            return

        card_class = MangaGridCard if self._layout_mode == self.GRID_MODE else MangaListCard
        self._cards = [card_class(item, self.scrollWidget) for item in self._filtered_items]
        self._relayoutCards()

    def _clearContentLayout(self):
        while self.contentLayout.count():
            layout_item = self.contentLayout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for column in range(self._last_columns):
            self.contentLayout.setColumnStretch(column, 0)
        self._last_columns = 0

    def _relayoutCards(self):
        if not self._cards:
            return

        while self.contentLayout.count():
            self.contentLayout.takeAt(0)
        for column in range(self._last_columns):
            self.contentLayout.setColumnStretch(column, 0)

        viewport_width = max(1, self.scrollArea.viewport().width())
        if self._layout_mode == self.LIST_MODE:
            self._last_columns = 1
            self.contentLayout.setColumnStretch(0, 1)
            for row, card in enumerate(self._cards):
                self.contentLayout.addWidget(card, row, 0)
            return

        spacing = self.contentLayout.horizontalSpacing()
        minimum_card_width = 188
        columns = max(1, (viewport_width + spacing) // (minimum_card_width + spacing))
        card_width = max(
            minimum_card_width,
            (viewport_width - spacing * (columns - 1)) // columns,
        )
        self._last_columns = columns
        for column in range(columns):
            self.contentLayout.setColumnStretch(column, 1)

        for index, card in enumerate(self._cards):
            card.setCardWidth(card_width)
            self.contentLayout.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._layout_mode == self.GRID_MODE:
            QTimer.singleShot(0, self._relayoutCards)
