from datetime import datetime

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

from app.domain.gallery_trash import (
    TRASH_DELETING,
    TRASH_FAILED,
    TRASH_MOVING,
    TRASH_RESTORING,
    TRASHED,
)


_STATE_TEXT = {
    TRASH_MOVING: "正在移入",
    TRASHED: "已在回收站",
    TRASH_RESTORING: "正在还原",
    TRASH_DELETING: "正在彻底删除",
    TRASH_FAILED: "操作中断",
}


class RecycleBinGalleryCard(SimpleCardWidget):
    selectionChanged = Signal(int, bool)

    def __init__(self, record, selected=False, menu_callback=None, parent=None):
        super().__init__(parent)
        self.record = record
        self.menuCallback = menu_callback
        self.setObjectName("recycleBinGalleryCard")
        self.selectionCheckBox = CheckBox(self)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.clicked.connect(
            lambda checked: self.selectionChanged.emit(record.gid, checked)
        )
        self.coverLabel = QLabel(self)
        self.coverLabel.setAlignment(Qt.AlignCenter)
        self.coverLabel.setObjectName("recycleBinGalleryCover")
        self.coverLabel.setStyleSheet(
            "QLabel#recycleBinGalleryCover { background: rgba(127, 127, 127, 0.12); }"
        )
        self._coverPixmap = self._loadCover()
        self.titleLabel = BodyLabel(record.title or record.dirname, self)
        self.titleLabel.setWordWrap(True)
        page_text = f" · {record.page_count} 页" if record.page_count else ""
        self.metaLabel = CaptionLabel(
            f"GID {record.gid}{page_text} · {_STATE_TEXT.get(record.state, record.state)}",
            self,
        )
        self.dirnameLabel = CaptionLabel(record.dirname, self)
        self.dirnameLabel.setToolTip(str(record.folder))
        deleted_at = datetime.fromtimestamp(record.deleted_at / 1_000_000_000)
        self.timeLabel = CaptionLabel(
            self.tr("删除于 {} ").format(deleted_at.strftime("%Y-%m-%d %H:%M")),
            self,
        )
        self.errorLabel = CaptionLabel(record.error or "", self)
        self.errorLabel.setVisible(bool(record.error))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        layout.addWidget(self.coverLabel)
        layout.addWidget(self.titleLabel)
        layout.addWidget(self.metaLabel)
        layout.addWidget(self.dirnameLabel)
        layout.addWidget(self.timeLabel)
        layout.addWidget(self.errorLabel)
        self.selectionCheckBox.move(14, 14)
        self.selectionCheckBox.raise_()
        self.setCardWidth(200)

    def _loadCover(self):
        pixmap = QPixmap()
        if self.record.cover_path is not None:
            pixmap.load(str(self.record.cover_path))
        if pixmap.isNull():
            pixmap = FIF.FOLDER.icon().pixmap(QSize(48, 48))
        return pixmap

    def _refreshCover(self):
        self.coverLabel.setPixmap(
            self._coverPixmap.scaled(
                self.coverLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def setCardWidth(self, width):
        width = max(170, int(width))
        cover_width = width - 20
        cover_height = round(cover_width * 1.36)
        self.setFixedWidth(width)
        self.coverLabel.setFixedSize(cover_width, cover_height)
        self.titleLabel.setFixedHeight(42)
        extra = 152 if self.errorLabel.isVisible() else 130
        self.setFixedHeight(cover_height + extra)
        self._refreshCover()
        self.selectionCheckBox.raise_()

    def setSelected(self, selected):
        self.selectionCheckBox.blockSignals(True)
        self.selectionCheckBox.setChecked(bool(selected))
        self.selectionCheckBox.blockSignals(False)

    def contextMenuEvent(self, event):
        if self.menuCallback is not None:
            self.menuCallback(self.record, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class RecycleBinInterface(QWidget):
    restoreRequested = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recycleBinInterface")
        self._records = {}
        self._cards = {}
        self._selected = set()
        self._busy = False
        self._lastColumns = 0
        self._relayoutPending = False

        title = SubtitleLabel(self.tr("回收站"), self)
        self.selectAllCheckBox = CheckBox(self.tr("全选"), self)
        self.selectAllCheckBox.clicked.connect(self._toggleSelectAll)
        self.countLabel = CaptionLabel(self.tr("0 项"), self)
        self.restoreButton = PushButton(FIF.SYNC, self.tr("还原"), self)
        self.deleteButton = PushButton(FIF.DELETE, self.tr("彻底删除"), self)
        self.restoreButton.clicked.connect(
            lambda: self.restoreRequested.emit(self.selectedRecords())
        )
        self.deleteButton.clicked.connect(
            lambda: self.deleteRequested.emit(self.selectedRecords())
        )

        header = QHBoxLayout()
        header.setContentsMargins(36, 28, 36, 16)
        header.setSpacing(12)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.selectAllCheckBox)
        header.addWidget(self.countLabel)
        header.addWidget(self.restoreButton)
        header.addWidget(self.deleteButton)

        self.contentWidget = QWidget()
        self.contentWidget.setObjectName("recycleBinContent")
        self.contentLayout = QGridLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(36, 0, 36, 28)
        self.contentLayout.setSpacing(16)
        self.contentLayout.setAlignment(Qt.AlignTop)
        self.emptyLabel = BodyLabel(self.tr("回收站为空"), self.contentWidget)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.contentLayout.addWidget(self.emptyLabel, 0, 0)

        self.scrollArea = ScrollArea(self)
        self.scrollArea.setWidget(self.contentWidget)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scrollArea.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#recycleBinContent { background: transparent; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.scrollArea, 1)
        self._updateSelectionState()

    def setRecords(self, records):
        records = tuple(records)
        self._records = {int(record.gid): record for record in records}
        self._selected.intersection_update(self._records)
        for card in self._cards.values():
            self.contentLayout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        for record in records:
            card = RecycleBinGalleryCard(
                record,
                record.gid in self._selected,
                self._showContextMenu,
                self.contentWidget,
            )
            card.selectionChanged.connect(self._setSelected)
            self._cards[int(record.gid)] = card
        self.emptyLabel.setVisible(not records)
        self._scheduleRelayout()
        self._updateSelectionState()

    def selectedRecords(self):
        return tuple(
            self._records[gid] for gid in self._records if gid in self._selected
        )

    def setBusy(self, busy, message=""):
        self._busy = bool(busy)
        for card in self._cards.values():
            card.setEnabled(not self._busy)
        if self._busy:
            self.countLabel.setText(str(message or self.tr("处理中")))
        else:
            self._updateSelectionState()

    def _setSelected(self, gid, selected):
        if selected:
            self._selected.add(int(gid))
        else:
            self._selected.discard(int(gid))
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
        enabled = not self._busy and selected > 0
        self.selectAllCheckBox.setEnabled(not self._busy and total > 0)
        self.restoreButton.setEnabled(enabled)
        self.deleteButton.setEnabled(enabled)
        for gid, card in self._cards.items():
            card.setSelected(gid in self._selected)
        if not self._busy:
            self.countLabel.setText(
                self.tr("已选择 {} / {} 项").format(selected, total)
                if selected
                else self.tr("{} 项").format(total)
            )

    def _showContextMenu(self, record, position):
        if record.gid not in self._selected:
            self._selected = {int(record.gid)}
            self._updateSelectionState()
        selected = self.selectedRecords()
        menu = RoundMenu(self.tr("回收站操作"), self)
        restore_action = QAction(FIF.SYNC.icon(), self.tr("还原"), menu)
        restore_action.triggered.connect(lambda: self.restoreRequested.emit(selected))
        delete_action = QAction(FIF.DELETE.icon(), self.tr("彻底删除"), menu)
        delete_action.triggered.connect(lambda: self.deleteRequested.emit(selected))
        menu.addAction(restore_action)
        menu.addAction(delete_action)
        menu.exec(position)

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
            1, (viewport_width + spacing) // (minimum_card_width + spacing)
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
