import base64
import os
import tempfile
import time
import unittest
from pathlib import Path

from app.domain.online_gallery import OnlineGallery
from app.services.online_thumbnail_cache import OnlineThumbnailCache
from app.workers.eh_online_worker import OnlineCoverWorker


class _ThumbnailProvider:
    def __init__(self, data=None):
        if data is None:
            data = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        self.data = data
        self.calls = 0

    def load_thumbnail(self, _url):
        self.calls += 1
        return self.data


class OnlineThumbnailCacheTests(unittest.TestCase):
    def test_cache_is_isolated_by_site_and_expires_lazily(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = OnlineThumbnailCache(Path(directory))
            url = "https://ehgt.org/example.jpg"
            self.assertTrue(cache.put("ehentai", url, b"eh"))
            self.assertTrue(cache.put("exhentai", url, b"ex"))
            self.assertEqual(b"eh", cache.get("ehentai", url, 1))
            self.assertEqual(b"ex", cache.get("exhentai", url, 1))

            path = cache.path_for("ehentai", url)
            old_time = time.time() - 7200
            os.utime(path, (old_time, old_time))
            self.assertIsNone(cache.get("ehentai", url, 1))
            self.assertFalse(path.exists())
            self.assertEqual(b"ex", cache.get("exhentai", url, 1))

    def test_cover_worker_reuses_disk_cache_without_second_request(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = OnlineThumbnailCache(Path(directory))
            provider = _ThumbnailProvider()
            item = OnlineGallery(
                1,
                "token",
                "https://e-hentai.org/g/1/token/",
                "Cached cover",
                thumbnail_url="https://ehgt.org/example.jpg",
            )
            loaded = []
            first = OnlineCoverWorker(provider, item, cache, "ehentai", 24)
            first.signals.loaded.connect(lambda _gid, data: loaded.append(data))
            first.run()
            second = OnlineCoverWorker(provider, item, cache, "ehentai", 24)
            second.signals.loaded.connect(lambda _gid, data: loaded.append(data))
            second.run()

            self.assertEqual(1, provider.calls)
            self.assertEqual([provider.data, provider.data], loaded)

            cache.path_for("ehentai", item.thumbnail_url).write_bytes(b"broken")
            third = OnlineCoverWorker(provider, item, cache, "ehentai", 24)
            third.signals.loaded.connect(lambda _gid, data: loaded.append(data))
            third.run()
            self.assertEqual(2, provider.calls)
            self.assertEqual(provider.data, loaded[-1])


if __name__ == "__main__":
    unittest.main()
