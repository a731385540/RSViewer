import logging
import os
import sys

from app.common.app_logging import (
    install_application_logging,
    shutdown_application_logging,
)


def main() -> int:
    """启动 RSViewer 桌面应用。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import FluentTranslator

    from app.common.app_paths import APP_ICON_PATH, APP_NAME, ORGANIZATION_NAME
    from app.common.config import cfg
    from app.view.main_window import MainWindow

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
    logging.getLogger(__name__).info("Qt event loop started")
    exit_code = app.exec()
    logging.getLogger(__name__).info(
        "Qt event loop stopped exit_code=%s", exit_code
    )
    return exit_code


def run() -> int:
    install_application_logging()
    exit_code = 1
    clean = False
    try:
        exit_code = main()
        clean = True
        return exit_code
    except BaseException:
        logging.getLogger(__name__).critical(
            "Application terminated by an unhandled exception", exc_info=True
        )
        raise
    finally:
        shutdown_application_logging(clean=clean, exit_code=exit_code)


if __name__ == "__main__":
    raise SystemExit(run())
