from collections import OrderedDict
from dataclasses import dataclass, field
from functools import partial
from typing import List, Optional, Tuple

from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QImage, QPainter, QPixmap
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
    FlowLayout,
    PushButton,
    RoundMenu,
    ScrollArea,
    SegmentedWidget,
    ToolButton,
    TitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.common.style_sheet import StyleSheet
from app.domain.online_gallery import OnlineGalleryPage, OnlineGalleryQuery
from app.services.online_thumbnail_cache import OnlineThumbnailCache
from app.sources.eh_online_source import (
    EhOnlineError,
    EhOnlineSettings,
    create_eh_online_provider,
)
from app.view.eh_tag_search_line_edit import EhTagSearchLineEdit
from app.workers.eh_online_worker import OnlineCoverWorker, OnlineSearchWorker


PageCacheKey = Tuple[str, str, str]
MAX_MEMORY_PAGES_PER_SITE = 64


@dataclass
class OnlinePageRequest:
    keyword: str = ""
    cursor: str = ""
    page_number: int = 1
    cursor_history: Tuple[str, ...] = ()
    scroll_position: int = 0


@dataclass
class OnlineSiteState:
    """In-memory browsing container isolated to one EH/EX site."""

    search_text: str = ""
    keyword: str = ""
    current_cursor: str = ""
    page_number: int = 1
    cursor_history: List[str] = field(default_factory=list)
    next_cursor: str = ""
    current_page: Optional[OnlineGalleryPage] = None
    current_cache_key: Optional[PageCacheKey] = None
    pages: OrderedDict = field(default_factory=OrderedDict)
    scroll_position: int = 0


def _rating_text(item):
    if item.rating is None:
        return "暂无评分"
    return f"★ {item.rating:.1f}"


def _category_style_key(category):
    normalized = "".join(
        character for character in (category or "").casefold() if character.isalnum()
    )
    return {
        "misc": "ct1",
        "miscellaneous": "ct1",
        "doujinshi": "ct2",
        "manga": "ct3",
        "artistcg": "ct4",
        "gamecg": "ct5",
        "imageset": "ct6",
        "cosplay": "ct7",
        "asianporn": "ct8",
        "nonh": "ct9",
        "western": "cta",
        "private": "private",
    }.get(normalized, "unknown")


def _configure_category_label(label, category):
    label.setObjectName("onlineGalleryCategory")
    label.setProperty("categoryStyle", _category_style_key(category))
    label.setAlignment(Qt.AlignCenter)
    label.setToolTip(category)


class MarqueeLabel(QLabel):
    """Scroll a long title while leaving short titles still and centered."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = text or ""
        self._offset = 0
        self._pause_ticks = 30
        self.setText(self._full_text)
        self.setToolTip(self._full_text)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def enterEvent(self, event):
        super().enterEvent(event)
        available = max(0, self.width() - 16)
        if self.fontMetrics().horizontalAdvance(self._full_text) > available:
            self._offset = 0
            self._pause_ticks = 12
            self._timer.start()
            self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._timer.stop()
        self._offset = 0
        self._pause_ticks = 30
        self.update()

    def _advance(self):
        available = max(0, self.width() - 16)
        overflow = self.fontMetrics().horizontalAdvance(self._full_text) - available
        if overflow <= 0:
            if self._offset:
                self._offset = 0
                self.update()
            return
        if self._pause_ticks:
            self._pause_ticks -= 1
            return
        self._offset += 1
        if self._offset > overflow + 24:
            self._offset = 0
            self._pause_ticks = 30
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        painter.setPen(self.palette().windowText().color())
        metrics = self.fontMetrics()
        text_width = metrics.horizontalAdvance(self._full_text)
        available = max(0, self.width() - 16)
        if text_width <= available:
            x = max(8, (self.width() - text_width) // 2)
            text = self._full_text
        elif not self._timer.isActive():
            x = 8
            text = metrics.elidedText(self._full_text, Qt.ElideRight, available)
        else:
            x = 8 - self._offset
            text = self._full_text
        y = (self.height() + metrics.ascent() - metrics.descent()) // 2
        painter.drawText(x, y, text)


class OnlineCoverLabel(QLabel):
    """Keep source pixels so layout changes can rescale an existing cover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sourceImage = QImage()

    def setCoverData(self, data: bytes):
        image = QImage.fromData(data)
        if image.isNull():
            self._sourceImage = QImage()
            self.clear()
            self.setText(self.tr("封面不可用"))
            return
        self._sourceImage = image
        self.setText("")
        self._rescaleCover()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescaleCover()

    def _rescaleCover(self):
        if self._sourceImage.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        pixmap = QPixmap.fromImage(self._sourceImage).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(pixmap)


