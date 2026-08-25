import faulthandler
import logging
import os
import platform
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qVersion

from app.common.app_paths import LOGS_DIR


LOG_FILE_NAME = "rsviewer.log"
CRASH_FILE_NAME = "crash.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
CRASH_BACKUP_COUNT = 2

_SESSION_START = "=== RSViewer session start"
_SESSION_CLEAN = "=== RSViewer session clean shutdown"
_RUNTIME = None

_REDACTION_RULES = (
    (
        re.compile(r"(?i)\b(cookie|authorization)\s*[:=]\s*[^\r\n]+"),
        r"\1=<redacted>",
    ),
    (
        re.compile(
            r"(?i)\b(igneous|ipb_member_id|ipb_pass_hash|sessionid|"
            r"cf_clearance)\s*=\s*[^;\s]+"
        ),
        r"\1=<redacted>",
    ),
    (
        re.compile(
            r"(?i)(https://(?:e-hentai\.org|exhentai\.org)/g/\d+/)"
            r"[^/?#\s]+"
        ),
        r"\1<redacted>",
    ),
    (
        re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@"),
        r"\1<credentials>@",
    ),
)


def redact_log_text(value):
    text = str(value or "")
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_log_text(super().format(record))


def _rotate_plain_file(path, max_bytes=MAX_LOG_BYTES, backups=CRASH_BACKUP_COUNT):
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size < int(max_bytes):
            return
        oldest = path.with_name(f"{path.name}.{int(backups)}")
        oldest.unlink(missing_ok=True)
        for index in range(int(backups) - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        # Logging must never keep the application from starting.
        return


def _previous_session_was_unclean(path):
    path = Path(path)
    try:
        if not path.is_file():
            return False
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 128 * 1024))
            tail = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return tail.rfind(_SESSION_START) > tail.rfind(_SESSION_CLEAN)


def _flush_logging():
    for handler in tuple(logging.getLogger().handlers):
        try:
            handler.flush()
        except Exception:
            pass


@dataclass
class ApplicationLoggingRuntime:
    log_dir: Path
    log_path: Path
    crash_path: Path
    session_id: str
    handler: logging.Handler
    crash_stream: object
    previous_qt_handler: object
    previous_excepthook: object
    previous_thread_excepthook: object
    previous_unraisablehook: object
    previous_root_level: int
    faulthandler_enabled: bool
    qt_handler_installed: bool


