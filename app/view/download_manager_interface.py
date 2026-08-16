from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ProgressBar,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    SubtitleLabel,
    ToolButton,
)
from qfluentwidgets import FluentIcon as FIF

from app.domain.online_download import (
    DOWNLOAD_MODE_ORIGINAL_DIRECT,
    DOWNLOAD_MODE_ORIGINAL_LOCAL,
)


STATE_TEXT = {
    "queued": "等待中",
    "downloading": "正在下载",
    "paused": "已暂停",
    "failed": "下载失败",
}

DOWNLOAD_MODE_TEXT = {
    DOWNLOAD_MODE_ORIGINAL_DIRECT: "直接下载原图",
    DOWNLOAD_MODE_ORIGINAL_LOCAL: "本地画廊下载原图",
}


def format_download_speed(bytes_per_second):
    speed = max(0.0, float(bytes_per_second or 0))
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    unit = units[0]
    for unit in units:
        if speed < 1024 or unit == units[-1]:
            break
        speed /= 1024
    if unit == "B/s":
        return f"{speed:.0f} {unit}"
    return f"{speed:.2f} {unit}"


class DownloadTaskCard(SimpleCardWidget):
    startRequested = Signal(int)
    pauseRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, record, active=False, speed=0, parent=None):
        super().__init__(parent)
        self.record = record
        self.active = bool(active)
        self.setObjectName("downloadTaskCard")
        self.setMinimumHeight(94)

        self.titleLabel = BodyLabel(record.title or str(record.gid), self)
        self.titleLabel.setWordWrap(True)
        self.metaLabel = CaptionLabel("", self)
        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setFixedWidth(180)
        self.actionButton = ToolButton(FIF.PAUSE if active else FIF.PLAY, self)
        self.actionButton.setToolTip("暂停下载" if active else "开始或继续下载")
        self.deleteButton = ToolButton(FIF.DELETE, self)
        self.deleteButton.setToolTip("删除任务记录，保留已下载文件")

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

        self.actionButton.clicked.connect(self._requestAction)
        self.deleteButton.clicked.connect(
            lambda: self.deleteRequested.emit(int(self.record.gid))
        )
        self.updateRecord(record, active, speed)

    def updateRecord(self, record, active=False, speed=0):
        self.record = record
        self.active = bool(active)
        total = max(0, int(record.page_count))
        completed = min(total, max(0, int(record.completed_pages)))
        percent = round(completed * 100 / total) if total else 0
        state_text = STATE_TEXT.get(record.state, record.state)
        if self.active:
            state_text = "正在下载"
        self.titleLabel.setText(record.title or str(record.gid))
        mode_text = DOWNLOAD_MODE_TEXT.get(record.download_mode, "基础下载")
        metadata = (
            f"GID {record.gid} · {mode_text} · "
            f"{completed} / {total} 页 · {state_text}"
        )
        if self.active:
            metadata += " · " + (
                format_download_speed(speed) if speed > 0 else self.tr("测速中")
            )
        self.metaLabel.setText(metadata)
        self.metaLabel.setToolTip(record.error or "")
        self.progressBar.setValue(percent)
        self.actionButton.setIcon(FIF.PAUSE if self.active else FIF.PLAY)
        self.actionButton.setToolTip(
            "暂停下载" if self.active else "开始或继续下载"
        )

    def _requestAction(self):
        if self.active:
            self.pauseRequested.emit(int(self.record.gid))
        else:
            self.startRequested.emit(int(self.record.gid))


class DownloadManagerInterface(QWidget):
    startRequested = Signal(int)
    pauseRequested = Signal(int)
    deleteRequested = Signal(int)
    startAllRequested = Signal()
    pauseAllRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("downloadManagerInterface")
        self._cards = {}

        title = SubtitleLabel(self.tr("正在下载"), self)
        self.countLabel = CaptionLabel("", self)
        self.startAllButton = PushButton(
            FIF.PLAY,
            self.tr("全部开始"),
            self,
        )
        self.pauseAllButton = PushButton(
            FIF.PAUSE,
            self.tr("全部暂停"),
            self,
        )
        self.startAllButton.setToolTip(self.tr("开始或继续所有未运行的下载任务"))
        self.pauseAllButton.setToolTip(self.tr("暂停所有正在准备或下载的任务"))
        self.startAllButton.clicked.connect(lambda: self.startAllRequested.emit())
        self.pauseAllButton.clicked.connect(lambda: self.pauseAllRequested.emit())
        self.startAllButton.setEnabled(False)
        self.pauseAllButton.setEnabled(False)
        header = QHBoxLayout()
        header.setContentsMargins(36, 28, 36, 16)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.startAllButton)
        header.addWidget(self.pauseAllButton)
        header.addWidget(self.countLabel)

        self.contentWidget = QWidget()
        self.contentWidget.setObjectName("downloadManagerContent")
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(36, 0, 36, 28)
        self.contentLayout.setSpacing(10)
        self.contentLayout.setAlignment(Qt.AlignTop)
        self.emptyLabel = BodyLabel(self.tr("当前没有未完成的下载任务"), self.contentWidget)
        self.emptyLabel.setAlignment(Qt.AlignCenter)
        self.contentLayout.addWidget(self.emptyLabel)

        scroll = ScrollArea(self)
        scroll.setWidget(self.contentWidget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#downloadManagerContent { background: transparent; }"
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
        wanted = {int(record.gid) for record in records}
        for gid in tuple(self._cards):
            if gid not in wanted:
                card = self._cards.pop(gid)
                self.contentLayout.removeWidget(card)
                card.deleteLater()
        for record in records:
            gid = int(record.gid)
            card = self._cards.get(gid)
            if card is None:
                card = DownloadTaskCard(
                    record,
                    gid in active_gids,
                    speeds.get(gid, 0),
                    self.contentWidget,
                )
                card.startRequested.connect(self.startRequested)
                card.pauseRequested.connect(self.pauseRequested)
                card.deleteRequested.connect(self.deleteRequested)
                self._cards[gid] = card
                self.contentLayout.addWidget(card)
            else:
                card.updateRecord(record, gid in active_gids, speeds.get(gid, 0))
        self.emptyLabel.setVisible(not records)
        total_speed = sum(speeds.get(gid, 0) for gid in active_gids)
        count_text = self.tr("{} 个任务").format(len(records))
        if total_speed > 0:
            count_text += " · " + format_download_speed(total_speed)
        self.countLabel.setText(count_text)
        self.startAllButton.setEnabled(
            any(int(record.gid) not in active_gids for record in records)
        )
        self.pauseAllButton.setEnabled(
            any(int(record.gid) in active_gids for record in records)
        )