class _OnlineGalleryCardBase(CardWidget):
    def __init__(
        self,
        item,
        parent=None,
        open_callback=None,
        download_callback=None,
    ):
        super().__init__(parent)
        self.item = item
        self.downloadCallback = download_callback
        self.setCursor(Qt.PointingHandCursor)
        if open_callback is not None:
            self.clicked.connect(lambda: open_callback(item))
        self.downloadedBadge = QLabel(self)
        self.downloadedBadge.setObjectName("onlineGalleryDownloadedBadge")
        self.downloadedBadge.setFixedSize(28, 28)
        self.downloadedBadge.setAlignment(Qt.AlignCenter)
        self.downloadedBadge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.downloadedBadge.setToolTip(self.tr("已下载到本地资源"))
        self.downloadedBadge.setPixmap(
            FIF.DOWNLOAD.icon(color=QColor("#2dbb68")).pixmap(16, 16)
        )
        self.downloadedBadge.move(16, 16)
        self.downloadedBadge.hide()

    def setDownloaded(self, downloaded):
        self.downloadedBadge.setVisible(bool(downloaded))
        if downloaded:
            self.downloadedBadge.raise_()

    def setCoverData(self, data: bytes):
        self.coverLabel.setCoverData(data)

    def contextMenuEvent(self, event):
        menu = RoundMenu(self.tr("在线画廊"), self)
        download_action = QAction(
            FIF.DOWNLOAD.icon(),
            self.tr("下载"),
            menu,
        )
        download_action.setEnabled(self.downloadCallback is not None)
        if self.downloadCallback is not None:
            download_action.triggered.connect(
                lambda _checked=False: self.downloadCallback(self.item)
            )
        menu.addAction(download_action)
        menu.exec(event.globalPos())
        event.accept()


class OnlineGalleryCard(_OnlineGalleryCardBase):
    def __init__(
        self,
        item,
        parent=None,
        is_downloaded=False,
        open_callback=None,
        download_callback=None,
    ):
        super().__init__(item, parent, open_callback, download_callback)
        self.setObjectName("onlineGalleryCard")
        self.setFixedWidth(254)
        self.setMinimumHeight(408)

        self.coverLabel = OnlineCoverLabel(self)
        self.coverLabel.setObjectName("onlineGalleryCover")
        self.coverLabel.setAlignment(Qt.AlignCenter)
        self.coverLabel.setFixedHeight(268)
        self.coverLabel.setText(
            self.tr("加载封面…") if item.thumbnail_url else self.tr("封面不可用")
        )
        self.coverLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.categoryLabel = CaptionLabel(item.category or self.tr("类型未知"), self)
        _configure_category_label(self.categoryLabel, item.category)
        self.ratingLabel = CaptionLabel(_rating_text(item), self)
        self.ratingLabel.setObjectName("onlineGalleryRating")
        self.ratingLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(8)
        info_layout.addWidget(self.categoryLabel)
        info_layout.addStretch(1)
        info_layout.addWidget(self.ratingLabel)

        self.titleLabel = MarqueeLabel(item.title, self)
        self.titleLabel.setObjectName("onlineGalleryTitleCell")

        self.postedLabel = CaptionLabel(item.posted or self.tr("时间未知"), self)
        self.postedLabel.setObjectName("onlineGalleryPosted")
        self.postedLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.postedLabel.setToolTip(self.tr("发布时间：{}").format(item.posted or "-"))
        self.uploaderLabel = CaptionLabel(item.uploader or "-", self)
        self.uploaderLabel.setObjectName("onlineGalleryUploader")
        self.uploaderLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.uploaderLabel.setToolTip(item.uploader)
        self.pageCountLabel = CaptionLabel(
            self.tr("{} 页").format(item.page_count) if item.page_count else self.tr("页数未知"),
            self,
        )
        self.pageCountLabel.setObjectName("onlineGalleryPageCount")
        self.pageCountLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        meta_layout = QGridLayout()
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setHorizontalSpacing(8)
        meta_layout.setVerticalSpacing(0)
        meta_layout.addWidget(self.postedLabel, 0, 0, 2, 1)
        meta_layout.addWidget(self.uploaderLabel, 0, 1)
        meta_layout.addWidget(self.pageCountLabel, 1, 1)
        meta_layout.setColumnStretch(0, 1)
        meta_layout.setColumnStretch(1, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 10)
        layout.setSpacing(6)
        layout.addWidget(self.coverLabel)
        layout.addLayout(info_layout)
        layout.addWidget(self.titleLabel)
        layout.addLayout(meta_layout)
        self.setDownloaded(is_downloaded)


