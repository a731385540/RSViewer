import base64
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt, QThreadPool
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from app.common.config import cfg
from app.domain.manga import MangaItem
from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreview,
    OnlineGalleryPreviewPage,
)
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache
from app.view.manga_reader_interface import MangaReaderInterface
from app.view.setting_interface import SettingInterface


class MangaReaderInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_config = {
            item: cfg.get(item)
            for item in (
                cfg.readerBackgroundColor,
                cfg.readerPageDirection,
                cfg.readerImageLoadSize,
                cfg.readerScrollShortcut,
                cfg.readerNextMangaShortcut,
                cfg.readerAutoPageEnabled,
                cfg.readerAutoPageInterval,
            )
        }
        cfg.set(cfg.readerBackgroundColor, QColor("#202020"))
        cfg.set(cfg.readerPageDirection, "right_to_left")
        cfg.set(cfg.readerImageLoadSize, "fit_window")
        cfg.set(cfg.readerScrollShortcut, "Space")
        cfg.set(cfg.readerNextMangaShortcut, "Ctrl+PageDown")
        cfg.set(cfg.readerAutoPageEnabled, False)
        cfg.set(cfg.readerAutoPageInterval, 5)
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        pages = []
        for index in range(4):
            path = root / f"{index + 1:07}.png"
            image = QImage(80 + index, 120 + index, QImage.Format_RGB32)
            image.fill(QColor(40 * index, 80, 160))
            self.assertTrue(image.save(str(path)))
            pages.append(path)
        self.item = MangaItem(
            gid=1,
            english_title="Reader test",
            original_title="阅读器测试",
            category=0,
            category_name="",
            primary_label="",
            multiple_labels=(),
            tags=(),
            folder=root,
            cover_path=pages[0],
            thumbnail_path=None,
            page_paths=tuple(pages),
            page_count=len(pages),
        )
        self.reader = MangaReaderInterface()
        self.reader.resize(800, 600)
        self.reader.show()

    def tearDown(self):
        self.reader.deactivate()
        QThreadPool.globalInstance().waitForDone(3000)
        for item, value in self.original_config.items():
            cfg.set(item, value)
        self.reader.close()
        self.reader.deleteLater()
        QApplication.processEvents()
        self.temp_directory.cleanup()

    def _wait_for_load(self):
        for _ in range(100):
            QApplication.processEvents()
            if self.reader._load_worker is None:
                return
            QTest.qWait(10)
        self.fail("reader page loading timed out")

    def test_loads_current_page_and_preloads_neighbors(self):
        self.reader.setManga(self.item)
        self._wait_for_load()

        self.assertEqual(1, self.reader.currentPage)
        self.assertEqual("第 1 / 4 页", self.reader.pageIndicatorLabel.text())
        self.assertLess(
            abs(
                self.reader.pageIndicatorLabel.mapTo(
                    self.reader,
                    self.reader.pageIndicatorLabel.rect().center(),
                ).x()
                - self.reader.rect().center().x()
            ),
            3,
        )
        self.assertLess(
            self.reader.pageIndicatorLabel.mapTo(
                self.reader,
                self.reader.pageIndicatorLabel.rect().bottomLeft(),
            ).y(),
            self.reader.graphicsView.geometry().top(),
        )
        self.assertIsNotNone(self.reader._pixmap_item)
        self.assertTrue({0, 1, 2}.issubset(self.reader._image_cache))

        self.reader.nextPage()
        self.assertEqual(2, self.reader.currentPage)
        self.assertEqual("第 2 / 4 页", self.reader.pageIndicatorLabel.text())
        self.assertIsNotNone(self.reader._pixmap_item)

    def test_hover_progress_slider_scrubs_pages_and_stays_synchronized(self):
        self.reader.setManga(self.item)
        self._wait_for_load()

        QApplication.sendEvent(
            self.reader.navigationWidget, QEvent(QEvent.Leave)
        )
        self.assertTrue(self.reader.pageProgressSlider.isHidden())
        self.assertEqual(4, self.reader.pageProgressSlider.maximum())
        self.assertEqual(1, self.reader.pageProgressSlider.value())
        self.assertLess(
            self.reader.pageProgressSliderHost.mapTo(
                self.reader,
                self.reader.pageProgressSliderHost.rect().bottomLeft(),
            ).y(),
            self.reader.previousButton.mapTo(
                self.reader, self.reader.previousButton.rect().topLeft()
            ).y(),
        )

        QApplication.sendEvent(
            self.reader.navigationWidget, QEvent(QEvent.Enter)
        )
        self.assertFalse(self.reader.pageProgressSlider.isHidden())

        slider = self.reader.pageProgressSlider
        QTest.mouseMove(
            slider,
            QPoint(slider.width() - 12, slider.rect().center().y()),
        )
        QApplication.processEvents()
        self.assertEqual(1, slider.value())
        self.assertEqual(1, self.reader.currentPage)

        QTest.mousePress(
            slider,
            Qt.LeftButton,
            pos=QPoint(11, slider.rect().center().y()),
        )
        QTest.mouseMove(
            slider,
            QPoint(slider.width() - 11, slider.rect().center().y()),
        )
        QApplication.processEvents()
        self.assertEqual(4, self.reader.currentPage)
        QTest.mouseRelease(
            slider,
            Qt.LeftButton,
            pos=QPoint(slider.width() - 11, slider.rect().center().y()),
        )
        self.assertEqual(4, self.reader.currentPage)
        self.assertEqual(4, self.reader.pageSpinBox.value())

        QApplication.sendEvent(
            self.reader.navigationWidget, QEvent(QEvent.Leave)
        )
        self.assertTrue(self.reader.pageProgressSlider.isHidden())

    def test_reader_accepts_page_from_resumed_download(self):
        self.reader.setManga(self.item, len(self.item.page_paths) - 1)
        self._wait_for_load()
        new_page = self.item.folder / "00000005.png"
        image = QImage(96, 140, QImage.Format_RGB32)
        image.fill(QColor("green"))
        self.assertTrue(image.save(str(new_page)))

        updated = self.reader.addDownloadedPage(
            self.item.gid,
            4,
            new_page,
            5,
            5,
        )

        self.assertEqual(5, len(updated.page_paths))
        self.assertEqual(5, self.reader.pageSpinBox.maximum())
        self.assertTrue(self.reader.nextButton.isEnabled())
        self.reader.nextPage()
        self.assertEqual(5, self.reader.currentPage)

    def test_incomplete_sidecar_keeps_missing_page_slot_and_requests_it(self):
        incomplete = replace(
            self.item,
            page_paths=(self.item.page_paths[0], self.item.page_paths[2]),
            page_count=3,
            downloaded_page_count=2,
            download_complete=False,
            page_tokens=("token-1", "token-2", "token-3"),
        )
        requested = []
        self.reader.localPageDownloadRequested.connect(
            lambda item, index: requested.append((item.gid, index))
        )

        self.reader.setManga(incomplete, 1)
        QApplication.processEvents()

        self.assertEqual(3, self.reader.pageSpinBox.maximum())
        self.assertEqual(2, self.reader.currentPage)
        self.assertEqual([(incomplete.gid, 1)], requested)

        updated = self.reader.addDownloadedPage(
            incomplete.gid,
            1,
            self.item.page_paths[1],
            3,
            3,
        )
        self._wait_for_load()

        self.assertEqual(2, self.reader.currentPage)
        self.assertEqual(3, len(updated.page_paths))
        self.assertIsNotNone(self.reader._pixmap_item)

    def test_reader_download_status_shows_progress_and_speed(self):
        self.reader.setManga(self.item)
        self.reader.setDownloadState(
            self.item.gid, "downloading", 2, 4, 2.5 * 1024 * 1024
        )

        self.assertFalse(self.reader.downloadStatusWidget.isHidden())
        self.assertEqual(50, self.reader.downloadStatusProgress.value())
        self.assertIn("2 / 4", self.reader.downloadStatusLabel.text())
        self.assertIn("2.5 MiB/s", self.reader.downloadStatusLabel.text())

    def test_online_reader_reuses_controls_and_preloads_through_memory_cache(self):
        image_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        gallery = OnlineGallery(
            9,
            "token",
            "https://e-hentai.org/g/9/token/",
            "Online reader",
            page_count=4,
        )
        previews = tuple(
            OnlineGalleryPreview(
                index,
                f"https://e-hentai.org/s/t{index}/9-{index + 1}",
                f"https://a.hath.network/{index}.webp",
            )
            for index in range(4)
        )
        detail = OnlineGalleryDetail(
            gallery=gallery,
            title="Online reader",
            page_count=4,
            previews=previews,
        )
        preview_page = OnlineGalleryPreviewPage(gallery, 1, 1, previews)
        cache = OnlineGalleryMemoryCache()
        cache.put_preview_page("ehentai", preview_page)

        class Provider:
            settings = SimpleNamespace(site="ehentai")

            def __init__(self):
                self.page_calls = []

            def load_gallery_page_image(self, _gallery, preview):
                self.page_calls.append(preview.page_index)
                return image_data

            def load_gallery_preview_page(self, _gallery, _page_number):
                raise AssertionError("preview page should come from memory")

        provider = Provider()
        progress = []
        self.reader.progressChanged.connect(lambda *args: progress.append(args))
        self.reader.setOnlineGallery(detail, provider, cache, 0)
        self._wait_for_load()

        self.assertTrue(self.reader.isOnlineGallery)
        self.assertIsNone(self.reader.currentItem)
        self.assertEqual(1, self.reader.currentPage)
        self.assertEqual("第 1 / 4 页", self.reader.pageIndicatorLabel.text())
        self.assertEqual([0, 1, 2], provider.page_calls)
        self.assertFalse(progress)
        self.assertIsNotNone(self.reader._pixmap_item)

        self.reader.nextPage()
        self.assertEqual(2, self.reader.currentPage)
        self.assertIsNotNone(cache.get_page_image("ehentai", gallery, 1))

    def test_plays_gif_detected_from_content_with_wrong_extension(self):
        disguised_gif = Path(self.temp_directory.name) / "0000000.jpg"
        disguised_gif.write_bytes(
            base64.b64decode(
                "R0lGODlhAwACAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQA"
                "CAAAACwAAAAAAwACAAAIBgABCBwYEAAh+QQBCAABACwAAAAAAwACAIEAAP8AAAAA"
                "AAAAAAAAAAAIBgABCBwYEAA7"
            )
        )
        animated_item = replace(
            self.item,
            page_paths=(disguised_gif, self.item.page_paths[0]),
            page_count=2,
            cover_path=disguised_gif,
        )

        self.reader.setManga(animated_item)
        self._wait_for_load()

        movie = self.reader._active_movie
        self.assertIsNotNone(movie)
        self.assertTrue(movie.isValid())
        self.assertGreaterEqual(movie.frameCount(), 2)
        observed_frames = []
        movie.frameChanged.connect(observed_frames.append)
        QTest.qWait(240)
        self.assertGreaterEqual(len(observed_frames), 2)
        self.assertIsNotNone(self.reader._pixmap_item)

        self.reader.nextPage()
        self.assertIsNone(self.reader._active_movie)

    def test_last_page_requests_next_sequence_manga(self):
        requested = []
        self.reader.nextMangaRequested.connect(lambda: requested.append(True))
        self.reader.setSequenceContinuation(True)
        self.reader.setManga(self.item, len(self.item.page_paths) - 1)
        self._wait_for_load()

        self.assertTrue(self.reader.nextButton.isEnabled())
        self.reader.nextPage()
        self.assertEqual([True], requested)
        self.assertEqual(len(self.item.page_paths), self.reader.currentPage)

    def test_first_page_requests_previous_sequence_manga(self):
        requested = []
        self.reader.previousMangaRequested.connect(lambda: requested.append(True))
        self.reader.setSequenceContinuation(True, True)
        self.reader.setManga(self.item, 0)
        self._wait_for_load()

        self.assertTrue(self.reader.previousButton.isEnabled())
        self.reader.previousPage()
        self.assertEqual([True], requested)
        self.assertEqual(1, self.reader.currentPage)

    def test_next_manga_button_and_shortcut_work_from_any_page(self):
        requested = []
        self.reader.nextMangaRequested.connect(lambda: requested.append(True))
        self.reader.setSequenceContinuation(True)
        self.reader.setManga(self.item, 1)
        self._wait_for_load()

        self.assertTrue(self.reader.nextMangaButton.isEnabled())
        self.reader.nextMangaButton.click()
        QTest.keyClick(
            self.reader.graphicsView.viewport(),
            Qt.Key_PageDown,
            Qt.ControlModifier,
        )

        self.assertEqual([True, True], requested)
        self.assertEqual(2, self.reader.currentPage)
        self.assertIn("Ctrl+PageDown", self.reader.nextMangaButton.toolTip())

        cfg.set(cfg.readerNextMangaShortcut, "Alt+N")
        QApplication.processEvents()
        QTest.keyClick(
            self.reader.graphicsView.viewport(), Qt.Key_N, Qt.AltModifier
        )
        self.assertEqual([True, True, True], requested)
        self.assertIn("Alt+N", self.reader.nextMangaButton.toolTip())

    def test_fullscreen_request_and_escape(self):
        states = []
        self.reader.fullscreenRequested.connect(states.append)

        self.reader.toggleFullscreen()
        self.assertTrue(self.reader.isFullscreen)
        QTest.keyClick(self.reader, Qt.Key_Escape)

        self.assertFalse(self.reader.isFullscreen)
        self.assertEqual([True, False], states)

    def test_fullscreen_controls_only_show_at_screen_edges(self):
        self.reader.setManga(self.item)
        self._wait_for_load()

        self.reader.setFullscreenState(True)
        self.assertFalse(self.reader.toolbarWidget.isVisible())
        self.assertFalse(self.reader.navigationWidget.isVisible())
        self.assertFalse(self.reader.pageIndicatorWidget.isVisible())
        self.assertTrue(self.reader.fullscreenPageIndicatorLabel.isVisible())
        self.assertEqual(
            "第 1 / 4 页", self.reader.fullscreenPageIndicatorLabel.text()
        )
        self.assertLess(
            self.reader.fullscreenPageIndicatorLabel.graphicsEffect().opacity(),
            0.7,
        )

        viewport = self.reader.graphicsView.viewport()
        QTest.keyClick(viewport, Qt.Key_Right)
        self.assertEqual(2, self.reader.currentPage)
        self.assertFalse(self.reader.toolbarWidget.isVisible())
        self.assertFalse(self.reader.navigationWidget.isVisible())
        self.assertTrue(self.reader.fullscreenPageIndicatorLabel.isVisible())

        QTest.mouseMove(viewport, viewport.rect().center())
        QApplication.processEvents()
        self.assertFalse(self.reader.toolbarWidget.isVisible())
        self.assertFalse(self.reader.navigationWidget.isVisible())

        QTest.mouseMove(viewport, QPoint(12, 1))
        QApplication.processEvents()
        self.assertTrue(self.reader.toolbarWidget.isVisible())
        self.assertFalse(self.reader.navigationWidget.isVisible())

        viewport = self.reader.graphicsView.viewport()
        QTest.mouseMove(viewport, viewport.rect().center())
        QApplication.processEvents()
        self.assertFalse(self.reader.toolbarWidget.isVisible())

        viewport = self.reader.graphicsView.viewport()
        QTest.mouseMove(viewport, QPoint(12, viewport.height() - 2))
        QApplication.processEvents()
        self.assertFalse(self.reader.toolbarWidget.isVisible())
        self.assertTrue(self.reader.navigationWidget.isVisible())

        self.reader.setFullscreenState(False)
        self.assertTrue(self.reader.toolbarWidget.isVisible())
        self.assertTrue(self.reader.navigationWidget.isVisible())
        self.assertTrue(self.reader.pageIndicatorWidget.isVisible())
        self.assertFalse(self.reader.fullscreenPageIndicatorLabel.isVisible())

    def test_direction_keys_work_after_clicking_image(self):
        self.reader.setManga(self.item)
        self._wait_for_load()
        viewport = self.reader.graphicsView.viewport()
        QTest.mouseClick(viewport, Qt.LeftButton, pos=viewport.rect().center())
        self.assertTrue(viewport.hasFocus())

        QTest.keyClick(viewport, Qt.Key_Right)
        self.assertEqual(2, self.reader.currentPage)

        self.reader.showPage(0)
        cfg.set(cfg.readerPageDirection, "left_to_right")
        QTest.keyClick(viewport, Qt.Key_Left)
        self.assertEqual(2, self.reader.currentPage)

    def test_scroll_shortcut_scrolls_long_image_then_advances(self):
        cfg.set(cfg.readerImageLoadSize, "fit_width")
        self.reader.setManga(self.item)
        self._wait_for_load()
        QApplication.processEvents()
        viewport = self.reader.graphicsView.viewport()
        bar = self.reader.graphicsView.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)

        QTest.keyClick(viewport, Qt.Key_Space)
        self.assertGreater(bar.value(), 0)
        bar.setValue(bar.maximum())
        QTest.keyClick(viewport, Qt.Key_Space)
        self.assertEqual(2, self.reader.currentPage)

    def test_auto_page_and_reader_settings_sync_immediately(self):
        global_settings = SettingInterface()
        self.reader.showReaderSettings()
        local_settings = self.reader._settings_dialog

        cfg.set(cfg.readerPageDirection, "top_to_bottom")
        cfg.set(cfg.readerImageLoadSize, "fit_width")
        cfg.set(cfg.readerScrollShortcut, "Ctrl+J")
        cfg.set(cfg.readerBackgroundColor, QColor("#123456"))
        QApplication.processEvents()

        self.assertEqual(
            global_settings.readerDirectionCard.choiceLabel.text(),
            local_settings.directionCard.choiceLabel.text(),
        )
        self.assertEqual(
            "Ctrl+J",
            global_settings.readerScrollShortcutCard.captureButton.sequence,
        )
        self.assertEqual(
            "Ctrl+PageDown",
            global_settings.readerNextMangaShortcutCard.captureButton.sequence,
        )
        self.assertEqual(
            "Ctrl+J", local_settings.scrollShortcutCard.captureButton.sequence
        )
        self.assertEqual("fit_width", self.reader._display_mode)
        self.assertEqual(
            QColor("#123456"), self.reader.scene.backgroundBrush().color()
        )

        self.reader.setManga(self.item)
        cfg.set(cfg.readerAutoPageEnabled, True)
        self.assertTrue(self.reader._auto_page_timer.isActive())
        self.reader._autoAdvance()
        self.assertEqual(2, self.reader.currentPage)
        cfg.set(cfg.readerAutoPageEnabled, False)
        self.assertFalse(self.reader._auto_page_timer.isActive())

        local_settings.close()
        global_settings.close()

    def test_reader_settings_dialog_follows_light_and_dark_theme(self):
        original_theme = cfg.get(cfg.themeMode)
        self.reader.showReaderSettings()
        dialog = self.reader._settings_dialog
        try:
            setTheme(Theme.DARK)
            QApplication.processEvents()
            dark_pixel = dialog.grab().toImage().pixelColor(2, 2)
            self.assertEqual(QColor("#202020"), dark_pixel)

            setTheme(Theme.LIGHT)
            QApplication.processEvents()
            light_pixel = dialog.grab().toImage().pixelColor(2, 2)
            self.assertEqual(QColor("#f3f3f3"), light_pixel)
        finally:
            setTheme(original_theme)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
