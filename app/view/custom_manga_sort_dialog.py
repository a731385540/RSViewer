from PySide6.QtCore import QObject, QRect, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidgetItem,
    QStyleOptionViewItem,
)
from qfluentwidgets import (
    BodyLabel,
    ListItemDelegate,
    ListWidget,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
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

        self.titleLabel = SubtitleLabel(self.tr("自定排序"), self.widget)
        self.scopeLabel = BodyLabel(str(title), self.widget)
        self.scopeLabel.setToolTip(str(title))
        self.listWidget = _CustomSortListWidget(self.widget)
        self.listWidget.setMinimumHeight(500)
        self.upButton = PushButton(FIF.UP, self.tr("上移"), self.widget)
        self.downButton = PushButton(FIF.DOWN, self.tr("下移"), self.widget)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.scopeLabel)
        self.viewLayout.addWidget(self.listWidget, 1)
        self.buttonLayout.insertWidget(0, self.upButton)
        self.buttonLayout.insertWidget(1, self.downButton)
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

        self.upButton.clicked.connect(lambda: self.listWidget.moveSelection(-1))
        self.downButton.clicked.connect(lambda: self.listWidget.moveSelection(1))
        self.listWidget.itemSelectionChanged.connect(self._updateMoveButtons)
        self.listWidget.orderChanged.connect(self._updateMoveButtons)
        self._updateMoveButtons()
        self._startCoverLoad(items)

    def orderedGids(self):
        return tuple(
            int(self.listWidget.item(row).data(Qt.UserRole))
            for row in range(self.listWidget.count())
        )

    def _updateMoveButtons(self):
        selected_rows = sorted(
            self.listWidget.row(item) for item in self.listWidget.selectedItems()
        )
        self.upButton.setEnabled(bool(selected_rows) and selected_rows[0] > 0)
        self.downButton.setEnabled(
            bool(selected_rows)
            and selected_rows[-1] < self.listWidget.count() - 1
        )

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
