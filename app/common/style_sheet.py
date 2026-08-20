from enum import Enum

from qfluentwidgets import StyleSheetBase, Theme, qconfig

from app.common.app_paths import QSS_ROOT


class StyleSheet(StyleSheetBase, Enum):
    """RSViewer 自定义样式表。"""

    SETTING_INTERFACE = "setting_interface"
    READER_SETTING_DIALOG = "reader_setting_dialog"
    MANGA_DETAIL_INTERFACE = "manga_detail_interface"
    ONLINE_MANGA_INTERFACE = "online_manga_interface"
    SIMILAR_GALLERY_BROWSER_WINDOW = "similar_gallery_browser_window"

    def path(self, theme=Theme.AUTO):
        theme = qconfig.theme if theme == Theme.AUTO else theme
        return str(QSS_ROOT / theme.value.lower() / f"{self.value}.qss")
