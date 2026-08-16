from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

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
        self.setFixedHeight(132)

        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.clicked.connect(
            lambda checked: self.selectionChanged.emit(entry.key, checked)
        )
        self.coverLabel = QLabel(self)
        self.coverLabel.setFixedSize(72, 96)
        self.coverLabel.setAlignment(Qt.AlignCenter)
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
        self.pathLabel = CaptionLabel(str(entry.folder), self)
        self.pathLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pathLabel.setToolTip(str(entry.folder))
        self.issueLabel = CaptionLabel(entry.issue or "", self)
        self.issueLabel.setVisible(bool(entry.issue))

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        text_layout.addWidget(self.titleLabel)
        text_layout.addWidget(self.metaLabel)
        text_layout.addWidget(self.pathLabel)
        text_layout.addWidget(self.issueLabel)
        text_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        layout.addWidget(self.selectionCheckBox, 0, Qt.AlignVCenter)
        layout.addWidget(self.coverLabel, 0, Qt.AlignVCenter)
        layout.addLayout(text_layout, 1)

    def _setCover(self):
        pixmap = QPixmap()
        if self.entry.cover_path is not None:
            pixmap.load(str(self.entry.cover_path))
        if pixmap.isNull():
            pixmap = FIF.FOLDER.icon().pixmap(QSize(48, 48))
        self.coverLabel.setPixmap(
            pixmap.scaled(
                self.coverLabel.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

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
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(36, 0, 36, 28)
        self.contentLayout.setSpacing(10)
        self.contentLayout.setAlignment(Qt.AlignTop)
        self.emptyLabel = BodyLabel(self.tr("尚未扫描"), self.contentWidget)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.contentLayout.addWidget(self.emptyLabel)

        scroll = ScrollArea(self)
        scroll.setWidget(self.contentWidget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#libraryOrganizerContent { background: transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(scroll, 1)
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
            self.contentLayout.addWidget(card)
        self.emptyLabel.setText(
            self.tr("没有发现未登记的本地资源目录")
            if not records
            else ""
        )
        self.emptyLabel.setVisible(not records)
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
        self.setBusy(False)

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
