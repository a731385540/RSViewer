import random
import re
import unicodedata

from PySide6.QtCore import (
    QItemSelectionModel,
    QObject,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidgetItem,
    QStyleOptionViewItem,
)
from qfluentwidgets import (
    BodyLabel,
    ListItemDelegate,
    ListWidget,
    MessageBoxBase,
    SearchLineEdit,
    SubtitleLabel,
    ToolButton,
)
from qfluentwidgets import FluentIcon as FIF


class _CustomSortRowDelegate(ListItemDelegate):
    HANDLE_WIDTH = 42

    def paint(self, painter, option, index):
        row_rect = QRect(option.rect)
        content_option = QStyleOptionViewItem(option)
        content_option.rect.setRight(
            max(content_option.rect.left(), option.rect.right() - self.HANDLE_WIDTH)
        )
        super().paint(painter, content_option, index)
        icon_rect = QRect(
            row_rect.right() - 31,
            row_rect.center().y() - 10,
            20,
            20,
        )
        FIF.MENU.icon().paint(painter, icon_rect, Qt.AlignCenter)


class _CustomSortListWidget(ListWidget):
    orderChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setIconSize(QSize(36, 48))
        self.setItemDelegate(_CustomSortRowDelegate(self))
        self.setSpacing(2)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()

    def selectedGids(self):
        return {
            int(item.data(Qt.UserRole))
            for item in self.selectedItems()
        }

    def _orderedItems(self):
        return [self.item(row) for row in range(self.count())]

    def _replaceItems(self, ordered_items):
        ordered_items = list(ordered_items)
        selected = self.selectedGids()
        current = self.currentItem()
        current_gid = (
            int(current.data(Qt.UserRole)) if current is not None else None
        )
        signals_blocked = self.blockSignals(True)
        self.setUpdatesEnabled(False)
        try:
            while self.count():
                self.takeItem(0)
            for item in ordered_items:
                self.addItem(item)
                item.setSelected(int(item.data(Qt.UserRole)) in selected)
                if int(item.data(Qt.UserRole)) == current_gid:
                    self.setCurrentItem(item, QItemSelectionModel.NoUpdate)
        finally:
            self.setUpdatesEnabled(True)
            self.blockSignals(signals_blocked)
        self.viewport().update()
        self.orderChanged.emit()

    def moveSelection(self, direction):
        direction = -1 if int(direction) < 0 else 1
        selected = self.selectedGids()
        if not selected:
            return
        if direction < 0:
            rows = range(1, self.count())
        else:
            rows = range(self.count() - 2, -1, -1)
        for row in rows:
            current = self.item(row)
            adjacent_row = row + direction
            adjacent = self.item(adjacent_row)
            if (
                int(current.data(Qt.UserRole)) in selected
                and int(adjacent.data(Qt.UserRole)) not in selected
            ):
                item = self.takeItem(row)
                self.insertItem(adjacent_row, item)
        for row in range(self.count()):
            item = self.item(row)
            item.setSelected(int(item.data(Qt.UserRole)) in selected)
        self.orderChanged.emit()

    def moveSelectionToBoundary(self, first):
        selected = self.selectedGids()
        if not selected:
            return
        items = self._orderedItems()
        selected_items = [
            item for item in items if int(item.data(Qt.UserRole)) in selected
        ]
        remaining_items = [
            item for item in items if int(item.data(Qt.UserRole)) not in selected
        ]
        self._replaceItems(
            selected_items + remaining_items
            if first
            else remaining_items + selected_items
        )

    def shuffleItems(self):
        items = self._orderedItems()
        random.shuffle(items)
        self._replaceItems(items)

    def sortItemsByName(self):
        self._replaceItems(
            sorted(
                self._orderedItems(),
                key=lambda item: _naturalNameKey(item.text()),
            )
        )

    def reverseItems(self):
        self._replaceItems(reversed(self._orderedItems()))


def _naturalNameKey(value):
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", normalized)
        if part
    )


def _normalizedSearchText(value):
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


class _CustomSortSearchLineEdit(SearchLineEdit):
    navigateRequested = Signal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_PageUp:
            self.navigateRequested.emit(-1)
            event.accept()
            return
        if event.key() in (Qt.Key_PageDown, Qt.Key_Return, Qt.Key_Enter):
            self.navigateRequested.emit(1)
            event.accept()
            return
        super().keyPressEvent(event)


class _CustomSortCoverSignals(QObject):
    imageReady = Signal(int, object)


class _CustomSortCoverWorker(QRunnable):
    def __init__(self, source, items):
        super().__init__()
        self.source = source
        self.items = tuple(items)
        self.cancelled = False
        self.signals = _CustomSortCoverSignals()

    def run(self):
        for item in self.items:
            if self.cancelled:
                return
            image = QImage()
            try:
                path = self.source.find_cover_path(item)
                image = self._readImage(path)
                if image.isNull():
                    first_page = self.source.find_first_page_path(item)
                    if first_page != path:
                        image = self._readImage(first_page)
            except (OSError, RuntimeError):
                image = QImage()
            if self.cancelled:
                return
            try:
                self.signals.imageReady.emit(int(item.gid), image)
            except RuntimeError:
                return

    @staticmethod
    def _readImage(path):
        if path is None:
            return QImage()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            source_size.scale(QSize(36, 48), Qt.KeepAspectRatio)
            reader.setScaledSize(source_size)
        return reader.read()


