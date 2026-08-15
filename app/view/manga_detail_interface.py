from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFrame,
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
    FlowLayout,
    ProgressBar,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SpinBox,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.style_sheet import StyleSheet
from app.domain.manga import MangaItem
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreviewPage,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.sources.ehviewer_source import EhViewerDataSource
from app.view.local_manga_interface import CoverLabel
from app.workers.eh_online_worker import (
    OnlinePreviewPageWorker,
    OnlinePreviewThumbnailWorker,
)


TAG_GROUPS = (
    ("artist", "作者", "creator"),
    ("group", "社团", "creator"),
    ("cosplayer", "Cosplayer", "creator"),
    ("parody", "原作", "work"),
    ("character", "角色", "work"),
    ("language", "语言", "language"),
    ("female", "女性", "female"),
    ("male", "男性", "male"),
    ("mixed", "混合", "mixed"),
    ("reclass", "重新分类", "neutral"),
    ("misc", "杂项", "neutral"),
    ("other", "其他", "neutral"),
)
TAG_GROUP_INFO = {
    namespace: (title, tone) for namespace, title, tone in TAG_GROUPS
}


def group_manga_tags(tags):
    """Return ordered namespace groups without duplicated search-only tags."""
    grouped = {}
    grouped_keys = {}
    namespaced_values = set()
    plain_values = []
    for raw_tag in tags:
        tag = str(raw_tag).strip()
        if not tag:
            continue
        if ":" not in tag:
            plain_values.append(tag)
            continue
        namespace, value = tag.split(":", 1)
        namespace = namespace.strip().casefold()
        value = value.strip()
        if not namespace or not value:
            continue
        namespaced_values.add(value.casefold())
        key = value.casefold()
        if key in grouped_keys.setdefault(namespace, set()):
            continue
        grouped_keys[namespace].add(key)
        grouped.setdefault(namespace, []).append(value)

    seen_plain = set()
    for value in plain_values:
        key = value.casefold()
        if key in namespaced_values or key in seen_plain:
            continue
        seen_plain.add(key)
        grouped.setdefault("other", []).append(value)

    known_order = [namespace for namespace, _title, _tone in TAG_GROUPS]
    ordered_namespaces = [name for name in known_order if grouped.get(name)]
    ordered_namespaces.extend(
        sorted(name for name in grouped if name not in TAG_GROUP_INFO)
    )
    return tuple(
        (
            namespace,
            TAG_GROUP_INFO.get(namespace, (namespace.upper(), "neutral"))[0],
            TAG_GROUP_INFO.get(namespace, (namespace.upper(), "neutral"))[1],
            tuple(grouped[namespace]),
        )
        for namespace in ordered_namespaces
    )


def _enable_text_copy(label: QLabel):
    label.setTextInteractionFlags(
        Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
    )
    label.setFocusPolicy(Qt.ClickFocus)
    label.setCursor(Qt.IBeamCursor)


