from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, TitleLabel


class MediaInterface(QWidget):
    """媒体功能页的轻量占位界面。"""

    def __init__(
        self,
        title: str,
        description: str,
        object_name: str,
        parent=None,
    ):
        super().__init__(parent=parent)
        self.setObjectName(object_name)

        self.titleLabel = TitleLabel(title, self)
        self.descriptionLabel = BodyLabel(description, self)
        self.descriptionLabel.setWordWrap(True)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(36, 32, 36, 32)
        self.layout.setSpacing(12)
        self.layout.setAlignment(Qt.AlignTop)
        self.layout.addWidget(self.titleLabel)
        self.layout.addWidget(self.descriptionLabel)
        self.layout.addStretch(1)
