from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
    RoundMenu,
    ScrollArea,
    SegmentedWidget,
    SimpleCardWidget,
    SpinBox,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    TransparentToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.style_sheet import StyleSheet
from app.domain.manga import (
    MangaItem,
    local_page_path_map,
    local_page_slot_count,
    merge_downloaded_page_path,
)
from app.domain.online_download import (
    ORIGINAL_PAGE_MODE_BASE,
    ORIGINAL_PAGE_MODE_ORIGINAL,
    ORIGINAL_STATE_ACTIVE,
    ORIGINAL_STATE_CLEANING,
    ORIGINAL_STATE_DOWNLOADING,
    ORIGINAL_STATE_FAILED,
    ORIGINAL_STATE_PAUSED,
    ORIGINAL_STATE_QUEUED,
    ORIGINAL_STATE_REPLACING_BASE,
    ORIGINAL_STATE_REPLACING_ORIGINAL,
    ORIGINAL_STATE_STAGED,
)
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


def _translated_tag_value(tag_search_index, namespace: str, raw_value: str) -> str:
    if tag_search_index is None:
        return raw_value
    translated = str(
        tag_search_index.translated_name(namespace, raw_value) or ""
    ).strip()
    return translated or raw_value


class TagChip(QLabel):
    """Theme-aware, selectable tag chip similar to Element's el-tag."""

    def __init__(
        self,
        text: str,
        namespace: str,
        tone: str,
        parent=None,
        raw_text=None,
    ):
        super().__init__(text, parent)
        raw_tag = str(text if raw_text is None else raw_text)
        self.setObjectName("mangaTagChip")
        self.setProperty("tagTone", tone)
        self.setProperty("tagNamespace", namespace)
        self.setProperty("rawTag", raw_tag)
        self.setToolTip(f"{namespace}:{raw_tag}")
        _enable_text_copy(self)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class GalleryQualityBadge(QLabel):
    def __init__(self, text: str, tone: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("galleryQualityBadge")
        self.setProperty("qualityTone", tone)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class TagGroupWidget(QWidget):
    def __init__(
        self,
        title: str,
        namespace: str,
        tone: str,
        values,
        parent=None,
        tag_search_index=None,
    ):
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
            chip_layout.addWidget(
                TagChip(
                    _translated_tag_value(tag_search_index, namespace, value),
                    namespace,
                    tone,
                    chip_container,
                    raw_text=value,
                )
            )
        layout.addWidget(chip_container)


class OnlineCommentWidget(QWidget):
    """Selectable, unframed comment row used by the shared detail page."""

    galleryLinkActivated = Signal(object)

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
        if comment.gallery_links:
            links_widget = QWidget(self)
            links_widget.setObjectName("onlineCommentGalleryLinks")
            links_layout = FlowLayout(links_widget, isTight=True)
            links_layout.setContentsMargins(0, 0, 0, 0)
            links_layout.setHorizontalSpacing(8)
            links_layout.setVerticalSpacing(7)
            for link in comment.gallery_links:
                label = str(link.text or "").strip()
                if not label or "://" in label:
                    label = self.tr("GID {}").format(link.gid)
                else:
                    if len(label) > 42:
                        label = label[:39] + "..."
                    label = self.tr("{} · GID {}").format(label, link.gid)
                button = PushButton(FIF.LINK, label, links_widget)
                button.setObjectName("onlineCommentGalleryLink")
                button.setToolTip(
                    self.tr("在应用内打开画廊 GID {}").format(link.gid)
                )
                button.clicked.connect(
                    lambda _checked=False, target=link: (
                        self.galleryLinkActivated.emit(target)
                    )
                )
                links_layout.addWidget(button)
            layout.addWidget(links_widget)
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

    def __init__(self, indexed_page_paths):
        super().__init__()
        self.indexedPagePaths = tuple(indexed_page_paths)
        self.signals = PreviewLoadSignals()
        self.cancelled = False

    def run(self):
        for index, path in self.indexedPagePaths:
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
            original_state = self.user_repository.gallery_original_state(item.gid)
            original_paths = ()
            if original_state is not None and original_state.state in {
                ORIGINAL_STATE_STAGED,
                ORIGINAL_STATE_REPLACING_BASE,
                ORIGINAL_STATE_REPLACING_ORIGINAL,
            }:
                original_paths = self.source.list_page_files(item.folder / "original")
            item = replace(
                item,
                original_mode=original_state.mode if original_state else "",
                original_state=original_state.state if original_state else "",
                original_page_paths=original_paths,
                original_completed_pages=(
                    original_state.completed_pages if original_state else 0
                ),
                original_fallback_to_standard=bool(
                    original_state is not None
                    and original_state.fallback_to_standard
                ),
                original_page_modes=(
                    original_state.page_modes if original_state is not None else ()
                ),
            )
            progress = self.user_repository.resolve_progress(
                item.gid,
                self.source.read_ehviewer_progress(item),
            )
            if progress is not None and item.page_paths:
                clamped_progress = min(progress, local_page_slot_count(item) - 1)
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
    onlineOriginalDownloadRequested = Signal(object)
    localOriginalDownloadRequested = Signal(object)
    originalReplaceRequested = Signal(object)
    compressedCleanupRequested = Signal(object)
    localMetadataSyncRequested = Signal(object)
    galleryUpdateRequested = Signal(object)
    folderOpenRequested = Signal(object)
    onlineGalleryLinkRequested = Signal(object)
    onlineDownloadCancelRequested = Signal(int)
    localMangaResolved = Signal(object)
    progressResolved = Signal(int, int, int)
    readingRecordClearRequested = Signal(int)
    selectedTitleSearchRequested = Signal(int, str)
    selectedTitleOnlineSearchRequested = Signal(str)
    categorySelectionRequested = Signal(object)
    similarResultsRequested = Signal()

    def __init__(
        self,
        source: EhViewerDataSource,
        user_repository: UserLibraryRepository,
        parent=None,
        tag_search_index=None,
    ):
        super().__init__(parent)
        self.source = source
        self.userRepository = user_repository
        self.tagSearchIndex = tag_search_index
        self.setObjectName("mangaDetailInterface")
        self._item: Optional[MangaItem] = None
        self._preview_tiles: List[PreviewTile] = []
        self._preview_columns = 0
        self._preview_page = 1
        self._preview_worker: Optional[PreviewLoadWorker] = None
        self._page_worker: Optional[PageDiscoveryWorker] = None
        self._online_gallery: Optional[OnlineGallery] = None
        self._online_detail: Optional[OnlineGalleryDetail] = None
        self._folder_open_item = None
        self._online_provider = None
        self._online_cache = None
        self._online_preview_worker = None
        self._local_online_detail = None
        self._local_online_provider = None
        self._local_online_cache = None
        self._local_preview_page_workers = set()
        self._online_thumbnail_workers = set()
        self._preview_patch_workers = set()
        self._online_download_active = False
        self._original_download_active = False
        self._original_operation_active = False
        self._preview_source = "standard"
        self._local_sync_active = False
        self._gallery_update_locked = False
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
        self.fullOriginalBadge = GalleryQualityBadge(
            "ORIGINAL", "full", self
        )
        self.originalCountBadge = GalleryQualityBadge(
            "0 ORIGINAL", "original", self
        )
        self.baseCountBadge = GalleryQualityBadge("0 BASE", "base", self)
        for badge in (
            self.fullOriginalBadge,
            self.originalCountBadge,
            self.baseCountBadge,
        ):
            badge.hide()
            header_layout.addWidget(badge)

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
        self.originalTitleLabel.setContextMenuPolicy(Qt.CustomContextMenu)
        self.originalTitleLabel.customContextMenuRequested.connect(
            lambda position: self._showTitleContextMenu(
                self.originalTitleLabel, position
            )
        )
        self.englishTitleLabel = BodyLabel("", self.infoCard)
        self.englishTitleLabel.setWordWrap(True)
        self.englishTitleLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.englishTitleLabel)
        self.englishTitleLabel.setContextMenuPolicy(Qt.CustomContextMenu)
        self.englishTitleLabel.customContextMenuRequested.connect(
            lambda position: self._showTitleContextMenu(
                self.englishTitleLabel, position
            )
        )
        self.metadataLabel = BodyLabel("", self.infoCard)
        self.metadataLabel.setWordWrap(True)
        self.metadataLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.metadataLabel)
        self.detailMetadataButton = PushButton(
            FIF.VIEW,
            self.tr("查看详细"),
            self.infoCard,
        )
        self.detailMetadataButton.clicked.connect(self._toggleDetailedMetadata)
        self.similarResultsButton = PushButton(
            FIF.SEARCH,
            self.tr("无相似画廊"),
            self.infoCard,
        )
        self.similarResultsButton.clicked.connect(self.similarResultsRequested)
        self.similarResultsButton.hide()
        self.keyTagsWidget = QWidget(self.infoCard)
        self.keyTagsWidget.setObjectName("mangaKeyTags")
        self.keyTagsLayout = FlowLayout(self.keyTagsWidget, isTight=True)
        self.keyTagsLayout.setContentsMargins(0, 0, 0, 0)
        self.keyTagsLayout.setHorizontalSpacing(8)
        self.keyTagsLayout.setVerticalSpacing(7)
        self.keyTagsWidget.hide()
        self.detailMetadataLabel = BodyLabel("", self.infoCard)
        self.detailMetadataLabel.setWordWrap(True)
        self.detailMetadataLabel.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred
        )
        _enable_text_copy(self.detailMetadataLabel)
        self.detailMetadataLabel.hide()
        self.detailMetadataButton.hide()
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
        text_layout.addWidget(self.keyTagsWidget)
        text_layout.addSpacing(4)
        text_layout.addWidget(self.metadataLabel)
        metadata_actions = QHBoxLayout()
        metadata_actions.setContentsMargins(0, 0, 0, 0)
        metadata_actions.setSpacing(8)
        metadata_actions.addWidget(self.detailMetadataButton)
        metadata_actions.addWidget(self.similarResultsButton)
        metadata_actions.addStretch(1)
        text_layout.addLayout(metadata_actions)
        text_layout.addWidget(self.detailMetadataLabel)
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
        operation_layout = QVBoxLayout(self.operationCard)
        operation_layout.setContentsMargins(18, 14, 18, 14)
        operation_layout.setSpacing(10)
        operation_layout.addWidget(SubtitleLabel(self.tr("操作"), self.operationCard))
        self.actionWidget = QWidget(self.operationCard)
        action_layout = FlowLayout(self.actionWidget, isTight=True)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setHorizontalSpacing(10)
        action_layout.setVerticalSpacing(10)
        self.updateButton = PushButton(
            FIF.SYNC,
            self.tr("更新到最新"),
            self.operationCard,
        )
        self.updateButton.clicked.connect(self._requestGalleryUpdate)
        action_layout.addWidget(self.updateButton)
        self.syncButton = PushButton(
            FIF.SYNC,
            self.tr("同步信息"),
            self.operationCard,
        )
        self.syncButton.clicked.connect(self._requestMetadataSync)
        action_layout.addWidget(self.syncButton)
        self.openFolderButton = PushButton(
            FIF.FOLDER,
            self.tr("在资源管理器中打开"),
            self.operationCard,
        )
        self.openFolderButton.clicked.connect(self._requestOpenFolder)
        action_layout.addWidget(self.openFolderButton)
        self.categoryButton = PushButton(
            FIF.TAG,
            self.tr("选择分类"),
            self.operationCard,
        )
        self.categoryButton.clicked.connect(self._requestCategorySelection)
        action_layout.addWidget(self.categoryButton)

        self.downloadControls = QWidget(self.operationCard)
        self.downloadControls.setFixedWidth(160)
        download_layout = QVBoxLayout(self.downloadControls)
        download_layout.setContentsMargins(0, 0, 0, 0)
        download_layout.setSpacing(5)
        self.downloadButton = PushButton(
            FIF.DOWNLOAD,
            self.tr("下载画廊"),
            self.downloadControls,
        )
        self.downloadButton.setFixedWidth(160)
        self.downloadButton.clicked.connect(self._requestDownload)
        self.downloadProgressBar = ProgressBar(self.downloadControls)
        self.downloadProgressBar.setRange(0, 100)
        self.downloadProgressBar.setFixedWidth(160)
        self.downloadProgressLabel = CaptionLabel("", self.downloadControls)
        self.downloadProgressLabel.setMaximumWidth(160)
        self.downloadProgressLabel.setWordWrap(True)
        download_layout.addWidget(self.downloadButton)
        download_layout.addWidget(self.downloadProgressBar)
        download_layout.addWidget(self.downloadProgressLabel)
        action_layout.addWidget(self.downloadControls)

        self.originalDownloadControls = QWidget(self.operationCard)
        self.originalDownloadControls.setFixedWidth(160)
        original_download_layout = QVBoxLayout(self.originalDownloadControls)
        original_download_layout.setContentsMargins(0, 0, 0, 0)
        original_download_layout.setSpacing(5)
        self.originalDownloadButton = PushButton(
            FIF.DOWNLOAD,
            self.tr("下载原图"),
            self.originalDownloadControls,
        )
        self.originalDownloadButton.setFixedWidth(160)
        self.originalDownloadButton.clicked.connect(self._requestOriginalDownload)
        self.originalDownloadProgressBar = ProgressBar(self.originalDownloadControls)
        self.originalDownloadProgressBar.setRange(0, 100)
        self.originalDownloadProgressBar.setFixedWidth(160)
        self.originalDownloadProgressLabel = CaptionLabel(
            "", self.originalDownloadControls
        )
        self.originalDownloadProgressLabel.setMaximumWidth(160)
        self.originalDownloadProgressLabel.setWordWrap(True)
        original_download_layout.addWidget(self.originalDownloadButton)
        original_download_layout.addWidget(self.originalDownloadProgressBar)
        original_download_layout.addWidget(self.originalDownloadProgressLabel)
        action_layout.addWidget(self.originalDownloadControls)

        self.originalReplaceButton = PushButton(
            FIF.SYNC,
            self.tr("原图替换"),
            self.operationCard,
        )
        self.originalReplaceButton.clicked.connect(self._requestOriginalReplace)
        action_layout.addWidget(self.originalReplaceButton)
        self.deleteCompressedButton = PushButton(
            FIF.DELETE,
            self.tr("删除压缩图"),
            self.operationCard,
        )
        self.deleteCompressedButton.clicked.connect(self._requestCompressedCleanup)
        action_layout.addWidget(self.deleteCompressedButton)
        self.readButton = PrimaryPushButton(
            FIF.BOOK_SHELF,
            self.tr("开始阅读"),
            self.operationCard,
        )
        self.readButton.clicked.connect(self._requestRead)
        action_layout.addWidget(self.readButton)
        self.clearProgressButton = PushButton(
            FIF.DELETE,
            self.tr("清空阅读记录"),
            self.operationCard,
        )
        self.clearProgressButton.clicked.connect(self._requestClearProgress)
        action_layout.addWidget(self.clearProgressButton)
        operation_layout.addWidget(self.actionWidget)
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.hide()
        self.originalDownloadProgressLabel.hide()
        self.originalDownloadProgressBar.hide()
        self.originalDownloadControls.hide()
        self.originalReplaceButton.hide()
        self.deleteCompressedButton.hide()
        self.syncButton.hide()
        self.updateButton.hide()
        self.openFolderButton.hide()
        self.categoryButton.hide()
        self.clearProgressButton.hide()

        self.previewCard = SimpleCardWidget(self)
        preview_layout = QVBoxLayout(self.previewCard)
        preview_layout.setContentsMargins(18, 16, 18, 18)
        preview_layout.setSpacing(12)
        self.previewTitle = SubtitleLabel(self.tr("页面预览"), self.previewCard)
        preview_header = QHBoxLayout()
        preview_header.setContentsMargins(0, 0, 0, 0)
        preview_header.addWidget(self.previewTitle)
        preview_header.addStretch(1)
        self.previewSourceSwitch = SegmentedWidget(self.previewCard)
        self.previewSourceSwitch.addItem(
            "standard",
            self.tr("标准画廊"),
            lambda: self._setPreviewSource("standard"),
        )
        self.previewSourceSwitch.addItem(
            "original",
            self.tr("原图画廊"),
            lambda: self._setPreviewSource("original"),
        )
        self.previewSourceSwitch.setCurrentItem("standard")
        self.previewSourceSwitch.hide()
        preview_header.addWidget(self.previewSourceSwitch)
        preview_layout.addLayout(preview_header)
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
        reset_metadata_details = (
            self._item is None
            or int(self._item.gid) != int(item.gid)
            or self._online_gallery is not None
        )
        self._cancelOnlineLoads()
        for worker in self._preview_patch_workers:
            worker.cancelled = True
        self._preview_patch_workers.clear()
        if self._page_worker is not None:
            self._page_worker.cancelled = True
            self._page_worker = None
        self._item = item
        self._folder_open_item = item
        self._online_gallery = None
        self._online_detail = None
        self._online_provider = None
        self._online_cache = None
        self._local_online_detail = None
        self._local_online_provider = None
        self._local_online_cache = None
        self._online_download_active = False
        self.setSimilarSearchRecord(None)
        self._original_download_active = False
        self._original_operation_active = False
        self._preview_source = "standard"
        self.previewSourceSwitch.setCurrentItem("standard")
        self.previewSourceSwitch.hide()
        self._local_sync_active = False
        self._gallery_update_locked = False
        self._updateOriginalQualityBadges(
            item.original_page_modes,
            item.page_count,
            item.original_state,
        )
        if item.metadata_synced:
            self.commentsCard.show()
            self._setComments(self.userRepository.online_gallery_comments(item.gid))
        else:
            self.commentsCard.hide()
        self.operationCard.show()
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.show()
        self.originalDownloadControls.show()
        self.originalDownloadProgressBar.hide()
        self.originalDownloadProgressLabel.hide()
        self.originalDownloadButton.setEnabled(bool(item.gallery_token))
        self.originalDownloadButton.setText(self.tr("下载原图"))
        self.originalReplaceButton.hide()
        self.deleteCompressedButton.hide()
        self.syncButton.show()
        self.openFolderButton.show()
        self.categoryButton.show()
        self.clearProgressButton.setVisible(
            item.progress_page_index is not None or item.reading_completed
        )
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
        self._setLocalMetadata(item, reset_details=reset_metadata_details)
        self._setGalleryVersionStatus(
            item.newer_gallery_urls,
            checked=item.metadata_synced,
        )
        self.updateButton.setVisible(bool(item.newer_gallery_urls))
        self.updateButton.setEnabled(bool(item.gallery_token))
        self._setTags(item.tags)
        self._setKeyTags(item.tags)

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
        self._folder_open_item = None
        self._local_online_detail = None
        self._local_online_provider = None
        self._local_online_cache = None
        self._online_gallery = item
        self._online_detail = None
        self._online_provider = provider
        self._online_cache = cache
        self._online_download_active = False
        self.setSimilarSearchRecord(None)
        self._original_download_active = False
        self._original_operation_active = False
        self._preview_source = "standard"
        self.previewSourceSwitch.setCurrentItem("standard")
        self.previewSourceSwitch.hide()
        self._local_sync_active = False
        self._updateOriginalQualityBadges()
        self.pageTitle.setText(self.tr("在线画廊详情"))
        self.originalTitleLabel.setText(item.title)
        self.englishTitleLabel.clear()
        self.englishTitleLabel.hide()
        self._setOnlineMetadata(item, reset_details=True)
        self.galleryVersionLabel.hide()
        self._setTags(item.tags)
        self._setKeyTags(item.tags)
        self._replaceCover(cover_data, loading=not bool(cover_data))
        self._clearPreview()
        self.operationCard.show()
        self.previewCard.show()
        self.downloadProgressLabel.hide()
        self.downloadProgressBar.hide()
        self.downloadControls.show()
        self.originalDownloadControls.show()
        self.originalDownloadProgressBar.hide()
        self.originalDownloadProgressLabel.hide()
        self.originalDownloadButton.setEnabled(False)
        self.originalDownloadButton.setText(self.tr("正在读取画廊信息…"))
        self.originalReplaceButton.hide()
        self.deleteCompressedButton.hide()
        self.syncButton.hide()
        self.updateButton.hide()
        self.openFolderButton.hide()
        self.categoryButton.hide()
        self.clearProgressButton.hide()
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

    def setLocalOnlineContext(self, detail, provider, cache):
        if (
            self._item is None
            or self._online_detail is not None
            or int(detail.gallery.gid) != int(self._item.gid)
        ):
            return
        self._local_online_detail = detail
        self._local_online_provider = provider
        self._local_online_cache = cache
        self._loadMissingLocalPreviews()

    def setOnlineDetail(
        self, detail: OnlineGalleryDetail, cover_data=b"", provider=None, cache=None
    ):
        self.categoryButton.hide()
        self._online_gallery = detail.gallery
        self._online_detail = detail
        if provider is not None:
            self._online_provider = provider
        if cache is not None:
            self._online_cache = cache
        self.originalTitleLabel.setText(detail.title)
        self.englishTitleLabel.setText(detail.secondary_title)
        self.englishTitleLabel.setVisible(bool(detail.secondary_title))
        self._setOnlineMetadata(detail.gallery, detail)
        self._setGalleryVersionStatus(detail.newer_gallery_urls, checked=True)
        self.updateButton.setVisible(bool(detail.newer_gallery_urls))
        self.updateButton.setEnabled(bool(detail.newer_gallery_urls))
        self._setTags(detail.tags)
        self._setKeyTags(detail.tags)
        self._replaceCover(cover_data)
        self._setComments(detail.comments)
        self.operationCard.show()
        self.previewCard.show()
        self.downloadControls.show()
        self.originalDownloadControls.show()
        self.downloadButton.setEnabled(detail.page_count > 0)
        self.downloadButton.setText(self.tr("下载画廊"))
        self.originalDownloadButton.setEnabled(detail.page_count > 0)
        self.originalDownloadButton.setText(self.tr("下载原图"))
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

    def setOriginalDownloadState(
        self,
        record=None,
        active=False,
        message="",
        has_compressed_backup=False,
        operation_active=False,
    ):
        if self._online_gallery is None and self._item is None:
            return
        self.originalDownloadControls.show()
        self._original_operation_active = bool(operation_active)
        state = str(record.state) if record is not None else "idle"
        completed = max(0, int(record.completed_pages)) if record else 0
        total = max(0, int(record.page_count)) if record else 0
        self._original_download_active = bool(
            active and state in {ORIGINAL_STATE_QUEUED, ORIGINAL_STATE_DOWNLOADING}
        )
        if record is not None and self._item is not None:
            self._item = replace(
                self._item,
                original_mode=record.mode,
                original_state=state,
                original_completed_pages=completed,
                original_fallback_to_standard=bool(
                    record.fallback_to_standard
                ),
                original_page_modes=record.page_modes,
            )

        progress_states = {
            ORIGINAL_STATE_QUEUED,
            ORIGINAL_STATE_DOWNLOADING,
            ORIGINAL_STATE_PAUSED,
            ORIGINAL_STATE_FAILED,
            ORIGINAL_STATE_STAGED,
        }
        self.originalDownloadProgressBar.setVisible(bool(total and state in progress_states))
        self.originalDownloadProgressBar.setValue(
            min(100, round(completed * 100 / total)) if total else 0
        )
        fallback_to_standard = bool(
            record is not None and record.fallback_to_standard
        )
        self._updateOriginalQualityBadges(
            record.page_modes if record is not None else (),
            total,
            state,
        )
        original_count = record.original_page_count if record is not None else 0
        base_count = record.base_page_count if record is not None else 0
        status_message = str(
            message
            or (record.error if record else "")
            or (
                self.tr("混合画廊：{} 张原图，{} 张基础图").format(
                    original_count, base_count
                )
                if fallback_to_standard
                else ""
            )
        )
        self.originalDownloadProgressLabel.setVisible(bool(status_message))
        self.originalDownloadProgressLabel.setToolTip(status_message)
        self.originalDownloadProgressLabel.setText(
            self.originalDownloadProgressLabel.fontMetrics().elidedText(
                status_message,
                Qt.ElideRight,
                self.originalDownloadProgressLabel.maximumWidth(),
            )
        )
        self.originalDownloadButton.setToolTip(status_message)

        if self._original_download_active:
            self.originalDownloadButton.setText(
                self.tr("取消等待原图 {} / {}").format(completed, total)
                if state == ORIGINAL_STATE_QUEUED
                else self.tr("暂停原图下载 {} / {}").format(completed, total)
            )
            self.originalDownloadButton.setEnabled(True)
        elif state in {ORIGINAL_STATE_PAUSED, ORIGINAL_STATE_FAILED}:
            self.originalDownloadButton.setText(
                self.tr("继续下载原图 {} / {}").format(completed, total)
            )
            self.originalDownloadButton.setEnabled(total > 0)
        elif state == ORIGINAL_STATE_STAGED:
            self.originalDownloadButton.setText(
                self.tr("原图已下载 {} / {}").format(completed, total)
            )
            self.originalDownloadButton.setEnabled(False)
        elif state in {
            ORIGINAL_STATE_REPLACING_BASE,
            ORIGINAL_STATE_REPLACING_ORIGINAL,
            ORIGINAL_STATE_CLEANING,
        }:
            self.originalDownloadButton.setText(self.tr("原图文件处理中"))
            self.originalDownloadButton.setEnabled(False)
        elif state == ORIGINAL_STATE_ACTIVE:
            self.originalDownloadButton.setText(
                self.tr("已是混合原图画廊")
                if fallback_to_standard
                else self.tr("已是原图画廊")
            )
            self.originalDownloadButton.setEnabled(False)
            self.downloadButton.setText(
                self.tr("已使用混合原图")
                if fallback_to_standard
                else self.tr("已使用原图")
            )
            self.downloadButton.setEnabled(False)
        else:
            self.originalDownloadButton.setText(self.tr("下载原图"))
            page_count = (
                self._online_detail.page_count
                if self._online_detail is not None
                else self._item.page_count
            )
            self.originalDownloadButton.setEnabled(bool(page_count))

        can_replace = (
            self._item is not None
            and state in {
                ORIGINAL_STATE_STAGED,
                ORIGINAL_STATE_REPLACING_BASE,
                ORIGINAL_STATE_REPLACING_ORIGINAL,
            }
        )
        self.originalReplaceButton.setVisible(can_replace)
        self.originalReplaceButton.setEnabled(can_replace and not operation_active)
        self.originalReplaceButton.setText(
            self.tr("继续原图替换")
            if state in {
                ORIGINAL_STATE_REPLACING_BASE,
                ORIGINAL_STATE_REPLACING_ORIGINAL,
            }
            else self.tr("原图替换")
        )
        can_cleanup = self._item is not None and (
            bool(has_compressed_backup) or state == ORIGINAL_STATE_CLEANING
        )
        self.deleteCompressedButton.setVisible(can_cleanup)
        self.deleteCompressedButton.setEnabled(can_cleanup and not operation_active)
        self.deleteCompressedButton.setText(
            self.tr("继续删除压缩图")
            if state == ORIGINAL_STATE_CLEANING else self.tr("删除压缩图")
        )
        self._updatePreviewSourceVisibility()

    def _updateOriginalQualityBadges(
        self, page_modes=(), page_count=0, state=""
    ):
        modes = tuple(page_modes or ())
        total = max(0, int(page_count or 0))
        original_count = modes.count(ORIGINAL_PAGE_MODE_ORIGINAL)
        base_count = modes.count(ORIGINAL_PAGE_MODE_BASE)
        complete = bool(total and original_count + base_count == total)
        show_full = complete and original_count == total and base_count == 0
        show_mixed = base_count > 0
        self.fullOriginalBadge.setVisible(show_full)
        self.originalCountBadge.setVisible(show_mixed)
        self.baseCountBadge.setVisible(show_mixed)
        if show_full:
            self.fullOriginalBadge.setToolTip(
                self.tr("全部 {} 页均为原图").format(total)
            )
        if show_mixed:
            self.originalCountBadge.setText(f"{original_count} ORIGINAL")
            self.baseCountBadge.setText(f"{base_count} BASE")
            tooltip = self.tr("原图下载画廊：{} 张原图，{} 张基础图").format(
                original_count, base_count
            )
            self.originalCountBadge.setToolTip(tooltip)
            self.baseCountBadge.setToolTip(tooltip)

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
        self._setLocalMetadata(self._item)
        self._setTags(self._item.tags)
        self._setKeyTags(self._item.tags)
        self._setComments(detail.comments)
        self.commentsCard.show()
        self._setGalleryVersionStatus(detail.newer_gallery_urls, checked=True)
        self.updateButton.setVisible(bool(detail.newer_gallery_urls))
        self.updateButton.setEnabled(bool(detail.newer_gallery_urls))
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
        if self._gallery_update_locked:
            return
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

    def _requestOriginalDownload(self):
        if self._gallery_update_locked or self._original_operation_active:
            return
        if self._original_download_active:
            gid = (
                self._online_detail.gallery.gid
                if self._online_detail is not None else self._item.gid
            )
            self.onlineDownloadCancelRequested.emit(gid)
            return
        if self._online_detail is not None and self._online_detail.page_count:
            self.onlineOriginalDownloadRequested.emit(self._online_detail)
        elif self._item is not None and self._item.page_tokens:
            self.localOriginalDownloadRequested.emit(self._item)

    def _requestOriginalReplace(self):
        if self._item is not None and not self._original_operation_active:
            self.originalReplaceRequested.emit(self._item)

    def _requestCompressedCleanup(self):
        if self._item is not None and not self._original_operation_active:
            self.compressedCleanupRequested.emit(self._item)

    def _requestGalleryUpdate(self):
        if (
            self._item is not None
            and self._online_detail is None
            and self._item.newer_gallery_urls
            and not self._gallery_update_locked
        ):
            self.galleryUpdateRequested.emit(self._item)

    def _requestOpenFolder(self):
        if self._folder_open_item is not None:
            self.folderOpenRequested.emit(self._folder_open_item)

    def _requestCategorySelection(self):
        if self._item is not None and self._online_detail is None:
            self.categorySelectionRequested.emit(self._item)

    def _requestClearProgress(self):
        item = self._item or self._folder_open_item
        if item is not None:
            self.readingRecordClearRequested.emit(int(item.gid))

    def setFolderOpenTarget(self, item=None):
        self._folder_open_item = item
        self.openFolderButton.setVisible(item is not None)
        self.clearProgressButton.setVisible(
            item is not None
            and (
                getattr(item, "progress_page_index", None) is not None
                or bool(getattr(item, "reading_completed", False))
            )
        )

    def setGalleryUpdateState(self, record=None, active=False, speed=0):
        """Lock destructive gallery actions while an update is unfinished."""
        if record is not None and (
            record.state == "completed" or int(record.status) >= 6
        ):
            record = None
        locked = record is not None
        self._gallery_update_locked = locked
        if self._item is None or self._online_detail is not None:
            return
        if record is None:
            self.updateButton.setVisible(bool(self._item.newer_gallery_urls))
            self.updateButton.setEnabled(bool(self._item.newer_gallery_urls))
            self.syncButton.setEnabled(bool(self._item.gallery_token))
            if self._item.original_state == ORIGINAL_STATE_ACTIVE:
                self.downloadButton.setText(
                    self.tr("已使用混合原图")
                    if self._item.original_fallback_to_standard
                    else self.tr("已使用原图")
                )
                self.downloadButton.setEnabled(False)
            else:
                self.downloadButton.setEnabled(bool(self._item.page_tokens))
            self.originalDownloadButton.setEnabled(
                bool(self._item.page_tokens)
                and self._item.original_state != ORIGINAL_STATE_ACTIVE
            )
            self.originalReplaceButton.setEnabled(
                self.originalReplaceButton.isVisible()
                and not self._original_operation_active
            )
            self.deleteCompressedButton.setEnabled(
                self.deleteCompressedButton.isVisible()
                and not self._original_operation_active
            )
            self.readButton.setEnabled(bool(self._item.page_paths))
            return
        self.updateButton.show()
        self.updateButton.setEnabled(not active)
        state_text = {
            "waiting_download": self.tr("正在先补齐原画廊"),
            "queued": self.tr("等待更新"),
            "updating": self.tr("正在更新"),
            "paused": self.tr("继续更新"),
            "failed": self.tr("重试更新"),
        }.get(record.state, self.tr("更新到最新"))
        if record.page_count:
            state_text += self.tr("（{} / {}）").format(
                record.completed_pages, record.page_count
            )
        self.updateButton.setText(state_text)
        self.updateButton.setToolTip(record.error or "")
        self.syncButton.setEnabled(False)
        self.downloadButton.setEnabled(False)
        self.originalDownloadButton.setEnabled(False)
        self.originalReplaceButton.setEnabled(False)
        self.deleteCompressedButton.setEnabled(False)
        self.readButton.setEnabled(False)

    def reloadCurrentMangaPages(self):
        if self._item is None or self._online_detail is not None:
            return
        self.setManga(
            replace(
                self._item,
                page_paths=(),
                original_page_paths=(),
                downloaded_page_count=0,
                download_complete=None,
            )
        )

    def _requestMetadataSync(self):
        if self._item is None or self._local_sync_active or self._gallery_update_locked:
            return
        self.localMetadataSyncRequested.emit(self._item)

    def _localDownloadButtonText(self, item):
        if item.download_complete is False:
            return self.tr("基础下载")
        if item.download_complete is True:
            return self.tr("基础下载")
        return self.tr("无法补齐")

    def _onlineMetadataText(self, item, detail=None):
        category = detail.category if detail is not None else item.category
        uploader = detail.uploader if detail is not None else item.uploader
        posted = detail.posted if detail is not None else item.posted
        rating = detail.rating if detail is not None else item.rating
        values = [
            self.tr("GID：{}").format(item.gid),
            self.tr("类别：{}").format(category or self.tr("未知")),
            self.tr("上传者：{}").format(uploader or self.tr("未知")),
            self.tr("发布时间：{}").format(posted or self.tr("未知")),
            self.tr("评分：{}").format(
                f"{rating:.2f}" if rating is not None else self.tr("暂无")
            ),
        ]
        if detail is not None:
            values.extend(
                (
                    self.tr("评分人数：{}").format(detail.rating_count),
                    self.tr("语言：{}").format(detail.language or self.tr("未知")),
                    self.tr("收藏：{}").format(detail.favorited or self.tr("未知")),
                    self.tr("父画廊：{}").format(detail.parent_gallery or self.tr("无")),
                )
            )
        values.append(self.tr("地址：{}").format(item.url))
        return "\n".join(values)

    def _onlineDetailedMetadataText(self, item, detail=None):
        pages = detail.page_count if detail is not None else item.page_count
        values = [
            self.tr("页数：{}").format(pages or self.tr("未知")),
        ]
        if detail is not None:
            values.extend(
                (
                    self.tr("文件大小：{}").format(
                        detail.file_size or self.tr("未知")
                    ),
                    self.tr("可见性：{}").format(
                        detail.visible or self.tr("未知")
                    ),
                )
            )
        return "\n".join(values)

    def _setOnlineMetadata(self, item, detail=None, reset_details=False):
        self._setMetadataTexts(
            self._onlineMetadataText(item, detail),
            self._onlineDetailedMetadataText(item, detail),
            reset_details=reset_details,
        )

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
            widget = OnlineCommentWidget(comment, self.commentsWidget)
            widget.galleryLinkActivated.connect(
                lambda link: self.onlineGalleryLinkRequested.emit(link)
            )
            self.commentsListLayout.addWidget(widget)

    def _clearComments(self):
        while self.commentsListLayout.count():
            layout_item = self.commentsListLayout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _showPages(self, item: MangaItem):
        self._preview_page = 1
        self._updatePreviewSourceVisibility()
        self._renderPreviewPage(item)

    def _renderPreviewPage(self, item: MangaItem):
        self._cancelOnlineLoads()
        self._clearPreviewTiles()
        page_count = self._previewPageCount(item)
        self._preview_page = min(max(1, self._preview_page), page_count)
        start = (self._preview_page - 1) * self.PREVIEW_PAGE_SIZE
        paths = self._localPreviewPaths(item)
        total = self._localPreviewTotal(item)
        end = min(total, start + self.PREVIEW_PAGE_SIZE)
        paths_by_index = local_page_path_map(paths)
        self._preview_tiles = [
            PreviewTile(index, self.previewWidget)
            for index in range(start, end)
        ]
        for tile in self._preview_tiles:
            tile.clicked.connect(self._requestLocalPreviewRead)
        self.previewTitle.setText(
            (
                self.tr("原图页面预览（共 {} 页，第 {} / {} 页）")
                if self._preview_source == "original"
                else self.tr("页面预览（共 {} 页，第 {} / {} 页）")
            ).format(total, self._preview_page, page_count)
        )
        self._updatePreviewPagination(page_count)
        QTimer.singleShot(0, self._relayoutPreview)
        indexed_paths = tuple(
            (index, paths_by_index[index])
            for index in range(start, end)
            if index in paths_by_index
        )
        if indexed_paths:
            worker = PreviewLoadWorker(indexed_paths)
            worker.signals.imageReady.connect(
                lambda index, image: self._setPreviewImage(worker, index, image)
            )
            worker.signals.finished.connect(lambda: self._finishPreviewLoad(worker))
            self._preview_worker = worker
            QThreadPool.globalInstance().start(worker)
        for tile in self._preview_tiles:
            if tile.pageIndex not in paths_by_index and self._preview_source == "standard":
                tile.imageLabel.setText(self.tr("正在加载在线预览…"))
        if self._preview_source == "standard":
            self._loadMissingLocalPreviews()

    def _localPreviewPaths(self, item=None):
        current_item = item or self._item
        if current_item is None:
            return ()
        if self._preview_source == "original":
            return tuple(current_item.original_page_paths)
        return tuple(current_item.page_paths)

    def _localPreviewTotal(self, item=None):
        current_item = item or self._item
        if current_item is None:
            return 0
        if self._preview_source == "original":
            return len(current_item.original_page_paths)
        return local_page_slot_count(current_item)

    def _updatePreviewSourceVisibility(self):
        has_original_preview = bool(
            self._item is not None
            and self._online_detail is None
            and self._item.original_page_paths
            and len(self._item.original_page_paths) >= self._item.page_count
        )
        self.previewSourceSwitch.setVisible(has_original_preview)
        if not has_original_preview and self._preview_source == "original":
            self._preview_source = "standard"
            self.previewSourceSwitch.setCurrentItem("standard")

    def _setPreviewSource(self, source):
        source = str(source)
        if self._item is None or self._online_detail is not None:
            return
        if source == "original" and not self._item.original_page_paths:
            return
        if source not in {"standard", "original"} or source == self._preview_source:
            return
        self._preview_source = source
        self.previewSourceSwitch.setCurrentItem(source)
        self._preview_page = 1
        self._renderPreviewPage(self._item)

    def _readerItemForPreviewSource(self):
        if self._item is None or self._preview_source != "original":
            return self._item
        paths = tuple(self._item.original_page_paths)
        return replace(
            self._item,
            page_paths=paths,
            page_count=len(paths),
            downloaded_page_count=len(paths),
            download_complete=bool(paths),
        )

    def _previewPageCount(self, item=None) -> int:
        if self._online_detail is not None:
            total = int(self._online_detail.page_count)
            return max(
                1,
                (total + self.ONLINE_PREVIEW_PAGE_SIZE - 1)
                // self.ONLINE_PREVIEW_PAGE_SIZE,
            )
        current_item = item or self._item
        total = self._localPreviewTotal(current_item)
        return max(1, (total + self.PREVIEW_PAGE_SIZE - 1) // self.PREVIEW_PAGE_SIZE)

    def _setPreviewPage(self, page: int):
        if self._online_detail is not None:
            page = min(max(1, int(page)), self._previewPageCount())
            if page == self._preview_page and self._preview_tiles:
                return
            self._loadOnlinePreviewPage(page)
            return
        if self._item is None or not self._localPreviewTotal(self._item):
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
            self._setLocalMetadata(item)
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
        for worker in self._preview_patch_workers:
            worker.cancelled = True
        self._preview_patch_workers.clear()
        self._cancelOnlineLoads()

    def _requestRead(self):
        if self._gallery_update_locked:
            return
        if self._online_detail is not None and self._online_detail.page_count:
            self.onlineReadRequested.emit(self._online_detail, 0)
            return
        reader_item = self._readerItemForPreviewSource()
        if reader_item is None or not reader_item.page_paths:
            return
        self.readRequested.emit(reader_item, -1)

    def updateReadingProgress(
        self, gid: int, page_index: int, page_count=0, completed=False
    ):
        if self._item is None or self._item.gid != gid:
            return
        self._item = replace(
            self._item,
            progress_page_index=max(0, int(page_index)),
            reading_completed=self._item.reading_completed or bool(completed),
            page_count=max(self._item.page_count, int(page_count or 0)),
        )
        self._setLocalMetadata(self._item)
        current_page = self._item.progress_page_number
        if self._item.page_count:
            current_page = min(current_page, self._item.page_count)
        self.readButton.setText(
            self.tr("继续阅读（第 {} 页）").format(
                current_page
            )
        )

    def clearReadingProgress(self, gid: int):
        if self._item is None or int(self._item.gid) != int(gid):
            return
        self._item = replace(
            self._item,
            progress_page_index=None,
            reading_completed=False,
        )
        self._setLocalMetadata(self._item)
        self.readButton.setText(self.tr("开始阅读"))
        self.clearProgressButton.hide()

    def addDownloadedPage(
        self,
        gid: int,
        page_index: int,
        page_path,
        completed_pages: int,
        page_count: int,
    ):
        if (
            self._item is None
            or self._online_detail is not None
            or int(self._item.gid) != int(gid)
        ):
            return None
        paths = merge_downloaded_page_path(
            self._item.page_paths, page_index, page_path
        )
        old_slot_count = local_page_slot_count(self._item)
        total = max(int(page_count or 0), self._item.page_count)
        completed = max(0, int(completed_pages or 0))
        self._item = replace(
            self._item,
            page_paths=paths,
            page_count=total,
            downloaded_page_count=completed,
            download_complete=bool(total and completed >= total),
        )
        self.readButton.setEnabled(bool(paths))
        if self._item.progress_page_number is not None:
            self.readButton.setText(
                self.tr("继续阅读（第 {} 页）").format(
                    min(self._item.progress_page_number, total)
                )
            )
        else:
            self.readButton.setText(self.tr("开始阅读"))
        if local_page_slot_count(self._item) != old_slot_count:
            self._renderPreviewPage(self._item)
        else:
            self._loadPreviewPatch(page_index, page_path)
        return self._item

    def _refreshLocalPreviewAfterDownload(self):
        """Compatibility no-op; pageSaved now updates one tile incrementally."""

    def _requestLocalPreviewRead(self, page_index):
        if (
            self._item is not None
            and self._online_detail is None
            and not self._gallery_update_locked
        ):
            self.readRequested.emit(
                self._readerItemForPreviewSource(), int(page_index)
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
        values = [
            self.tr(
                "GID：{gid}\n分类：{primary}\n来源类别：{category}\n目录：{folder}"
            ).format(
                gid=item.gid,
                primary=primary_label,
                category=item.category_name,
                folder=item.folder,
            )
        ]
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
        if item.favorited:
            values.append(self.tr("收藏：{}").format(item.favorited))
        if item.parent_gallery:
            values.append(self.tr("父画廊：{}").format(item.parent_gallery))
        return "\n".join(values)

    def _detailedMetadataText(self, item: MangaItem) -> str:
        taxonomy = "、".join(item.taxonomy_labels) or self.tr("无")
        values = [
            self.tr("归类：{}").format(taxonomy),
            self.tr("页数：{}").format(item.page_count or self.tr("读取中…")),
            self.tr("阅读进度：{}").format(self._progressText(item)),
        ]
        if item.download_complete is not None:
            values.extend(
                (
                    self.tr("已下载：{} / {} 页").format(
                        item.downloaded_page_count,
                        item.page_count,
                    ),
                    self.tr("下载状态：{}").format(
                        self.tr("完整")
                        if item.download_complete else self.tr("未完成")
                    ),
                )
            )
        if item.file_size:
            values.append(self.tr("文件大小：{}").format(item.file_size))
        if item.visible:
            values.append(self.tr("可见性：{}").format(item.visible))
        return "\n".join(values)

    def _setLocalMetadata(self, item: MangaItem, reset_details=False):
        self._setMetadataTexts(
            self._metadataText(item),
            self._detailedMetadataText(item),
            reset_details=reset_details,
        )

    def _setMetadataTexts(self, primary, details, reset_details=False):
        self.metadataLabel.setText(primary)
        self.detailMetadataLabel.setText(details)
        self.detailMetadataButton.setVisible(bool(details))
        if reset_details or not details:
            self._setDetailedMetadataExpanded(False)

    def _toggleDetailedMetadata(self):
        self._setDetailedMetadataExpanded(self.detailMetadataLabel.isHidden())

    def _setDetailedMetadataExpanded(self, expanded):
        expanded = bool(expanded and self.detailMetadataLabel.text())
        self.detailMetadataLabel.setVisible(expanded)
        self.detailMetadataButton.setText(
            self.tr("收起详细") if expanded else self.tr("查看详细")
        )
        self.detailMetadataButton.setIcon(FIF.HIDE if expanded else FIF.VIEW)

    def _currentGalleryGid(self):
        if self._item is not None:
            return int(self._item.gid)
        if self._online_gallery is not None:
            return int(self._online_gallery.gid)
        return None

    def _showTitleContextMenu(self, label, position):
        selected_text = " ".join(str(label.selectedText() or "").split())
        if not selected_text:
            return
        menu = RoundMenu(parent=label)
        copy_action = QAction(self.tr("复制"), menu)
        copy_action.triggered.connect(
            lambda: QApplication.clipboard().setText(selected_text)
        )
        search_action = QAction(self.tr("在本地搜索所选文本"), menu)
        effective_length = len("".join(selected_text.split()))
        search_action.setEnabled(
            self._currentGalleryGid() is not None and effective_length >= 2
        )
        search_action.triggered.connect(
            lambda: self.selectedTitleSearchRequested.emit(
                self._currentGalleryGid(), selected_text
            )
        )
        online_search_action = QAction(
            self.tr("在线搜索所选文本"), menu
        )
        online_search_action.setEnabled(effective_length >= 2)
        online_search_action.triggered.connect(
            lambda: self.selectedTitleOnlineSearchRequested.emit(
                selected_text
            )
        )
        menu.addAction(copy_action)
        menu.addAction(search_action)
        menu.addAction(online_search_action)
        menu.exec(label.mapToGlobal(position))

    def updateLocalItem(self, item):
        if (
            self._item is None
            or self._online_gallery is not None
            or int(self._item.gid) != int(item.gid)
        ):
            return False
        self._item = item
        self._folder_open_item = item
        self._setLocalMetadata(item, reset_details=False)
        return True

    def setSimilarSearchRecord(self, record):
        gid = self._currentGalleryGid()
        if record is None or gid is None or int(record.source_gid) != gid:
            self.similarResultsButton.hide()
            return
        count = len(record.result_gids)
        self.similarResultsButton.setText(
            self.tr("展开 {} 个相似画廊").format(count)
            if count
            else self.tr("无相似画廊")
        )
        self.similarResultsButton.setEnabled(bool(count))
        self.similarResultsButton.setToolTip(
            self.tr("最近搜索：{} ").format(record.selected_text).rstrip()
        )
        self.similarResultsButton.show()

    def _setKeyTags(self, tags):
        while self.keyTagsLayout.count():
            layout_item = self.keyTagsLayout.takeAt(0)
            widget = (
                layout_item.widget()
                if hasattr(layout_item, "widget")
                else layout_item
            )
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        key_groups = {
            namespace: values
            for namespace, _title, _tone, values in group_manga_tags(tags)
            if namespace in ("language", "artist")
        }
        labels = []
        for namespace, title, tone in (
            ("language", self.tr("语言"), "language"),
            ("artist", self.tr("作者"), "creator"),
        ):
            for value in key_groups.get(namespace, ()):
                display_value = _translated_tag_value(
                    self.tagSearchIndex, namespace, value
                )
                chip = TagChip(
                    f"{title}：{display_value}",
                    namespace,
                    tone,
                    self.keyTagsWidget,
                    raw_text=value,
                )
                chip.setObjectName("mangaKeyTagChip")
                labels.append(chip)
        for chip in labels:
            self.keyTagsLayout.addWidget(chip)
        self.keyTagsWidget.setVisible(bool(labels))

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
                tag_search_index=self.tagSearchIndex,
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

    def _loadMissingLocalPreviews(self):
        if (
            self._preview_source != "standard"
            or
            self._item is None
            or self._local_online_detail is None
            or self._local_online_provider is None
            or self._local_online_cache is None
        ):
            return
        paths_by_index = local_page_path_map(self._item.page_paths)
        missing_indexes = {
            tile.pageIndex
            for tile in self._preview_tiles
            if tile.pageIndex not in paths_by_index
        }
        if not missing_indexes:
            return
        detail = self._local_online_detail
        provider = self._local_online_provider
        cache = self._local_online_cache
        site = provider.settings.site
        page_numbers = sorted({index // self.ONLINE_PREVIEW_PAGE_SIZE + 1 for index in missing_indexes})
        for page_number in page_numbers:
            page = cache.get_preview_page(site, detail.gallery, page_number)
            if page is not None:
                self._applyMissingLocalPreviewPage(page)
                continue
            worker = OnlinePreviewPageWorker(provider, detail.gallery, page_number)
            worker.signals.loaded.connect(
                lambda page, current_worker=worker: self._finishMissingLocalPreviewPage(
                    current_worker, page
                )
            )
            worker.signals.failed.connect(
                lambda _message, current_worker=worker: self._finishMissingLocalPreviewPage(
                    current_worker, None
                )
            )
            self._local_preview_page_workers.add(worker)
            self.onlineThreadPool.start(worker)

    def _finishMissingLocalPreviewPage(self, worker, page):
        if worker not in self._local_preview_page_workers:
            return
        self._local_preview_page_workers.discard(worker)
        if page is None or self._local_online_cache is None or self._local_online_provider is None:
            return
        self._local_online_cache.put_preview_page(
            self._local_online_provider.settings.site, page
        )
        self._applyMissingLocalPreviewPage(page)

    def _applyMissingLocalPreviewPage(self, page):
        if (
            self._item is None
            or self._local_online_detail is None
            or int(page.gallery.gid) != int(self._item.gid)
        ):
            return
        paths_by_index = local_page_path_map(self._item.page_paths)
        tiles = {tile.pageIndex: tile for tile in self._preview_tiles}
        provider = self._local_online_provider
        cache = self._local_online_cache
        site = provider.settings.site
        for preview in page.items:
            index = int(preview.page_index)
            tile = tiles.get(index)
            if tile is None or index in paths_by_index:
                continue
            data = cache.get_preview_image(site, page.gallery, index)
            if data:
                tile.setImage(QImage.fromData(data))
                continue
            if not preview.thumbnail_url:
                tile.imageLabel.setText(self.tr("无在线预览"))
                continue
            worker = OnlinePreviewThumbnailWorker(
                provider, page.gallery, preview, cache, site
            )
            worker.signals.loaded.connect(
                lambda ready_index, image, current_worker=worker: self._setOnlinePreviewImage(
                    current_worker, ready_index, image
                )
            )
            worker.signals.finished.connect(
                lambda current_worker=worker: self._finishOnlineThumbnail(current_worker)
            )
            self._online_thumbnail_workers.add(worker)
            self.onlineThreadPool.start(worker)

    def _loadPreviewPatch(self, page_index, page_path):
        if not any(tile.pageIndex == int(page_index) for tile in self._preview_tiles):
            return
        worker = PreviewLoadWorker(((int(page_index), Path(page_path)),))
        worker.signals.imageReady.connect(
            lambda index, image, current_worker=worker: self._setPreviewPatchImage(
                current_worker, index, image
            )
        )
        worker.signals.finished.connect(
            lambda current_worker=worker: self._preview_patch_workers.discard(
                current_worker
            )
        )
        self._preview_patch_workers.add(worker)
        QThreadPool.globalInstance().start(worker)

    def _setPreviewPatchImage(self, worker, index, image):
        if worker not in self._preview_patch_workers:
            return
        for tile in self._preview_tiles:
            if tile.pageIndex == int(index):
                tile.setImage(image)
                break

    def _cancelOnlineLoads(self):
        if self._online_preview_worker is not None:
            self._online_preview_worker.cancelled = True
            self._online_preview_worker = None
        for worker in self._online_thumbnail_workers:
            worker.cancelled = True
        self._online_thumbnail_workers.clear()
        for worker in self._local_preview_page_workers:
            worker.cancelled = True
        self._local_preview_page_workers.clear()

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