class TagChip(QLabel):
    """Theme-aware, selectable tag chip similar to Element's el-tag."""

    def __init__(self, text: str, namespace: str, tone: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("mangaTagChip")
        self.setProperty("tagTone", tone)
        self.setToolTip(f"{namespace}:{text}")
        _enable_text_copy(self)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class TagGroupWidget(QWidget):
    def __init__(self, title: str, namespace: str, tone: str, values, parent=None):
        super().__init__(parent)
        self.setObjectName("mangaTagGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        heading = CaptionLabel(title, self)
        heading.setObjectName("mangaTagGroupTitle")
        layout.addWidget(heading)
        chip_container = QWidget(self)
        chip_container.setObjectName("mangaTagChipContainer")
        chip_layout = FlowLayout(chip_container, isTight=True)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setHorizontalSpacing(8)
        chip_layout.setVerticalSpacing(7)
        for value in values:
            chip_layout.addWidget(TagChip(value, namespace, tone, chip_container))
        layout.addWidget(chip_container)


class OnlineCommentWidget(QWidget):
    """Selectable, unframed comment row used by the shared detail page."""

    def __init__(self, comment, parent=None):
        super().__init__(parent)
        self.setObjectName("onlineCommentRow")

        self.authorLabel = BodyLabel(comment.author or self.tr("匿名用户"), self)
        self.authorLabel.setObjectName("onlineCommentAuthor")
        _enable_text_copy(self.authorLabel)
        self.postedLabel = CaptionLabel(comment.posted or self.tr("时间未知"), self)
        self.postedLabel.setObjectName("onlineCommentMeta")
        _enable_text_copy(self.postedLabel)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self.authorLabel)
        if comment.is_uploader:
            uploader_label = CaptionLabel(self.tr("上传者"), self)
            uploader_label.setObjectName("onlineCommentUploaderBadge")
            header.addWidget(uploader_label)
        header.addStretch(1)
        if comment.score is not None:
            score_label = CaptionLabel(
                self.tr("评分 {:+d}").format(comment.score), self
            )
            score_label.setObjectName("onlineCommentScore")
            score_label.setProperty(
                "scoreTone", "positive" if comment.score >= 0 else "negative"
            )
            header.addWidget(score_label)

        self.bodyLabel = BodyLabel(comment.text or self.tr("（无文字内容）"), self)
        self.bodyLabel.setObjectName("onlineCommentBody")
        self.bodyLabel.setWordWrap(True)
        self.bodyLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        _enable_text_copy(self.bodyLabel)

        separator = QFrame(self)
        separator.setObjectName("onlineCommentSeparator")
        separator.setFrameShape(QFrame.HLine)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(7)
        layout.addLayout(header)
        layout.addWidget(self.postedLabel)
        layout.addWidget(self.bodyLabel)
        layout.addWidget(separator)


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
        self.imageLabel.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.imageLabel.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

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
    ONLINE_PREVIEW_PAGE_SIZE = 20

    backRequested = Signal()
    readRequested = Signal(object, int)
    onlineReadRequested = Signal(object, int)
    onlineDownloadRequested = Signal(object)
    localDownloadRequested = Signal(object)
    localMetadataSyncRequested = Signal(object)
    onlineDownloadCancelRequested = Signal(int)
    localMangaResolved = Signal(object)
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
        self._online_gallery: Optional[OnlineGallery] = None
        self._online_detail: Optional[OnlineGalleryDetail] = None
        self._online_provider = None
        self._online_cache = None
        self._online_preview_worker = None
        self._online_thumbnail_workers = set()
        self._online_download_active = False
        self._local_sync_active = False
        self.onlineThreadPool = QThreadPool(self)
        self.onlineThreadPool.setMaxThreadCount(6)
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
        self.originalTitleLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.originalTitleLabel)
        self.englishTitleLabel = BodyLabel("", self.infoCard)
        self.englishTitleLabel.setWordWrap(True)
        self.englishTitleLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.englishTitleLabel)
        self.metadataLabel = BodyLabel("", self.infoCard)
        self.metadataLabel.setWordWrap(True)
        self.metadataLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.metadataLabel)
        self.galleryVersionLabel = BodyLabel("", self.infoCard)
        self.galleryVersionLabel.setObjectName("galleryVersionStatus")
        self.galleryVersionLabel.setWordWrap(True)
        self.galleryVersionLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.galleryVersionLabel)
        self.galleryVersionLabel.hide()

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 2, 0, 2)
        text_layout.setSpacing(10)
        text_layout.addWidget(self.originalTitleLabel)
        text_layout.addWidget(self.englishTitleLabel)
        text_layout.addSpacing(4)
        text_layout.addWidget(self.metadataLabel)
        text_layout.addWidget(self.galleryVersionLabel)
        text_layout.addStretch(1)
        info_layout.addWidget(self.coverLabel, 0, Qt.AlignTop)
        info_layout.addLayout(text_layout, 1)

        self.tagCard = SimpleCardWidget(self)
        self.tagCard.setObjectName("mangaTagCard")
        self.tagCardLayout = QVBoxLayout(self.tagCard)
        self.tagCardLayout.setContentsMargins(18, 16, 18, 18)
        self.tagCardLayout.setSpacing(12)
        self.tagCardLayout.addWidget(SubtitleLabel(self.tr("标签"), self.tagCard))
        self.tagGroupsWidget = QWidget(self.tagCard)
        self.tagGroupsWidget.setObjectName("mangaTagGroups")
        self.tagGroupsLayout = QGridLayout(self.tagGroupsWidget)
        self.tagGroupsLayout.setContentsMargins(0, 0, 0, 0)
        self.tagGroupsLayout.setHorizontalSpacing(24)
        self.tagGroupsLayout.setVerticalSpacing(14)
        self.tagGroupsLayout.setColumnStretch(0, 1)
        self.tagGroupsLayout.setColumnStretch(1, 1)
        self.tagCardLayout.addWidget(self.tagGroupsWidget)

        self.commentsCard = SimpleCardWidget(self)
        self.commentsCard.setObjectName("onlineCommentsCard")
        comments_layout = QVBoxLayout(self.commentsCard)
        comments_layout.setContentsMargins(18, 16, 18, 18)
        comments_layout.setSpacing(12)
        comments_header = QHBoxLayout()
        comments_header.setContentsMargins(0, 0, 0, 0)
        self.commentsTitle = SubtitleLabel(self.tr("评论"), self.commentsCard)
        self.commentsCountLabel = CaptionLabel("", self.commentsCard)
        self.commentsCountLabel.setObjectName("onlineCommentsCount")
        comments_header.addWidget(self.commentsTitle)
        comments_header.addWidget(self.commentsCountLabel)
        comments_header.addStretch(1)
        comments_layout.addLayout(comments_header)
        self.commentsStatusLabel = BodyLabel("", self.commentsCard)
        self.commentsStatusLabel.setObjectName("onlineCommentsStatus")
        self.commentsStatusLabel.setWordWrap(True)
        _enable_text_copy(self.commentsStatusLabel)
        comments_layout.addWidget(self.commentsStatusLabel)
        self.commentsWidget = QWidget(self.commentsCard)
        self.commentsWidget.setObjectName("onlineCommentsList")
        self.commentsListLayout = QVBoxLayout(self.commentsWidget)
        self.commentsListLayout.setContentsMargins(0, 0, 0, 0)
        self.commentsListLayout.setSpacing(8)
        comments_layout.addWidget(self.commentsWidget)
        self.commentsCard.hide()

        self.operationCard = SimpleCardWidget(self)
        operation_layout = QHBoxLayout(self.operationCard)
        operation_layout.setContentsMargins(18, 14, 18, 14)
        operation_layout.setSpacing(10)
        operation_layout.addWidget(SubtitleLabel(self.tr("操作"), self.operationCard))
        operation_layout.addStretch(1)
        self.syncButton = PushButton(
            FIF.SYNC,
            self.tr("同步信息"),
            self.operationCard,
        )
        self.syncButton.clicked.connect(self._requestMetadataSync)
        operation_layout.addWidget(self.syncButton)

        self.downloadControls = QWidget(self.operationCard)
        self.downloadControls.setFixedWidth(170)
        download_layout = QVBoxLayout(self.downloadControls)
        download_layout.setContentsMargins(0, 0, 0, 0)
        download_layout.setSpacing(5)
        self.downloadButton = PushButton(
            FIF.DOWNLOAD,
            self.tr("下载画廊"),
            self.downloadControls,
        )
        self.downloadButton.setFixedWidth(170)
        self.downloadButton.clicked.connect(self._requestDownload)
        self.downloadProgressBar = ProgressBar(self.downloadControls)
        self.downloadProgressBar.setRange(0, 100)
        self.downloadProgressBar.setFixedWidth(170)
        self.downloadProgressLabel = CaptionLabel("", self.downloadControls)
        self.downloadProgressLabel.setMaximumWidth(170)
        self.downloadProgressLabel.setWordWrap(True)
        download_layout.addWidget(self.downloadButton)
        download_layout.addWidget(self.downloadProgressBar)
        download_layout.addWidget(self.downloadProgressLabel)
        operation_layout.addWidget(self.downloadControls)
        self.readButton = PrimaryPushButton(
            FIF.BOOK_SHELF,
            self.tr("开始阅读"),
            self.operationCard,
        )
        self.readButton.clicked.connect(self._requestRead)
        operation_layout.addWidget(self.readButton)
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.hide()
        self.syncButton.hide()

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
        content_layout.addWidget(self.tagCard)
        content_layout.addWidget(self.operationCard)
        content_layout.addWidget(self.previewCard)
        content_layout.addWidget(self.commentsCard)
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
        StyleSheet.MANGA_DETAIL_INTERFACE.apply(self)

    @property
    def currentItem(self) -> Optional[MangaItem]:
        return self._item

    @property
    def isOnlineGallery(self) -> bool:
        return self._online_gallery is not None

    @property
    def currentOnlineDetail(self):
        return self._online_detail

    def setSource(self, source: EhViewerDataSource):
        self.source = source
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None

    def setManga(self, item: MangaItem):
        self._cancelOnlineLoads()
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None
        self._item = item
        self._online_gallery = None
        self._online_detail = None
        self._online_provider = None
        self._online_cache = None
        self._online_download_active = False
        self._local_sync_active = False
        if item.metadata_synced:
            self.commentsCard.show()
            self._setComments(self.userRepository.online_gallery_comments(item.gid))
        else:
            self.commentsCard.hide()
        self.operationCard.show()
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.show()
        self.syncButton.show()
        self.syncButton.setEnabled(bool(item.gallery_token))
        self.syncButton.setText(self.tr("同步信息"))
        self.syncButton.setToolTip(
            "" if item.gallery_token else self.tr("正在从 .ehviewer 读取画廊标识")
        )
        self.downloadButton.setEnabled(bool(item.page_paths and item.page_tokens))
        self.downloadButton.setText(
            self.tr("正在检查下载状态…")
            if not item.page_paths else self._localDownloadButtonText(item)
        )
        self.previewCard.show()
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
        self._setGalleryVersionStatus(
            item.newer_gallery_urls,
            checked=item.metadata_synced,
        )
        self._setTags(item.tags)

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
            self.localMangaResolved.emit(item)
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

    def setOnlineLoading(
        self, item: OnlineGallery, provider=None, cache=None, cover_data=b""
    ):
        self.cancelLoads()
        self._item = None
        self._online_gallery = item
        self._online_detail = None
        self._online_provider = provider
        self._online_cache = cache
        self._online_download_active = False
        self._local_sync_active = False
        self.pageTitle.setText(self.tr("在线画廊详情"))
        self.originalTitleLabel.setText(item.title)
        self.englishTitleLabel.clear()
        self.englishTitleLabel.hide()
        self.metadataLabel.setText(self._onlineMetadataText(item))
        self.galleryVersionLabel.hide()
        self._setTags(item.tags)
        self._replaceCover(cover_data, loading=not bool(cover_data))
        self._clearPreview()
        self.operationCard.show()
        self.previewCard.show()
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.show()
        self.syncButton.hide()
        self.downloadButton.setEnabled(False)
        self.downloadButton.setText(self.tr("正在读取画廊信息…"))
        self.readButton.setEnabled(False)
        self.readButton.setText(self.tr("正在准备在线阅读…"))
        self.previewTitle.setText(self.tr("正在加载页面预览…"))
        self.commentsCard.show()
        self._clearComments()
        self.commentsCountLabel.clear()
        self.commentsStatusLabel.setText(self.tr("正在加载画廊详情与评论…"))
        self.commentsStatusLabel.show()
        self.scrollArea.verticalScrollBar().setValue(0)

    def setOnlineDetail(
        self, detail: OnlineGalleryDetail, cover_data=b"", provider=None, cache=None
    ):
        self._online_gallery = detail.gallery
        self._online_detail = detail
        if provider is not None:
            self._online_provider = provider
        if cache is not None:
            self._online_cache = cache
        self.originalTitleLabel.setText(detail.title)
        self.englishTitleLabel.setText(detail.secondary_title)
        self.englishTitleLabel.setVisible(bool(detail.secondary_title))
        self.metadataLabel.setText(self._onlineMetadataText(detail.gallery, detail))
        self._setGalleryVersionStatus(detail.newer_gallery_urls, checked=True)
        self._setTags(detail.tags)
        self._replaceCover(cover_data)
        self._setComments(detail.comments)
        self.operationCard.show()
        self.previewCard.show()
        self.downloadControls.show()
        self.downloadButton.setEnabled(detail.page_count > 0)
        self.downloadButton.setText(self.tr("下载画廊"))
        self.readButton.setEnabled(detail.page_count > 0)
        self.readButton.setText(
            self.tr("开始在线阅读")
            if detail.page_count
            else self.tr("没有可阅读页面")
        )
        if detail.page_count:
            self._loadOnlinePreviewPage(1)
        else:
            self._clearPreview()
            self.previewTitle.setText(self.tr("没有可预览页面"))

    def setOnlineError(self, message: str):
        if self._online_gallery is None:
            return
        self._clearComments()
        self.commentsCountLabel.clear()
        self.commentsStatusLabel.setText(
            self.tr("画廊详情或评论加载失败：{}").format(message)
        )
        self.commentsStatusLabel.show()

    def setOnlineDownloadState(
        self,
        state,
        completed_pages=0,
        page_count=0,
        message="",
    ):
        if self._online_gallery is None and self._item is None:
            return
        completed_pages = max(0, int(completed_pages or 0))
        fallback_count = (
            self._online_gallery.page_count
            if self._online_gallery is not None
            else self._item.page_count
        )
        page_count = max(0, int(page_count or fallback_count or 0))
        self.downloadControls.show()
        self._online_download_active = state in {"queued", "downloading"}
        if page_count:
            percent = min(100, round(completed_pages * 100 / page_count))
            self.downloadProgressBar.setValue(percent)
        else:
            self.downloadProgressBar.setValue(0)
        show_progress = bool(
            page_count
            and state in {"queued", "downloading", "paused", "failed"}
            and (completed_pages or self._online_download_active)
        )
        self.downloadProgressBar.setVisible(show_progress)
        self.downloadProgressLabel.setVisible(bool(message))
        self.downloadButton.setToolTip(str(message or ""))
        if message:
            self._setDownloadProgressMessage(message)

        if state == "completed":
            if self._item is not None:
                self.downloadButton.setText(self.tr("检查并补齐"))
                self.downloadButton.setEnabled(bool(self._item.page_tokens))
            else:
                self.downloadButton.setText(self.tr("已下载"))
                self.downloadButton.setEnabled(False)
            self.downloadProgressBar.hide()
            self.downloadProgressLabel.hide()
        elif self._online_download_active:
            self.downloadButton.setText(
                self.tr("取消等待 {} / {}").format(completed_pages, page_count)
                if state == "queued"
                else self.tr("暂停下载 {} / {}").format(
                    completed_pages, page_count
                )
            )
            self.downloadButton.setEnabled(True)
        elif state in {"paused", "failed"}:
            self.downloadButton.setText(
                self.tr("继续下载 {} / {}").format(completed_pages, page_count)
                if page_count else self.tr("继续下载")
            )
            self.downloadButton.setEnabled(page_count > 0)
        else:
            if self._item is not None:
                self.downloadButton.setText(self._localDownloadButtonText(self._item))
                self.downloadButton.setEnabled(bool(self._item.page_tokens))
            else:
                self.downloadButton.setText(self.tr("下载画廊"))
                self.downloadButton.setEnabled(page_count > 0)

    def setLocalSyncState(self, active, message=""):
        if self._item is None:
            return
        self._local_sync_active = bool(active)
        self.syncButton.show()
        self.syncButton.setEnabled(not self._local_sync_active)
        self.syncButton.setText(
            self.tr("正在同步…") if self._local_sync_active else self.tr("同步信息")
        )
        self.syncButton.setToolTip(str(message or ""))

    def applyLocalSyncedDetail(self, detail):
        if self._item is None or int(self._item.gid) != int(detail.gallery.gid):
            return None
        self._item = replace(
            self._item,
            english_title=detail.title or self._item.english_title,
            original_title=detail.secondary_title,
            tags=tuple(detail.tags),
            gallery_token=detail.gallery.token,
            source_site=(
                "exhentai"
                if "exhentai.org" in detail.gallery.url
                else "ehentai"
            ),
            posted=detail.posted,
            uploader=detail.uploader,
            rating=detail.rating,
            language=detail.language,
            file_size=detail.file_size,
            rating_count=detail.rating_count,
            visible=detail.visible,
            favorited=detail.favorited,
            parent_gallery=detail.parent_gallery,
            newer_gallery_urls=tuple(detail.newer_gallery_urls),
            metadata_synced=True,
        )
        self.originalTitleLabel.setText(self._item.display_title)
        self.englishTitleLabel.setText(self._item.secondary_title)
        self.englishTitleLabel.setVisible(bool(self._item.secondary_title))
        self.metadataLabel.setText(self._metadataText(self._item))
        self._setTags(self._item.tags)
        self._setComments(detail.comments)
        self.commentsCard.show()
        self._setGalleryVersionStatus(detail.newer_gallery_urls, checked=True)
        self.setLocalSyncState(False, self.tr("同步完成"))
        self.localMangaResolved.emit(self._item)
        return self._item

    def _setGalleryVersionStatus(self, newer_gallery_urls, checked=False):
        urls = tuple(newer_gallery_urls or ())
        if urls:
            self.galleryVersionLabel.setProperty("versionState", "outdated")
            self.galleryVersionLabel.setText(
                self.tr("版本状态：旧的父画廊，检测到 {} 个更新版本").format(
                    len(urls)
                )
            )
            self.galleryVersionLabel.setToolTip("\n".join(urls))
            self.galleryVersionLabel.show()
        elif checked:
            self.galleryVersionLabel.setProperty("versionState", "current")
            self.galleryVersionLabel.setText(
                self.tr("版本状态：未检测到更新版本")
            )
            self.galleryVersionLabel.setToolTip("")
            self.galleryVersionLabel.show()
        else:
            self.galleryVersionLabel.hide()
        self.galleryVersionLabel.style().unpolish(self.galleryVersionLabel)
        self.galleryVersionLabel.style().polish(self.galleryVersionLabel)

    def _setDownloadProgressMessage(self, message):
        message = str(message or "")
        self.downloadProgressLabel.setToolTip(message)
        self.downloadProgressLabel.setText(
            self.downloadProgressLabel.fontMetrics().elidedText(
                message, Qt.ElideRight, self.downloadProgressLabel.maximumWidth()
            )
        )

    def _requestDownload(self):
        if self._online_download_active:
            gid = (
                self._online_detail.gallery.gid
                if self._online_detail is not None else self._item.gid
            )
            self.onlineDownloadCancelRequested.emit(gid)
            return
        if self._online_detail is not None and self._online_detail.page_count:
            self.onlineDownloadRequested.emit(self._online_detail)
        elif self._item is not None and self._item.page_tokens:
            self.localDownloadRequested.emit(self._item)

    def _requestMetadataSync(self):
        if self._item is None or self._local_sync_active:
            return
        self.localMetadataSyncRequested.emit(self._item)

    def _localDownloadButtonText(self, item):
        if item.download_complete is False:
            return self.tr("继续下载")
        if item.download_complete is True:
            return self.tr("检查并补齐")
        return self.tr("无法补齐")

    def _onlineMetadataText(self, item, detail=None):
        category = detail.category if detail is not None else item.category
        uploader = detail.uploader if detail is not None else item.uploader
        posted = detail.posted if detail is not None else item.posted
        pages = detail.page_count if detail is not None else item.page_count
        rating = detail.rating if detail is not None else item.rating
        values = [
            self.tr("GID：{}").format(item.gid),
            self.tr("类别：{}").format(category or self.tr("未知")),
            self.tr("上传者：{}").format(uploader or self.tr("未知")),
            self.tr("发布时间：{}").format(posted or self.tr("未知")),
            self.tr("页数：{}").format(pages or self.tr("未知")),
            self.tr("评分：{}").format(
                f"{rating:.2f}" if rating is not None else self.tr("暂无")
            ),
        ]
        if detail is not None:
            values.extend(
                (
                    self.tr("评分人数：{}").format(detail.rating_count),
                    self.tr("语言：{}").format(detail.language or self.tr("未知")),
                    self.tr("文件大小：{}").format(detail.file_size or self.tr("未知")),
                    self.tr("可见性：{}").format(detail.visible or self.tr("未知")),
                    self.tr("收藏：{}").format(detail.favorited or self.tr("未知")),
                    self.tr("父画廊：{}").format(detail.parent_gallery or self.tr("无")),
                )
            )
        values.append(self.tr("地址：{}").format(item.url))
        return "\n".join(values)

    def _replaceCover(self, data=b"", loading=False):
        image = QImage.fromData(data) if data else QImage()
        old_cover = self.coverLabel
        self.coverLabel = CoverLabel(
            Path(), image=image, defer_load=loading, parent=self.infoCard
        )
        self.coverLabel.setFixedSize(220, 300)
        self.infoCard.layout().replaceWidget(old_cover, self.coverLabel)
        old_cover.deleteLater()
        self.currentCoverPath = None

    def _setComments(self, comments):
        self._clearComments()
        count = len(comments)
        self.commentsCountLabel.setText(self.tr("{} 条").format(count))
        if not comments:
            self.commentsStatusLabel.setText(self.tr("这个画廊暂时没有评论"))
            self.commentsStatusLabel.show()
            return
        self.commentsStatusLabel.hide()
        for comment in comments:
            self.commentsListLayout.addWidget(
                OnlineCommentWidget(comment, self.commentsWidget)
            )

    def _clearComments(self):
        while self.commentsListLayout.count():
            layout_item = self.commentsListLayout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

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
        if self._online_detail is not None:
            total = int(self._online_detail.page_count)
            return max(
                1,
                (total + self.ONLINE_PREVIEW_PAGE_SIZE - 1)
                // self.ONLINE_PREVIEW_PAGE_SIZE,
            )
        current_item = item or self._item
        total = len(current_item.page_paths) if current_item else 0
        return max(1, (total + self.PREVIEW_PAGE_SIZE - 1) // self.PREVIEW_PAGE_SIZE)

    def _setPreviewPage(self, page: int):
        if self._online_detail is not None:
            page = min(max(1, int(page)), self._previewPageCount())
            if page == self._preview_page and self._preview_tiles:
                return
            self._loadOnlinePreviewPage(page)
            return
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
            self.metadataLabel.setText(self._metadataText(item))
            self.downloadControls.show()
            self.downloadButton.setEnabled(bool(item.page_tokens))
            self.downloadButton.setText(self._localDownloadButtonText(item))
            self.syncButton.setEnabled(bool(item.gallery_token))
            self.syncButton.setToolTip(
                "" if item.gallery_token else self.tr("本地画廊缺少 gallery token")
            )
            self.localMangaResolved.emit(item)
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
        self._cancelOnlineLoads()

    def _requestRead(self):
        if self._online_detail is not None and self._online_detail.page_count:
            self.onlineReadRequested.emit(self._online_detail, 0)
            return
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
        playlists = "、".join(item.multiple_labels) or self.tr("无")
        taxonomy = "、".join(item.taxonomy_labels) or self.tr("无")
        values = [
            self.tr(
                "GID：{gid}\n分类：{primary}\n播放列表：{playlists}\n归类：{taxonomy}\n"
                "来源类别：{category}\n页数：{pages}\n阅读进度：{progress}\n目录：{folder}"
            ).format(
                gid=item.gid,
                primary=primary_label,
                playlists=playlists,
                taxonomy=taxonomy,
                category=item.category_name,
                pages=item.page_count or self.tr("读取中…"),
                progress=self._progressText(item),
                folder=item.folder,
            )
        ]
        if item.download_complete is not None:
            values.append(
                self.tr("已下载：{} / {} 页").format(
                    item.downloaded_page_count,
                    item.page_count,
                )
            )
            values.append(
                self.tr("下载状态：{}").format(
                    self.tr("完整")
                    if item.download_complete else self.tr("未完成")
                )
            )
        if item.uploader:
            values.append(self.tr("上传者：{}").format(item.uploader))
        if item.posted:
            values.append(self.tr("发布时间：{}").format(item.posted))
        if item.rating is not None:
            values.append(self.tr("评分：{:.2f}").format(item.rating))
        if item.rating_count:
            values.append(self.tr("评分人数：{}").format(item.rating_count))
        if item.language:
            values.append(self.tr("语言：{}").format(item.language))
        if item.file_size:
            values.append(self.tr("文件大小：{}").format(item.file_size))
        if item.visible:
            values.append(self.tr("可见性：{}").format(item.visible))
        if item.favorited:
            values.append(self.tr("收藏：{}").format(item.favorited))
        if item.parent_gallery:
            values.append(self.tr("父画廊：{}").format(item.parent_gallery))
        return "\n".join(values)

    def _setTags(self, tags):
        while self.tagGroupsLayout.count():
            layout_item = self.tagGroupsLayout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._tagGroupWidgets = []
        groups = group_manga_tags(tags)
        if not groups:
            empty_label = CaptionLabel(self.tr("暂无标签"), self.tagGroupsWidget)
            empty_label.setObjectName("mangaTagEmptyLabel")
            self.tagGroupsLayout.addWidget(empty_label, 0, 0, 1, 2)
            return
        for index, (namespace, title, tone, values) in enumerate(groups):
            group = TagGroupWidget(
                self.tr(title),
                namespace,
                tone,
                values,
                self.tagGroupsWidget,
            )
            group.setProperty("tagNamespace", namespace)
            self._tagGroupWidgets.append(group)
            self.tagGroupsLayout.addWidget(group, index // 2, index % 2)

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

    def _loadOnlinePreviewPage(self, page_number):
        if (
            self._online_detail is None
            or self._online_provider is None
            or self._online_cache is None
        ):
            self.previewTitle.setText(self.tr("在线预览暂不可用"))
            return
        self._cancelOnlineLoads()
        page_number = min(max(1, int(page_number)), self._previewPageCount())
        site = self._online_provider.settings.site
        gallery = self._online_detail.gallery
        page = self._online_cache.get_preview_page(site, gallery, page_number)
        if page is None and page_number == 1 and self._online_detail.previews:
            page = OnlineGalleryPreviewPage(
                gallery=gallery,
                page_number=1,
                page_count=self._previewPageCount(),
                items=self._online_detail.previews,
            )
            self._online_cache.put_preview_page(site, page)
        if page is not None:
            self._applyOnlinePreviewPage(page)
            return
        self._clearPreviewTiles()
        self._preview_page = page_number
        self.previewTitle.setText(
            self.tr("正在加载页面预览（第 {} / {} 页）…").format(
                page_number, self._previewPageCount()
            )
        )
        self._updatePreviewPagination(self._previewPageCount())
        worker = OnlinePreviewPageWorker(
            self._online_provider, gallery, page_number
        )
        worker.signals.loaded.connect(
            lambda page: self._finishOnlinePreviewPage(worker, page)
        )
        worker.signals.failed.connect(
            lambda message: self._failOnlinePreviewPage(worker, message)
        )
        self._online_preview_worker = worker
        self.onlineThreadPool.start(worker)

    def _finishOnlinePreviewPage(self, worker, page):
        if self._online_preview_worker is not worker or self._online_detail is None:
            return
        self._online_preview_worker = None
        site = self._online_provider.settings.site
        self._online_cache.put_preview_page(site, page)
        self._applyOnlinePreviewPage(page)

    def _failOnlinePreviewPage(self, worker, message):
        if self._online_preview_worker is not worker:
            return
        self._online_preview_worker = None
        self._clearPreviewTiles()
        self.previewTitle.setText(
            self.tr("页面预览加载失败：{}").format(message)
        )

    def _applyOnlinePreviewPage(self, page):
        if self._online_detail is None or page.gallery.gid != self._online_detail.gallery.gid:
            return
        self._clearPreviewTiles()
        self._preview_page = int(page.page_number)
        self._preview_tiles = [
            PreviewTile(preview.page_index, self.previewWidget)
            for preview in page.items
        ]
        for tile in self._preview_tiles:
            tile.clicked.connect(
                lambda page_index, detail=self._online_detail: self.onlineReadRequested.emit(
                    detail, page_index
                )
            )
        self.previewTitle.setText(
            self.tr("页面预览（共 {} 页，第 {} / {} 页）").format(
                self._online_detail.page_count,
                self._preview_page,
                self._previewPageCount(),
            )
        )
        self._updatePreviewPagination(self._previewPageCount())
        self._relayoutPreview()
        site = self._online_provider.settings.site
        gallery = self._online_detail.gallery
        tiles = {tile.pageIndex: tile for tile in self._preview_tiles}
        for preview in page.items:
            data = self._online_cache.get_preview_image(
                site, gallery, preview.page_index
            )
            if data:
                tiles[preview.page_index].setImage(QImage.fromData(data))
                continue
            if not preview.thumbnail_url:
                tiles[preview.page_index].imageLabel.setText(self.tr("无预览"))
                continue
            worker = OnlinePreviewThumbnailWorker(
                self._online_provider,
                gallery,
                preview,
                self._online_cache,
                site,
            )
            worker.signals.loaded.connect(
                lambda index, image, current_worker=worker: self._setOnlinePreviewImage(
                    current_worker, index, image
                )
            )
            worker.signals.finished.connect(
                lambda current_worker=worker: self._finishOnlineThumbnail(
                    current_worker
                )
            )
            self._online_thumbnail_workers.add(worker)
            self.onlineThreadPool.start(worker)

    def _setOnlinePreviewImage(self, worker, index, image):
        if worker not in self._online_thumbnail_workers:
            return
        for tile in self._preview_tiles:
            if tile.pageIndex == index:
                tile.setImage(image)
                break

    def _finishOnlineThumbnail(self, worker):
        self._online_thumbnail_workers.discard(worker)

    def _cancelOnlineLoads(self):
        if self._online_preview_worker is not None:
            self._online_preview_worker.cancelled = True
            self._online_preview_worker = None
        for worker in self._online_thumbnail_workers:
            worker.cancelled = True
        self._online_thumbnail_workers.clear()

    def waitForOnlineLoads(self, timeout=3000):
        self.onlineThreadPool.waitForDone(timeout)

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
