import math
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFontMetrics,
    QImage,
    QImageReader,
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
    QInputDialog,
    QLabel,
    QSizePolicy,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedToolWidget,
    SimpleCardWidget,
    SpinBox,
    ToolButton,
    TreeWidget,
    TitleLabel,
    TransparentPushButton,
    isDarkTheme,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.domain.manga import MangaItem
from app.repositories.user_library_repository import UserLibraryRepository
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

    def __init__(
        self,
        image_path: Path,
        image=None,
        defer_load=False,
        parent=None,
    ):
        super().__init__(parent)
        if image is not None and not image.isNull():
            self._pixmap = QPixmap.fromImage(image)
        elif defer_load:
            self._pixmap = QPixmap()
        else:
            self._pixmap = QPixmap(str(image_path))
        self._loading = defer_load and image is None
        self.setMinimumSize(72, 96)

    def setImage(self, image):
        self._loading = False
        self._pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self.update()

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
            text = self.tr("加载中…") if self._loading else self.tr("无封面")
            painter.drawText(self.rect(), Qt.AlignCenter, text)
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


def manga_metadata_text(item: MangaItem, translate) -> str:
    if item.page_count:
        return translate("{} · {} 页").format(item.category_name, item.page_count)
    return item.category_name


class MangaGridCard(CardWidget):
    """大封面漫画卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.labelMenuCallback = label_menu_callback
        if open_callback is not None:
            self.clicked.connect(lambda: open_callback(self.item))
        self.coverLabel = CoverLabel(
            item.cover_image_path,
            image=cover_image,
            defer_load=True,
            parent=self,
        )
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            manga_metadata_text(item, self.tr),
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

    def contextMenuEvent(self, event):
        if self.labelMenuCallback is not None:
            self.labelMenuCallback(self.item, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class MangaListCard(CardWidget):
    """一行一个条目的标题布局卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.labelMenuCallback = label_menu_callback
        if open_callback is not None:
            self.clicked.connect(lambda: open_callback(self.item))
        self.setFixedHeight(116)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.coverLabel = CoverLabel(
            item.cover_image_path,
            image=cover_image,
            defer_load=True,
            parent=self,
        )
        self.coverLabel.setFixedSize(72, 96)
        self.titleLabel = FadeTextLabel(item.display_title, parent=self)
        self.englishTitleLabel = FadeTextLabel(
            item.secondary_title,
            muted=True,
            parent=self,
        )
        self.metaLabel = CaptionLabel(
            manga_metadata_text(item, self.tr),
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

    def contextMenuEvent(self, event):
        if self.labelMenuCallback is not None:
            self.labelMenuCallback(self.item, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class CoverPreloadSignals(QObject):
    imageReady = Signal(int, object)
    finished = Signal()


class CoverPreloadWorker(QRunnable):
    """后台预读当前页和后续页封面，并为缺失缩略图的漫画寻找第一页。"""

    def __init__(self, source: EhViewerDataSource, items):
        super().__init__()
        self.source = source
        self.items = tuple(items)
        self.cancelled = False
        self.signals = CoverPreloadSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            image = QImage()
            try:
                cover_path = self.source.find_cover_path(item)
                image = self._readImage(cover_path)
                if image.isNull():
                    first_page = self.source.find_first_page_path(item)
                    if first_page != cover_path:
                        image = self._readImage(first_page)
            except (OSError, RuntimeError):
                image = QImage()
            if self.cancelled:
                return
            try:
                self.signals.imageReady.emit(item.gid, image)
            except RuntimeError:
                return
        try:
            self.signals.finished.emit()
        except RuntimeError:
            pass

    @staticmethod
    def _readImage(path):
        if path is None:
            return QImage()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            source_size.scale(QSize(180, 245), Qt.KeepAspectRatio)
            reader.setScaledSize(source_size)
        return reader.read()


class MangaLoadSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class MangaLoadWorker(QRunnable):
    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
    ):
        super().__init__()
        self.source = source
        self.user_repository = user_repository
        self.signals = MangaLoadSignals()
        self.cancelled = False

    def run(self):
        try:
            items = self.source.list_local_manga()
            assignments = self.user_repository.labels_for_manga(
                [item.gid for item in items]
            )
            items = [
                replace(item, multiple_labels=assignments.get(item.gid, ()))
                for item in items
            ]
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(
                        (
                            items,
                            self.source.list_primary_labels(),
                            self.user_repository.list_labels(),
                        )
                    )
                except RuntimeError:
                    pass
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass


class LocalMangaInterface(QWidget):
    """EhViewer 本地下载漫画的搜索、分类与布局视图。"""

    GRID_MODE = "grid"
    LIST_MODE = "list"
    mangaActivated = Signal(object)

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        parent=None,
    ):
        super().__init__(parent=parent)
        self.setObjectName("localMangaInterface")
        self.source = source
        self.userRepository = user_repository
        self._all_items: List[MangaItem] = []
        self._filtered_items: List[MangaItem] = []
        self._cards: List[QWidget] = []
        self._empty_label: Optional[BodyLabel] = None
        self._layout_mode = self.GRID_MODE
        self._primary_label_filter = "__all__"
        self._multiple_label_filters: Set[str] = set()
        self._page = 1
        self._page_size = cfg.get(cfg.mangaPageSize)
        self._last_columns = 0
        self._load_worker = None
        self._cover_worker = None
        self._cover_cache = OrderedDict()

        self.titleLabel = TitleLabel(self.tr("本地资源"), self)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText(self.tr("搜索英语标题、原标题或标签"))
        self.searchEdit.setMinimumWidth(260)
        self.searchPanel = QWidget(self)
        search_layout = QHBoxLayout(self.searchPanel)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.addWidget(self.searchEdit)
        self.searchPanel.hide()

        self.searchButton = TransparentPushButton(
            FIF.SEARCH,
            self.tr("搜索"),
            self,
        )
        self.searchButton.clicked.connect(self.toggleSearch)
        self.tagButton = TransparentPushButton(
            FIF.TAG,
            self.tr("标签"),
            self,
        )
        self.tagButton.clicked.connect(self.toggleClassification)

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

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.titleLabel)
        header_layout.addStretch(1)
        header_layout.addWidget(self.layoutSwitch)
        header_layout.addWidget(self.tagButton)
        header_layout.addWidget(self.searchButton)

        self.classificationCard = SimpleCardWidget(self)
        self.classificationCard.setFixedWidth(210)
        classification_layout = QVBoxLayout(self.classificationCard)
        classification_layout.setContentsMargins(12, 16, 12, 16)
        classification_layout.setSpacing(10)

        self.primaryLabelTree = TreeWidget(self.classificationCard)
        self.primaryLabelTree.setHeaderHidden(True)
        self.primaryLabelTree.setFixedHeight(230)
        classification_layout.addWidget(self.primaryLabelTree)

        classification_layout.addSpacing(8)
        multi_hint = CaptionLabel(
            self.tr("可多选筛选；右键漫画可分配标签"),
            self.classificationCard,
        )
        multi_hint.setWordWrap(True)
        classification_layout.addWidget(multi_hint)

        self.multipleLabelTree = TreeWidget(self.classificationCard)
        self.multipleLabelTree.setHeaderHidden(True)
        self.multipleLabelTree.setFixedHeight(190)
        classification_layout.addWidget(self.multipleLabelTree)

        self.addMultipleLabelButton = PushButton(
            FIF.ADD,
            self.tr("新建复数标签"),
            self.classificationCard,
        )
        classification_layout.addWidget(self.addMultipleLabelButton)
        classification_layout.addStretch(1)
        self._multiple_label_items: Dict[str, QTreeWidgetItem] = {}
        self.classificationCard.hide()

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

        self.pageSizeCombo = ComboBox(self)
        for size in (20, 40, 60, 100):
            self.pageSizeCombo.addItem(str(size), userData=size)
        page_size_index = next(
            (
                index
                for index in range(self.pageSizeCombo.count())
                if self.pageSizeCombo.itemData(index) == self._page_size
            ),
            1,
        )
        self.pageSizeCombo.setCurrentIndex(page_size_index)
        self.pageSizeCombo.setFixedWidth(76)

        self.firstPageButton = ToolButton(FIF.PAGE_LEFT, self)
        self.previousPageButton = ToolButton(FIF.LEFT_ARROW, self)
        self.nextPageButton = ToolButton(FIF.RIGHT_ARROW, self)
        self.lastPageButton = ToolButton(FIF.PAGE_RIGHT, self)
        self.pageSpinBox = SpinBox(self)
        self.pageSpinBox.setRange(1, 1)
        self.pageSpinBox.setFixedWidth(82)
        self.pageCountLabel = BodyLabel(self.tr("/ 1 页"), self)

        pagination_layout = QHBoxLayout()
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.setSpacing(8)
        pagination_layout.addWidget(BodyLabel(self.tr("每页"), self))
        pagination_layout.addWidget(self.pageSizeCombo)
        pagination_layout.addWidget(BodyLabel(self.tr("部"), self))
        pagination_layout.addStretch(1)
        pagination_layout.addWidget(self.firstPageButton)
        pagination_layout.addWidget(self.previousPageButton)
        pagination_layout.addWidget(self.pageSpinBox)
        pagination_layout.addWidget(self.pageCountLabel)
        pagination_layout.addWidget(self.nextPageButton)
        pagination_layout.addWidget(self.lastPageButton)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addLayout(header_layout)
        content_layout.addWidget(self.searchPanel)
        content_layout.addWidget(self.resultLabel)
        content_layout.addWidget(self.scrollArea, 1)
        content_layout.addLayout(pagination_layout)

        self.mainLayout = QHBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 32, 36, 24)
        self.mainLayout.setSpacing(16)
        self.mainLayout.addWidget(self.classificationCard)
        self.mainLayout.addLayout(content_layout, 1)

        self.searchTimer = QTimer(self)
        self.searchTimer.setSingleShot(True)
        self.searchTimer.setInterval(180)
        self.searchTimer.timeout.connect(self.applyFilters)
        self.searchEdit.textChanged.connect(self._scheduleSearch)
        self.primaryLabelTree.currentItemChanged.connect(self._onPrimaryLabelChanged)
        self.multipleLabelTree.itemChanged.connect(self._onMultipleLabelChanged)
        self.addMultipleLabelButton.clicked.connect(lambda: self._createMultipleLabel())
        self.pageSizeCombo.currentIndexChanged.connect(self._onPageSizeChanged)
        self.pageSpinBox.valueChanged.connect(self._onPageChanged)
        self.firstPageButton.clicked.connect(lambda: self.setPage(1))
        self.previousPageButton.clicked.connect(lambda: self.setPage(self._page - 1))
        self.nextPageButton.clicked.connect(lambda: self.setPage(self._page + 1))
        self.lastPageButton.clicked.connect(lambda: self.setPage(self.pageCount()))

        self.reload()

    def setSource(self, source: EhViewerDataSource):
        self.source = source
        self.reload()

    def cancelLoad(self):
        if self._load_worker is not None:
            self._load_worker.cancelled = True
            self._load_worker = None
        self._cancelCoverPreload()

    def _cancelCoverPreload(self):
        if self._cover_worker is not None:
            self._cover_worker.cancelled = True
            self._cover_worker = None

    def reload(self):
        self.cancelLoad()
        self._cover_cache.clear()
        self.resultLabel.setText(self.tr("正在读取本地漫画…"))
        worker = MangaLoadWorker(self.source, self.userRepository)
        worker.signals.loaded.connect(self._onLoaded)
        worker.signals.failed.connect(self._onLoadFailed)
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onLoaded(self, payload):
        self._load_worker = None
        self._all_items, primary_labels, multiple_labels = payload
        self._populatePrimaryLabels(primary_labels)
        self._populateMultipleLabels(multiple_labels)
        self.applyFilters(reset_page=True)

    def _onLoadFailed(self, message: str):
        self._load_worker = None
        self._all_items = []
        self._filtered_items = []
        self.resultLabel.setText(self.tr("读取失败：{}").format(message))
        self._renderCards()

    def _populatePrimaryLabels(self, labels: List[str]):
        self.primaryLabelTree.clear()
        root = QTreeWidgetItem([self.tr("标签")])
        root.setFlags(root.flags() & ~Qt.ItemIsSelectable)
        self.primaryLabelTree.addTopLevelItem(root)
        entries = [
            (self.tr("全部漫画"), "__all__"),
            (self.tr("未分类"), "__none__"),
            *((label, label) for label in labels),
        ]
        selected_item = None
        for text, value in entries:
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.UserRole, value)
            root.addChild(item)
            if value == self._primary_label_filter:
                selected_item = item
        root.setExpanded(True)
        self.primaryLabelTree.setCurrentItem(selected_item or root.child(0))

    def _populateMultipleLabels(self, labels):
        self.multipleLabelTree.blockSignals(True)
        self.multipleLabelTree.clear()
        root = QTreeWidgetItem([self.tr("分类标签（复数标签）")])
        root.setFlags(root.flags() & ~Qt.ItemIsSelectable)
        self.multipleLabelTree.addTopLevelItem(root)
        self._multiple_label_items = {}
        for _label_id, name, count in labels:
            item = QTreeWidgetItem([f"{name} ({count})"])
            item.setData(0, Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.Checked if name in self._multiple_label_filters else Qt.Unchecked,
            )
            root.addChild(item)
            self._multiple_label_items[name] = item
        if not labels:
            empty_item = QTreeWidgetItem([self.tr("尚未创建分类标签")])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsSelectable)
            root.addChild(empty_item)
        root.setExpanded(True)
        self.multipleLabelTree.blockSignals(False)

    def _onPrimaryLabelChanged(self, current, previous=None):
        if current is None:
            return
        value = current.data(0, Qt.UserRole)
        if value is None:
            return
        self._primary_label_filter = value
        self.applyFilters(reset_page=True)

    def _onMultipleLabelChanged(self, changed_item=None, column=0):
        self._multiple_label_filters = {
            name
            for name, item in self._multiple_label_items.items()
            if item.checkState(0) == Qt.Checked
        }
        self.applyFilters(reset_page=True)

    def _createMultipleLabel(self, assign_to_gid=None):
        name, accepted = QInputDialog.getText(
            self,
            self.tr("新建复数标签"),
            self.tr("标签名称"),
        )
        if not accepted or not name.strip():
            return
        label_id = self.userRepository.create_label(name)
        if assign_to_gid is not None:
            self.userRepository.assign_label(assign_to_gid, label_id)
        self._refreshUserLabels()

    def _showMultipleLabelMenu(self, item: MangaItem, global_position):
        menu = RoundMenu(self.tr("复数标签"), self)
        labels = self.userRepository.list_labels()
        if labels:
            for label_id, name, _count in labels:
                action = QAction(name, menu)
                action.setCheckable(True)
                action.setChecked(name in item.multiple_labels)
                action.toggled.connect(
                    lambda checked, current_id=label_id, current_name=name: (
                        self._setMangaMultipleLabel(
                            item.gid,
                            current_id,
                            current_name,
                            checked,
                        )
                    )
                )
                menu.addAction(action)
            menu.addSeparator()

        create_action = QAction(self.tr("新建并分配标签…"), menu)
        create_action.triggered.connect(lambda: self._createMultipleLabel(item.gid))
        menu.addAction(create_action)
        menu.exec(global_position)

    def _setMangaMultipleLabel(
        self,
        gid: int,
        label_id: int,
        label_name: str,
        checked: bool,
    ):
        if checked:
            self.userRepository.assign_label(gid, label_id)
        else:
            self.userRepository.unassign_label(gid, label_id)

        updated_items = []
        for item in self._all_items:
            if item.gid != gid:
                updated_items.append(item)
                continue
            labels = set(item.multiple_labels)
            if checked:
                labels.add(label_name)
            else:
                labels.discard(label_name)
            updated_items.append(
                replace(item, multiple_labels=tuple(sorted(labels, key=str.casefold)))
            )
        self._all_items = updated_items
        self._refreshUserLabels()

    def _refreshUserLabels(self):
        assignments = self.userRepository.labels_for_manga(
            [item.gid for item in self._all_items]
        )
        self._all_items = [
            replace(item, multiple_labels=assignments.get(item.gid, ()))
            for item in self._all_items
        ]
        self._populateMultipleLabels(self.userRepository.list_labels())
        self.applyFilters(reset_page=True)

    def _scheduleSearch(self):
        self.searchTimer.start()

    def toggleSearch(self):
        if self.searchPanel.isVisible():
            self.searchPanel.hide()
            self.searchButton.setIcon(FIF.SEARCH)
        else:
            self.openSearch()

    def toggleClassification(self):
        self.classificationCard.setVisible(self.classificationCard.isHidden())
        self.tagButton.setIcon(
            FIF.CARE_LEFT_SOLID if not self.classificationCard.isHidden() else FIF.TAG
        )

    def openSearch(self):
        self.searchPanel.show()
        self.searchButton.setIcon(FIF.UP)
        self.searchEdit.setFocus(Qt.ShortcutFocusReason)
        self.searchEdit.selectAll()

    def applyFilters(self, reset_page=False):
        query = self.searchEdit.text().strip()
        self._filtered_items = [
            item
            for item in self._all_items
            if self._matchesPrimaryLabel(item)
            and self._matchesMultipleLabels(item)
            and item.matches(query)
        ]
        if reset_page:
            self._page = 1
        self._page = min(max(1, self._page), self.pageCount())
        self.resultLabel.setText(
            self.tr("显示 {} / {} 部漫画").format(
                len(self._filtered_items),
                len(self._all_items),
            )
        )
        self._updatePagination()
        self._renderCards()

    def _matchesPrimaryLabel(self, item: MangaItem) -> bool:
        if self._primary_label_filter == "__all__":
            return True
        if self._primary_label_filter == "__none__":
            return not item.primary_label
        return item.primary_label == self._primary_label_filter

    def _matchesMultipleLabels(self, item: MangaItem) -> bool:
        if not self._multiple_label_filters:
            return True
        return bool(self._multiple_label_filters.intersection(item.multiple_labels))

    def pageCount(self) -> int:
        return max(1, math.ceil(len(self._filtered_items) / self._page_size))

    def setPage(self, page: int):
        page = min(max(1, page), self.pageCount())
        if page == self._page:
            return
        self._page = page
        self._updatePagination()
        self._renderCards()
        self.scrollArea.verticalScrollBar().setValue(0)

    def _onPageChanged(self, page: int):
        self.setPage(page)

    def _onPageSizeChanged(self):
        page_size = self.pageSizeCombo.currentData()
        if not page_size:
            return
        self._page_size = int(page_size)
        cfg.set(cfg.mangaPageSize, self._page_size)
        self._page = 1
        self._updatePagination()
        self._renderCards()

    def _updatePagination(self):
        page_count = self.pageCount()
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setRange(1, page_count)
        self.pageSpinBox.setValue(self._page)
        self.pageSpinBox.blockSignals(False)
        self.pageCountLabel.setText(self.tr("/ {} 页").format(page_count))
        self.firstPageButton.setEnabled(self._page > 1)
        self.previousPageButton.setEnabled(self._page > 1)
        self.nextPageButton.setEnabled(self._page < page_count)
        self.lastPageButton.setEnabled(self._page < page_count)

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
            self._cancelCoverPreload()
            message = self.tr("没有找到符合条件的本地漫画")
            if not self._all_items and self._load_worker is None:
                message = self.tr("当前数据源中没有可用的本地漫画")
            self._empty_label = BodyLabel(message, self.scrollWidget)
            self.contentLayout.addWidget(self._empty_label, 0, 0)
            return

        start = (self._page - 1) * self._page_size
        page_items = self._filtered_items[start:start + self._page_size]
        card_class = MangaGridCard if self._layout_mode == self.GRID_MODE else MangaListCard
        self._cards = [
            card_class(
                item,
                self.mangaActivated.emit,
                self._showMultipleLabelMenu,
                self._cover_cache.get(item.gid),
                self.scrollWidget,
            )
            for item in page_items
        ]
        self._relayoutCards()
        self._preloadCovers()

    def _preloadCovers(self):
        self._cancelCoverPreload()
        start = (self._page - 1) * self._page_size
        end = min(len(self._filtered_items), start + self._page_size * 4)
        items = [
            item
            for item in self._filtered_items[start:end]
            if item.gid not in self._cover_cache
        ]
        if not items:
            return
        worker = CoverPreloadWorker(self.source, items)
        worker.signals.imageReady.connect(
            lambda gid, image: self._onCoverReady(worker, gid, image)
        )
        worker.signals.finished.connect(lambda: self._finishCoverPreload(worker))
        self._cover_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onCoverReady(self, worker, gid: int, image):
        if self._cover_worker is not worker:
            return
        self._cover_cache[gid] = image
        self._cover_cache.move_to_end(gid)
        cache_limit = max(160, self._page_size * 4)
        while len(self._cover_cache) > cache_limit:
            self._cover_cache.popitem(last=False)
        for card in self._cards:
            if card.item.gid == gid:
                card.coverLabel.setImage(image)
                break

    def _finishCoverPreload(self, worker):
        if self._cover_worker is worker:
            self._cover_worker = None

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