class OnlineGalleryExtendedCard(_OnlineGalleryCardBase):
    def __init__(
        self,
        item,
        parent=None,
        is_downloaded=False,
        open_callback=None,
        download_callback=None,
    ):
        super().__init__(item, parent, open_callback, download_callback)
        self.setObjectName("onlineGalleryExtendedCard")
        self.setMinimumWidth(520)
        self.setMinimumHeight(188)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.coverLabel = OnlineCoverLabel(self)
        self.coverLabel.setObjectName("onlineGalleryCover")
        self.coverLabel.setAlignment(Qt.AlignCenter)
        self.coverLabel.setFixedSize(132, 166)
        self.coverLabel.setText(
            self.tr("加载封面…") if item.thumbnail_url else self.tr("封面不可用")
        )

        self.titleLabel = BodyLabel(item.title, self)
        self.titleLabel.setObjectName("onlineGalleryExtendedTitle")
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setToolTip(item.title)
        self.titleLabel.setMaximumHeight(44)

        self.categoryLabel = CaptionLabel(item.category or self.tr("类型未知"), self)
        _configure_category_label(self.categoryLabel, item.category)
        self.ratingLabel = CaptionLabel(_rating_text(item), self)
        self.ratingLabel.setObjectName("onlineGalleryRating")
        self.ratingLabel.setAlignment(Qt.AlignCenter)

        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)
        info_layout.addWidget(self.categoryLabel)
        info_layout.addWidget(self.ratingLabel)
        info_layout.addStretch(1)

        self.tagsWidget = QWidget(self)
        self.tagsWidget.setObjectName("onlineGalleryTags")
        tags_layout = FlowLayout(self.tagsWidget, isTight=True)
        tags_layout.setContentsMargins(0, 0, 0, 0)
        tags_layout.setHorizontalSpacing(5)
        tags_layout.setVerticalSpacing(5)
        self.tagLabels = []
        for tag in item.tags:
            label = CaptionLabel(tag, self.tagsWidget)
            label.setObjectName("onlineGalleryTagChip")
            label.setToolTip(tag)
            tags_layout.addWidget(label)
            self.tagLabels.append(label)
        if not self.tagLabels:
            if item.source_mode.casefold().startswith("minimal"):
                empty_text = self.tr("Minimal 源页面未提供标签")
            else:
                empty_text = self.tr("无标签数据")
            self.tagsPlaceholder = CaptionLabel(empty_text, self.tagsWidget)
            self.tagsPlaceholder.setObjectName("onlineGalleryTagsPlaceholder")
            tags_layout.addWidget(self.tagsPlaceholder)

        metadata = []
        if item.posted:
            metadata.append(item.posted)
        metadata.append(item.uploader or "-")
        metadata.append(
            self.tr("{} 页").format(item.page_count) if item.page_count else self.tr("页数未知")
        )
        self.detailLabel = CaptionLabel("  ·  ".join(metadata), self)
        self.detailLabel.setObjectName("onlineGalleryExtendedMeta")
        self.detailLabel.setToolTip(self.detailLabel.text())

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 2, 0, 2)
        content_layout.setSpacing(7)
        content_layout.addWidget(self.titleLabel)
        content_layout.addLayout(info_layout)
        content_layout.addWidget(self.tagsWidget, 1)
        content_layout.addWidget(self.detailLabel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 12, 10)
        layout.setSpacing(12)
        layout.addWidget(self.coverLabel)
        layout.addLayout(content_layout, 1)
        self.setDownloaded(is_downloaded)


