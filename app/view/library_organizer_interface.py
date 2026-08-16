from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    PushButton,
    RoundMenu,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
)
from qfluentwidgets import FluentIcon as FIF


class OrganizerGalleryCard(SimpleCardWidget):
    selectionChanged = Signal(str, bool)

    def __init__(self, entry, selected=False, menu_callback=None, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.menuCallback = menu_callback
        self.setObjectName("organizerGalleryCard")

        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.clicked.connect(
            lambda checked: self.selectionChanged.emit(entry.key, checked)
        )
        self.coverLabel = QLabel(self)
        self.coverLabel.setAlignment(Qt.AlignCenter)
        self.coverLabel.setObjectName("organizerGalleryCover")
        self.coverLabel.setStyleSheet(
            "QLabel#organizerGalleryCover { background: rgba(127, 127, 127, 0.12); }"
        )
        self._coverPixmap = QPixmap()
        self._setCover()

        self.titleLabel = BodyLabel(entry.title or entry.dirname, self)
        self.titleLabel.setWordWrap(True)
        gid_text = f"GID {entry.gid}" if entry.gid else self.tr("GID 未知")
        pages = (
            f"{entry.downloaded_pages} / {entry.page_count} 页"
            if entry.page_count
            else f"{entry.downloaded_pages} 页"
        )
        state = self.tr("可同步") if entry.syncable else self.tr("仅可删除")
        self.metaLabel = CaptionLabel(f"{gid_text} · {pages} · {state}", self)
        self.pathLabel = CaptionLabel(entry.dirname, self)
        self.pathLabel.setToolTip(str(entry.folder))
        self.issueLabel = CaptionLabel(entry.issue or "", self)
        self.issueLabel.setVisible(bool(entry.issue))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        layout.addWidget(self.coverLabel)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.metaLabel)
        layout.addWidget(self.pathLabel)
        layout.addWidget(self.issueLabel)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
        self.setCardWidth(200)

    def _setCover(self):
        pixmap = QPixmap()
        if self.entry.cover_path is not None:
            pixmap.load(str(self.entry.cover_path))
        if pixmap.isNull():
            pixmap = FIF.FOLDER.icon().pixmap(QSize(48, 48))
        self._coverPixmap = pixmap
        self._refreshCover()

    def _refreshCover(self):
        if self._coverPixmap.isNull():
            return
        self.coverLabel.setPixmap(
            self._coverPixmap.scaled(
                self.coverLabel.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def setCardWidth(self, width):
        width = max(170, int(width))
        cover_width = width - 20
        cover_height = round(cover_width * 1.36)
        self.setFixedWidth(width)
        self.coverLabel.setFixedSize(cover_width, cover_height)
        self.titleLabel.setFixedHeight(42)
        self.setFixedHeight(cover_height + (132 if self.issueLabel.isVisible() else 110))
        self._refreshCover()
        self.selectionCheckBox.raise_()

    def setSelected(self, selected):
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)

    def contextMenuEvent(self, event):
        if self.menuCallback is not None:
            self.menuCallback(self.entry, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class LibraryOrganizerInterface(QWidget):
    scanRequested = Signal()
    syncRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("libraryOrganizerInterface")
        self._records = {}
        self._cards = {}
        self._selected = set()
        self._scanned = False
        self._busy = False
        self._lastColumns = 0
        self._relayoutPending = False

        title = SubtitleLabel(self.tr("整理"), self)
        self.selectAllCheckBox = CheckBox(self.tr("全选"), self)
        self.selectAllCheckBox.clicked.connect(self._toggleSelectAll)
        self.countLabel = CaptionLabel(self.tr("尚未扫描"), self)
        self.scanButton = PushButton(FIF.SEARCH, self.tr("扫描目录"), self)
        self.scanButton.setToolTip(self.tr("扫描数据库中没有登记的本地资源目录"))
        self.scanButton.clicked.connect(lambda: self.scanRequested.emit())

        header = QHBoxLayout()
        header.setContentsMargins(36, 28, 36, 16)
        header.setSpacing(12)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.selectAllCheckBox)
        header.addWidget(self.countLabel)
        header.addWidget(self.scanButton)

        self.contentWidget = QWidget()
        self.contentWidget.setObjectName("libraryOrganizerContent")
        self.contentLayout = QGridLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(36, 0, 36, 28)
        self.contentLayout.setSpacing(16)
        self.contentLayout.setAlignment(Qt.AlignTop)
        self.emptyLabel = BodyLabel(self.tr("尚未扫描"), self.contentWidget)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.contentLayout.addWidget(self.emptyLabel, 0, 0)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidget(self.contentWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#libraryOrganizerContent { background: transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.scrollArea, 1)
        self._updateSelectionState()

    def setRecords(self, records):
        records = tuple(records)
        self._scanned = True
        self._records = {entry.key: entry for entry in records}
        self._selected.intersection_update(self._records)
        for card in self._cards.values():
            self.contentLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        for entry in records:
            card = OrganizerGalleryCard(
                entry,
                entry.key in self._selected,
                self._showContextMenu,
                self.contentWidget,
            )
            card.selectionChanged.connect(self._setSelected)
            self._cards[entry.key] = card
        self.emptyLabel.setText(
            self.tr("没有发现未登记的本地资源目录")
            if not records
            else ""
        )
        self.emptyLabel.setVisible(not records)
        self._scheduleRelayout()
        self._updateSelectionState()

    def reset(self):
        self._scanned = False
        self._selected.clear()
        self._records.clear()
        for card in self._cards.values():
            self.contentLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self.emptyLabel.setText(self.tr("尚未扫描"))
        self.emptyLabel.show()
        self._scheduleRelayout()
        self.setBusy(False)

    def _scheduleRelayout(self):
        if self._relayoutPending:
            return
        self._relayoutPending = True
        QTimer.singleShot(0, self._relayoutCards)

    def _relayoutCards(self):
        self._relayoutPending = False
        while self.contentLayout.count():
            self.contentLayout.takeAt(0)
        for column in range(self._lastColumns):
            self.contentLayout.setColumnStretch(column, 0)

        viewport_width = max(1, self.scrollArea.viewport().width() - 72)
        spacing = self.contentLayout.horizontalSpacing()
        minimum_card_width = 188
        columns = max(
            1,
            (viewport_width + spacing) // (minimum_card_width + spacing),
        )
        card_width = max(
            minimum_card_width,
            (viewport_width - spacing * (columns - 1)) // columns,
        )
        self._lastColumns = columns
        for column in range(columns):
            self.contentLayout.setColumnStretch(column, 1)

        if not self._cards:
            self.contentLayout.addWidget(self.emptyLabel, 0, 0, 1, columns)
            return
        for index, card in enumerate(self._cards.values()):
            card.setCardWidth(card_width)
            self.contentLayout.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scheduleRelayout()

    def setBusy(self, busy, message=""):
        self._busy = bool(busy)
        self.scanButton.setEnabled(not self._busy)
        self.selectAllCheckBox.setEnabled(not self._busy and bool(self._records))
        for card in self._cards.values():
            card.setEnabled(not self._busy)
        if self._busy:
            self.countLabel.setText(str(message or self.tr("处理中")))
        else:
            self._updateSelectionState()

    def selectedEntries(self):
        return tuple(
            self._records[key]
            for key in self._records
            if key in self._selected
        )

    def _setSelected(self, key, selected):
        if selected:
            self._selected.add(str(key))
        else:
            self._selected.discard(str(key))
        self._updateSelectionState()

    def _toggleSelectAll(self, checked):
        self._selected = set(self._records) if checked else set()
        self._updateSelectionState()

    def _updateSelectionState(self):
        total = len(self._records)
        selected = len(self._selected.intersection(self._records))
        self.selectAllCheckBox.blockSignals(True)
        self.selectAllCheckBox.setTristate(0 < selected < total)
        self.selectAllCheckBox.setCheckState(
            Qt.PartiallyChecked
            if 0 < selected < total
            else (Qt.Checked if total and selected == total else Qt.Unchecked)
        )
        self.selectAllCheckBox.blockSignals(False)
        self.selectAllCheckBox.setEnabled(not self._busy and total > 0)
        for key, card in self._cards.items():
            card.setSelected(key in self._selected)
        if not self._busy:
            if not self._scanned:
                text = self.tr("尚未扫描")
            elif selected:
                text = self.tr("已选择 {} / {} 项").format(selected, total)
            else:
                text = self.tr("{} 项").format(total)
            self.countLabel.setText(text)

    def _showContextMenu(self, entry, position):
        if entry.key not in self._selected:
            self._selected = {entry.key}
            self._updateSelectionState()
        selected = self.selectedEntries()
        menu = RoundMenu(self.tr("整理资源"), self)
        sync_action = QAction(FIF.SYNC.icon(), self.tr("同步到数据库"), menu)
        sync_action.setEnabled(bool(selected) and all(item.syncable for item in selected))
        sync_action.triggered.connect(lambda: self.syncRequested.emit(selected))
        delete_action = QAction(
            FIF.DELETE.icon(), self.tr("删除本地资源"), menu
        )
        delete_action.triggered.connect(lambda: self.deleteRequested.emit(selected))
        menu.addAction(sync_action)
        menu.addAction(delete_action)
        menu.exec(position)
