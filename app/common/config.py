# coding:utf-8
import sys
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QLocale
from qfluentwidgets import (
    BoolValidator,
    ColorConfigItem,
    ConfigItem,
    ConfigSerializer,
    FolderListValidator,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    qconfig,
)


class Language(Enum):
    """ Language enumeration """

    CHINESE_SIMPLIFIED = QLocale(QLocale.Chinese, QLocale.China)
    CHINESE_TRADITIONAL = QLocale(QLocale.Chinese, QLocale.HongKong)
    ENGLISH = QLocale(QLocale.English)
    AUTO = QLocale()


class LanguageSerializer(ConfigSerializer):
    """ Language serializer """

    def serialize(self, language):
        return language.value.name() if language != Language.AUTO else "Auto"

    def deserialize(self, value: str):
        return Language(QLocale(value)) if value != "Auto" else Language.AUTO


def isWin11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """ Config of application """

    libraryFolders = ConfigItem(
        "Library", "Folders", [], FolderListValidator())
    ehViewerMangaRoot = ConfigItem("ExternalData", "EhViewerMangaRoot", "")
    mangaPageSize = OptionsConfigItem(
        "Library", "MangaPageSize", 40, OptionsValidator([20, 40, 60, 100]))
    mangaSortOrder = OptionsConfigItem(
        "Library", "MangaSortOrder", "desc", OptionsValidator(["desc", "asc"])
    )
    mangaPrimaryLabelFilter = ConfigItem(
        "Library", "MangaPrimaryLabelFilter", "__none__"
    )
    mangaSearchHoverEnabled = ConfigItem(
        "Library", "MangaSearchHoverEnabled", True, BoolValidator()
    )
    searchHistoryLimit = OptionsConfigItem(
        "Library", "SearchHistoryLimit", 20, OptionsValidator([5, 10, 15, 20])
    )
    searchShortcut = ConfigItem("Shortcuts", "OpenSearch", "Ctrl+K")
    tagSidebarShortcut = ConfigItem("Shortcuts", "ToggleMangaTags", "Ctrl+L")
    backShortcut = ConfigItem("Shortcuts", "NavigateBack", "Z")

    # online E-Hentai / ExHentai source
    onlineEhSite = OptionsConfigItem(
        "OnlineEH", "Site", "ehentai", OptionsValidator(["ehentai", "exhentai"])
    )
    onlineEhCookie = ConfigItem("OnlineEH", "Cookie", "")
    onlineEhProxyMode = OptionsConfigItem(
        "OnlineEH",
        "ProxyMode",
        "system",
        OptionsValidator(["system", "direct", "manual"]),
    )
    onlineEhManualProxy = ConfigItem("OnlineEH", "ManualProxy", "")
    onlineEhRequestTimeout = OptionsConfigItem(
        "OnlineEH", "RequestTimeout", 20, OptionsValidator([10, 20, 30, 60])
    )
    onlineEhViewMode = OptionsConfigItem(
        "OnlineEH",
        "ViewMode",
        "card",
        OptionsValidator(["card", "list", "extended"]),
    )
    onlineEhThumbnailConcurrency = OptionsConfigItem(
        "OnlineEH", "ThumbnailConcurrency", 6, OptionsValidator([1, 2, 4, 6, 8, 12])
    )
    onlineEhDownloadConcurrency = OptionsConfigItem(
        "OnlineEH", "DownloadConcurrency", 2, OptionsValidator([1, 2, 3])
    )
    onlineEhDownloadThreads = OptionsConfigItem(
        "OnlineEH", "DownloadThreads", 6, OptionsValidator([1, 2, 3, 4, 5, 6])
    )
    onlineEhDownloadLabel = ConfigItem("OnlineEH", "DownloadLabel", "")
    onlineEhThumbnailCacheHours = OptionsConfigItem(
        "OnlineEH",
        "ThumbnailCacheHours",
        168,
        OptionsValidator([1, 6, 12, 24, 72, 168, 720]),
    )

    # manga reader
    readerBackgroundColor = ColorConfigItem(
        "MangaReader", "BackgroundColor", "#202020"
    )
    readerPageDirection = OptionsConfigItem(
        "MangaReader",
        "PageDirection",
        "right_to_left",
        OptionsValidator(
            ["left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top"]
        ),
    )
    readerImageLoadSize = OptionsConfigItem(
        "MangaReader",
        "ImageLoadSize",
        "fit_window",
        OptionsValidator(["fit_window", "fit_width", "original"]),
    )
    readerScrollShortcut = ConfigItem("MangaReader", "ScrollShortcut", "Space")
    readerAutoPageEnabled = ConfigItem(
        "MangaReader", "AutoPageEnabled", False, BoolValidator()
    )
    readerAutoPageInterval = OptionsConfigItem(
        "MangaReader",
        "AutoPageInterval",
        5,
        OptionsValidator([2, 3, 5, 8, 10, 15, 30]),
    )

    # main window
    micaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    dpiScale = OptionsConfigItem(
        "MainWindow", "DpiScale", "Auto", OptionsValidator([1, 1.25, 1.5, 1.75, 2, "Auto"]), restart=True)
    language = OptionsConfigItem(
        "MainWindow", "Language", Language.AUTO, OptionsValidator(Language), LanguageSerializer(), restart=True)

cfg = Config()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "app" / "config" / "config.json"
qconfig.load(str(CONFIG_PATH), cfg)
