from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import SegmentedWidget

from app.view.local_manga_interface import LocalMangaInterface
from app.view.media_interface import MediaInterface


class MangaHistoryInterface(QWidget):
    """Local browsing history with a reserved online-history route."""

    LOCAL = "local"
    ONLINE = "online"

    def __init__(self, source, user_repository, parent=None):
        super().__init__(parent)
        self.setObjectName("mangaHistoryInterface")
        self.modeSwitch = SegmentedWidget(self)
        self.localHistoryInterface = LocalMangaInterface(
            source,
            user_repository,
            self,
            collection_kind="history",
            object_name="localMangaHistoryInterface",
        )
        self.onlineHistoryInterface = MediaInterface(
            self.tr("在线历史"),
            self.tr("在线浏览历史接口已预留，当前版本暂不记录在线内容。"),
            "onlineMangaHistoryInterface",
            self,
        )
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.localHistoryInterface)
        self.stack.addWidget(self.onlineHistoryInterface)
        self.modeSwitch.addItem(
            self.LOCAL,
            self.tr("本地历史"),
            lambda: self.stack.setCurrentWidget(self.localHistoryInterface),
        )
        self.modeSwitch.addItem(
            self.ONLINE,
            self.tr("在线历史"),
            lambda: self.stack.setCurrentWidget(self.onlineHistoryInterface),
        )
        self.modeSwitch.setCurrentItem(self.LOCAL)
        switch_container = QWidget(self)
        switch_layout = QHBoxLayout(switch_container)
        switch_layout.setContentsMargins(36, 24, 36, 0)
        switch_layout.addWidget(self.modeSwitch)
        switch_layout.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(switch_container)
        layout.addWidget(self.stack, 1)

    def setSource(self, source):
        self.localHistoryInterface.setSource(source)

    def setCollectionItems(self, items, ordered_gids):
        self.localHistoryInterface.setCollectionItems(items, ordered_gids)

    def cancelLoad(self):
        self.localHistoryInterface.cancelLoad()
