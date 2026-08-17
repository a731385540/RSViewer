from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ProgressBar,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
    ToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.view.download_manager_interface import format_download_speed


UPDATE_STATE_TEXT = {
    "waiting_download": "等待原画廊补齐",
    "queued": "等待更新",
    "updating": "正在更新",
    "paused": "已暂停",
    "failed": "更新失败",
}

CHECKPOINT_TEXT = {
    0: "已保存最新画廊信息",
    1: "已标记旧页面",
    2: "已按新版重排",
    3: "已补齐新版页面",
    4: "已完成图片校验",
    5: "正在恢复标准文件名",
    6: "更新完成",
}


class GalleryUpdateTaskCard(SimpleCardWidget):
    startRequested = Signal(int)
    pauseRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, record, active=False, speed=0, parent=None):
        super().__init__(parent)
        self.record = record
        self.active = bool(active)
        self.setObjectName("galleryUpdateTaskCard")
        self.setMinimumHeight(96)

        self.titleLabel = BodyLabel("", self)
        self.titleLabel.setWordWrap(True)
        self.metaLabel = CaptionLabel("", self)
        self.metaLabel.setWordWrap(True)
        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setFixedWidth(180)
        self.actionButton = ToolButton(FIF.PAUSE if active else FIF.PLAY, self)
        self.actionButton.clicked.connect(self._requestAction)
        self.deleteButton = ToolButton(FIF.DELETE, self)
        self.deleteButton.setToolTip(
            "删除任务记录，保留画廊文件和目录恢复记录"
        )
        self.deleteButton.clicked.connect(
            lambda: self.deleteRequested.emit(int(self.record.source_gid))
        )

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(5)
        text_layout.addWidget(self.titleLabel)
        text_layout.addWidget(self.metaLabel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(12)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.actionButton)
        layout.addWidget(self.deleteButton)
        self.updateRecord(record, active, speed)

    def updateRecord(self, record, active=False, speed=0):
        self.record = record
        self.active = bool(active)
        total = max(0, int(record.page_count))
        completed = min(total, max(0, int(record.completed_pages)))
        percent = round(completed * 100 / total) if total else 0
        state = UPDATE_STATE_TEXT.get(record.state, record.state)
        checkpoint = CHECKPOINT_TEXT.get(int(record.status), "准备更新")
        target = f" -> GID {record.target_gid}" if record.target_gid else ""
        metadata = (
            f"GID {record.source_gid}{target} · {state} · {checkpoint}"
        )
        if total:
            metadata += f" · {completed} / {total} 页"
        if self.active and speed > 0:
            metadata += " · " + format_download_speed(speed)
        self.titleLabel.setText(record.title or str(record.source_gid))
        self.metaLabel.setText(metadata)
        self.metaLabel.setToolTip(record.error or "")
        self.progressBar.setValue(percent)
        self.actionButton.setIcon(FIF.PAUSE if self.active else FIF.PLAY)
        self.actionButton.setToolTip("暂停更新" if self.active else "开始或继续更新")

    def _requestAction(self):
        if self.active:
            self.pauseRequested.emit(int(self.record.source_gid))
        else:
            self.startRequested.emit(int(self.record.source_gid))


class UpdateManagerInterface(QWidget):
    startRequested = Signal(int)
    pauseRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateManagerInterface")
        self._cards = {}

        title = SubtitleLabel(self.tr("更新管理"), self)
        self.countLabel = CaptionLabel("", self)
        header = QHBoxLayout()
        header.setContentsMargins(36, 28, 36, 16)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.countLabel)

        self.contentWidget = QWidget()
        self.contentWidget.setObjectName("updateManagerContent")
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(36, 0, 36, 28)
        self.contentLayout.setSpacing(10)
        self.contentLayout.setAlignment(Qt.AlignTop)
        self.emptyLabel = BodyLabel(self.tr("当前没有未完成的画廊更新任务"), self.contentWidget)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.contentLayout.addWidget(self.emptyLabel)

        scroll = ScrollArea(self)
        scroll.setWidget(self.contentWidget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#updateManagerContent { background: transparent; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(scroll, 1)

    def setRecords(self, records, active_gids=(), speeds=None):
        active_gids = {int(gid) for gid in active_gids}
        speeds = {int(gid): float(speed) for gid, speed in (speeds or {}).items()}
        records = tuple(record for record in records if record.state != "completed")
        wanted = {int(record.source_gid) for record in records}
        for gid in tuple(self._cards):
            if gid not in wanted:
                card = self._cards.pop(gid)
                self.contentLayout.removeWidget(card)
                card.deleteLater()
        for record in records:
            gid = int(record.source_gid)
            card = self._cards.get(gid)
            if card is None:
                card = GalleryUpdateTaskCard(
                    record, gid in active_gids, speeds.get(gid, 0), self.contentWidget
                )
                card.startRequested.connect(self.startRequested)
                card.pauseRequested.connect(self.pauseRequested)
                card.deleteRequested.connect(self.deleteRequested)
                self._cards[gid] = card
                self.contentLayout.addWidget(card)
            else:
                card.updateRecord(record, gid in active_gids, speeds.get(gid, 0))
        self.emptyLabel.setVisible(not records)
        self.countLabel.setText(self.tr("{} 个任务").format(len(records)))
