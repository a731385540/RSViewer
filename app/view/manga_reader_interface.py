from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Optional

from PySide6.QtCore import (
    QBuffer,
    QEvent,
    QIODevice,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader, QKeyEvent, QKeySequence, QMovie, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    SimpleCardWidget,
    SpinBox,
    SubtitleLabel,
    ToolButton,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.domain.manga import MangaItem
from app.domain.online_gallery import OnlineGalleryDetail
from app.view.reader_setting_dialog import ReaderSettingDialog
from app.workers.eh_online_worker import OnlineReaderLoadWorker


class ReaderLoadSignals(QObject):
    imageReady = Signal(int, object)
    finished = Signal()


@dataclass(frozen=True)
class ReaderPageImage:
    """A preloaded first frame plus the file type detected from its contents."""

    image: QImage
    is_gif: bool = False


def _has_gif_signature(path) -> bool:
    """Recognize GIF87a/GIF89a without trusting a possibly renamed extension."""
    try:
        with open(path, "rb") as stream:
            return stream.read(6) in (b"GIF87a", b"GIF89a")
    except OSError:
        return False


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
            path = self.page_paths[index]
            is_gif = _has_gif_signature(path)
            reader = QImageReader(str(path))
            if is_gif:
                # An explicit format keeps renamed GIF files working on every Qt build.
                reader.setFormat(b"gif")
            reader.setAutoTransform(True)
            image = reader.read()
            if self.cancelled:
                return
            try:
                self.signals.imageReady.emit(index, ReaderPageImage(image, is_gif))
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
    progressChanged = Signal(int, int, int)
    nextMangaRequested = Signal()
    previousMangaRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mangaReaderInterface")
        self.setFocusPolicy(Qt.StrongFocus)
        self._item: Optional[MangaItem] = None
        self._online_detail: Optional[OnlineGalleryDetail] = None
        self._online_provider = None
        self._online_cache = None
        self._page_index = 0
        self._image_cache = OrderedDict()
        self._load_worker: Optional[ReaderLoadWorker] = None
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._active_movie: Optional[QMovie] = None
        self._movie_buffer: Optional[QBuffer] = None
        self._movie_page_index = -1
        self._display_mode = cfg.get(cfg.readerImageLoadSize)
        self._zoom_factor = 1.0
        self._fullscreen = False
        self._reader_active = False
        self._has_following_manga = False
        self._has_previous_manga = False
        self._settings_dialog: Optional[ReaderSettingDialog] = None
        self._auto_page_timer = QTimer(self)
        self._auto_page_timer.timeout.connect(self._autoAdvance)

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
        self.autoPageButton = ToolButton(FIF.PLAY, self)
        self.autoPageButton.clicked.connect(self.toggleAutoPage)
        self.settingsButton = ToolButton(FIF.SETTING, self)
        self.settingsButton.setToolTip(self.tr("阅读设置"))
        self.settingsButton.clicked.connect(self.showReaderSettings)

        self.toolbarWidget = QWidget(self)
        toolbar = QHBoxLayout(self.toolbarWidget)
        toolbar.setContentsMargins(18, 10, 18, 10)
        toolbar.setSpacing(8)
        toolbar.addWidget(self.backButton)
        toolbar.addWidget(self.titleLabel, 1)
        toolbar.addWidget(self.zoomOutButton)
        toolbar.addWidget(self.actualSizeButton)
        toolbar.addWidget(self.fitButton)
        toolbar.addWidget(self.zoomInButton)
        toolbar.addWidget(self.autoPageButton)
        toolbar.addWidget(self.settingsButton)
        toolbar.addWidget(self.fullscreenButton)

        self.pageIndicatorCard = SimpleCardWidget(self)
        self.pageIndicatorLabel = SubtitleLabel(
            self.tr("尚未打开漫画"), self.pageIndicatorCard
        )
        self.pageIndicatorLabel.setAlignment(Qt.AlignCenter)
        self.pageIndicatorLabel.setMinimumWidth(220)
        page_indicator_card_layout = QHBoxLayout(self.pageIndicatorCard)
        page_indicator_card_layout.setContentsMargins(18, 5, 18, 5)
        page_indicator_card_layout.addWidget(self.pageIndicatorLabel)
        self.pageIndicatorWidget = QWidget(self)
        page_indicator_layout = QHBoxLayout(self.pageIndicatorWidget)
        page_indicator_layout.setContentsMargins(18, 2, 18, 8)
        page_indicator_layout.addStretch(1)
        page_indicator_layout.addWidget(self.pageIndicatorCard)
        page_indicator_layout.addStretch(1)

        self.scene = QGraphicsScene(self)
        self.graphicsView = QGraphicsView(self.scene, self)
        self.graphicsView.setFrameShape(QFrame.NoFrame)
        self.graphicsView.setAlignment(Qt.AlignCenter)
        self.graphicsView.setDragMode(QGraphicsView.ScrollHandDrag)
        self.graphicsView.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.graphicsView.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.graphicsView.installEventFilter(self)
        self.graphicsView.viewport().installEventFilter(self)

        self.fullscreenPageIndicatorLabel = CaptionLabel(
            self.tr("尚未打开漫画"), self.graphicsView.viewport()
        )
        self.fullscreenPageIndicatorLabel.setAlignment(Qt.AlignCenter)
        self.fullscreenPageIndicatorLabel.setAttribute(
            Qt.WA_TransparentForMouseEvents
        )
        indicator_opacity = QGraphicsOpacityEffect(
            self.fullscreenPageIndicatorLabel
        )
        indicator_opacity.setOpacity(0.58)
        self.fullscreenPageIndicatorLabel.setGraphicsEffect(indicator_opacity)
        self.fullscreenPageIndicatorLabel.hide()

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
        self.zoomLabel = QLabel("100%", self)

        self.navigationWidget = QWidget(self)
        navigation = QHBoxLayout(self.navigationWidget)
        navigation.setContentsMargins(18, 8, 18, 12)
        navigation.setSpacing(8)
        navigation.addStretch(1)
        navigation.addWidget(self.previousButton)
        navigation.addWidget(CaptionLabel(self.tr("跳转到"), self))
        navigation.addWidget(self.pageSpinBox)
        navigation.addWidget(CaptionLabel(self.tr("页"), self))
        navigation.addWidget(self.nextButton)
        navigation.addSpacing(12)
        navigation.addWidget(self.zoomLabel)
        navigation.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbarWidget)
        layout.addWidget(self.pageIndicatorWidget)
        layout.addWidget(self.graphicsView, 1)
        layout.addWidget(self.navigationWidget)

        self.setMouseTracking(True)
        for widget in self.findChildren(QWidget):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

        cfg.readerBackgroundColor.valueChanged.connect(self._applyBackgroundColor)
        cfg.readerPageDirection.valueChanged.connect(self._updateDirectionControls)
        cfg.readerImageLoadSize.valueChanged.connect(self._setImageLoadSize)
        cfg.readerScrollShortcut.valueChanged.connect(
            lambda _value: self._updateDirectionControls()
        )
        cfg.readerAutoPageEnabled.valueChanged.connect(self._updateAutoPageTimer)
        cfg.readerAutoPageInterval.valueChanged.connect(self._updateAutoPageTimer)
        self._applyBackgroundColor(cfg.get(cfg.readerBackgroundColor))
        self._updateDirectionControls()
        self._updateAutoPageTimer()

    @property
    def currentItem(self) -> Optional[MangaItem]:
        return self._item

    @property
    def currentPage(self) -> int:
        return self._page_index + 1 if self._pageCount() else 0

    @property
    def isOnlineGallery(self) -> bool:
        return self._online_detail is not None

    @property
    def isFullscreen(self) -> bool:
        return self._fullscreen

    def setManga(self, item: MangaItem, page_index=0):
        self.cancelLoads()
        self._stopMovie()
        self._reader_active = True
        self._item = item
        self._online_detail = None
        self._online_provider = None
        self._online_cache = None
        self._image_cache.clear()
        self.titleLabel.setText(item.display_title)
        page_count = len(item.page_paths)
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setRange(1, max(1, page_count))
        self.pageSpinBox.setValue(1 if not page_count else page_index + 1)
        self.pageSpinBox.blockSignals(False)
        if not page_count:
            self._page_index = 0
            self._updatePageIndicator(0)
            self.scene.clear()
            self.scene.addText(self.tr("没有可读取的图片页面"))
            self._pixmap_item = None
            self._updateControls()
            self._updateAutoPageTimer()
            return
        self.showPage(min(max(0, page_index), page_count - 1))

    def setOnlineGallery(self, detail, provider, cache, page_index=0):
        self.cancelLoads()
        self._stopMovie()
        self._reader_active = True
        self._item = None
        self._online_detail = detail
        self._online_provider = provider
        self._online_cache = cache
        self._image_cache.clear()
        self.titleLabel.setText(detail.title)
        page_count = int(detail.page_count)
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setRange(1, max(1, page_count))
        self.pageSpinBox.setValue(1 if not page_count else int(page_index) + 1)
        self.pageSpinBox.blockSignals(False)
        if not page_count:
            self._page_index = 0
            self._updatePageIndicator(0)
            self.scene.clear()
            self.scene.addText(self.tr("没有可读取的在线页面"))
            self._pixmap_item = None
            self._updateControls()
            self._updateAutoPageTimer()
            return
        self.showPage(min(max(0, int(page_index)), page_count - 1))

    def showPage(self, index: int):
        page_count = self._pageCount()
        if page_count <= 0:
            return
        index = min(max(0, index), page_count - 1)
        self._page_index = index
        if self._item is not None:
            self._item = replace(self._item, progress_page_index=index)
            self.progressChanged.emit(
                self._item.gid,
                index,
                page_count,
            )
        self.pageSpinBox.blockSignals(True)
        self.pageSpinBox.setValue(index + 1)
        self.pageSpinBox.blockSignals(False)
        self._updatePageIndicator(page_count)
        self._updateControls()
        self._auto_page_timer.stop()
        self._stopMovie()
        if index in self._image_cache:
            self._displayPageImage(index, self._image_cache[index])
            self._image_cache.move_to_end(index)
            self._preloadAround(index, include_current=False)
            return
        self.scene.clear()
        self.scene.addText(self.tr("正在读取第 {} 页…").format(index + 1))
        self._pixmap_item = None
        self._preloadAround(index, include_current=True)

    def nextPage(self):
        if (
            self._pageCount()
            and self._page_index + 1 >= self._pageCount()
            and self._has_following_manga
        ):
            self._auto_page_timer.stop()
            self.nextMangaRequested.emit()
            return
        self.showPage(self._page_index + 1)

    def previousPage(self):
        if self._page_index <= 0 and self._has_previous_manga:
            self._auto_page_timer.stop()
            self.previousMangaRequested.emit()
            return
        self.showPage(self._page_index - 1)

    def fitToWindow(self):
        cfg.set(cfg.readerImageLoadSize, "fit_window")

    def actualSize(self):
        cfg.set(cfg.readerImageLoadSize, "original")

    def zoomBy(self, factor: float):
        if self._pixmap_item is None:
            return
        self._display_mode = "custom"
        self._zoom_factor = min(8.0, max(0.1, self._zoom_factor * factor))
        self._applyViewTransform()

    def showReaderSettings(self):
        if self._settings_dialog is None:
            self._settings_dialog = ReaderSettingDialog(self.window())
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def toggleAutoPage(self):
        cfg.set(
            cfg.readerAutoPageEnabled,
            not cfg.get(cfg.readerAutoPageEnabled),
        )

    def scrollForward(self):
        """Scroll a long image by one viewport, then advance at the bottom."""
        bar = self.graphicsView.verticalScrollBar()
        if bar.maximum() > bar.minimum() and bar.value() < bar.maximum():
            step = max(1, round(self.graphicsView.viewport().height() * 0.85))
            bar.setValue(min(bar.maximum(), bar.value() + step))
            return
        self.nextPage()

    def deactivate(self):
        self._reader_active = False
        self._auto_page_timer.stop()
        self.cancelLoads()
        self._stopMovie()

    def setPlaylistContinuation(
        self, has_following_manga: bool, has_previous_manga: bool = False
    ):
        self._has_following_manga = bool(has_following_manga)
        self._has_previous_manga = bool(has_previous_manga)
        self._updateControls()
        self._updateAutoPageTimer()

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
        if self._fullscreen:
            self.pageIndicatorWidget.hide()
            self.fullscreenPageIndicatorLabel.show()
            self.fullscreenPageIndicatorLabel.raise_()
            self._positionFullscreenPageIndicator()
            self.toolbarWidget.hide()
            self.navigationWidget.hide()
        else:
            self.toolbarWidget.show()
            self.pageIndicatorWidget.show()
            self.navigationWidget.show()
            self.fullscreenPageIndicatorLabel.hide()
        if emit_request:
            self.fullscreenRequested.emit(self._fullscreen)

    def _updateFullscreenControlsForPointer(self, event):
        if not self._fullscreen:
            return
        pointer = self.mapFromGlobal(event.globalPosition().toPoint())
        y = pointer.y()
        edge_size = 12
        keep_top_visible = (
            self.toolbarWidget.isVisible() and y <= self.toolbarWidget.height()
        )
        keep_bottom_visible = (
            self.navigationWidget.isVisible()
            and y >= self.height() - self.navigationWidget.height()
        )
        show_top = y <= edge_size or keep_top_visible
        show_bottom = y >= self.height() - edge_size or keep_bottom_visible
        if self.toolbarWidget.isVisible() != show_top:
            self.toolbarWidget.setVisible(show_top)
        if self.navigationWidget.isVisible() != show_bottom:
            self.navigationWidget.setVisible(show_bottom)
        self.fullscreenPageIndicatorLabel.raise_()

    def _positionFullscreenPageIndicator(self):
        viewport = self.graphicsView.viewport()
        label = self.fullscreenPageIndicatorLabel
        hint = label.sizeHint()
        width = max(120, hint.width() + 20)
        height = max(22, hint.height())
        x = max(0, (viewport.width() - width) // 2)
        y = max(0, viewport.height() - height - 14)
        label.setGeometry(x, y, width, height)

    def cancelLoads(self):
        if self._load_worker is not None:
            self._load_worker.cancelled = True
            self._load_worker = None

    def _preloadAround(self, index: int, include_current: bool):
        page_count = self._pageCount()
        if page_count <= 0:
            return
        self.cancelLoads()
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
        if self._online_detail is not None:
            worker = OnlineReaderLoadWorker(
                self._online_provider,
                self._online_detail.gallery,
                indexes,
                self._online_cache,
                self._online_provider.settings.site,
            )
            worker.signals.imageFailed.connect(
                lambda page_index, message: self._onImageFailed(
                    worker, page_index, message
                )
            )
        else:
            worker = ReaderLoadWorker(self._item.page_paths, indexes)
        worker.signals.imageReady.connect(
            lambda page_index, image: self._onImageReady(worker, page_index, image)
        )
        worker.signals.finished.connect(lambda: self._finishLoad(worker))
        self._load_worker = worker
        QThreadPool.globalInstance().start(worker)

    def _onImageReady(self, worker, index: int, page_image):
        if self._load_worker is not worker:
            return
        self._image_cache[index] = page_image
        self._image_cache.move_to_end(index)
        while len(self._image_cache) > 5:
            self._image_cache.popitem(last=False)
        if index == self._page_index:
            self._displayPageImage(index, page_image)

    def _finishLoad(self, worker):
        if self._load_worker is worker:
            self._load_worker = None

    def _onImageFailed(self, worker, index, message):
        if self._load_worker is not worker or index != self._page_index:
            return
        self.scene.clear()
        self.scene.addText(
            self.tr("第 {} 页加载失败：{}").format(index + 1, message)
        )
        self._pixmap_item = None
        self._updateAutoPageTimer()

    def _displayPageImage(self, index: int, page_image: ReaderPageImage):
        self._stopMovie()
        self._displayImage(page_image.image)
        if page_image.is_gif and not page_image.image.isNull():
            self._startMovie(index, page_image)

    def _displayImage(self, image):
        self.scene.clear()
        self._pixmap_item = None
        if image.isNull():
            self.scene.addText(self.tr("当前页面无法解码"))
            self._updateAutoPageTimer()
            return
        self._pixmap_item = self.scene.addPixmap(QPixmap.fromImage(image))
        self.scene.setSceneRect(self._pixmap_item.boundingRect())
        self._applyViewTransform()
        self._updateAutoPageTimer()
        QTimer.singleShot(
            0,
            lambda: self.graphicsView.verticalScrollBar().setValue(
                self.graphicsView.verticalScrollBar().minimum()
            ),
        )

    def _startMovie(self, index: int, page_image):
        if index >= self._pageCount():
            return
        if self._online_detail is not None:
            buffer = QBuffer(self)
            buffer.setData(page_image.data)
            if not buffer.open(QIODevice.ReadOnly):
                buffer.deleteLater()
                return
            movie = QMovie(buffer, b"gif", self)
            self._movie_buffer = buffer
        else:
            movie = QMovie(str(self._item.page_paths[index]), b"gif", self)
        movie.setCacheMode(QMovie.CacheNone)
        if not movie.isValid():
            movie.deleteLater()
            if self._movie_buffer is not None:
                self._movie_buffer.close()
                self._movie_buffer.deleteLater()
                self._movie_buffer = None
            return
        self._active_movie = movie
        self._movie_page_index = index
        movie.frameChanged.connect(
            lambda _frame_number, active_movie=movie: self._updateMovieFrame(
                active_movie
            )
        )
        movie.start()

    def _updateMovieFrame(self, movie: QMovie):
        if (
            movie is not self._active_movie
            or self._movie_page_index != self._page_index
            or self._pixmap_item is None
        ):
            return
        pixmap = movie.currentPixmap()
        if pixmap.isNull():
            return
        old_size = self._pixmap_item.boundingRect().size()
        self._pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(self._pixmap_item.boundingRect())
        if old_size != self._pixmap_item.boundingRect().size() and self._display_mode in (
            "fit_window",
            "fit_width",
        ):
            self._applyViewTransform()

    def _stopMovie(self):
        movie = self._active_movie
        self._active_movie = None
        self._movie_page_index = -1
        if movie is not None:
            movie.stop()
            # Release QMovie's Windows file handle immediately; deferred QObject
            # deletion can otherwise keep a removable/local file locked briefly.
            movie.setFileName("")
            movie.deleteLater()
        buffer = self._movie_buffer
        self._movie_buffer = None
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()

    def _applyViewTransform(self):
        if self._pixmap_item is None:
            return
        self.graphicsView.resetTransform()
        if self._display_mode == "fit_window":
            self.graphicsView.fitInView(self._pixmap_item, Qt.KeepAspectRatio)
            scale = self.graphicsView.transform().m11()
        elif self._display_mode == "fit_width":
            image_width = max(1.0, self._pixmap_item.boundingRect().width())
            viewport_width = max(1, self.graphicsView.viewport().width() - 4)
            scale = viewport_width / image_width
            self.graphicsView.scale(scale, scale)
        elif self._display_mode == "original":
            scale = 1.0
        else:
            self.graphicsView.scale(self._zoom_factor, self._zoom_factor)
            scale = self._zoom_factor
        self.zoomLabel.setText(f"{round(scale * 100)}%")

    def _applyBackgroundColor(self, color):
        self.scene.setBackgroundBrush(color)

    def _setImageLoadSize(self, mode: str):
        self._display_mode = mode
        self._zoom_factor = 1.0
        self._applyViewTransform()

    def _directionKeys(self):
        return {
            "left_to_right": (Qt.Key_Left, Qt.Key_Right, FIF.LEFT_ARROW, FIF.RIGHT_ARROW),
            "right_to_left": (Qt.Key_Right, Qt.Key_Left, FIF.RIGHT_ARROW, FIF.LEFT_ARROW),
            "top_to_bottom": (Qt.Key_Up, Qt.Key_Down, FIF.UP, FIF.DOWN),
            "bottom_to_top": (Qt.Key_Down, Qt.Key_Up, FIF.DOWN, FIF.UP),
        }[cfg.get(cfg.readerPageDirection)]

    def _updateDirectionControls(self, _value=None):
        next_key, previous_key, next_icon, previous_icon = self._directionKeys()
        self.nextButton.setIcon(next_icon)
        self.previousButton.setIcon(previous_icon)
        self.nextButton.setToolTip(
            self.tr("下一页 ({})").format(QKeySequence(next_key).toString())
        )
        self.previousButton.setToolTip(
            self.tr("上一页 ({})").format(QKeySequence(previous_key).toString())
        )

    def _updateAutoPageTimer(self, _value=None):
        enabled = cfg.get(cfg.readerAutoPageEnabled)
        self.autoPageButton.setIcon(FIF.PAUSE if enabled else FIF.PLAY)
        self.autoPageButton.setToolTip(
            self.tr("关闭自动翻页") if enabled else self.tr("开启自动翻页")
        )
        has_next_page = bool(
            self._pageCount()
            and (
                self._page_index + 1 < self._pageCount()
                or self._has_following_manga
            )
        )
        if enabled and self._reader_active and has_next_page:
            self._auto_page_timer.start(
                int(cfg.get(cfg.readerAutoPageInterval)) * 1000
            )
        else:
            self._auto_page_timer.stop()

    def _autoAdvance(self):
        if self._pageCount() and (
            self._page_index + 1 < self._pageCount()
            or self._has_following_manga
        ):
            self.nextPage()
        else:
            self._auto_page_timer.stop()

    def _jumpToPage(self, page: int):
        self.showPage(page - 1)

    def _updatePageIndicator(self, page_count: int):
        if page_count <= 0:
            text = self.tr("没有可阅读页面")
            self.pageIndicatorLabel.setText(text)
            self.fullscreenPageIndicatorLabel.setText(text)
            self._positionFullscreenPageIndicator()
            return
        text = self.tr("第 {} / {} 页").format(self._page_index + 1, page_count)
        self.pageIndicatorLabel.setText(text)
        self.fullscreenPageIndicatorLabel.setText(text)
        self._positionFullscreenPageIndicator()

    def _updateControls(self):
        page_count = self._pageCount()
        self.previousButton.setEnabled(
            self._page_index > 0 or self._has_previous_manga
        )
        self.nextButton.setEnabled(
            self._page_index + 1 < page_count or self._has_following_manga
        )
        self.pageSpinBox.setEnabled(page_count > 0)

    def _matchesScrollShortcut(self, event: QKeyEvent) -> bool:
        pressed = QKeySequence(event.keyCombination()).toString(
            QKeySequence.PortableText
        )
        return bool(pressed and pressed == cfg.get(cfg.readerScrollShortcut))

    def _isReaderKey(self, event: QKeyEvent) -> bool:
        next_key, previous_key, _next_icon, _previous_icon = self._directionKeys()
        if self._matchesScrollShortcut(event):
            return True
        if event.modifiers() & Qt.ControlModifier:
            return event.key() in (Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus)
        return event.key() in {
            Qt.Key_F11,
            Qt.Key_Escape,
            next_key,
            previous_key,
            Qt.Key_PageDown,
            Qt.Key_PageUp,
            Qt.Key_Backspace,
            Qt.Key_Home,
            Qt.Key_End,
        }

    def _handleReaderKey(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key_F11:
            self.toggleFullscreen()
            return True
        if event.key() == Qt.Key_Escape:
            if self._fullscreen:
                self.setFullscreenState(False, emit_request=True)
            else:
                self.backRequested.emit()
            return True

        if self._matchesScrollShortcut(event):
            self.scrollForward()
            return True

        next_key, previous_key, _next_icon, _previous_icon = self._directionKeys()
        if event.key() in (next_key, Qt.Key_PageDown):
            self.nextPage()
            return True
        if event.key() in (previous_key, Qt.Key_PageUp, Qt.Key_Backspace):
            self.previousPage()
            return True
        if event.key() == Qt.Key_Home:
            self.showPage(0)
            return True
        if event.key() == Qt.Key_End and self._pageCount():
            self.showPage(self._pageCount() - 1)
            return True
        if event.modifiers() & Qt.ControlModifier and event.key() in (
            Qt.Key_Plus,
            Qt.Key_Equal,
        ):
            self.zoomBy(1.25)
            return True
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_Minus:
            self.zoomBy(0.8)
            return True
        return False

    def _pageCount(self):
        if self._online_detail is not None:
            return max(0, int(self._online_detail.page_count))
        return len(self._item.page_paths) if self._item is not None else 0

    def eventFilter(self, watched, event):
        if self._fullscreen and event.type() == QEvent.MouseMove:
            self._updateFullscreenControlsForPointer(event)
        if watched is self.graphicsView.viewport() and event.type() == QEvent.Resize:
            self._positionFullscreenPageIndicator()
        if watched in (self.graphicsView, self.graphicsView.viewport()):
            if event.type() == QEvent.ShortcutOverride and self._isReaderKey(event):
                event.accept()
                return True
            if event.type() == QEvent.KeyPress and self._handleReaderKey(event):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent):
        if self._handleReaderKey(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._positionFullscreenPageIndicator()
        if self._display_mode in ("fit_window", "fit_width"):
            self._applyViewTransform()

    def showEvent(self, event):
        super().showEvent(event)
        self._reader_active = True
        if self._active_movie is not None:
            self._active_movie.setPaused(False)
        self._updateAutoPageTimer()

    def hideEvent(self, event):
        self._reader_active = False
        self._auto_page_timer.stop()
        if self._active_movie is not None:
            self._active_movie.setPaused(True)
        super().hideEvent(event)