class OnlineMangaInterface(QWidget):
    """Online EH/EX browser whose gallery cards open the shared detail page."""

    galleryActivated = Signal(object, object, bytes)
    galleryDownloadRequested = Signal(object, object, bytes)

    def __init__(
        self,
        parent=None,
        provider_factory=create_eh_online_provider,
        thumbnail_cache=None,
        auto_load_on_show=True,
        tag_search_index=None,
        search_history_service=None,
    ):
        super().__init__(parent)
        self.setObjectName("onlineMangaInterface")
        self._provider_factory = provider_factory
        self._thumbnail_cache = thumbnail_cache or OnlineThumbnailCache()
        self._auto_load_on_show = auto_load_on_show
        self._activated = False
        self._rendered_site = None
        self._current_site = cfg.get(cfg.onlineEhSite)
        self._site_states = {
            "ehentai": OnlineSiteState(),
            "exhentai": OnlineSiteState(),
        }
        self._search_worker = None
        self._cover_workers = set()
        self._cover_data = {}
        self._site_providers = {}
        self._downloaded_gids = set()
        self._cards = []
        self._cards_by_gid = {}
        self._filters = {}
        self._relayoutTimer = QTimer(self)
        self._relayoutTimer.setSingleShot(True)
        self._relayoutTimer.timeout.connect(self._relayoutCards)
        self._scrollRestoreTimer = QTimer(self)
        self._scrollRestoreTimer.setSingleShot(True)
        self._scrollRestoreTimer.timeout.connect(self._applyPendingScrollPosition)
        self._pendingScrollRestore = None

        self.searchThreadPool = QThreadPool(self)
        self.searchThreadPool.setMaxThreadCount(2)
        self.coverThreadPool = QThreadPool(self)
        self.coverThreadPool.setMaxThreadCount(
            int(cfg.get(cfg.onlineEhThumbnailConcurrency))
        )
        # Compatibility alias retained for callers that wait for search tasks.
        self.threadPool = self.searchThreadPool

        self.titleLabel = TitleLabel(self.tr("在线资源"), self)
        self.siteSwitch = SegmentedWidget(self)
        self.siteSwitch.addItem("ehentai", "E-Hentai", lambda: self.setSite("ehentai"))
        self.siteSwitch.addItem("exhentai", "ExHentai", lambda: self.setSite("exhentai"))
        self.siteSwitch.setCurrentItem(self._current_site)
        self.viewModeButton = ToolButton(self)
        self.viewModeButton.setObjectName("onlineViewModeButton")
        self.viewModeButton.setFixedSize(36, 36)
        self.viewModeButton.clicked.connect(self.toggleViewMode)
        self._updateViewModeButton(cfg.get(cfg.onlineEhViewMode))

        self.searchEdit = EhTagSearchLineEdit(
            tag_search_index,
            self,
            search_history_service,
        )
        self.searchEdit.setPlaceholderText(self.tr("搜索标题、作者或标签；留空显示最新画廊"))
        self.searchEdit.setMinimumWidth(320)
        self.searchEdit.searchSignal.connect(self.search)
        self.searchEdit.returnPressed.connect(self.search)
        self.searchButton = PushButton(FIF.SEARCH, self.tr("搜索"), self)
        self.searchButton.clicked.connect(self.search)
        self.searchButton.clicked.connect(self.searchEdit.recordCurrentSearch)
        self.refreshButton = PushButton(FIF.SYNC, self.tr("刷新"), self)
        self.refreshButton.clicked.connect(self.refresh)

        header = QHBoxLayout()
        header.addWidget(self.titleLabel)
        header.addStretch(1)
        header.addWidget(self.viewModeButton)
        header.addWidget(self.siteSwitch)

        search_row = QHBoxLayout()
        search_row.addWidget(self.searchEdit, 1)
        search_row.addWidget(self.searchButton)
        search_row.addWidget(self.refreshButton)

        self.resultLabel = BodyLabel(
            self.tr("进入页面后会自动加载当前站点的最新画廊。"),
            self,
        )
        self.resultLabel.setWordWrap(True)
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setObjectName("onlineMangaScrollArea")
        self.scrollArea.viewport().setObjectName("onlineMangaScrollViewport")
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollWidget = QWidget(self.scrollArea)
        self.scrollWidget.setObjectName("onlineMangaScrollWidget")
        self.gridLayout = QGridLayout(self.scrollWidget)
        self.gridLayout.setContentsMargins(0, 4, 0, 12)
        self.gridLayout.setSpacing(14)
        self.gridLayout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scrollArea.setWidget(self.scrollWidget)

        self.previousButton = PushButton(FIF.LEFT_ARROW, self.tr("上一页"), self)
        self.nextButton = PushButton(self.tr("下一页"), self)
        self.nextButton.setIcon(FIF.RIGHT_ARROW)
        self.pageLabel = BodyLabel(self.tr("第 1 页"), self)
        self.previousButton.clicked.connect(self.previousPage)
        self.nextButton.clicked.connect(self.nextPage)
        self.previousButton.setEnabled(False)
        self.nextButton.setEnabled(False)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.previousButton)
        footer.addWidget(self.pageLabel)
        footer.addWidget(self.nextButton)
        footer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 24)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addLayout(search_row)
        layout.addWidget(self.resultLabel)
        layout.addWidget(self.scrollArea, 1)
        layout.addLayout(footer)

        cfg.onlineEhSite.valueChanged.connect(self._syncSite)
        cfg.onlineEhViewMode.valueChanged.connect(self._syncViewMode)
        cfg.onlineEhThumbnailConcurrency.valueChanged.connect(
            self._setCoverConcurrency
        )
        StyleSheet.ONLINE_MANGA_INTERFACE.apply(self)

    @property
    def currentState(self):
        return self._site_states[self._current_site]

    def showEvent(self, event):
        super().showEvent(event)
        self._scheduleRelayout()
        if not self._auto_load_on_show:
            return
        first_activation = not self._activated
        self._activated = True
        if first_activation or self._rendered_site != self._current_site:
            QTimer.singleShot(0, self._activateCurrentSite)

    def closeEvent(self, event):
        self._saveActiveState()
        self.cancelLoad()
        super().closeEvent(event)

    def _syncSite(self, site):
        if site not in self._site_states:
            return
        self.siteSwitch.setCurrentItem(site)
        if site == self._current_site:
            return
        self._saveActiveState()
        self.cancelLoad()
        self._current_site = site
        if self._activated and self.isVisible():
            self._activateCurrentSite()

    def setSite(self, site):
        if site not in self._site_states:
            return
        if site == cfg.get(cfg.onlineEhSite):
            if self._activated and self.currentState.current_page is None:
                self._activateCurrentSite()
            return
        cfg.set(cfg.onlineEhSite, site)

    def setViewMode(self, mode):
        if mode not in ("card", "extended"):
            return
        if mode == cfg.get(cfg.onlineEhViewMode):
            self._updateViewModeButton(mode)
            return
        cfg.set(cfg.onlineEhViewMode, mode)

    def toggleViewMode(self):
        mode = cfg.get(cfg.onlineEhViewMode)
        self.setViewMode("extended" if mode == "card" else "card")

    def _syncViewMode(self, mode):
        if mode not in ("card", "extended"):
            return
        self._updateViewModeButton(mode)
        state = self.currentState
        if state.current_page is None or self._rendered_site != self._current_site:
            return
        position = self.scrollArea.verticalScrollBar().value()
        self._cancelCoverLoads()
        self._setItems(state.current_page.items)
        self._restoreScrollPosition(self._current_site, position)
        request = OnlinePageRequest(
            keyword=state.keyword,
            cursor=state.current_cursor,
            page_number=state.page_number,
            cursor_history=tuple(state.cursor_history),
            scroll_position=position,
        )
        self._requestPage(
            self._current_site,
            request,
            force_network=True,
            display_mode=self._siteDisplayMode(mode),
        )

    def _updateViewModeButton(self, mode):
        if mode == "extended":
            icon = FIF.TILES
            target_mode = "card"
            tool_tip = self.tr("切换到卡片布局")
        else:
            icon = FIF.MENU
            target_mode = "extended"
            tool_tip = self.tr("切换到 Extended 布局")
        self.viewModeButton.setIcon(icon)
        self.viewModeButton.setProperty("targetViewMode", target_mode)
        self.viewModeButton.setToolTip(tool_tip)
        self.viewModeButton.setAccessibleName(tool_tip)

    def _activateCurrentSite(self):
        site = self._current_site
        state = self.currentState
        self._activated = True
        self._rendered_site = site
        self.siteSwitch.setCurrentItem(site)
        self.searchEdit.setText(state.search_text or state.keyword)
        if state.current_page is not None:
            self._displayPage(site, state, state.current_page)
            return

        self._cover_data.clear()
        self._setItems(())
        self.pageLabel.setText(self.tr("第 1 页"))
        self.previousButton.setEnabled(False)
        self.nextButton.setEnabled(False)
        request = OnlinePageRequest(keyword="", cursor="", page_number=1)
        self._requestPage(
            site,
            request,
            display_mode=self._siteDisplayMode(cfg.get(cfg.onlineEhViewMode)),
        )

    def _saveActiveState(self):
        if self._rendered_site != self._current_site:
            return
        state = self.currentState
        state.search_text = self.searchEdit.text()
        state.scroll_position = self.scrollArea.verticalScrollBar().value()

    def setFilters(self, filters):
        """Set provider-defined filters without coupling them to this widget."""

        self._filters = dict(filters or {})

    def setDownloadedGids(self, gids):
        downloaded_gids = {int(gid) for gid in gids}
        if downloaded_gids == self._downloaded_gids:
            return
        self._downloaded_gids = downloaded_gids
        for card in self._cards:
            card.setDownloaded(card.item.gid in downloaded_gids)

    def setGalleryDownloaded(self, gid, downloaded=True):
        downloaded_gids = set(self._downloaded_gids)
        if downloaded:
            downloaded_gids.add(int(gid))
        else:
            downloaded_gids.discard(int(gid))
        self.setDownloadedGids(downloaded_gids)

    def _makeProvider(self, site=None):
        site = site or self._current_site
        settings = EhOnlineSettings.create(
            site=site,
            cookie=cfg.get(cfg.onlineEhCookie),
            proxy_mode=cfg.get(cfg.onlineEhProxyMode),
            manual_proxy=cfg.get(cfg.onlineEhManualProxy),
            timeout_seconds=cfg.get(cfg.onlineEhRequestTimeout),
        )
        return self._provider_factory(settings)

    def search(self, *_args):
        site = self._current_site
        state = self.currentState
        self._activated = True
        self._rendered_site = site
        keyword = self.searchEdit.text().strip()
        state.search_text = keyword
        request = OnlinePageRequest(keyword=keyword, cursor="", page_number=1)
        self._requestPage(site, request)

    def refresh(self, *_args):
        site = self._current_site
        state = self.currentState
        self._activated = True
        self._rendered_site = site
        state.search_text = self.searchEdit.text()
        request = OnlinePageRequest(
            keyword=state.keyword if state.current_page is not None else state.search_text.strip(),
            cursor=state.current_cursor if state.current_page is not None else "",
            page_number=state.page_number if state.current_page is not None else 1,
            cursor_history=tuple(state.cursor_history),
            scroll_position=self.scrollArea.verticalScrollBar().value(),
        )
        self._requestPage(site, request, force_network=True)

    def _requestPage(
        self,
        site,
        request,
        force_network=False,
        display_mode=None,
    ):
        self.cancelLoad()
        state = self._site_states[site]
        cache_key = self._pageCacheKey(request.keyword, request.cursor)
        if not force_network and cache_key in state.pages:
            page = state.pages.pop(cache_key)
            state.pages[cache_key] = page
            self._applyPage(site, cache_key, request, page)
            return

        try:
            provider = self._makeProvider(site)
        except EhOnlineError as error:
            self._setLoading(False)
            self._showError(str(error))
            return
        query = OnlineGalleryQuery(
            keyword=request.keyword,
            cursor=request.cursor,
            page_number=request.page_number,
            filters=dict(self._filters),
        )
        self.resultLabel.setText(self.tr("正在加载 {}…").format(self._siteName(site)))
        self._setLoading(True)
        worker = OnlineSearchWorker(provider, query, display_mode=display_mode)
        worker.signals.loaded.connect(
            partial(
                self._finishSearch,
                worker,
                provider,
                site,
                cache_key,
                request,
            )
        )
        worker.signals.failed.connect(partial(self._failSearch, worker, site))
        self._search_worker = worker
        self.searchThreadPool.start(worker)

    def _finishSearch(self, worker, provider, site, cache_key, request, page):
        if self._search_worker is not worker:
            return
        self._search_worker = None
        self._setLoading(False)
        self._applyPage(site, cache_key, request, page, provider)

    def _applyPage(self, site, cache_key, request, page, provider=None):
        state = self._site_states[site]
        state.keyword = request.keyword
        state.current_cursor = request.cursor
        state.page_number = request.page_number
        state.cursor_history = list(request.cursor_history)
        state.next_cursor = page.next_cursor
        state.current_page = page
        state.current_cache_key = cache_key
        state.scroll_position = request.scroll_position
        self._rememberPage(state, cache_key, page)
        if site == self._current_site and self._rendered_site == site:
            self._displayPage(site, state, page, provider)

    def _rememberPage(self, state, cache_key, page):
        state.pages.pop(cache_key, None)
        state.pages[cache_key] = page
        while len(state.pages) > MAX_MEMORY_PAGES_PER_SITE:
            state.pages.popitem(last=False)

    def _displayPage(self, site, state, page, provider=None):
        self._setLoading(False)
        self.nextButton.setEnabled(bool(page.next_cursor))
        self.previousButton.setEnabled(bool(state.cursor_history))
        self.pageLabel.setText(self.tr("第 {} 页").format(state.page_number))
        self.resultLabel.setText(
            self.tr("{} · 本页 {} 个画廊；点击卡片查看详情。").format(
                self._siteName(site), len(page.items)
            )
        )
        self._retainCurrentPageCovers(site, page.items)
        self._setItems(page.items)
        self._restoreScrollPosition(site, state.scroll_position)
        if not page.items:
            return
        if provider is None:
            provider = self._site_providers.get(site)
            if provider is None:
                try:
                    provider = self._makeProvider(site)
                except EhOnlineError:
                    return
        self._site_providers[site] = provider
        self._startCoverLoads(site, provider, page.items)

    def _failSearch(self, worker, site, message):
        if self._search_worker is not worker:
            return
        self._search_worker = None
        self._setLoading(False)
        if site == self._current_site:
            self._showError(message)

    def _showError(self, message):
        state = self.currentState
        self.resultLabel.setText(self.tr("加载失败：{}").format(message))
        self.previousButton.setEnabled(bool(state.cursor_history))
        self.nextButton.setEnabled(bool(state.next_cursor))

    def _setLoading(self, loading):
        self.searchButton.setEnabled(not loading)
        self.refreshButton.setEnabled(not loading)
        if loading:
            self.previousButton.setEnabled(False)
            self.nextButton.setEnabled(False)

    def _setItems(self, items):
        while self.gridLayout.count():
            item = self.gridLayout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        card_class = (
            OnlineGalleryExtendedCard
            if cfg.get(cfg.onlineEhViewMode) == "extended"
            else OnlineGalleryCard
        )
        self._cards = [
            card_class(
                item,
                self.scrollWidget,
                is_downloaded=item.gid in self._downloaded_gids,
                open_callback=self._openGallery,
                download_callback=self._downloadGallery,
            )
            for item in items
        ]
        self._cards_by_gid = {card.item.gid: card for card in self._cards}
        for card in self._cards:
            data = self._cover_data.get(
                self._coverMemoryKey(self._current_site, card.item)
            )
            if data:
                card.setCoverData(data)
        self._relayoutCards()
        self._scheduleRelayout()

    def _openGallery(self, item):
        site = self._rendered_site or self._current_site
        provider = self._site_providers.get(site)
        if provider is None:
            try:
                provider = self._makeProvider(site)
            except EhOnlineError as error:
                self._showError(str(error))
                return
            self._site_providers[site] = provider
        cover_data = self._cover_data.get(self._coverMemoryKey(site, item), b"")
        self.galleryActivated.emit(item, provider, cover_data)

    def _downloadGallery(self, item):
        site = self._rendered_site or self._current_site
        provider = self._site_providers.get(site)
        if provider is None:
            try:
                provider = self._makeProvider(site)
            except EhOnlineError as error:
                self._showError(str(error))
                return
            self._site_providers[site] = provider
        cover_data = self._cover_data.get(self._coverMemoryKey(site, item), b"")
        self.galleryDownloadRequested.emit(item, provider, cover_data)

    def _startCoverLoads(self, site, provider, items):
        self._cancelCoverLoads()
        cache_hours = int(cfg.get(cfg.onlineEhThumbnailCacheHours))
        for item in items:
            if not item.thumbnail_url:
                continue
            data = self._cover_data.get(self._coverMemoryKey(site, item))
            if data:
                card = self._cards_by_gid.get(item.gid)
                if card is not None:
                    card.setCoverData(data)
                continue
            worker = OnlineCoverWorker(
                provider,
                item,
                self._thumbnail_cache,
                site,
                cache_hours,
            )
            worker.signals.loaded.connect(partial(self._setCover, worker, site))
            worker.signals.finished.connect(
                partial(self._finishCoverWorker, worker)
            )
            self._cover_workers.add(worker)
            self.coverThreadPool.start(worker)

    def _setCover(self, worker, site, gid, data):
        if worker not in self._cover_workers:
            return
        if data:
            self._cover_data[self._coverMemoryKey(site, worker.item)] = data
        if site != self._current_site or self._rendered_site != site:
            return
        card = self._cards_by_gid.get(gid)
        if card is not None:
            card.setCoverData(data)

    def _finishCoverWorker(self, worker):
        self._cover_workers.discard(worker)

    def _cancelCoverLoads(self):
        for worker in tuple(self._cover_workers):
            worker.cancelled = True
        self._cover_workers.clear()
        self.coverThreadPool.clear()

    def _setCoverConcurrency(self, value):
        self.coverThreadPool.setMaxThreadCount(max(1, int(value)))

    @staticmethod
    def _coverMemoryKey(site, item):
        return site, item.thumbnail_url

    def _retainCurrentPageCovers(self, site, items):
        keys = {
            self._coverMemoryKey(site, item)
            for item in items
            if item.thumbnail_url
        }
        self._cover_data = {
            key: data for key, data in self._cover_data.items() if key in keys
        }

    def _scheduleRelayout(self):
        self._relayoutTimer.start(0)

    def _relayoutCards(self):
        while self.gridLayout.count():
            self.gridLayout.takeAt(0)
        for column in range(self.gridLayout.columnCount()):
            self.gridLayout.setColumnStretch(column, 0)

        if cfg.get(cfg.onlineEhViewMode) == "extended":
            self.gridLayout.setColumnStretch(0, 1)
            for index, card in enumerate(self._cards):
                self.gridLayout.addWidget(card, index, 0)
            return

        width = max(254, self.scrollArea.viewport().width())
        columns = max(1, (width + 14) // (254 + 14))
        for index, card in enumerate(self._cards):
            self.gridLayout.addWidget(card, index // columns, index % columns)

    def _restoreScrollPosition(self, site, position):
        self._pendingScrollRestore = site, position
        self._scrollRestoreTimer.start(0)

    def _applyPendingScrollPosition(self):
        pending = self._pendingScrollRestore
        self._pendingScrollRestore = None
        if pending is None:
            return
        site, position = pending
        if site == self._current_site and self._rendered_site == site:
            self.scrollArea.verticalScrollBar().setValue(position)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayoutCards()
        self._scheduleRelayout()

    def nextPage(self):
        state = self.currentState
        if not state.next_cursor:
            return
        history = tuple(state.cursor_history + [state.next_cursor])
        request = OnlinePageRequest(
            keyword=state.keyword,
            cursor=state.next_cursor,
            page_number=state.page_number + 1,
            cursor_history=history,
        )
        self._requestPage(self._current_site, request)

    def previousPage(self):
        state = self.currentState
        if not state.cursor_history:
            return
        history = tuple(state.cursor_history[:-1])
        cursor = history[-1] if history else ""
        request = OnlinePageRequest(
            keyword=state.keyword,
            cursor=cursor,
            page_number=max(1, state.page_number - 1),
            cursor_history=history,
        )
        self._requestPage(self._current_site, request)

    def cancelLoad(self):
        if self._search_worker is not None:
            self._search_worker.cancelled = True
            self._search_worker = None
        self._cancelCoverLoads()

    def _pageCacheKey(self, keyword, cursor):
        return keyword, cursor, repr(self._freezeCacheValue(self._filters))

    @classmethod
    def _freezeCacheValue(cls, value):
        if isinstance(value, dict):
            return tuple(
                sorted((str(key), cls._freezeCacheValue(item)) for key, item in value.items())
            )
        if isinstance(value, (list, tuple)):
            return tuple(cls._freezeCacheValue(item) for item in value)
        if isinstance(value, set):
            return tuple(
                sorted(
                    (cls._freezeCacheValue(item) for item in value),
                    key=repr,
                )
            )
        try:
            hash(value)
        except TypeError:
            return repr(value)
        return value

    @staticmethod
    def _siteName(site):
        return "ExHentai" if site == "exhentai" else "E-Hentai"

    @staticmethod
    def _siteDisplayMode(view_mode):
        return "extended" if view_mode == "extended" else "compact"
