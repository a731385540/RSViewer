from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap
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
    PrimaryPushButton,
    ScrollArea,
    SimpleCardWidget,
    SpinBox,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.domain.manga import MangaItem
from app.repositories.user_library_repository import UserLibraryRepository
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import CoverLabel, visible_tags


class PreviewTile(QWidget):
    """详情页中的单页缩略预览。"""

    clicked = Signal(int)

    def __init__(self, page_index: int, parent=None):
        super().__init__(parent)
        self.pageIndex = page_index
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(126, 184)
        self.imageLabel = QLabel(self)
        self.imageLabel.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.imageLabel.setAlignment(Qt.AlignCenter)
        self.imageLabel.setFixedSize(116, 154)
        self.imageLabel.setText(self.tr("加载中…"))
        number = CaptionLabel(str(page_index + 1), self)
        number.setAttribute(Qt.WA_TransparentForMouseEvents)
        number.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        layout.addWidget(self.imageLabel)
        layout.addWidget(number)

    def setImage(self, image):
        if image.isNull():
            self.imageLabel.setText(self.tr("无法预览"))
            return
        self.imageLabel.setText("")
        self.imageLabel.setPixmap(QPixmap.fromImage(image))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.pageIndex)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PreviewLoadSignals(QObject):
    imageReady = Signal(int, object)
    finished = Signal()


class PreviewLoadWorker(QRunnable):
    """在后台解码页面缩略图，避免打开详情时阻塞界面。"""

    def __init__(self, page_paths, start_index=0):
        super().__init__()
        self.pagePaths = tuple(page_paths)
        self.startIndex = int(start_index)
        self.signals = PreviewLoadSignals()
        self.cancelled = False

    def run(self):
        for index, path in enumerate(self.pagePaths, self.startIndex):
            if self.cancelled:
                break
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            source_size = reader.size()
            if source_size.isValid():
                source_size.scale(QSize(116, 154), Qt.KeepAspectRatio)
                reader.setScaledSize(source_size)
            image = reader.read()
            if not image.isNull() and (image.width() > 116 or image.height() > 154):
                image = image.scaled(
                    QSize(116, 154),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            try:
                self.signals.imageReady.emit(index, image)
            except RuntimeError:
                return
        try:
            self.signals.finished.emit()
        except RuntimeError:
            pass


class PageDiscoverySignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class PageDiscoveryWorker(QRunnable):
    """仅在打开详情时枚举单本漫画，避免资源列表扫描整个图库。"""

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        item: MangaItem,
    ):
        super().__init__()
        self.source = source
        self.user_repository = user_repository
        self.item = item
        self.cancelled = False
        self.signals = PageDiscoverySignals()

    def run(self):
        try:
            item = self.source.load_pages(self.item)
            progress = self.user_repository.resolve_progress(
                item.gid,
                self.source.read_ehviewer_progress(item),
            )
            if progress is not None and item.page_paths:
                clamped_progress = min(progress, len(item.page_paths) - 1)
                item = replace(item, progress_page_index=clamped_progress)
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(item)
                except RuntimeError:
                    pass
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass


