import math
import sqlite3
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
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QDialog,
    QDialogButtonBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PushButton,
    RoundMenu,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
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
    parts = [item.category_name]
    if item.page_count:
        parts.append(translate("{} 页").format(item.page_count))
    if item.progress_page_number is not None:
        if item.page_count:
            parts.append(
                translate("进度 {}/{}").format(
                    min(item.progress_page_number, item.page_count),
                    item.page_count,
                )
            )
        else:
            parts.append(translate("进度第 {} 页").format(item.progress_page_number))
    return " · ".join(parts)


class MangaGridCard(CardWidget):
    """大封面漫画卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        selection_callback=None,
        selection_mode=False,
        selected=False,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.openCallback = open_callback
        self.labelMenuCallback = label_menu_callback
        self.selectionCallback = selection_callback
        self.selectionMode = bool(selection_mode)
        self.clicked.connect(self._handleCardClick)
        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.clicked.connect(self._handleSelectionClick)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
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
        self.selectionCheckBox.raise_()

    def setItem(self, item: MangaItem):
        self.item = item
        self.metaLabel.setText(manga_metadata_text(item, self.tr))

    def setSelectionState(self, selection_mode: bool, selected: bool):
        self.selectionMode = bool(selection_mode)
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.raise_()

    def _handleCardClick(self):
        if self.selectionMode and self.selectionCallback is not None:
            self.selectionCallback(
                self.item.gid, not self.selectionCheckBox.isChecked()
            )
        elif self.openCallback is not None:
            self.openCallback(self.item)

    def _handleSelectionClick(self, checked: bool):
        if self.selectionCallback is not None:
            self.selectionCallback(self.item.gid, checked)

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

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MangaListCard(CardWidget):
    """一行一个条目的标题布局卡片。"""

    def __init__(
        self,
        item: MangaItem,
        open_callback=None,
        label_menu_callback=None,
        selection_callback=None,
        selection_mode=False,
        selected=False,
        cover_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.openCallback = open_callback
        self.labelMenuCallback = label_menu_callback
        self.selectionCallback = selection_callback
        self.selectionMode = bool(selection_mode)
        self.clicked.connect(self._handleCardClick)
        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.clicked.connect(self._handleSelectionClick)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
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
        self.selectionCheckBox.raise_()

    def setItem(self, item: MangaItem):
        self.item = item
        self.metaLabel.setText(manga_metadata_text(item, self.tr))

    def setSelectionState(self, selection_mode: bool, selected: bool):
        self.selectionMode = bool(selection_mode)
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)
        self.selectionCheckBox.setVisible(self.selectionMode)
        self.selectionCheckBox.raise_()

    def _handleCardClick(self):
        if self.selectionMode and self.selectionCallback is not None:
            self.selectionCallback(
                self.item.gid, not self.selectionCheckBox.isChecked()
            )
        elif self.openCallback is not None:
            self.openCallback(self.item)

    def _handleSelectionClick(self, checked: bool):
        if self.selectionCallback is not None:
            self.selectionCallback(self.item.gid, checked)

    def contextMenuEvent(self, event):
        if self.labelMenuCallback is not None:
            self.labelMenuCallback(self.item, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CoverPreloadSignals(QObject):
    imageReady = Signal(int, object)
    progressReady = Signal(int, int)
    finished = Signal()


class CoverPreloadWorker(QRunnable):
    """后台预读当前页和后续页封面，并为缺失缩略图的漫画寻找第一页。"""

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        items,
    ):
        super().__init__()
        self.source = source
        self.user_repository = user_repository
        self.items = tuple(items)
        self.cancelled = False
        self.signals = CoverPreloadSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            if item.progress_page_index is None:
                try:
                    progress = self.user_repository.resolve_progress(
                        item.gid,
                        self.source.read_ehviewer_progress(item),
                    )
                    if progress is not None:
                        self.signals.progressReady.emit(item.gid, progress)
                except (OSError, RuntimeError, sqlite3.Error):
                    pass
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
            taxonomy_assignments = self.user_repository.taxonomy_for_mangas(
                [item.gid for item in items]
            )
            progress = self.user_repository.progress_for_mangas(
                [item.gid for item in items]
            )
            items = [
                replace(
                    item,
                    multiple_labels=assignments.get(item.gid, ()),
                    progress_page_index=progress.get(item.gid),
                    taxonomy_label_ids=tuple(
                        label_id
                        for label_id, _name in taxonomy_assignments.get(
                            item.gid, ()
                        )
                    ),
                    taxonomy_labels=tuple(
                        name
                        for _label_id, name in taxonomy_assignments.get(
                            item.gid, ()
                        )
                    ),
                )
                for item in items
            ]
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(
                        (
                            items,
                            self.source.list_primary_labels(),
                            self.user_repository.list_playlists(),
                            self.user_repository.list_taxonomy_labels(),
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


class LabelMutationSignals(QObject):
    succeeded = Signal()
    failed = Signal(str)


class LabelMutationWorker(QRunnable):
    """Run an explicit label database mutation outside the GUI thread."""

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = LabelMutationSignals()

    def run(self):
        try:
            self.operation()
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                pass
            return
        try:
            self.signals.succeeded.emit()
        except RuntimeError:
            pass


class PlaylistOrderDialog(QDialog):
    """Drag-and-drop playlist ordering with explicit keyboard-style controls."""

    def __init__(self, playlist_name: str, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("编排播放顺序：{}").format(playlist_name))
        self.resize(520, 560)
        self.listWidget = QListWidget(self)
        self.listWidget.setDragEnabled(True)
        self.listWidget.setAcceptDrops(True)
        self.listWidget.setDropIndicatorShown(True)
        self.listWidget.setDragDropMode(QAbstractItemView.InternalMove)
        self.listWidget.setDefaultDropAction(Qt.MoveAction)
        self.listWidget.setDragDropOverwriteMode(False)
        for item in items:
            self.listWidget.addItem(
                self.tr("{}  ·  GID {}").format(item.display_title, item.gid)
            )
            self.listWidget.item(self.listWidget.count() - 1).setData(
                Qt.UserRole, item.gid
            )
        self.upButton = PushButton(FIF.UP, self.tr("上移"), self)
        self.downButton = PushButton(FIF.DOWN, self.tr("下移"), self)
        self.topButton = PushButton(FIF.CARE_UP_SOLID, self.tr("移到顶部"), self)
        self.bottomButton = PushButton(
            FIF.CARE_DOWN_SOLID, self.tr("移到底部"), self
        )
        self.upButton.clicked.connect(lambda: self._moveCurrent(-1))
        self.downButton.clicked.connect(lambda: self._moveCurrent(1))
        self.topButton.clicked.connect(lambda: self._moveCurrentTo(0))
        self.bottomButton.clicked.connect(
            lambda: self._moveCurrentTo(self.listWidget.count() - 1)
        )
        move_layout = QHBoxLayout()
        move_layout.addWidget(self.upButton)
        move_layout.addWidget(self.downButton)
        move_layout.addWidget(self.topButton)
        move_layout.addWidget(self.bottomButton)
        move_layout.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            BodyLabel(
                self.tr("拖动条目或使用下方按钮调整，完成后点击“保存”。"),
                self,
            )
        )
        layout.addWidget(self.listWidget, 1)
        layout.addLayout(move_layout)
        layout.addWidget(buttons)
        if self.listWidget.count():
            self.listWidget.setCurrentRow(0)
        self.listWidget.currentRowChanged.connect(self._updateMoveButtons)
        self._updateMoveButtons()

    def _moveCurrent(self, offset: int):
        row = self.listWidget.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.listWidget.count():
            return
        item = self.listWidget.takeItem(row)
        self.listWidget.insertItem(target, item)
        self.listWidget.setCurrentRow(target)

    def _moveCurrentTo(self, target: int):
        row = self.listWidget.currentRow()
        if row < 0 or row == target or not 0 <= target < self.listWidget.count():
            return
        item = self.listWidget.takeItem(row)
        self.listWidget.insertItem(target, item)
        self.listWidget.setCurrentRow(target)

    def _updateMoveButtons(self, _row=None):
        row = self.listWidget.currentRow()
        last = self.listWidget.count() - 1
        self.upButton.setEnabled(row > 0)
        self.topButton.setEnabled(row > 0)
        self.downButton.setEnabled(0 <= row < last)
        self.bottomButton.setEnabled(0 <= row < last)

    def orderedGids(self):
        return tuple(
            int(self.listWidget.item(index).data(Qt.UserRole))
            for index in range(self.listWidget.count())
        )


class LocalMangaInterface(QWidget):
    """EhViewer 本地下载漫画的搜索、分类与布局视图。"""

    GRID_MODE = "grid"
    LIST_MODE = "list"
    TAG_CATEGORY = "category"
    TAG_PLAYLIST = "playlist"
    TAG_TAXONOMY = "taxonomy"
    mangaActivated = Signal(object)
    playlistMangaActivated = Signal(object, int, object, int)
    playlistPlayRequested = Signal(int, object, int, bool)

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
        self._tag_mode = self.TAG_CATEGORY
        self._show_all_manga = False
        self._primary_label_filter = str(
            cfg.get(cfg.mangaPrimaryLabelFilter) or "__none__"
        )
        self._playlist_filter_id: Optional[int] = None
        self._playlist_filter_name = ""
        self._playlist_order = ()
        self._taxonomy_filter_id: Optional[int] = None
        self._primary_labels: List[str] = []
        self._playlists = []
        self._taxonomy_labels = []
        self._selection_mode = False
        self._selected_gids: Set[int] = set()
        self._label_workers = set()
        self._sort_order = cfg.get(cfg.mangaSortOrder)
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

        self.sortCombo = ComboBox(self)
        self.sortCombo.addItem(self.tr("添加时间：最新优先"), userData="desc")
        self.sortCombo.addItem(self.tr("添加时间：最早优先"), userData="asc")
        self.sortCombo.setCurrentIndex(0 if self._sort_order == "desc" else 1)
        self.sortCombo.setFixedWidth(176)

        self.multiSelectCheckBox = CheckBox(self)
        self.multiSelectCheckBox.setText(self.tr("复选"))
        self.selectionCountLabel = CaptionLabel(self.tr("已选 0 项"), self)
        self.selectionCountLabel.hide()
        self.playlistContinueButton = PushButton(
            FIF.HISTORY, self.tr("继续上一次"), self
        )
        self.playlistPlayButton = PushButton(FIF.PLAY, self.tr("播放"), self)
        self.playlistOrderButton = PushButton(
            FIF.MENU, self.tr("编排顺序"), self
        )
        for button in (
            self.playlistContinueButton,
            self.playlistPlayButton,
            self.playlistOrderButton,
        ):
            button.hide()

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
        header_layout.addWidget(self.selectionCountLabel)
        header_layout.addWidget(self.multiSelectCheckBox)
        header_layout.addWidget(self.playlistContinueButton)
        header_layout.addWidget(self.playlistPlayButton)
        header_layout.addWidget(self.playlistOrderButton)
        header_layout.addWidget(self.sortCombo)
        header_layout.addWidget(self.layoutSwitch)
        header_layout.addWidget(self.tagButton)
        header_layout.addWidget(self.searchButton)

        self.classificationCard = SimpleCardWidget(self)
        self.classificationCard.setMinimumWidth(190)
        classification_layout = QVBoxLayout(self.classificationCard)
        classification_layout.setContentsMargins(12, 16, 12, 16)
        classification_layout.setSpacing(10)

        self.tagModeSwitch = SegmentedWidget(self.classificationCard)
        self.tagModeSwitch.addItem(
            self.TAG_CATEGORY,
            self.tr("分类"),
            lambda: self._setTagMode(self.TAG_CATEGORY),
        )
        self.tagModeSwitch.addItem(
            self.TAG_PLAYLIST,
            self.tr("播放列表"),
            lambda: self._setTagMode(self.TAG_PLAYLIST),
        )
        self.tagModeSwitch.addItem(
            self.TAG_TAXONOMY,
            self.tr("归类"),
            lambda: self._setTagMode(self.TAG_TAXONOMY),
        )
        self.tagModeSwitch.setCurrentItem(self.TAG_CATEGORY)
        classification_layout.addWidget(self.tagModeSwitch)
        self.showAllMangaButton = PushButton(
            FIF.APPLICATION, self.tr("显示全部漫画"), self.classificationCard
        )
        classification_layout.addWidget(self.showAllMangaButton)

        self.tagStack = QStackedWidget(self.classificationCard)
        classification_layout.addWidget(self.tagStack, 1)

        self.categoryPanel = QWidget(self.tagStack)
        category_layout = QVBoxLayout(self.categoryPanel)
        category_layout.setContentsMargins(0, 0, 0, 0)
        category_header = QHBoxLayout()
        category_header.addWidget(BodyLabel(self.tr("分类"), self.categoryPanel))
        category_header.addStretch(1)
        self.addCategoryButton = ToolButton(FIF.ADD, self.categoryPanel)
        self.addCategoryButton.setToolTip(self.tr("新增分类"))
        category_header.addWidget(self.addCategoryButton)
        category_layout.addLayout(category_header)
        self.primaryLabelTree = TreeWidget(self.categoryPanel)
        self.primaryLabelTree.setHeaderHidden(True)
        self.primaryLabelTree.setContextMenuPolicy(Qt.CustomContextMenu)
        category_layout.addWidget(self.primaryLabelTree, 1)
        self.tagStack.addWidget(self.categoryPanel)

        self.playlistPanel = QWidget(self.tagStack)
        playlist_layout = QVBoxLayout(self.playlistPanel)
        playlist_layout.setContentsMargins(0, 0, 0, 0)
        playlist_header = QHBoxLayout()
        playlist_header.addWidget(BodyLabel(self.tr("播放列表"), self.playlistPanel))
        playlist_header.addStretch(1)
        self.addPlaylistButton = ToolButton(FIF.ADD, self.playlistPanel)
        self.addPlaylistButton.setToolTip(self.tr("新增播放列表"))
        playlist_header.addWidget(self.addPlaylistButton)
        playlist_layout.addLayout(playlist_header)
        self.playlistTree = TreeWidget(self.playlistPanel)
        self.playlistTree.setHeaderHidden(True)
        self.playlistTree.setContextMenuPolicy(Qt.CustomContextMenu)
        playlist_layout.addWidget(self.playlistTree, 1)
        self.tagStack.addWidget(self.playlistPanel)

        self.taxonomyPanel = QWidget(self.tagStack)
        taxonomy_layout = QVBoxLayout(self.taxonomyPanel)
        taxonomy_layout.setContentsMargins(0, 0, 0, 0)
        taxonomy_header = QHBoxLayout()
        taxonomy_header.addWidget(BodyLabel(self.tr("归类"), self.taxonomyPanel))
        taxonomy_header.addStretch(1)
        self.addTaxonomyButton = ToolButton(FIF.ADD, self.taxonomyPanel)
        self.addTaxonomyButton.setToolTip(self.tr("新增归类节点"))
        taxonomy_header.addWidget(self.addTaxonomyButton)
        taxonomy_layout.addLayout(taxonomy_header)
        self.taxonomyTree = TreeWidget(self.taxonomyPanel)
        self.taxonomyTree.setHeaderHidden(True)
        self.taxonomyTree.setContextMenuPolicy(Qt.CustomContextMenu)
        taxonomy_layout.addWidget(self.taxonomyTree, 1)
        self.tagStack.addWidget(self.taxonomyPanel)

        self._playlist_items: Dict[int, QTreeWidgetItem] = {}
        self._taxonomy_items: Dict[int, QTreeWidgetItem] = {}

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

        self.contentPanel = QWidget(self)
        content_layout = QVBoxLayout(self.contentPanel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addLayout(header_layout)
        content_layout.addWidget(self.searchPanel)
        content_layout.addWidget(self.resultLabel)
        content_layout.addWidget(self.scrollArea, 1)
        content_layout.addLayout(pagination_layout)

        self.tagSplitter = QSplitter(Qt.Horizontal, self)
        self.tagSplitter.setChildrenCollapsible(False)
        self.tagSplitter.setHandleWidth(6)
        self.tagSplitter.addWidget(self.classificationCard)
        self.tagSplitter.addWidget(self.contentPanel)
        self.tagSplitter.setStretchFactor(0, 0)
        self.tagSplitter.setStretchFactor(1, 1)
        self.tagSplitter.setSizes([230, 730])

        self.mainLayout = QHBoxLayout(self)
        self.mainLayout.setContentsMargins(36, 32, 36, 24)
        self.mainLayout.setSpacing(0)
        self.mainLayout.addWidget(self.tagSplitter)
        self.classificationCard.hide()

        self.searchTimer = QTimer(self)
        self.searchTimer.setSingleShot(True)
        self.searchTimer.setInterval(180)
        self.searchTimer.timeout.connect(self.applyFilters)
        self.searchEdit.textChanged.connect(self._scheduleSearch)
        self.primaryLabelTree.currentItemChanged.connect(self._onPrimaryLabelChanged)
        self.playlistTree.currentItemChanged.connect(self._onPlaylistChanged)
        self.taxonomyTree.currentItemChanged.connect(self._onTaxonomyChanged)
        self.primaryLabelTree.customContextMenuRequested.connect(
            lambda position: self._showTagTreeMenu(
                self.TAG_CATEGORY, self.primaryLabelTree, position
            )
        )
        self.playlistTree.customContextMenuRequested.connect(
            lambda position: self._showTagTreeMenu(
                self.TAG_PLAYLIST, self.playlistTree, position
            )
        )
        self.taxonomyTree.customContextMenuRequested.connect(
            lambda position: self._showTagTreeMenu(
                self.TAG_TAXONOMY, self.taxonomyTree, position
            )
        )
        self.showAllMangaButton.clicked.connect(self._showAllManga)
        self.addCategoryButton.clicked.connect(self._createPrimaryLabel)
        self.addPlaylistButton.clicked.connect(self._createPlaylist)
        self.addTaxonomyButton.clicked.connect(self._createTaxonomyLabel)
        self.playlistContinueButton.clicked.connect(
            lambda: self._requestPlaylistPlayback(True)
        )
        self.playlistPlayButton.clicked.connect(
            lambda: self._requestPlaylistPlayback(False)
        )
        self.playlistOrderButton.clicked.connect(self._editPlaylistOrder)
        self.multiSelectCheckBox.toggled.connect(self._onSelectionModeChanged)
        self.sortCombo.currentIndexChanged.connect(self._onSortOrderChanged)
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
        self._selected_gids.clear()
        self._updateSelectionState()
        self._cover_cache.clear()
        self.resultLabel.setText(self.tr("正在读取本地漫画…"))
        worker = MangaLoadWorker(self.source, self.userRepository)
        worker.signals.loaded.connect(self._onLoaded)
        worker.signals.failed.connect(self._onLoadFailed)
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onLoaded(self, payload):
        self._load_worker = None
        if len(payload) == 3:
            self._all_items, primary_labels, playlists = payload
            taxonomy_labels = []
        else:
            self._all_items, primary_labels, playlists, taxonomy_labels = payload
        self._primary_labels = list(
            dict.fromkeys(
                [
                    *primary_labels,
                    *(item.primary_label for item in self._all_items if item.primary_label),
                ]
            )
        )
        self._playlists = list(playlists)
        self._taxonomy_labels = list(taxonomy_labels)
        self._populatePrimaryLabels(self._primary_labels)
        self._populatePlaylists(self._playlists)
        self._populateTaxonomy(self._taxonomy_labels)
        self._setTagMode(self.TAG_CATEGORY, reset_page=False)
        self.applyFilters(reset_page=True)

    def _onLoadFailed(self, message: str):
        self._load_worker = None
        self._all_items = []
        self._filtered_items = []
        self.resultLabel.setText(self.tr("读取失败：{}").format(message))
        self._renderCards()

    def _populatePrimaryLabels(self, labels: List[str]):
        self.primaryLabelTree.blockSignals(True)
        self.primaryLabelTree.clear()
        entries = [
            (self.tr("未分类"), "__none__"),
            *((label, label) for label in labels),
        ]
        valid_values = {value for _text, value in entries}
        if self._primary_label_filter not in valid_values:
            self._primary_label_filter = "__none__"
        selected_item = None
        for text, value in entries:
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.UserRole, value)
            self.primaryLabelTree.addTopLevelItem(item)
            if value == self._primary_label_filter:
                selected_item = item
        self.primaryLabelTree.setCurrentItem(
            selected_item or self.primaryLabelTree.topLevelItem(0)
        )
        self.primaryLabelTree.blockSignals(False)
        cfg.set(cfg.mangaPrimaryLabelFilter, self._primary_label_filter)

    def _populatePlaylists(self, playlists):
        selected_id = self._playlist_filter_id
        self.playlistTree.blockSignals(True)
        self.playlistTree.clear()
        self._playlist_items = {}
        selected_item = None
        for label_id, name, count, _last_gid in playlists:
            item = QTreeWidgetItem([f"{name} ({count})"])
            item.setData(0, Qt.UserRole, int(label_id))
            item.setData(0, Qt.UserRole + 1, name)
            self.playlistTree.addTopLevelItem(item)
            self._playlist_items[int(label_id)] = item
            if int(label_id) == selected_id:
                selected_item = item
        if not playlists:
            empty_item = QTreeWidgetItem([self.tr("尚未创建播放列表")])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsSelectable)
            self.playlistTree.addTopLevelItem(empty_item)
        elif selected_item is None:
            selected_item = self.playlistTree.topLevelItem(0)
        self.playlistTree.setCurrentItem(selected_item)
        self.playlistTree.blockSignals(False)
        if selected_item is not None:
            self._playlist_filter_id = int(selected_item.data(0, Qt.UserRole))
            self._playlist_filter_name = str(
                selected_item.data(0, Qt.UserRole + 1)
            )
            self._playlist_order = self.userRepository.playlist_items(
                self._playlist_filter_id
            )
        else:
            self._playlist_filter_id = None
            self._playlist_filter_name = ""
            self._playlist_order = ()
        self._updatePlaylistActions()

    def _populateTaxonomy(self, labels):
        selected_id = self._taxonomy_filter_id
        self.taxonomyTree.blockSignals(True)
        self.taxonomyTree.clear()
        self._taxonomy_items = {}
        selected_item = None
        for label_id, _parent_id, name, count in labels:
            item = QTreeWidgetItem([f"{name} ({count})"])
            item.setData(0, Qt.UserRole, int(label_id))
            item.setData(0, Qt.UserRole + 1, name)
            self._taxonomy_items[int(label_id)] = item
        for label_id, parent_id, _name, _count in labels:
            item = self._taxonomy_items[int(label_id)]
            parent_item = self._taxonomy_items.get(parent_id)
            if parent_item is None:
                self.taxonomyTree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if int(label_id) == selected_id:
                selected_item = item
        if not labels:
            empty_item = QTreeWidgetItem([self.tr("尚未创建归类")])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemIsSelectable)
            self.taxonomyTree.addTopLevelItem(empty_item)
        elif selected_item is None:
            selected_item = self.taxonomyTree.topLevelItem(0)
        self.taxonomyTree.expandAll()
        self.taxonomyTree.setCurrentItem(selected_item)
        self.taxonomyTree.blockSignals(False)
        if selected_item is not None:
            self._taxonomy_filter_id = int(selected_item.data(0, Qt.UserRole))

    def _onPrimaryLabelChanged(self, current, previous=None):
        if current is None:
            return
        value = current.data(0, Qt.UserRole)
        if value is None:
            return
        self._primary_label_filter = value
        self._show_all_manga = False
        cfg.set(cfg.mangaPrimaryLabelFilter, value)
        self.applyFilters(reset_page=True)

    def _onPlaylistChanged(self, current, previous=None):
        if current is None or current.data(0, Qt.UserRole) is None:
            return
        self._playlist_filter_id = int(current.data(0, Qt.UserRole))
        self._playlist_filter_name = str(current.data(0, Qt.UserRole + 1))
        self._playlist_order = self.userRepository.playlist_items(
            self._playlist_filter_id
        )
        self._show_all_manga = False
        self._updatePlaylistActions()
        if self._tag_mode == self.TAG_PLAYLIST:
            self.applyFilters(reset_page=True)

    def _onTaxonomyChanged(self, current, previous=None):
        if current is None or current.data(0, Qt.UserRole) is None:
            return
        self._taxonomy_filter_id = int(current.data(0, Qt.UserRole))
        self._show_all_manga = False
        if self._tag_mode == self.TAG_TAXONOMY:
            self.applyFilters(reset_page=True)

    def _setTagMode(self, mode: str, reset_page=True):
        if mode not in (self.TAG_CATEGORY, self.TAG_PLAYLIST, self.TAG_TAXONOMY):
            return
        self._tag_mode = mode
        self._show_all_manga = False
        self.tagModeSwitch.setCurrentItem(mode)
        self.tagStack.setCurrentWidget({
            self.TAG_CATEGORY: self.categoryPanel,
            self.TAG_PLAYLIST: self.playlistPanel,
            self.TAG_TAXONOMY: self.taxonomyPanel,
        }[mode])
        is_playlist = mode == self.TAG_PLAYLIST
        self.sortCombo.setEnabled(not is_playlist)
        for button in (
            self.playlistContinueButton,
            self.playlistPlayButton,
            self.playlistOrderButton,
        ):
            button.setVisible(is_playlist)
        self._updatePlaylistActions()
        if self._all_items:
            self.applyFilters(reset_page=reset_page)

    def _showAllManga(self):
        self._show_all_manga = True
        self.sortCombo.setEnabled(True)
        self.applyFilters(reset_page=True)

    def _createPrimaryLabel(self):
        name, accepted = QInputDialog.getText(
            self, self.tr("新增分类"), self.tr("分类名称")
        )
        normalized = name.strip()
        if not accepted or not normalized:
            return
        self._startLabelMutation(
            lambda: self.source.create_primary_label(normalized),
            lambda: self._finishCreatePrimaryLabel(normalized),
        )

    def _finishCreatePrimaryLabel(self, name: str):
        if name not in self._primary_labels:
            self._primary_labels.append(name)
        self._populatePrimaryLabels(self._primary_labels)

    def _createPlaylist(self, assign_to_gids=None):
        name, accepted = QInputDialog.getText(
            self, self.tr("新增播放列表"), self.tr("播放列表名称")
        )
        normalized = name.strip()
        if not accepted or not normalized:
            return
        result = {}

        def operation():
            result["id"] = self.userRepository.create_playlist(normalized)
            if assign_to_gids:
                self.userRepository.assign_label_to_mangas(
                    assign_to_gids, result["id"]
                )

        self._startLabelMutation(operation, self._refreshTagData)

    def _createTaxonomyLabel(self, assign_to_gids=None):
        name, accepted = QInputDialog.getText(
            self, self.tr("新增归类"), self.tr("归类名称")
        )
        normalized = name.strip()
        if not accepted or not normalized:
            return
        parent_entries = [(self.tr("根节点"), None)] + self._taxonomyPathEntries()
        parent_texts = [text for text, _label_id in parent_entries]
        parent_text, parent_accepted = QInputDialog.getItem(
            self,
            self.tr("选择父级"),
            self.tr("新节点放在"),
            parent_texts,
            0,
            False,
        )
        if not parent_accepted:
            return
        parent_id = dict(parent_entries).get(parent_text)
        result = {}

        def operation():
            result["id"] = self.userRepository.create_taxonomy_label(
                normalized, parent_id
            )
            if assign_to_gids:
                self.userRepository.assign_taxonomy_to_mangas(
                    assign_to_gids, result["id"]
                )

        self._startLabelMutation(operation, self._refreshTagData)

    def _taxonomyPathEntries(self):
        by_id = {
            int(label_id): (parent_id, name)
            for label_id, parent_id, name, _count in self._taxonomy_labels
        }

        def path_for(label_id):
            values = []
            seen = set()
            while label_id in by_id and label_id not in seen:
                seen.add(label_id)
                parent_id, name = by_id[label_id]
                values.append(name)
                label_id = parent_id
            return " / ".join(reversed(values))

        return sorted(
            ((path_for(label_id), label_id) for label_id in by_id),
            key=lambda pair: pair[0].casefold(),
        )

    def _showTagTreeMenu(self, tag_mode, tree, position):
        item = tree.itemAt(position)
        if item is None:
            return
        menu = self._buildTagTreeMenu(tag_mode, item)
        if menu is not None:
            menu.exec(tree.viewport().mapToGlobal(position))

    def _buildTagTreeMenu(self, tag_mode, item):
        value = item.data(0, Qt.UserRole)
        if value is None or (tag_mode == self.TAG_CATEGORY and value == "__none__"):
            return None
        name = (
            str(value)
            if tag_mode == self.TAG_CATEGORY
            else str(item.data(0, Qt.UserRole + 1) or item.text(0))
        )
        menu = RoundMenu(name, self)
        delete_action = QAction(self.tr("删除"), menu)
        delete_action.triggered.connect(
            lambda: self._deleteTag(tag_mode, value, name)
        )
        menu.addAction(delete_action)
        return menu

    def _deleteTag(self, tag_mode, value, name):
        descriptions = {
            self.TAG_CATEGORY: self.tr(
                "使用该分类的漫画会移到“未分类”，分类本身将从目标数据库删除。"
            ),
            self.TAG_PLAYLIST: self.tr(
                "播放列表及其中的成员关系和编排顺序将被删除。"
            ),
            self.TAG_TAXONOMY: self.tr(
                "该归类、全部子归类及其漫画关联将被删除。"
            ),
        }
        if not self._confirmDeleteTag(name, descriptions[tag_mode]):
            return
        if tag_mode == self.TAG_CATEGORY:
            self._startLabelMutation(
                lambda: self.source.delete_primary_label(name),
                lambda: self._finishDeletePrimaryLabel(name),
            )
        elif tag_mode == self.TAG_PLAYLIST:
            self._startLabelMutation(
                lambda: self.userRepository.delete_playlist(int(value)),
                self._refreshTagData,
            )
        else:
            self._startLabelMutation(
                lambda: self.userRepository.delete_taxonomy_label(int(value)),
                self._refreshTagData,
            )

    def _confirmDeleteTag(self, name: str, description: str) -> bool:
        dialog = MessageBox(
            self.tr("删除“{}”？").format(name),
            description,
            self.window(),
        )
        dialog.yesButton.setText(self.tr("删除"))
        dialog.cancelButton.setText(self.tr("取消"))
        return bool(dialog.exec())

    def _finishDeletePrimaryLabel(self, name: str):
        target = name.casefold()
        self._primary_labels = [
            label for label in self._primary_labels
            if label.casefold() != target
        ]
        self._all_items = [
            replace(item, primary_label="")
            if item.primary_label.casefold() == target else item
            for item in self._all_items
        ]
        if self._primary_label_filter.casefold() == target:
            self._primary_label_filter = "__none__"
            cfg.set(cfg.mangaPrimaryLabelFilter, "__none__")
        self._populatePrimaryLabels(self._primary_labels)
        self.applyFilters(reset_page=True)

    def _refreshTagData(self):
        self._playlists = self.userRepository.list_playlists()
        self._taxonomy_labels = self.userRepository.list_taxonomy_labels()
        assignments = self.userRepository.labels_for_manga(
            [item.gid for item in self._all_items]
        )
        taxonomy = self.userRepository.taxonomy_for_mangas(
            [item.gid for item in self._all_items]
        )
        self._all_items = [
            replace(
                item,
                multiple_labels=assignments.get(item.gid, ()),
                taxonomy_label_ids=tuple(
                    label_id for label_id, _name in taxonomy.get(item.gid, ())
                ),
                taxonomy_labels=tuple(
                    name for _label_id, name in taxonomy.get(item.gid, ())
                ),
            )
            for item in self._all_items
        ]
        self._populatePlaylists(self._playlists)
        self._populateTaxonomy(self._taxonomy_labels)
        if self._playlist_filter_id is not None:
            self._playlist_order = self.userRepository.playlist_items(
                self._playlist_filter_id
            )
        self.applyFilters(reset_page=False)


    def _showLabelMenu(self, item: MangaItem, global_position):
        if self._selection_mode:
            if item.gid not in self._selected_gids:
                self._selected_gids = {item.gid}
                self._updateSelectionState()
            target_gids = tuple(sorted(self._selected_gids))
        else:
            target_gids = (item.gid,)
        menu = self._buildLabelMenu(item, target_gids)
        menu.exec(global_position)

    def _buildLabelMenu(self, item: MangaItem, target_gids=None):
        target_gids = tuple(dict.fromkeys(target_gids or (item.gid,)))
        target_gid_set = set(target_gids)
        target_items = [
            current for current in self._all_items
            if current.gid in target_gid_set
        ]
        title = self.tr("漫画标签")
        if len(target_gids) > 1:
            title = self.tr("批量操作（{} 项）").format(len(target_gids))
        menu = RoundMenu(title, self)
        primary_menu = RoundMenu(self.tr("添加到分类"), menu)
        if self._primary_labels:
            for name in self._primary_labels:
                action = QAction(name, primary_menu)
                action.setCheckable(True)
                action.setChecked(
                    bool(target_items)
                    and all(current.primary_label == name for current in target_items)
                )
                action.triggered.connect(
                    lambda _checked=False, current_name=name: (
                        self._setMangaPrimaryLabel(target_gids, current_name)
                    )
                )
                primary_menu.addAction(action)
        else:
            empty_action = QAction(self.tr("暂无分类"), primary_menu)
            empty_action.setEnabled(False)
            primary_menu.addAction(empty_action)
        menu.addMenu(primary_menu)

        playlist_menu = RoundMenu(self.tr("添加到播放列表"), menu)
        if self._playlists:
            for label_id, name, _count, _last_gid in self._playlists:
                action = QAction(name, playlist_menu)
                action.setCheckable(True)
                action.setChecked(
                    bool(target_items)
                    and all(name in current.multiple_labels for current in target_items)
                )
                action.toggled.connect(
                    lambda checked, current_id=label_id, current_name=name: (
                        self._setMangaMultipleLabel(
                            target_gids,
                            current_id,
                            current_name,
                            checked,
                        )
                    )
                )
                playlist_menu.addAction(action)
            playlist_menu.addSeparator()

        create_action = QAction(self.tr("新建并添加…"), playlist_menu)
        create_action.triggered.connect(
            lambda: self._createPlaylist(target_gids)
        )
        playlist_menu.addAction(create_action)
        menu.addMenu(playlist_menu)

        taxonomy_menu = RoundMenu(self.tr("添加到归类"), menu)
        self._buildTaxonomySubmenus(
            taxonomy_menu, None, target_gids, target_items
        )
        if not self._taxonomy_labels:
            empty_action = QAction(self.tr("暂无归类"), taxonomy_menu)
            empty_action.setEnabled(False)
            taxonomy_menu.addAction(empty_action)
        create_taxonomy_action = QAction(self.tr("新建并添加…"), taxonomy_menu)
        create_taxonomy_action.triggered.connect(
            lambda: self._createTaxonomyLabel(target_gids)
        )
        taxonomy_menu.addSeparator()
        taxonomy_menu.addAction(create_taxonomy_action)
        menu.addMenu(taxonomy_menu)
        return menu

    def _buildTaxonomySubmenus(
        self, parent_menu, parent_id, target_gids, target_items
    ):
        children = [
            entry for entry in self._taxonomy_labels if entry[1] == parent_id
        ]
        for label_id, _parent_id, name, _count in children:
            grandchildren = [
                entry for entry in self._taxonomy_labels
                if entry[1] == label_id
            ]
            checked = bool(target_items) and all(
                int(label_id) in item.taxonomy_label_ids for item in target_items
            )
            if grandchildren:
                child_menu = RoundMenu(name, parent_menu)
                assign_action = QAction(self.tr("添加到此归类"), child_menu)
                assign_action.setCheckable(True)
                assign_action.setChecked(checked)
                assign_action.toggled.connect(
                    lambda value, current_id=label_id: self._setMangaTaxonomyLabel(
                        target_gids, current_id, value
                    )
                )
                child_menu.addAction(assign_action)
                child_menu.addSeparator()
                self._buildTaxonomySubmenus(
                    child_menu, label_id, target_gids, target_items
                )
                parent_menu.addMenu(child_menu)
            else:
                action = QAction(name, parent_menu)
                action.setCheckable(True)
                action.setChecked(checked)
                action.toggled.connect(
                    lambda value, current_id=label_id: self._setMangaTaxonomyLabel(
                        target_gids, current_id, value
                    )
                )
                parent_menu.addAction(action)

    def _setMangaPrimaryLabel(self, gids, label_name: str):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))

        def update_items():
            target_gid_set = set(target_gids)
            self._all_items = [
                replace(item, primary_label=label_name)
                if item.gid in target_gid_set else item
                for item in self._all_items
            ]
            self.applyFilters(reset_page=False)

        self._startLabelMutation(
            lambda: self.source.set_primary_label(target_gids, label_name),
            update_items,
        )

    def _setMangaMultipleLabel(
        self,
        gids,
        label_id: int,
        label_name: str,
        checked: bool,
    ):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if checked:
            operation = lambda: self.userRepository.assign_label_to_mangas(
                target_gids, label_id
            )
        else:
            operation = lambda: self.userRepository.unassign_label_from_mangas(
                target_gids, label_id
            )
        self._startLabelMutation(
            operation,
            self._refreshTagData,
        )

    def _setMangaTaxonomyLabel(self, gids, label_id: int, checked: bool):
        target_gids = tuple(dict.fromkeys(int(gid) for gid in gids))
        if checked:
            operation = lambda: self.userRepository.assign_taxonomy_to_mangas(
                target_gids, label_id
            )
        else:
            operation = lambda: self.userRepository.unassign_taxonomy_from_mangas(
                target_gids, label_id
            )
        self._startLabelMutation(operation, self._refreshTagData)

    def _startLabelMutation(self, operation, on_success):
        worker = LabelMutationWorker(operation)
        worker.signals.succeeded.connect(
            lambda: self._finishLabelMutation(worker, on_success)
        )
        worker.signals.failed.connect(
            lambda message: self._failLabelMutation(worker, message)
        )
        self._label_workers.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _finishLabelMutation(self, worker, on_success):
        self._label_workers.discard(worker)
        on_success()

    def _failLabelMutation(self, worker, message: str):
        self._label_workers.discard(worker)
        InfoBar.error(
            title=self.tr("标签操作失败"),
            content=message,
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self,
        )

    def _onSelectionModeChanged(self, enabled: bool):
        self._selection_mode = bool(enabled)
        if not self._selection_mode:
            self._selected_gids.clear()
        self._updateSelectionState()

    def _setMangaSelected(self, gid: int, selected: bool):
        if selected:
            self._selected_gids.add(int(gid))
        else:
            self._selected_gids.discard(int(gid))
        self._updateSelectionState()

    def _updateSelectionState(self):
        self.selectionCountLabel.setVisible(self._selection_mode)
        self.selectionCountLabel.setText(
            self.tr("已选 {} 项").format(len(self._selected_gids))
        )
        for card in self._cards:
            card.setSelectionState(
                self._selection_mode,
                card.item.gid in self._selected_gids,
            )

    def _scheduleSearch(self):
        self.searchTimer.start()

    def _onSortOrderChanged(self):
        order = self.sortCombo.currentData()
        if order not in ("desc", "asc"):
            return
        self._sort_order = order
        cfg.set(cfg.mangaSortOrder, order)
        self.applyFilters(reset_page=True)

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
        taxonomy_label_ids = (
            self._activeTaxonomyLabelIds()
            if self._tag_mode == self.TAG_TAXONOMY and not self._show_all_manga
            else set()
        )
        self._filtered_items = [
            item
            for item in self._all_items
            if self._matchesActiveTag(item, taxonomy_label_ids) and item.matches(query)
        ]
        if (
            self._tag_mode == self.TAG_PLAYLIST
            and not self._show_all_manga
            and self._playlist_filter_id is not None
        ):
            order = {gid: position for position, gid in enumerate(self._playlist_order)}
            self._filtered_items.sort(
                key=lambda item: (order.get(item.gid, len(order)), item.gid)
            )
        else:
            self._filtered_items.sort(
                key=lambda item: (item.added_time, item.gid),
                reverse=self._sort_order == "desc",
            )
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

    def _matchesActiveTag(self, item: MangaItem, taxonomy_label_ids=None) -> bool:
        if self._show_all_manga:
            return True
        if self._tag_mode == self.TAG_CATEGORY:
            if self._primary_label_filter == "__none__":
                return not item.primary_label
            return item.primary_label == self._primary_label_filter
        if self._tag_mode == self.TAG_PLAYLIST:
            return bool(
                self._playlist_filter_name
                and self._playlist_filter_name in item.multiple_labels
            )
        return bool(
            self._taxonomy_filter_id is not None
            and set(item.taxonomy_label_ids).intersection(
                taxonomy_label_ids or ()
            )
        )

    def _activeTaxonomyLabelIds(self):
        if self._taxonomy_filter_id is None:
            return set()
        result = {self._taxonomy_filter_id}
        changed = True
        while changed:
            changed = False
            for label_id, parent_id, _name, _count in self._taxonomy_labels:
                if parent_id in result and label_id not in result:
                    result.add(label_id)
                    changed = True
        return result

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
                item=item,
                open_callback=self._activateManga,
                label_menu_callback=self._showLabelMenu,
                selection_callback=self._setMangaSelected,
                selection_mode=self._selection_mode,
                selected=item.gid in self._selected_gids,
                cover_image=self._cover_cache.get(item.gid),
                parent=self.scrollWidget,
            )
            for item in page_items
        ]
        self._relayoutCards()
        self._preloadCovers()

    def _orderedPlaylistItems(self):
        if self._playlist_filter_id is None:
            return []
        by_gid = {item.gid: item for item in self._all_items}
        return [by_gid[gid] for gid in self._playlist_order if gid in by_gid]

    def _activateManga(self, item: MangaItem):
        if (
            self._tag_mode == self.TAG_PLAYLIST
            and not self._show_all_manga
            and self._playlist_filter_id is not None
        ):
            items = self._orderedPlaylistItems()
            try:
                position = next(
                    index for index, current in enumerate(items)
                    if current.gid == item.gid
                )
            except StopIteration:
                self.mangaActivated.emit(item)
                return
            self.playlistMangaActivated.emit(
                item, self._playlist_filter_id, tuple(items), position
            )
            return
        self.mangaActivated.emit(item)

    def _updatePlaylistActions(self):
        has_playlist = self._playlist_filter_id is not None
        has_items = bool(self._playlist_order)
        self.playlistPlayButton.setEnabled(has_playlist and has_items)
        self.playlistContinueButton.setEnabled(has_playlist and has_items)
        self.playlistOrderButton.setEnabled(has_playlist and has_items)

    def _requestPlaylistPlayback(self, continue_previous=False):
        if self._playlist_filter_id is None:
            return
        items = self._orderedPlaylistItems()
        if not items:
            return
        position = 0
        if continue_previous:
            last_gid = self.userRepository.playlist_last_gid(
                self._playlist_filter_id
            )
            if last_gid is not None:
                position = next(
                    (
                        index for index, item in enumerate(items)
                        if item.gid == last_gid
                    ),
                    0,
                )
        self.playlistPlayRequested.emit(
            self._playlist_filter_id, tuple(items), position, bool(continue_previous)
        )

    def _editPlaylistOrder(self):
        if self._playlist_filter_id is None:
            return
        playlist_id = self._playlist_filter_id
        playlist_name = self._playlist_filter_name
        items = self._orderedPlaylistItems()
        if not items:
            return
        dialog = PlaylistOrderDialog(playlist_name, items, self)
        if dialog.exec() != QDialog.Accepted:
            return
        ordered_gids = dialog.orderedGids()

        def finish():
            if self._playlist_filter_id == playlist_id:
                self._playlist_order = tuple(ordered_gids)
                self.applyFilters(reset_page=False)
            InfoBar.success(
                title=self.tr("播放顺序已保存"),
                content=self.tr("“{}”的新顺序将在播放时生效。").format(
                    playlist_name
                ),
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500,
                parent=self,
            )

        self._startLabelMutation(
            lambda: self.userRepository.set_playlist_order(
                playlist_id, ordered_gids
            ),
            finish,
        )

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
        worker = CoverPreloadWorker(self.source, self.userRepository, items)
        worker.signals.imageReady.connect(
            lambda gid, image: self._onCoverReady(worker, gid, image)
        )
        worker.signals.progressReady.connect(
            lambda gid, page_index: self._onProgressReady(
                worker, gid, page_index
            )
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

    def _onProgressReady(self, worker, gid: int, page_index: int):
        if self._cover_worker is not worker:
            return
        self.updateReadingProgress(gid, page_index)

    def updateReadingProgress(self, gid: int, page_index: int, page_count=0):
        def update(item):
            if item.gid != gid:
                return item
            return replace(
                item,
                progress_page_index=max(0, int(page_index)),
                page_count=max(item.page_count, int(page_count or 0)),
            )

        self._all_items = [update(item) for item in self._all_items]
        self._filtered_items = [update(item) for item in self._filtered_items]
        for card in self._cards:
            if card.item.gid == gid:
                card.setItem(update(card.item))
                break

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
        maximum_sidebar_width = max(190, int(self.width() * 0.3))
        self.classificationCard.setMaximumWidth(maximum_sidebar_width)
        sizes = self.tagSplitter.sizes()
        if len(sizes) == 2 and sizes[0] > maximum_sidebar_width:
            self.tagSplitter.setSizes(
                [maximum_sidebar_width, max(1, sum(sizes) - maximum_sidebar_width)]
            )
        if self._layout_mode == self.GRID_MODE:
            QTimer.singleShot(0, self._relayoutCards)
