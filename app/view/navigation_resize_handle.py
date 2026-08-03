from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget
from qfluentwidgets import themeColor


class NavigationResizeHandle(QWidget):
    """在展开导航栏右缘提供轻量的鼠标拖拽调宽能力。"""

    HANDLE_WIDTH = 7
    MIN_WIDTH = 220
    MAX_WIDTH = 480

    def __init__(self, navigation_interface, parent=None):
        super().__init__(parent)
        self.navigation_interface = navigation_interface
        self.panel = navigation_interface.panel
        self._dragging = False
        self._hovered = False
        self.setCursor(Qt.SplitHCursor)
        self.setMouseTracking(True)
        self.panel.installEventFilter(self)
        self.syncGeometry()

    def eventFilter(self, watched, event):
        if watched is self.panel and event.type() in (
            QEvent.Resize,
            QEvent.Move,
            QEvent.Show,
            QEvent.Hide,
        ):
            self.syncGeometry()
        return super().eventFilter(watched, event)

    def syncGeometry(self):
        parent = self.parentWidget()
        if parent is None:
            return

        panel_width = self.panel.width()
        expanded = panel_width > 48
        top = parent.titleBar.height() if hasattr(parent, "titleBar") else 0
        self.setGeometry(
            panel_width - self.HANDLE_WIDTH // 2,
            top,
            self.HANDLE_WIDTH,
            max(0, parent.height() - top),
        )
        self.setVisible(expanded)
        if expanded:
            self.raise_()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.grabMouse()
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._dragging:
            super().mouseMoveEvent(event)
            return

        parent = self.parentWidget()
        cursor_x = parent.mapFromGlobal(event.globalPosition().toPoint()).x()
        maximum = max(
            self.MIN_WIDTH,
            min(self.MAX_WIDTH, parent.width() - 360),
        )
        width = max(self.MIN_WIDTH, min(maximum, cursor_x))
        self.navigation_interface.setExpandWidth(width)
        self.panel.resize(width, self.panel.height())
        self.syncGeometry()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.releaseMouse()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        color = themeColor()
        color.setAlpha(190 if self._hovered or self._dragging else 45)
        painter = QPainter(self)
        painter.setPen(QPen(color, 1))
        x = self.width() // 2
        painter.drawLine(x, 0, x, self.height())