def install_application_logging(
    log_dir=None,
    *,
    install_qt_handler=True,
    enable_faulthandler=True,
):
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME

    log_dir = Path(log_dir or LOGS_DIR).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    crash_path = log_dir / CRASH_FILE_NAME
    previous_unclean = _previous_session_was_unclean(crash_path)
    _rotate_plain_file(crash_path)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        _RedactingFormatter(
            "%(asctime)s.%(msecs)03d %(levelname)s "
            "[pid=%(process)d thread=%(threadName)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._rsviewer_handler = True
    root_logger = logging.getLogger()
    previous_root_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    crash_stream = crash_path.open("a", encoding="utf-8", buffering=1)
    crash_stream.write(
        f"\n{_SESSION_START} id={session_id} "
        f"time={datetime.now().isoformat(timespec='seconds')} ===\n"
    )
    crash_stream.flush()
    fault_enabled = False
    if enable_faulthandler:
        try:
            faulthandler.enable(file=crash_stream, all_threads=True)
            fault_enabled = True
        except (OSError, RuntimeError):
            logging.getLogger(__name__).exception("Failed to enable faulthandler")

    previous_excepthook = sys.excepthook
    previous_thread_excepthook = getattr(threading, "excepthook", None)
    previous_unraisablehook = getattr(sys, "unraisablehook", None)

    def exception_hook(exc_type, exc_value, exc_traceback):
        logging.getLogger("rsviewer.crash").critical(
            "Unhandled exception on the main thread",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        _flush_logging()
        previous_excepthook(exc_type, exc_value, exc_traceback)

    def thread_exception_hook(args):
        logging.getLogger("rsviewer.crash").critical(
            "Unhandled exception in thread name=%s ident=%s",
            getattr(args.thread, "name", ""),
            getattr(args.thread, "ident", None),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _flush_logging()
        if previous_thread_excepthook is not None:
            previous_thread_excepthook(args)

    def unraisable_hook(args):
        logging.getLogger("rsviewer.crash").error(
            "Unraisable exception object=%r message=%s",
            getattr(args, "object", None),
            getattr(args, "err_msg", ""),
            exc_info=(
                getattr(args, "exc_type", None),
                getattr(args, "exc_value", None),
                getattr(args, "exc_traceback", None),
            ),
        )
        _flush_logging()
        if previous_unraisablehook is not None:
            previous_unraisablehook(args)

    sys.excepthook = exception_hook
    if previous_thread_excepthook is not None:
        threading.excepthook = thread_exception_hook
    if previous_unraisablehook is not None:
        sys.unraisablehook = unraisable_hook

    previous_qt_handler = None
    if install_qt_handler:
        qt_logger = logging.getLogger("qt")
        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }

        def qt_message_handler(message_type, context, message):
            location = ""
            if context is not None and getattr(context, "file", None):
                location = " [%s:%s %s]" % (
                    context.file,
                    getattr(context, "line", 0),
                    getattr(context, "function", "") or "",
                )
            qt_logger.log(
                levels.get(message_type, logging.INFO),
                "%s%s",
                message,
                location,
            )
            if message_type == QtMsgType.QtFatalMsg:
                _flush_logging()

        previous_qt_handler = qInstallMessageHandler(qt_message_handler)

    _RUNTIME = ApplicationLoggingRuntime(
        log_dir=log_dir,
        log_path=log_path,
        crash_path=crash_path,
        session_id=session_id,
        handler=handler,
        crash_stream=crash_stream,
        previous_qt_handler=previous_qt_handler,
        previous_excepthook=previous_excepthook,
        previous_thread_excepthook=previous_thread_excepthook,
        previous_unraisablehook=previous_unraisablehook,
        previous_root_level=previous_root_level,
        faulthandler_enabled=fault_enabled,
        qt_handler_installed=bool(install_qt_handler),
    )

    logger = logging.getLogger(__name__)
    logger.info(
        "Logging initialized session=%s python=%s pyside=%s qt=%s "
        "platform=%s frozen=%s",
        session_id,
        platform.python_version(),
        PYSIDE_VERSION,
        qVersion(),
        platform.platform(),
        bool(getattr(sys, "frozen", False)),
    )
    logger.info("Log directory: %s", log_dir)
    if previous_unclean:
        logger.warning(
            "The previous session did not write a clean shutdown marker; "
            "inspect %s and the preceding rsviewer.log entries",
            crash_path,
        )
    return _RUNTIME


def shutdown_application_logging(*, clean=True, exit_code=None):
    global _RUNTIME
    runtime = _RUNTIME
    if runtime is None:
        return

    logger = logging.getLogger(__name__)
    logger.info(
        "Logging shutdown session=%s clean=%s exit_code=%s",
        runtime.session_id,
        bool(clean),
        exit_code,
    )
    _flush_logging()
    if clean:
        runtime.crash_stream.write(
            f"{_SESSION_CLEAN} id={runtime.session_id} "
            f"time={datetime.now().isoformat(timespec='seconds')} "
            f"exit_code={exit_code} ===\n"
        )
        runtime.crash_stream.flush()

    if runtime.faulthandler_enabled:
        try:
            faulthandler.disable()
        except RuntimeError:
            pass
    if runtime.qt_handler_installed:
        qInstallMessageHandler(runtime.previous_qt_handler)
    sys.excepthook = runtime.previous_excepthook
    if runtime.previous_thread_excepthook is not None:
        threading.excepthook = runtime.previous_thread_excepthook
    if runtime.previous_unraisablehook is not None:
        sys.unraisablehook = runtime.previous_unraisablehook

    root_logger = logging.getLogger()
    root_logger.removeHandler(runtime.handler)
    root_logger.setLevel(runtime.previous_root_level)
    runtime.handler.close()
    runtime.crash_stream.close()
    _RUNTIME = None
