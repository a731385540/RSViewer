from functools import partial

from PySide6.QtCore import QThreadPool, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    PushButton,
    ScrollArea,
    SearchLineEdit,
    SegmentedWidget,
    TitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.config import cfg
from app.domain.online_gallery import OnlineGalleryQuery
from app.sources.eh_online_source import (
    EhOnlineError,
    EhOnlineSettings,
    create_eh_online_provider,
)
from app.workers.eh_online_worker import OnlineCoverWorker, OnlineSearchWorker


class OnlineGalleryCard(CardWidget):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(230)
        self.setMinimumHeight(330)

        self.coverLabel = QLabel(self)
        self.coverLabel.setAlignment(Qt.AlignCenter)
        self.coverLabel.setFixedHeight(238)
        self.coverLabel.setText(
            self.tr("加载封面…") if item.thumbnail_url else self.tr("封面不可用")
        )
        self.coverLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.titleLabel = BodyLabel(item.title, self)
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setToolTip(item.title)
        self.titleLabel.setMaximumHeight(44)
        details = [part for part in (item.category, item.posted) if part]
        if item.page_count:
            details.append(self.tr(f"{item.page_count} 页"))
        self.detailLabel = CaptionLabel(" · ".join(details), self)
        self.detailLabel.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(7)
        layout.addWidget(self.coverLabel)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.detailLabel)
        layout.addStretch(1)
        self.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(item.url)))

    def setCoverData(self, data: bytes):
        image = QImage.fromData(data)
        if image.isNull():
            self.coverLabel.setText(self.tr("封面不可用"))
            return
        pixmap = QPixmap.fromImage(image).scaled(
            self.coverLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.coverLabel.setText("")
        self.coverLabel.setPixmap(pixmap)


class OnlineMangaInterface(QWidget):
    """Search-only online EH/EX browser; gallery pages open in the system browser."""

    def __init__(self, parent=None, provider_factory=create_eh_online_provider):
        super().__init__(parent)
        self.setObjectName("onlineMangaInterface")
        self._provider_factory = provider_factory
        self._search_worker = None
        self._cover_worker = None
        self._cards = []
        self._cards_by_gid = {}
        self._page_number = 1
        self._cursor_history = []
        self._next_cursor = ""
        self._filters = {}
        self.threadPool = QThreadPool(self)
        self.threadPool.setMaxThreadCount(2)

        self.titleLabel = TitleLabel(self.tr("在线资源"), self)
        self.siteSwitch = SegmentedWidget(self)
        self.siteSwitch.addItem("ehentai", "E-Hentai", lambda: self.setSite("ehentai"))
        self.siteSwitch.addItem("exhentai", "ExHentai", lambda: self.setSite("exhentai"))
        self.siteSwitch.setCurrentItem(cfg.get(cfg.onlineEhSite))

        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText(self.tr("搜索标题、作者或标签；留空显示最新画廊"))
        self.searchEdit.setMinimumWidth(320)
        self.searchEdit.searchSignal.connect(self.search)
        self.searchEdit.returnPressed.connect(self.search)
        self.searchButton = PushButton(FIF.SEARCH, self.tr("搜索"), self)
        self.searchButton.clicked.connect(self.search)

        header = QHBoxLayout()
        header.addWidget(self.titleLabel)
        header.addStretch(1)
        header.addWidget(self.siteSwitch)

        search_row = QHBoxLayout()
        search_row.addWidget(self.searchEdit, 1)
        search_row.addWidget(self.searchButton)

        self.resultLabel = BodyLabel(
            self.tr("爬虫接口和运行配置已就绪；接入 provider 后可在这里搜索。"),
            self,
        )
        self.resultLabel.setWordWrap(True)
        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollWidget = QWidget(self.scrollArea)
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

    def _syncSite(self, site):
        if site in {"ehentai", "exhentai"}:
            self.siteSwitch.setCurrentItem(site)
            self._cursor_history = []
            self._page_number = 1
            self._next_cursor = ""

    def setSite(self, site):
        if site == cfg.get(cfg.onlineEhSite):
            return
        cfg.set(cfg.onlineEhSite, site)

    def setFilters(self, filters):
        """Set provider-defined filters without coupling them to this widget."""

        self._filters = dict(filters or {})

    def _makeProvider(self):
        settings = EhOnlineSettings.create(
            site=cfg.get(cfg.onlineEhSite),
            cookie=cfg.get(cfg.onlineEhCookie),
            proxy_mode=cfg.get(cfg.onlineEhProxyMode),
            manual_proxy=cfg.get(cfg.onlineEhManualProxy),
            timeout_seconds=cfg.get(cfg.onlineEhRequestTimeout),
        )
        return self._provider_factory(settings)

    def search(self, *_args, cursor="", keep_history=False):
        self.cancelLoad()
        if not keep_history:
            self._cursor_history = []
            self._page_number = 1
        try:
            provider = self._makeProvider()
        except EhOnlineError as error:
            self._showError(str(error))
            return
        query = OnlineGalleryQuery(
            keyword=self.searchEdit.text().strip(),
            cursor=cursor,
            page_number=self._page_number,
            filters=dict(self._filters),
        )
        self.resultLabel.setText(self.tr("正在调用在线 provider…"))
        self.searchButton.setEnabled(False)
        self.previousButton.setEnabled(False)
        self.nextButton.setEnabled(False)
        worker = OnlineSearchWorker(provider, query)
        worker.signals.loaded.connect(partial(self._finishSearch, worker, provider))
        worker.signals.failed.connect(partial(self._failSearch, worker))
        self._search_worker = worker
        self.threadPool.start(worker)

    def _finishSearch(self, worker, provider, page):
        if self._search_worker is not worker:
            return
        self._search_worker = None
        self.searchButton.setEnabled(True)
        self._next_cursor = page.next_cursor
        self.nextButton.setEnabled(bool(page.next_cursor))
        self.previousButton.setEnabled(bool(self._cursor_history))
        self.pageLabel.setText(self.tr(f"第 {self._page_number} 页"))
        self.resultLabel.setText(self.tr(f"本页 {len(page.items)} 个画廊；点击卡片在浏览器中打开。"))
        self._setItems(page.items)
        if page.items:
            cover_worker = OnlineCoverWorker(provider, page.items)
            cover_worker.signals.loaded.connect(partial(self._setCover, cover_worker))
            self._cover_worker = cover_worker
            self.threadPool.start(cover_worker)

    def _failSearch(self, worker, message):
        if self._search_worker is not worker:
            return
        self._search_worker = None
        self.searchButton.setEnabled(True)
        self._showError(message)

    def _showError(self, message):
        self.resultLabel.setText(self.tr(f"加载失败：{message}"))
        self.previousButton.setEnabled(bool(self._cursor_history))
        self.nextButton.setEnabled(bool(self._next_cursor))

    def _setItems(self, items):
        while self.gridLayout.count():
            item = self.gridLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards = [OnlineGalleryCard(item, self.scrollWidget) for item in items]
        self._cards_by_gid = {card.item.gid: card for card in self._cards}
        self._relayoutCards()

    def _setCover(self, worker, gid, data):
        if self._cover_worker is not worker:
            return
        card = self._cards_by_gid.get(gid)
        if card is not None:
            card.setCoverData(data)

    def _relayoutCards(self):
        width = max(230, self.scrollArea.viewport().width())
        columns = max(1, width // 244)
        for index, card in enumerate(self._cards):
            self.gridLayout.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayoutCards()

    def nextPage(self):
        if not self._next_cursor:
            return
        self._cursor_history.append(self._next_cursor)
        self._page_number += 1
        self.search(cursor=self._next_cursor, keep_history=True)

    def previousPage(self):
        if not self._cursor_history:
            return
        self._cursor_history.pop()
        self._page_number = max(1, self._page_number - 1)
        cursor = self._cursor_history[-1] if self._cursor_history else ""
        self.search(cursor=cursor, keep_history=True)

    def cancelLoad(self):
        if self._search_worker is not None:
            self._search_worker.cancelled = True
            self._search_worker = None
        if self._cover_worker is not None:
            self._cover_worker.cancelled = True
            self._cover_worker = None
