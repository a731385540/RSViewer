from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from app.domain.online_download import GallerySyncRecord
from app.domain.online_gallery import gallery_preview_page_number
from app.services.online_download_builder import online_detail_metadata


class OnlineSearchSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class OnlineSearchWorker(QRunnable):
    def __init__(self, provider, query, display_mode=None):
        super().__init__()
        self.provider = provider
        self.query = query
        self.display_mode = display_mode
        self.cancelled = False
        self.signals = OnlineSearchSignals()

    def run(self):
        try:
            if self.display_mode:
                self.provider.set_display_mode(self.display_mode)
            if self.cancelled:
                return
            page = self.provider.search(self.query)
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.loaded.emit(page)
            except RuntimeError:
                pass


class OnlineCoverSignals(QObject):
    loaded = Signal(int, bytes)
    finished = Signal()


class OnlineCoverWorker(QRunnable):
    def __init__(self, provider, item, cache, site, cache_hours):
        super().__init__()
        self.provider = provider
        self.item = item
        self.cache = cache
        self.site = site
        self.cache_hours = cache_hours
        self.cancelled = False
        self.signals = OnlineCoverSignals()

    def run(self):
        try:
            if self.cancelled or not self.item.thumbnail_url:
                return
            data = self.cache.get(
                self.site,
                self.item.thumbnail_url,
                self.cache_hours,
            )
            if data is not None and QImage.fromData(data).isNull():
                self.cache.discard(self.site, self.item.thumbnail_url)
                data = None
            if data is None:
                try:
                    data = self.provider.load_thumbnail(self.item.thumbnail_url)
                except Exception:
                    data = b""
                if data and QImage.fromData(data).isNull():
                    data = b""
                if data:
                    self.cache.put(self.site, self.item.thumbnail_url, data)
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(self.item.gid, data or b"")
                except RuntimeError:
                    pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


class OnlineDetailSignals(QObject):
    loaded = Signal(object, bytes)
    failed = Signal(str)


class OnlineDetailWorker(QRunnable):
    def __init__(self, provider, item, cover_data=b"", fetch_cover=True):
        super().__init__()
        self.provider = provider
        self.item = item
        self.cover_data = bytes(cover_data or b"")
        self.fetch_cover = bool(fetch_cover)
        self.cancelled = False
        self.signals = OnlineDetailSignals()

    def run(self):
        try:
            detail = self.provider.load_gallery_detail(self.item)
            if self.cancelled:
                return
            cover_data = self.cover_data
            if self.fetch_cover and not cover_data and detail.cover_url:
                cover_data = self.provider.load_thumbnail(detail.cover_url)
            if cover_data and QImage.fromData(cover_data).isNull():
                cover_data = b""
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.loaded.emit(detail, cover_data)
            except RuntimeError:
                pass


class LocalGallerySyncSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class LocalGallerySyncWorker(QRunnable):
    """Fetch and persist local gallery metadata without downloading images."""

    def __init__(
        self,
        provider,
        gallery,
        ehviewer_repository,
        user_repository,
        gallery_loader=None,
    ):
        super().__init__()
        self.provider = provider
        self.gallery = gallery
        self.ehviewer_repository = ehviewer_repository
        self.user_repository = user_repository
        self.gallery_loader = gallery_loader
        self.cancelled = False
        self.signals = LocalGallerySyncSignals()

    def run(self):
        try:
            gallery = (
                self.gallery_loader()
                if self.gallery_loader is not None
                else self.gallery
            )
            if self.cancelled:
                return
            self.gallery = gallery
            detail = self.provider.load_gallery_detail(gallery)
            if self.cancelled:
                return
            self.ehviewer_repository.sync_metadata(detail)
            if self.cancelled:
                return
            self.user_repository.save_gallery_sync(
                GallerySyncRecord(
                    gid=int(detail.gallery.gid),
                    site=str(self.provider.settings.site),
                    token=str(detail.gallery.token),
                    metadata=online_detail_metadata(detail),
                ),
                detail.comments,
            )
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.loaded.emit(detail)
            except RuntimeError:
                pass


class OnlinePreviewPageSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str)


class OnlinePreviewPageWorker(QRunnable):
    def __init__(self, provider, gallery, page_number):
        super().__init__()
        self.provider = provider
        self.gallery = gallery
        self.page_number = int(page_number)
        self.cancelled = False
        self.signals = OnlinePreviewPageSignals()

    def run(self):
        try:
            page = self.provider.load_gallery_preview_page(
                self.gallery, self.page_number
            )
        except Exception as error:
            if not self.cancelled:
                try:
                    self.signals.failed.emit(str(error))
                except RuntimeError:
                    pass
            return
        if not self.cancelled:
            try:
                self.signals.loaded.emit(page)
            except RuntimeError:
                pass


