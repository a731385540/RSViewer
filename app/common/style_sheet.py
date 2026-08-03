from enum import Enum
from pathlib import Path

from qfluentwidgets import StyleSheetBase, Theme, qconfig


class StyleSheet(StyleSheetBase, Enum):
    """RSViewer 自定义样式表。"""

    SETTING_INTERFACE = "setting_interface"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        resource_dir = Path(__file__).resolve().parent.parent / "resource" / "qss"
        return str(resource_dir / theme.value.lower() / f"{self.value}.qss")
