from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


SOURCE_LABELS = {
    "ehentai": "EH",
    "exhentai": "EXH",
    "nhc": "NHC",
    "nhn": "NHN",
}

SOURCE_COLORS = {
    "ehentai": (QColor("#7F1D1D"), QColor("#FFFFFF")),
    "exhentai": (QColor("#7F1D1D"), QColor("#FFFFFF")),
    "nhc": (QColor("#F2C94C"), QColor("#202020")),
    "nhn": (QColor("#D32F2F"), QColor("#FFFFFF")),
}


def normalize_gallery_source(source):
    value = str(source or "").strip().casefold()
    return value if value in SOURCE_LABELS else "exhentai"


class GallerySourceBadge(QWidget):
    """Compact provider marker shared by local and online gallery cards."""

    def __init__(self, source="exhentai", parent=None):
        super().__init__(parent)
        self._source = "exhentai"
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(18)
        self.setSource(source)

    @property
    def source(self):
        return self._source

    def setSource(self, source):
        self._source = normalize_gallery_source(source)
        label = SOURCE_LABELS[self._source]
        width = max(30, self.fontMetrics().horizontalAdvance(label) + 12)
        self.setFixedWidth(width)
        self.setToolTip(self.tr("来源：{}").format(label))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background, foreground = SOURCE_COLORS[self._source]
        painter.setPen(Qt.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.setPen(foreground)
        painter.drawText(self.rect(), Qt.AlignCenter, SOURCE_LABELS[self._source])