class MangaDetailInterface(QWidget):
    """本地漫画的信息、操作和页面预览。"""

    PREVIEW_PAGE_SIZE = 40

    backRequested = Signal()
    readRequested = Signal(object, int)
    progressResolved = Signal(int, int, int)

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        parent=None,
    ):
        super().__init__(parent)
        self.source = source
        self.userRepository = user_repository
        self.setObjectName("mangaDetailInterface")
        self._item: Optional[MangaItem] = None
        self._preview_tiles: List[PreviewTile] = []
        self._preview_columns = 0
        self._preview_page = 1
        self._preview_worker: Optional[PreviewLoadWorker] = None
        self._page_worker: Optional[PageDiscoveryWorker] = None
        self.currentCoverPath: Optional[Path] = None

        self.backButton = TransparentToolButton(FIF.LEFT_ARROW, self)
        self.backButton.setToolTip(self.tr("返回"))
        self.backButton.clicked.connect(self.backRequested)
        self.pageTitle = TitleLabel(self.tr("漫画详情"), self)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.backButton)
        header_layout.addWidget(self.pageTitle)
        header_layout.addStretch(1)

        self.infoCard = SimpleCardWidget(self)
        info_layout = QHBoxLayout(self.infoCard)
        info_layout.setContentsMargins(18, 18, 18, 18)
        info_layout.setSpacing(22)

        self.coverLabel = CoverLabel(Path(), parent=self.infoCard)
        self.coverLabel.setFixedSize(220, 300)
        self.originalTitleLabel = SubtitleLabel("", self.infoCard)
        self.originalTitleLabel.setWordWrap(True)
        self.englishTitleLabel = BodyLabel("", self.infoCard)
        self.englishTitleLabel.setWordWrap(True)
        self.metadataLabel = BodyLabel("", self.infoCard)
        self.metadataLabel.setWordWrap(True)
        self.metadataLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.tagsLabel = CaptionLabel("", self.infoCard)
        self.tagsLabel.setWordWrap(True)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 2, 0, 2)
        text_layout.setSpacing(10)
        text_layout.addWidget(self.originalTitleLabel)
        text_layout.addWidget(self.englishTitleLabel)
        text_layout.addSpacing(4)
        text_layout.addWidget(self.metadataLabel)
        text_layout.addWidget(self.tagsLabel)
        text_layout.addStretch(1)
        info_layout.addWidget(self.coverLabel, 0, Qt.AlignTop)
        info_layout.addLayout(text_layout, 1)

        self.operationCard = SimpleCardWidget(self)
        operation_layout = QHBoxLayout(self.operationCard)
        operation_layout.setContentsMargins(18, 14, 18, 14)
        operation_layout.addWidget(SubtitleLabel(self.tr("操作"), self.operationCard))
        operation_layout.addStretch(1)
        self.readButton = PrimaryPushButton(
            FIF.BOOK_SHELF,
            self.tr("开始阅读"),
            self.operationCard,
        )
        self.readButton.clicked.connect(self._requestRead)
        operation_layout.addWidget(self.readButton)

        self.previewCard = SimpleCardWidget(self)
        preview_layout = QVBoxLayout(self.previewCard)
        preview_layout.setContentsMargins(18, 16, 18, 18)
        preview_layout.setSpacing(12)
        self.previewTitle = SubtitleLabel(self.tr("页面预览"), self.previewCard)
        preview_layout.addWidget(self.previewTitle)
        self.previewWidget = QWidget(self.previewCard)
        self.previewWidget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.previewGrid = QGridLayout(self.previewWidget)
        self.previewGrid.setContentsMargins(0, 0, 0, 0)
        self.previewGrid.setHorizontalSpacing(12)
        self.previewGrid.setVerticalSpacing(12)
        self.previewGrid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        preview_layout.addWidget(self.previewWidget)

        self.previewPaginationWidget = QWidget(self.previewCard)
        preview_pagination_layout = QHBoxLayout(self.previewPaginationWidget)
        preview_pagination_layout.setContentsMargins(0, 4, 0, 0)
        preview_pagination_layout.setSpacing(8)
        preview_pagination_layout.addStretch(1)
        self.previewFirstPageButton = ToolButton(FIF.PAGE_LEFT, self.previewCard)
        self.previewPreviousPageButton = ToolButton(FIF.LEFT_ARROW, self.previewCard)
        self.previewPageSpinBox = SpinBox(self.previewCard)
        self.previewPageSpinBox.setRange(1, 1)
        self.previewPageSpinBox.setFixedWidth(82)
        self.previewPageCountLabel = BodyLabel(self.tr("/ 1 页"), self.previewCard)
        self.previewNextPageButton = ToolButton(FIF.RIGHT_ARROW, self.previewCard)
        self.previewLastPageButton = ToolButton(FIF.PAGE_RIGHT, self.previewCard)
        preview_pagination_layout.addWidget(self.previewFirstPageButton)
        preview_pagination_layout.addWidget(self.previewPreviousPageButton)
        preview_pagination_layout.addWidget(self.previewPageSpinBox)
        preview_pagination_layout.addWidget(self.previewPageCountLabel)
        preview_pagination_layout.addWidget(self.previewNextPageButton)
        preview_pagination_layout.addWidget(self.previewLastPageButton)
        preview_pagination_layout.addStretch(1)
        preview_layout.addWidget(self.previewPaginationWidget)
        self.previewPaginationWidget.hide()

        self.previewFirstPageButton.clicked.connect(
            lambda: self._setPreviewPage(1)
        )
        self.previewPreviousPageButton.clicked.connect(
            lambda: self._setPreviewPage(self._preview_page - 1)
        )
        self.previewPageSpinBox.valueChanged.connect(self._setPreviewPage)
        self.previewNextPageButton.clicked.connect(
            lambda: self._setPreviewPage(self._preview_page + 1)
        )
        self.previewLastPageButton.clicked.connect(
            lambda: self._setPreviewPage(self._previewPageCount())
        )

        self.scrollWidget = QWidget()
        self.scrollWidget.setObjectName("mangaDetailScrollWidget")
        content_layout = QVBoxLayout(self.scrollWidget)
        content_layout.setContentsMargins(36, 0, 36, 28)
        content_layout.setSpacing(16)
        content_layout.addWidget(self.infoCard)
        content_layout.addWidget(self.operationCard)
        content_layout.addWidget(self.previewCard)
        content_layout.addStretch(1)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#mangaDetailScrollWidget { background: transparent; }"
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 28, 0, 0)
        main_layout.setSpacing(14)
        header_container = QWidget(self)
        header_container.setLayout(header_layout)
        header_container.layout().setContentsMargins(36, 0, 36, 0)
        main_layout.addWidget(header_container)
        main_layout.addWidget(self.scrollArea, 1)

    @property
    def currentItem(self) -> Optional[MangaItem]:
        return self._item

    def setSource(self, source: EhViewerDataSource):
        self.source = source
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None

    def setManga(self, item: MangaItem):
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None
        self._item = item
        self.readButton.setEnabled(bool(item.page_paths))
        if not item.page_paths:
            self.readButton.setText(self.tr("正在准备…"))
        elif item.progress_page_number is not None:
            self.readButton.setText(
                self.tr("继续阅读（第 {} 页）").format(
                    min(item.progress_page_number, item.page_count)
                )
            )
        else:
            self.readButton.setText(self.tr("开始阅读"))
        self.pageTitle.setText(self.tr("漫画详情"))
        self.originalTitleLabel.setText(item.display_title)
        self.englishTitleLabel.setText(item.secondary_title)
        self.englishTitleLabel.setVisible(bool(item.secondary_title))
        self.metadataLabel.setText(self._metadataText(item))
        tags = visible_tags(item)
        self.tagsLabel.setText(self.tr("标签信息：{}").format(tags or self.tr("无")))

        cover_path = item.thumbnail_path or item.cover_path
        pixmap = QPixmap(str(cover_path))
        if pixmap.isNull() and cover_path != item.cover_path:
            cover_path = item.cover_path
        self.currentCoverPath = cover_path
        old_cover = self.coverLabel
        self.coverLabel = CoverLabel(cover_path, parent=self.infoCard)
        self.coverLabel.setFixedSize(220, 300)
        self.infoCard.layout().replaceWidget(old_cover, self.coverLabel)
        old_cover.deleteLater()

        self._clearPreview()
        if item.page_paths:
            self._showPages(item)
        else:
            self.previewTitle.setText(self.tr("正在读取页面…"))
            worker = PageDiscoveryWorker(self.source, self.userRepository, item)
            worker.signals.loaded.connect(
                lambda loaded_item: self._finishPageDiscovery(worker, loaded_item)
            )
            worker.signals.failed.connect(
                lambda message: self._failPageDiscovery(worker, message)
            )
            self._page_worker = worker
            QThreadPool.globalInstance().start(worker)
        self.scrollArea.verticalScrollBar().setValue(0)

    def _showPages(self, item: MangaItem):
        self._preview_page = 1
        self._renderPreviewPage(item)

    def _renderPreviewPage(self, item: MangaItem):
        self._clearPreviewTiles()
        page_count = self._previewPageCount(item)
        self._preview_page = min(max(1, self._preview_page), page_count)
        start = (self._preview_page - 1) * self.PREVIEW_PAGE_SIZE
        end = min(len(item.page_paths), start + self.PREVIEW_PAGE_SIZE)
        self._preview_tiles = [
            PreviewTile(index, self.previewWidget)
            for index in range(start, end)
        ]
        for tile in self._preview_tiles:
            tile.clicked.connect(
                lambda page_index, current_item=item: self.readRequested.emit(
                    current_item, page_index
                )
            )
        self.previewTitle.setText(
            self.tr("页面预览（共 {} 页，第 {} / {} 页）").format(
                len(item.page_paths), self._preview_page, page_count
            )
        )
        self._updatePreviewPagination(page_count)
        QTimer.singleShot(0, self._relayoutPreview)
        worker = PreviewLoadWorker(item.page_paths[start:end], start)
        worker.signals.imageReady.connect(
            lambda index, image: self._setPreviewImage(worker, index, image)
        )
        worker.signals.finished.connect(lambda: self._finishPreviewLoad(worker))
        self._preview_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _previewPageCount(self, item=None) -> int:
        current_item = item or self._item
        total = len(current_item.page_paths) if current_item else 0
        return max(1, (total + self.PREVIEW_PAGE_SIZE - 1) // self.PREVIEW_PAGE_SIZE)

    def _setPreviewPage(self, page: int):
        if self._item is None or not self._item.page_paths:
            return
        page = min(max(1, int(page)), self._previewPageCount())
        if page == self._preview_page and self._preview_tiles:
            return
        self._preview_page = page
        self._renderPreviewPage(self._item)

    def _updatePreviewPagination(self, page_count: int):
        self.previewPaginationWidget.setVisible(page_count > 1)
        self.previewPageSpinBox.blockSignals(True)
        self.previewPageSpinBox.setRange(1, page_count)
        self.previewPageSpinBox.setValue(self._preview_page)
        self.previewPageSpinBox.blockSignals(False)
        self.previewPageCountLabel.setText(self.tr("/ {} 页").format(page_count))
        self.previewFirstPageButton.setEnabled(self._preview_page > 1)
        self.previewPreviousPageButton.setEnabled(self._preview_page > 1)
        self.previewNextPageButton.setEnabled(self._preview_page < page_count)
        self.previewLastPageButton.setEnabled(self._preview_page < page_count)

    def _finishPageDiscovery(self, worker, item: MangaItem):
        if self._page_worker is not worker:
            return
        self._page_worker = None
        if not item.page_paths:
            self._item = item
            self.readButton.setEnabled(False)
            self.readButton.setText(self.tr("无法阅读"))
            self.previewTitle.setText(self.tr("未找到可读取的图片页面"))
            return
        if item.progress_page_index is not None:
            self.progressResolved.emit(
                item.gid,
                item.progress_page_index,
                item.page_count,
            )
        self.setManga(item)

    def _failPageDiscovery(self, worker, message: str):
        if self._page_worker is worker:
            self._page_worker = None
            self.readButton.setEnabled(False)
            self.readButton.setText(self.tr("无法阅读"))
            self.previewTitle.setText(self.tr("页面读取失败：{}").format(message))

    def cancelLoads(self):
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None
        if self._preview_worker is not None:
            self._preview_worker.cancelled = True
            self._preview_worker = None

    def _requestRead(self):
        if self._item is None or not self._item.page_paths:
            return
        self.readRequested.emit(self._item, -1)

    def updateReadingProgress(self, gid: int, page_index: int, page_count=0):
        if self._item is None or self._item.gid != gid:
            return
        self._item = replace(
            self._item,
            progress_page_index=max(0, int(page_index)),
            page_count=max(self._item.page_count, int(page_count or 0)),
        )
        self.metadataLabel.setText(self._metadataText(self._item))
        current_page = self._item.progress_page_number
        if self._item.page_count:
            current_page = min(current_page, self._item.page_count)
        self.readButton.setText(
            self.tr("继续阅读（第 {} 页）").format(
                current_page
            )
        )

    def _progressText(self, item: MangaItem) -> str:
        if item.progress_page_number is None:
            return self.tr("未开始")
        if item.page_count:
            return self.tr("第 {} / {} 页").format(
                min(item.progress_page_number, item.page_count),
                item.page_count,
            )
        return self.tr("第 {} 页").format(item.progress_page_number)

    def _metadataText(self, item: MangaItem) -> str:
        primary_label = item.primary_label or self.tr("未分类")
        multiple_labels = "、".join(item.multiple_labels) or self.tr("无")
        return self.tr(
            "GID：{gid}\n标签：{primary}\n分类标签：{multiple}\n"
            "来源类别：{category}\n页数：{pages}\n阅读进度：{progress}\n目录：{folder}"
        ).format(
            gid=item.gid,
            primary=primary_label,
            multiple=multiple_labels,
            category=item.category_name,
            pages=item.page_count or self.tr("读取中…"),
            progress=self._progressText(item),
            folder=item.folder,
        )

    def _clearPreview(self):
        self._clearPreviewTiles()
        self._preview_page = 1
        self.previewPaginationWidget.hide()

    def _clearPreviewTiles(self):
        if self._preview_worker is not None:
            self._preview_worker.cancelled = True
            self._preview_worker = None
        while self.previewGrid.count():
            layout_item = self.previewGrid.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._preview_tiles = []
        self._preview_columns = 0

    def _setPreviewImage(self, worker, index: int, image):
        if self._preview_worker is not worker:
            return
        start = (self._preview_page - 1) * self.PREVIEW_PAGE_SIZE
        local_index = index - start
        if 0 <= local_index < len(self._preview_tiles):
            self._preview_tiles[local_index].setImage(image)

    def _finishPreviewLoad(self, worker):
        if self._preview_worker is worker:
            self._preview_worker = None

    def _relayoutPreview(self):
        if not self._preview_tiles:
            return
        available_width = max(126, self.previewWidget.width())
        columns = max(1, available_width // 138)
        if columns == self._preview_columns and self.previewGrid.count():
            return
        while self.previewGrid.count():
            self.previewGrid.takeAt(0)
        self._preview_columns = columns
        for index, tile in enumerate(self._preview_tiles):
            self.previewGrid.addWidget(tile, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._relayoutPreview)