class OnlinePreviewThumbnailSignals(QObject):
    loaded = Signal(int, object)
    finished = Signal()


class OnlinePreviewThumbnailWorker(QRunnable):
    def __init__(self, provider, gallery, preview, cache, site):
        super().__init__()
        self.provider = provider
        self.gallery = gallery
        self.preview = preview
        self.cache = cache
        self.site = site
        self.cancelled = False
        self.signals = OnlinePreviewThumbnailSignals()

    def run(self):
        try:
            data = self.cache.get_preview_image(
                self.site, self.gallery, self.preview.page_index
            )
            image = QImage.fromData(data or b"")
            if data is not None and image.isNull():
                self.cache.discard_preview_image(
                    self.site, self.gallery, self.preview.page_index
                )
                data = None
            if data is None:
                source_data = self.cache.get_preview_source(
                    self.site, self.gallery, self.preview.thumbnail_url
                )
                source_image = QImage.fromData(source_data or b"")
                if source_data is not None and source_image.isNull():
                    self.cache.discard_preview_source(
                        self.site, self.gallery, self.preview.thumbnail_url
                    )
                    source_data = None
                if source_data is None:
                    source_data = self.provider.load_preview_thumbnail(self.preview)
                    source_image = QImage.fromData(source_data or b"")
                    if source_data and not source_image.isNull():
                        self.cache.put_preview_source(
                            self.site,
                            self.gallery,
                            self.preview.thumbnail_url,
                            source_data,
                        )
                image = _crop_preview_image(source_image, self.preview)
                data = _encode_png(image)
                if data:
                    self.cache.put_preview_image(
                        self.site, self.gallery, self.preview.page_index, data
                    )
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(self.preview.page_index, image)
                except RuntimeError:
                    pass
        except Exception:
            if not self.cancelled:
                try:
                    self.signals.loaded.emit(self.preview.page_index, QImage())
                except RuntimeError:
                    pass
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass


def _crop_preview_image(image, preview):
    if image.isNull():
        return QImage()
    width = int(preview.thumbnail_width or 0)
    height = int(preview.thumbnail_height or 0)
    if width <= 0 or height <= 0:
        return image
    x = max(0, int(preview.thumbnail_x or 0))
    y = max(0, int(preview.thumbnail_y or 0))
    if x >= image.width() or y >= image.height():
        return QImage()
    return image.copy(
        x,
        y,
        min(width, image.width() - x),
        min(height, image.height() - y),
    )


def _encode_png(image):
    if image.isNull():
        return b""
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "PNG"):
        return b""
    return bytes(data)


@dataclass(frozen=True)
class OnlineReaderPageImage:
    image: QImage
    is_gif: bool = False
    data: bytes = b""


class OnlineReaderLoadSignals(QObject):
    imageReady = Signal(int, object)
    imageFailed = Signal(int, str)
    finished = Signal()


class OnlineReaderLoadWorker(QRunnable):
    def __init__(self, provider, gallery, indexes, cache, site):
        super().__init__()
        self.provider = provider
        self.gallery = gallery
        self.indexes = tuple(indexes)
        self.cache = cache
        self.site = site
        self.cancelled = False
        self.signals = OnlineReaderLoadSignals()

    def run(self):
        try:
            for index in self.indexes:
                if self.cancelled:
                    return
                try:
                    data = self.cache.get_page_image(
                        self.site, self.gallery, index
                    )
                    image = QImage.fromData(data or b"")
                    if data is not None and image.isNull():
                        self.cache.discard_page_image(
                            self.site, self.gallery, index
                        )
                        data = None
                    if data is None:
                        preview_page_number = gallery_preview_page_number(
                            self.gallery, index
                        )
                        preview_page = self.cache.get_preview_page(
                            self.site, self.gallery, preview_page_number
                        )
                        if preview_page is None:
                            preview_page = self.provider.load_gallery_preview_page(
                                self.gallery, preview_page_number
                            )
                            self.cache.put_preview_page(self.site, preview_page)
                        preview = next(
                            item
                            for item in preview_page.items
                            if item.page_index == index
                        )
                        data = self.provider.load_gallery_page_image(
                            self.gallery, preview
                        )
                        image = QImage.fromData(data or b"")
                        if data and not image.isNull():
                            self.cache.put_page_image(
                                self.site, self.gallery, index, data
                            )
                    is_gif = (data or b"")[:6] in (b"GIF87a", b"GIF89a")
                    page_image = OnlineReaderPageImage(image, is_gif, data or b"")
                    if not self.cancelled:
                        self.signals.imageReady.emit(index, page_image)
                except Exception as error:
                    if not self.cancelled:
                        self.signals.imageFailed.emit(index, str(error))
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass
