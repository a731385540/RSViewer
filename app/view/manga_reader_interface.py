from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImageReader, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, SpinBox, ToolButton, TransparentToolButton
from qfluentwidgets import FluentIcon as FIF

from app.domain.manga import MangaItem


class ReaderLoadSignals(QObject):
    imageReady = Signal(int, object)
    finished = Signal()


class ReaderLoadWorker(QRunnable):
    """Decode the current page first, then preload nearby pages."""

    def __init__(self, page_paths, indexes):
        super().__init__()
        self.page_paths = tuple(page_paths)
        self.indexes = tuple(indexes)
        self.cancelled = False
        self.signals = ReaderLoadSignals()

    def run(self):
        for index in self.indexes:
            if self.cancelled:
                return
            reader = QImageReader(str(self.page_paths[index]))
            reader.setAutoTransform(True)
            image = reader.read()
            if self.cancelled:
                return
            try:
                self.signals.imageReady.emit(index, image)
            except RuntimeError:
                return
        try:
            self.signals.finished.emit()
        except RuntimeError:
            pass


class MangaReaderInterface(QWidget):
    """Single-page manga reader with windowed and full-screen modes."""

    backRequested = Signal()
    fullscreenRequested = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mangaReaderInterface")
        self.setFocusPolicy(Qt.StrongFocus)
        self._item: Optional[MangaItem] = None
        self._page_index = 0
        self._image_cache = OrderedDict()
        self._load_worker: Optional[ReaderLoadWorker] = None
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._fit_mode = True
        self._zoom_factor = 1.0
        self._fullscreen = False

        self.backButton = TransparentToolButton(FIF.LEFT_ARROW, self)
        self.backButton.setToolTip(self.tr("返回详情"))
        self.backButton.clicked.connect(self.backRequested)
        self.titleLabel = BodyLabel("", self)
        self.titleLabel.setMinimumWidth(120)

        self.fitButton = ToolButton(FIF.FIT_PAGE, self)
        self.fitButton.setToolTip(self.tr("适应窗口"))
        self.fitButton.clicked.connect(self.fitToWindow)
        self.actualSizeButton = ToolButton(FIF.ZOOM, self)
        self.actualSizeButton.setToolTip(self.tr("原始大小"))
        self.actualSizeButton.clicked.connect(self.actualSize)
        self.zoomOutButton = ToolButton(FIF.ZOOM_OUT, self)
        self.zoomOutButton.setToolTip(self.tr("缩小"))
        self.zoomOutButton.clicked.connect(lambda: self.zoomBy(0.8))
        self.zoomInButton = ToolButton(FIF.ZOOM_IN, self)
        self.zoomInButton.setToolTip(self.tr("放大"))
        self.zoomInButton.clicked.connect(lambda: self.zoomBy(1.25))
        self.fullscreenButton = ToolButton(FIF.FULL_SCREEN, self)
        self.fullscreenButton.setToolTip(self.tr("全屏 (F11)"))
        self.fullscreenButton.clicked.connect(self.toggleFullscreen)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(18, 10, 18, 10)
        toolbar.setSpacing(8)
        toolbar.addWidget(self.backButton)
        toolbar.addWidget(self.titleLabel, 1)
        toolbar.addWidget(self.zoomOutButton)
        toolbar.addWidget(self.actualSizeButton)
        toolbar.addWidget(self.fitButton)
        toolbar.addWidget(self.zoomInButton)
        toolbar.addWidget(self.fullscreenButton)

        self.scene = QGraphicsScene(self)
        self.graphicsView = QGraphicsView(self.scene, self)
        self.graphicsView.setFrameShape(QFrame.NoFrame)
        self.graphicsView.setAlignment(Qt.AlignCenter)
        self.graphicsView.setDragMode(QGraphicsView.ScrollHandDrag)
        self.graphicsView.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.graphicsView.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        self.previousButton = ToolButton(FIF.LEFT_ARROW, self)
        self.previousButton.setToolTip(self.tr("上一页 (←)"))
        self.previousButton.clicked.connect(self.previousPage)
        self.nextButton = ToolButton(FIF.RIGHT_ARROW, self)
        self.nextButton.setToolTip(self.tr("下一页 (→)"))
        self.nextButton.clicked.connect(self.nextPage)
        self.pageSpinBox = SpinBox(self)
        self.pageSpinBox.setRange(1, 1)
        self.pageSpinBox.setFixedWidth(88)
        self.pageSpinBox.valueChanged.connect(self._jumpToPage)
        self.pageCountLabel = QLabel(self.tr("/ 0 页"), self)
        self.zoomLabel = QLabel("100%", self)

        navigation = QHBoxLayout()
        navigation.setContentsMargins(18, 8, 18, 12)
        navigation.setSpacing(8)
        navigation.addStretch(1)
        navigation.addWidget(self.previousButton)
        navigation.addWidget(self.pageSpinBox)
        navigation.addWidget(self.pageCountLabel)
        navigation.addWidget(self.nextButton)
        navigation.addSpacing(12)
        navigation.addWidget(self.zoomLabel)
        navigation.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self.graphicsView, 1)
        layout.addLayout(navigation)

    @property
    def currentItem(self) -> Optional[MangaItem]:
        return self._item

    @property
    def currentPage(self) -> int:
        return self._page_index + 1 if self._item and self._item.page_paths else 0

    @property
    def isFullscreen(self) -> bool:
        return self._fullscreen

    def setManga(self, item: MangaItem, page_index=0):
        self.cancelLoads()
        self._item = item
        self._image_cache.clear()
        self.titleLabel.setText(item.display_title)
        page_count = len(item.page_paths)
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setRange(1, max(1, page_count))
        self.pageSpinBox.setValue(1 if not page_count else page_index + 1)
        self.pageSpinBox.blockSignals(False)
        self.pageCountLabel.setText(self.tr("/ {} 页").format(page_count))
        if not page_count:
            self._page_index = 0
            self.scene.clear()
            self.scene.addText(self.tr("没有可读取的图片页面"))
            self._pixmap_item = None
            self._updateControls()
            return
        self.showPage(min(max(0, page_index), page_count - 1))

    def showPage(self, index: int):
        if self._item is None or not self._item.page_paths:
            return
        index = min(max(0, index), len(self._item.page_paths) - 1)
        self._page_index = index
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setValue(index + 1)
        self.pageSpinBox.blockSignals(False)
        self._updateControls()
        if index in self._image_cache:
            self._displayImage(self._image_cache[index])
            self._image_cache.move_to_end(index)
            self._preloadAround(index, include_current=False)
            return
        self.scene.clear()
        self.scene.addText(self.tr("正在读取第 {} 页…").format(index + 1))
        self._pixmap_item = None
        self._preloadAround(index, include_current=True)

    def nextPage(self):
        self.showPage(self._page_index + 1)

    def previousPage(self):
        self.showPage(self._page_index - 1)

    def fitToWindow(self):
        self._fit_mode = True
        self._applyViewTransform()

    def actualSize(self):
        self._fit_mode = False
        self._zoom_factor = 1.0
        self._applyViewTransform()

    def zoomBy(self, factor: float):
        if self._pixmap_item is None:
            return
        self._fit_mode = False
        self._zoom_factor = min(8.0, max(0.1, self._zoom_factor * factor))
        self._applyViewTransform()

    def toggleFullscreen(self):
        self.setFullscreenState(not self._fullscreen, emit_request=True)

    def setFullscreenState(self, fullscreen: bool, emit_request=False):
        self._fullscreen = bool(fullscreen)
        self.fullscreenButton.setIcon(
            FIF.BACK_TO_WINDOW if self._fullscreen else FIF.FULL_SCREEN
        )
        self.fullscreenButton.setToolTip(
            self.tr("退出全屏 (F11/Esc)")
            if self._fullscreen
            else self.tr("全屏 (F11)")
        )
        if emit_request:
            self.fullscreenRequested.emit(self._fullscreen)

    def cancelLoads(self):
        if self._load_worker is not None:
            self._load_worker.cancelled = True
            self._load_worker = None

    def _preloadAround(self, index: int, include_current: bool):
        if self._item is None:
            return
        self.cancelLoads()
        page_count = len(self._item.page_paths)
        candidates = [index, index + 1, index + 2, index - 1]
        indexes = []
        for candidate in candidates:
            if not 0 <= candidate < page_count:
                continue
            if candidate in self._image_cache:
                continue
            if candidate == index and not include_current:
                continue
            if candidate not in indexes:
                indexes.append(candidate)
        if not indexes:
            return
        worker = ReaderLoadWorker(self._item.page_paths, indexes)
        worker.signals.imageReady.connect(
            lambda page_index, image: self._onImageReady(worker, page_index, image)
        )
        worker.signals.finished.connect(lambda: self._finishLoad(worker))
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onImageReady(self, worker, index: int, image):
        if self._load_worker is not worker:
            return
        self._image_cache[index] = image
        self._image_cache.move_to_end(index)
        while len(self._image_cache) > 5:
            self._image_cache.popitem(last=False)
        if index == self._page_index:
            self._displayImage(image)

    def _finishLoad(self, worker):
        if self._load_worker is worker:
            self._load_worker = None

    def _displayImage(self, image):
        self.scene.clear()
        self._pixmap_item = None
        if image.isNull():
            self.scene.addText(self.tr("当前页面无法解码"))
            return
        self._pixmap_item = self.scene.addPixmap(QPixmap.fromImage(image))
        self.scene.setSceneRect(self._pixmap_item.boundingRect())
        self._applyViewTransform()

    def _applyViewTransform(self):
        if self._pixmap_item is None:
            return
        self.graphicsView.resetTransform()
        if self._fit_mode:
            self.graphicsView.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
            scale = self.graphicsView.transform().m11()
        else:
            self.graphicsView.scale(self._zoom_factor, self._zoom_factor)
            scale = self._zoom_factor
        self.zoomLabel.setText(f"{round(scale * 100)}%")

    def _jumpToPage(self, page: int):
        self.showPage(page - 1)

    def _updateControls(self):
        page_count = len(self._item.page_paths) if self._item else 0
        self.previousButton.setEnabled(self._page_index > 0)
        self.nextButton.setEnabled(self._page_index + 1 < page_count)
        self.pageSpinBox.setEnabled(page_count > 0)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_F11:
            self.toggleFullscreen()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            if self._fullscreen:
                self.setFullscreenState(False, emit_request=True)
            else:
                self.backRequested.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space):
            self.nextPage()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace):
            self.previousPage()
            event.accept()
            return
        if event.key() == Qt.Key_Home:
            self.showPage(0)
            event.accept()
            return
        if event.key() == Qt.Key_End and self._item is not None:
            self.showPage(len(self._item.page_paths) - 1)
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier and event.key() in (
            Qt.Key_Plus,
            Qt.Key_Equal,
        ):
            self.zoomBy(1.25)
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_Minus:
            self.zoomBy(0.8)
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self._applyViewTransform()
