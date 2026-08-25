import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from PySide6.QtCore import qWarning

from app.common.app_logging import (
    CRASH_FILE_NAME,
    LOG_FILE_NAME,
    install_application_logging,
    shutdown_application_logging,
)


class ApplicationLoggingTests(unittest.TestCase):
    def tearDown(self):
        shutdown_application_logging(clean=False)

    def test_install_creates_logs_and_clean_shutdown_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            install_application_logging(
                log_dir,
                install_qt_handler=False,
                enable_faulthandler=False,
            )
            logging.getLogger("test.application").info("application marker")
            shutdown_application_logging(clean=True, exit_code=0)

            application_log = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
            crash_log = (log_dir / CRASH_FILE_NAME).read_text(encoding="utf-8")

        self.assertIn("Logging initialized", application_log)
        self.assertIn("application marker", application_log)
        self.assertIn("RSViewer session start", crash_log)
        self.assertIn("RSViewer session clean shutdown", crash_log)

    def test_sensitive_values_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            install_application_logging(
                log_dir,
                install_qt_handler=False,
                enable_faulthandler=False,
            )
            logger = logging.getLogger("test.redaction")
            logger.warning("Cookie: sessionid=secret-value")
            logger.warning("gallery=https://exhentai.org/g/123/token-value/")
            logger.warning("proxy=https://username:password@example.test:443")
            shutdown_application_logging(clean=True, exit_code=0)
            contents = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")

        self.assertNotIn("secret-value", contents)
        self.assertNotIn("token-value", contents)
        self.assertNotIn("username:password", contents)
        self.assertIn("Cookie=<redacted>", contents)
        self.assertIn("/g/123/<redacted>/", contents)
        self.assertIn("https://<credentials>@example.test:443", contents)

    def test_main_thread_exception_hook_records_traceback_and_forwards(self):
        forwarded = []
        original_hook = sys.excepthook
        sys.excepthook = lambda *args: forwarded.append(args)
        try:
            with tempfile.TemporaryDirectory() as directory:
                log_dir = Path(directory)
                install_application_logging(
                    log_dir,
                    install_qt_handler=False,
                    enable_faulthandler=False,
                )
                try:
                    raise ValueError("diagnostic failure")
                except ValueError:
                    sys.excepthook(*sys.exc_info())
                shutdown_application_logging(clean=True, exit_code=0)
                contents = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
        finally:
            sys.excepthook = original_hook

        self.assertEqual(1, len(forwarded))
        self.assertIn("Unhandled exception on the main thread", contents)
        self.assertIn("ValueError: diagnostic failure", contents)

    def test_previous_unclean_session_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            log_dir.joinpath(CRASH_FILE_NAME).write_text(
                "=== RSViewer session start id=old ===\n",
                encoding="utf-8",
            )
            install_application_logging(
                log_dir,
                install_qt_handler=False,
                enable_faulthandler=False,
            )
            shutdown_application_logging(clean=True, exit_code=0)
            contents = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")

        self.assertIn("previous session did not write a clean shutdown marker", contents)

    def test_background_thread_exception_is_recorded_and_forwarded(self):
        forwarded = []
        original_hook = threading.excepthook
        threading.excepthook = lambda args: forwarded.append(args)
        try:
            with tempfile.TemporaryDirectory() as directory:
                log_dir = Path(directory)
                install_application_logging(
                    log_dir,
                    install_qt_handler=False,
                    enable_faulthandler=False,
                )

                def fail_in_background():
                    raise RuntimeError("worker diagnostic failure")

                worker = threading.Thread(
                    target=fail_in_background,
                    name="diagnostic-worker",
                )
                worker.start()
                worker.join()
                shutdown_application_logging(clean=True, exit_code=0)
                contents = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
        finally:
            threading.excepthook = original_hook

        self.assertEqual(1, len(forwarded))
        self.assertIn("Unhandled exception in thread name=diagnostic-worker", contents)
        self.assertIn("RuntimeError: worker diagnostic failure", contents)

    def test_qt_warning_is_written_to_application_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            install_application_logging(log_dir, enable_faulthandler=False)
            qWarning("qt diagnostic warning")
            shutdown_application_logging(clean=True, exit_code=0)
            contents = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")

        self.assertIn("WARNING", contents)
        self.assertIn("qt diagnostic warning", contents)


if __name__ == "__main__":
    unittest.main()