class CustomMangaSortDialog(MessageBoxBase):
    """Edit one category or taxonomy node's persistent manga order."""

    def __init__(self, title, items, source, parent=None):
        super().__init__(parent)
        self.widget.setMinimumSize(620, 650)
        self._source = source
        self._items = {int(item.gid): item for item in items}
        self._listItems = {}
        self._coverWorker = None
        self._searchMatches = []
        self._searchPosition = -1
        self._searchActiveGid = None

        self.titleLabel = SubtitleLabel(self.tr("自定排序"), self.widget)
        self.scopeLabel = BodyLabel(str(title), self.widget)
        self.scopeLabel.setToolTip(str(title))
        self.listWidget = _CustomSortListWidget(self.widget)
        self.listWidget.setMinimumHeight(500)
        self.searchEdit = _CustomSortSearchLineEdit(self.widget)
        self.searchEdit.setPlaceholderText(self.tr("定位标题"))
        self.searchPreviousButton = self._createOrderButton(
            FIF.UP, self.tr("上一个匹配项 (Page Up)")
        )
        self.searchNextButton = self._createOrderButton(
            FIF.DOWN, self.tr("下一个匹配项 (Page Down)")
        )
        self.searchPositionLabel = BodyLabel("", self.widget)
        self.searchPositionLabel.setAlignment(Qt.AlignCenter)
        self.searchPositionLabel.setFixedWidth(58)
        self.searchLayout = QHBoxLayout()
        self.searchLayout.setContentsMargins(0, 0, 0, 0)
        self.searchLayout.setSpacing(6)
        self.searchLayout.addWidget(self.searchEdit, 1)
        self.searchLayout.addWidget(self.searchPreviousButton)
        self.searchLayout.addWidget(self.searchNextButton)
        self.searchLayout.addWidget(self.searchPositionLabel)
        self.moveFirstButton = self._createOrderButton(
            FIF.CARE_UP_SOLID, self.tr("将选中项移到第一个")
        )
        self.upButton = self._createOrderButton(FIF.UP, self.tr("上移"))
        self.downButton = self._createOrderButton(FIF.DOWN, self.tr("下移"))
        self.moveLastButton = self._createOrderButton(
            FIF.CARE_DOWN_SOLID, self.tr("将选中项移到最后一个")
        )
        self.shuffleButton = self._createOrderButton(
            FIF.SYNC, self.tr("随机打乱")
        )
        self.nameSortButton = self._createOrderButton(
            FIF.FONT, self.tr("按名称排序")
        )
        self.reverseButton = self._createOrderButton(
            FIF.ROTATE, self.tr("倒序排列")
        )
        self.orderToolLayout = QHBoxLayout()
        self.orderToolLayout.setContentsMargins(0, 0, 0, 0)
        self.orderToolLayout.setSpacing(6)
        for button in (
            self.moveFirstButton,
            self.upButton,
            self.downButton,
            self.moveLastButton,
        ):
            self.orderToolLayout.addWidget(button)
        self.orderToolLayout.addSpacing(8)
        for button in (
            self.shuffleButton,
            self.nameSortButton,
            self.reverseButton,
        ):
            self.orderToolLayout.addWidget(button)
        self.orderToolLayout.addStretch(1)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.scopeLabel)
        self.viewLayout.addLayout(self.searchLayout)
        self.viewLayout.addLayout(self.orderToolLayout)
        self.viewLayout.addWidget(self.listWidget, 1)
        self.yesButton.setText(self.tr("保存"))
        self.cancelButton.setText(self.tr("取消"))

        for item in items:
            row = QListWidgetItem(item.display_title, self.listWidget)
            row.setData(Qt.UserRole, int(item.gid))
            row.setToolTip(item.display_title)
            row.setSizeHint(QSize(0, 58))
            row.setFlags(
                row.flags()
                | Qt.ItemIsDragEnabled
                | Qt.ItemIsDropEnabled
            )
            self._listItems[int(item.gid)] = row

        self.moveFirstButton.clicked.connect(
            lambda: self.listWidget.moveSelectionToBoundary(True)
        )
        self.upButton.clicked.connect(lambda: self.listWidget.moveSelection(-1))
        self.downButton.clicked.connect(lambda: self.listWidget.moveSelection(1))
        self.moveLastButton.clicked.connect(
            lambda: self.listWidget.moveSelectionToBoundary(False)
        )
        self.shuffleButton.clicked.connect(self.listWidget.shuffleItems)
        self.nameSortButton.clicked.connect(self.listWidget.sortItemsByName)
        self.reverseButton.clicked.connect(self.listWidget.reverseItems)
        self.searchEdit.textChanged.connect(self._refreshSearchMatches)
        self.searchEdit.searchSignal.connect(
            lambda _query: self._navigateSearch(1)
        )
        self.searchEdit.navigateRequested.connect(self._navigateSearch)
        self.searchPreviousButton.clicked.connect(
            lambda: self._navigateSearch(-1)
        )
        self.searchNextButton.clicked.connect(lambda: self._navigateSearch(1))
        self.listWidget.itemSelectionChanged.connect(self._updateMoveButtons)
        self.listWidget.orderChanged.connect(self._updateMoveButtons)
        self.listWidget.orderChanged.connect(self._refreshSearchAfterReorder)
        self._updateMoveButtons()
        self._updateSearchControls()
        self._startCoverLoad(items)

    def _createOrderButton(self, icon, tooltip):
        button = ToolButton(icon, self.widget)
        button.setFixedSize(34, 34)
        button.setToolTip(tooltip)
        return button

    def orderedGids(self):
        return tuple(
            int(self.listWidget.item(row).data(Qt.UserRole))
            for row in range(self.listWidget.count())
        )

    def _matchingSearchItems(self):
        query = _normalizedSearchText(self.searchEdit.text())
        if not query:
            return []
        return [
            self.listWidget.item(row)
            for row in range(self.listWidget.count())
            if query
            in _normalizedSearchText(self.listWidget.item(row).text())
        ]

    def _refreshSearchMatches(self, _text=""):
        self._searchMatches = self._matchingSearchItems()
        self._searchPosition = 0 if self._searchMatches else -1
        self._searchActiveGid = None
        if self._searchPosition >= 0:
            self._locateSearchMatch()
        else:
            self._updateSearchControls()

    def _refreshSearchAfterReorder(self):
        self._searchMatches = self._matchingSearchItems()
        matching_position = next(
            (
                position
                for position, item in enumerate(self._searchMatches)
                if int(item.data(Qt.UserRole)) == self._searchActiveGid
            ),
            None,
        )
        self._searchPosition = (
            matching_position
            if matching_position is not None
            else (0 if self._searchMatches else -1)
        )
        if self._searchMatches:
            self._searchActiveGid = int(
                self._searchMatches[self._searchPosition].data(Qt.UserRole)
            )
        else:
            self._searchActiveGid = None
        self._updateSearchControls()

    def _navigateSearch(self, direction):
        if not self._searchMatches:
            self._refreshSearchMatches()
            if not self._searchMatches:
                return
        step = -1 if int(direction) < 0 else 1
        self._searchPosition = (
            self._searchPosition + step
        ) % len(self._searchMatches)
        self._locateSearchMatch()

    def _locateSearchMatch(self):
        if not 0 <= self._searchPosition < len(self._searchMatches):
            self._updateSearchControls()
            return
        item = self._searchMatches[self._searchPosition]
        self._searchActiveGid = int(item.data(Qt.UserRole))
        self.listWidget.setCurrentItem(
            item, QItemSelectionModel.ClearAndSelect
        )
        self.listWidget.scrollToItem(
            item, QAbstractItemView.PositionAtCenter
        )
        self._updateSearchControls()

    def _updateSearchControls(self):
        has_matches = bool(self._searchMatches)
        self.searchPreviousButton.setEnabled(has_matches)
        self.searchNextButton.setEnabled(has_matches)
        if not self.searchEdit.text().strip():
            self.searchPositionLabel.clear()
        elif has_matches:
            self.searchPositionLabel.setText(
                f"{self._searchPosition + 1} / {len(self._searchMatches)}"
            )
        else:
            self.searchPositionLabel.setText("0 / 0")

    def _updateMoveButtons(self):
        selected_rows = sorted(
            self.listWidget.row(item) for item in self.listWidget.selectedItems()
        )
        can_move_up = bool(selected_rows) and selected_rows[0] > 0
        can_move_down = (
            bool(selected_rows)
            and selected_rows[-1] < self.listWidget.count() - 1
        )
        self.moveFirstButton.setEnabled(can_move_up)
        self.upButton.setEnabled(can_move_up)
        self.downButton.setEnabled(can_move_down)
        self.moveLastButton.setEnabled(can_move_down)
        has_multiple_items = self.listWidget.count() > 1
        self.shuffleButton.setEnabled(has_multiple_items)
        self.nameSortButton.setEnabled(has_multiple_items)
        self.reverseButton.setEnabled(has_multiple_items)

    def _startCoverLoad(self, items):
        if not items:
            return
        worker = _CustomSortCoverWorker(self._source, items)
        worker.signals.imageReady.connect(
            lambda gid, image: self._setCover(worker, gid, image)
        )
        self._coverWorker = worker
        QThreadPool.globalInstance().start(worker)

    def _setCover(self, worker, gid, image):
        if self._coverWorker is not worker or image.isNull():
            return
        row = self._listItems.get(int(gid))
        if row is not None:
            row.setIcon(QIcon(QPixmap.fromImage(image)))

    def done(self, result):
        if self._coverWorker is not None:
            self._coverWorker.cancelled = True
            self._coverWorker = None
        super().done(result)

    def closeEvent(self, event):
        if self._coverWorker is not None:
            self._coverWorker.cancelled = True
            self._coverWorker = None
        super().closeEvent(event)
