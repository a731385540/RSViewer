import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentTranslator

from app.common.app_paths import APP_ICON_PATH, APP_NAME, ORGANIZATION_NAME
from app.common.config import cfg
from app.view.main_window import MainWindow


def main() -> int:
    """启动 RSViewer 桌面应用。"""
    if cfg.get(cfg.dpiScale) != "Auto":
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = str(cfg.get(cfg.dpiScale))

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    translator = FluentTranslator(cfg.get(cfg.language).value)
    app.installTranslator(translator)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
