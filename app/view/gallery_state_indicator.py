from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


DOWNLOAD_NONE = "none"
DOWNLOAD_INCOMPLETE = "incomplete"
DOWNLOAD_COMPLETE = "complete"
READING_NONE = "none"
READING_PARTIAL = "partial"
READING_COMPLETE = "complete"

_DOWNLOAD_COLORS = {
    DOWNLOAD_NONE: QColor("#FFFFFF"),
    DOWNLOAD_INCOMPLETE: QColor("#F2C94C"),
    DOWNLOAD_COMPLETE: QColor("#2DBB68"),
}
_READING_COLORS = {
    READING_NONE: QColor("#FFFFFF"),
    READING_PARTIAL: QColor("#9ACD32"),
    READING_COMPLETE: QColor("#2DBB68"),
}


class GalleryStateIndicator(QWidget):
    """Two stable status dots: download above reading."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.downloadState = DOWNLOAD_NONE
        self.readingState = READING_NONE
        self.setFixedSize(16, 32)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def setStates(self, download_state, reading_state):
        self.downloadState = (
            download_state
            if download_state in _DOWNLOAD_COLORS else DOWNLOAD_NONE
        )
        self.readingState = (
            reading_state
            if reading_state in _READING_COLORS else READING_NONE
        )
        download_text = {
            DOWNLOAD_NONE: self.tr("未下载"),
            DOWNLOAD_INCOMPLETE: self.tr("下载不完整"),
            DOWNLOAD_COMPLETE: self.tr("下载完成"),
        }[self.downloadState]
        reading_text = {
            READING_NONE: self.tr("未阅读"),
            READING_PARTIAL: self.tr("阅读中"),
            READING_COMPLETE: self.tr("曾经读完"),
        }[self.readingState]
        self.setToolTip(
            self.tr("下载状态：{}\n阅读状态：{}").format(
                download_text, reading_text
            )
        )
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.setBrush(_DOWNLOAD_COLORS[self.downloadState])
        painter.drawEllipse(2, 1, 12, 12)
        painter.setBrush(_READING_COLORS[self.readingState])
        painter.drawEllipse(2, 18, 12, 12)
