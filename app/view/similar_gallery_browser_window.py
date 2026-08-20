from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentWindow,
    SubtitleLabel,
    TitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from app.common.style_sheet import StyleSheet
from app.view.manga_detail_interface import MangaDetailInterface


class SimilarGalleryResultRow(QWidget):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.setObjectName("similarGalleryResultRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        cover = QLabel(self)
        cover.setFixedSize(72, 96)
        cover.setAlignment(Qt.AlignCenter)
        cover_path = item.thumbnail_path or item.cover_path
        pixmap = QPixmap(str(cover_path))
        if pixmap.isNull():
            cover.setText(self.tr("无封面"))
        else:
            cover.setPixmap(
                pixmap.scaled(
                    cover.size(),
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
            )

        title = SubtitleLabel(item.display_title, self)
        title.setWordWrap(True)
        metadata = BodyLabel(
            self.tr("{} · {} 页").format(item.category_name, item.page_count), self
        )
        secondary = CaptionLabel(item.secondary_title, self)
        secondary.setWordWrap(True)
        secondary.setVisible(bool(item.secondary_title))

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 2, 0, 2)
        text_layout.setSpacing(4)
        text_layout.addWidget(title)
        text_layout.addWidget(secondary)
        text_layout.addWidget(metadata)
        text_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(14)
        layout.addWidget(cover)
        layout.addLayout(text_layout, 1)


class SimilarGalleryBrowserWindow(FluentWindow):
    """One reusable non-modal window for the latest selected-title search."""

    readRequested = Signal(object, int)
    folderOpenRequested = Signal(object)
    readingRecordClearRequested = Signal(int)
    selectedTitleSearchRequested = Signal(int, str)

    def __init__(self, source, repository, tag_search_index=None, parent=None):
        super().__init__(parent)
        self.setObjectName("similarGalleryBrowserWindow")
        self.setWindowTitle(self.tr("相似画廊浏览窗口"))
        self.setWindowIcon(FIF.SEARCH.icon())
        self.resize(980, 720)
        self._items = {}
        self._boundOwner = None
        self._actionConnections = ()
        self.navigationInterface.hide()
        self.widgetLayout.setContentsMargins(0, 48, 0, 0)

        self.stack = self.stackedWidget
        self.stack.setAnimationEnabled(False)
        self.resultPage = QWidget(self.stack)
        self.resultPage.setObjectName("similarGalleryResultPage")
        self.resultPage.setAttribute(Qt.WA_StyledBackground, True)
        result_layout = QVBoxLayout(self.resultPage)
        result_layout.setContentsMargins(24, 22, 24, 22)
        result_layout.setSpacing(12)
        self.titleLabel = TitleLabel(self.tr("相似画廊"), self.resultPage)
        self.summaryLabel = BodyLabel("", self.resultPage)
        self.resultList = QListWidget(self.resultPage)
        self.resultList.setObjectName("similarGalleryResultList")
        self.resultList.setIconSize(QSize(72, 96))
        self.resultList.setSpacing(4)
        self.resultList.itemDoubleClicked.connect(self._openResult)
        result_layout.addWidget(self.titleLabel)
        result_layout.addWidget(self.summaryLabel)
        result_layout.addWidget(self.resultList, 1)

        self.detail = MangaDetailInterface(
            source,
            repository,
            self.stack,
            tag_search_index=tag_search_index,
        )
        self.detail.backRequested.connect(self.showResults)
        self.detail.readRequested.connect(self.readRequested)
        self.detail.folderOpenRequested.connect(self.folderOpenRequested)
        self.detail.readingRecordClearRequested.connect(
            self.readingRecordClearRequested
        )
        self.detail.selectedTitleSearchRequested.connect(
            self.selectedTitleSearchRequested
        )
        self.stack.addWidget(self.resultPage)
        self.stack.addWidget(self.detail)
        self.stack.setCurrentWidget(self.resultPage)
        StyleSheet.SIMILAR_GALLERY_BROWSER_WINDOW.apply(self)

    def bindActions(
        self,
        owner,
        read_action,
        folder_action,
        clear_action,
        search_action,
    ):
        """Route singleton-window actions without blind signal disconnection."""
        if self._boundOwner is owner:
            return
        for signal, slot in self._actionConnections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._boundOwner = owner
        self._actionConnections = (
            (self.readRequested, read_action),
            (self.folderOpenRequested, folder_action),
            (self.readingRecordClearRequested, clear_action),
            (self.selectedTitleSearchRequested, search_action),
        )
        for signal, slot in self._actionConnections:
            signal.connect(slot)

    def setSource(self, source):
        self.detail.setSource(source)

    def setSearch(self, record, items):
        self.detail.cancelLoads()
        self._items = {int(item.gid): item for item in items}
        self.resultList.clear()
        for item in items:
            row = QListWidgetItem(self.resultList)
            row.setData(Qt.UserRole, int(item.gid))
            row.setSizeHint(QSize(0, 116))
            self.resultList.setItemWidget(
                row, SimilarGalleryResultRow(item, self.resultList)
            )
        self.summaryLabel.setText(
            self.tr("“{}” · {} 个结果 · 双击查看详情").format(
                record.selected_text, len(items)
            )
        )
        self.showResults()

    def showResults(self):
        self.stack.setCurrentWidget(self.resultPage)

    def _openResult(self, row):
        item = self._items.get(int(row.data(Qt.UserRole)))
        if item is None:
            return
        self.detail.setManga(item)
        self.stack.setCurrentWidget(self.detail)

    def closeEvent(self, event):
        self.detail.shutdown(1500)
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.titleBar.move(0, 0)
        self.titleBar.resize(self.width(), self.titleBar.height())
