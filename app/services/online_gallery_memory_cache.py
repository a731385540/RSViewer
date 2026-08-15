from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock


@dataclass
class OnlineGalleryCacheEntry:
    detail: object = None
    cover_data: bytes = b""
    preview_pages: dict = field(default_factory=dict)
    preview_images: OrderedDict = field(default_factory=OrderedDict)
    preview_sources: OrderedDict = field(default_factory=OrderedDict)


class OnlineGalleryMemoryCache:
    """Thread-safe LRU cache for recently visited online galleries."""

    def __init__(
        self,
        max_galleries=20,
        max_preview_images_per_gallery=80,
        max_preview_sources_per_gallery=8,
        max_page_images_per_gallery=5,
        max_page_image_bytes=128 * 1024 * 1024,
    ):
        self.max_galleries = max(1, int(max_galleries))
        self.max_preview_images_per_gallery = max(
            1, int(max_preview_images_per_gallery)
        )
        self.max_preview_sources_per_gallery = max(
            1, int(max_preview_sources_per_gallery)
        )
        self.max_page_images_per_gallery = max(
            1, int(max_page_images_per_gallery)
        )
        self.max_page_image_bytes = max(1, int(max_page_image_bytes))
        self._entries = OrderedDict()
        self._page_images = OrderedDict()
        self._page_image_bytes = 0
        self._lock = RLock()

    @staticmethod
    def gallery_key(site, gallery):
        return str(site), int(gallery.gid), str(gallery.token)

    def touch(self, site, gallery):
        key = self.gallery_key(site, gallery)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                entry = OnlineGalleryCacheEntry()
            self._entries[key] = entry
            self._trim_galleries()
            return entry

    def get_detail(self, site, gallery):
        entry = self._find(site, gallery)
        return entry.detail if entry is not None else None

    def put_detail(self, site, detail, cover_data=b""):
        with self._lock:
            entry = self.touch(site, detail.gallery)
            entry.detail = detail
            if cover_data:
                entry.cover_data = bytes(cover_data)

    def cover_data(self, site, gallery):
        entry = self._find(site, gallery)
        return entry.cover_data if entry is not None else b""

    def put_cover_data(self, site, gallery, data):
        if not data:
            return
        with self._lock:
            self.touch(site, gallery).cover_data = bytes(data)

    def get_preview_page(self, site, gallery, page_number):
        entry = self._find(site, gallery)
        if entry is None:
            return None
        return entry.preview_pages.get(int(page_number))

    def put_preview_page(self, site, preview_page):
        with self._lock:
            entry = self.touch(site, preview_page.gallery)
            entry.preview_pages[int(preview_page.page_number)] = preview_page

    def get_preview_image(self, site, gallery, page_index):
        with self._lock:
            entry = self._find(site, gallery)
            if entry is None:
                return None
            images = entry.preview_images
            data = images.pop(int(page_index), None)
            if data is not None:
                images[int(page_index)] = data
            return data

    def put_preview_image(self, site, gallery, page_index, data):
        if not data:
            return
        with self._lock:
            images = self.touch(site, gallery).preview_images
            images.pop(int(page_index), None)
            images[int(page_index)] = bytes(data)
            while len(images) > self.max_preview_images_per_gallery:
                images.popitem(last=False)

    def discard_preview_image(self, site, gallery, page_index):
        with self._lock:
            entry = self._find(site, gallery)
            if entry is not None:
                entry.preview_images.pop(int(page_index), None)

    def get_preview_source(self, site, gallery, url):
        with self._lock:
            entry = self._find(site, gallery)
            if entry is None:
                return None
            sources = entry.preview_sources
            data = sources.pop(str(url), None)
            if data is not None:
                sources[str(url)] = data
            return data

    def put_preview_source(self, site, gallery, url, data):
        if not url or not data:
            return
        with self._lock:
            sources = self.touch(site, gallery).preview_sources
            sources.pop(str(url), None)
            sources[str(url)] = bytes(data)
            while len(sources) > self.max_preview_sources_per_gallery:
                sources.popitem(last=False)

    def discard_preview_source(self, site, gallery, url):
        with self._lock:
            entry = self._find(site, gallery)
            if entry is not None:
                entry.preview_sources.pop(str(url), None)

    def get_page_image(self, site, gallery, page_index):
        key = (*self.gallery_key(site, gallery), int(page_index))
        with self._lock:
            if self._find(site, gallery) is None:
                return None
            data = self._page_images.pop(key, None)
            if data is not None:
                self._page_images[key] = data
            return data

    def put_page_image(self, site, gallery, page_index, data):
        if not data:
            return
        key = (*self.gallery_key(site, gallery), int(page_index))
        encoded = bytes(data)
        with self._lock:
            self.touch(site, gallery)
            previous = self._page_images.pop(key, None)
            if previous is not None:
                self._page_image_bytes -= len(previous)
            self._page_images[key] = encoded
            self._page_image_bytes += len(encoded)
            gallery_key = key[:3]
            gallery_pages = [
                current_key
                for current_key in self._page_images
                if current_key[:3] == gallery_key
            ]
            while len(gallery_pages) > self.max_page_images_per_gallery:
                self._drop_page_image(gallery_pages.pop(0))
            while (
                self._page_images
                and self._page_image_bytes > self.max_page_image_bytes
            ):
                self._drop_page_image(next(iter(self._page_images)))

    def discard_page_image(self, site, gallery, page_index):
        key = (*self.gallery_key(site, gallery), int(page_index))
        with self._lock:
            self._drop_page_image(key)

    def __len__(self):
        with self._lock:
            return len(self._entries)

    def keys(self):
        with self._lock:
            return tuple(self._entries)

    def _trim_galleries(self):
        while len(self._entries) > self.max_galleries:
            key, _entry = self._entries.popitem(last=False)
            for page_key in tuple(self._page_images):
                if page_key[:3] == key:
                    self._drop_page_image(page_key)

    def _find(self, site, gallery):
        key = self.gallery_key(site, gallery)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                self._entries[key] = entry
            return entry

    def _drop_page_image(self, key):
        data = self._page_images.pop(key, None)
        if data is not None:
            self._page_image_bytes -= len(data)
