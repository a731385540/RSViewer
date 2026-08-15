import unittest

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreviewPage,
)
from app.services.online_gallery_memory_cache import OnlineGalleryMemoryCache


def make_gallery(gid):
    return OnlineGallery(
        gid,
        f"token{gid}",
        f"https://e-hentai.org/g/{gid}/token{gid}/",
        f"Gallery {gid}",
    )


class OnlineGalleryMemoryCacheTests(unittest.TestCase):
    def test_keeps_twenty_most_recently_accessed_galleries(self):
        cache = OnlineGalleryMemoryCache(max_galleries=20)
        galleries = [make_gallery(gid) for gid in range(1, 23)]
        for gallery in galleries[:21]:
            cache.put_detail(
                "ehentai",
                OnlineGalleryDetail(gallery=gallery, title=gallery.title),
            )

        self.assertEqual(20, len(cache))
        self.assertIsNone(cache.get_detail("ehentai", galleries[0]))
        self.assertIsNotNone(cache.get_detail("ehentai", galleries[1]))

        cache.get_detail("ehentai", galleries[1])
        cache.put_detail(
            "ehentai",
            OnlineGalleryDetail(gallery=galleries[21], title=galleries[21].title),
        )
        self.assertIsNotNone(cache.get_detail("ehentai", galleries[1]))
        self.assertIsNone(cache.get_detail("ehentai", galleries[2]))
        self.assertEqual(20, len(cache))

    def test_caches_preview_pages_and_bounds_reader_images(self):
        cache = OnlineGalleryMemoryCache(
            max_preview_sources_per_gallery=2,
            max_page_images_per_gallery=2,
            max_page_image_bytes=8,
        )
        gallery = make_gallery(1)
        preview_page = OnlineGalleryPreviewPage(gallery, 1, 1)
        cache.put_preview_page("ehentai", preview_page)
        cache.put_preview_image("ehentai", gallery, 0, b"thumb")
        cache.put_preview_source("ehentai", gallery, "sprite-1", b"one")
        cache.put_preview_source("ehentai", gallery, "sprite-2", b"two")
        cache.put_preview_source("ehentai", gallery, "sprite-3", b"three")
        cache.put_page_image("ehentai", gallery, 0, b"1234")
        cache.put_page_image("ehentai", gallery, 1, b"5678")
        cache.put_page_image("ehentai", gallery, 2, b"90ab")

        self.assertIs(
            preview_page, cache.get_preview_page("ehentai", gallery, 1)
        )
        self.assertEqual(b"thumb", cache.get_preview_image("ehentai", gallery, 0))
        self.assertIsNone(cache.get_preview_source("ehentai", gallery, "sprite-1"))
        self.assertEqual(b"two", cache.get_preview_source("ehentai", gallery, "sprite-2"))
        self.assertEqual(b"three", cache.get_preview_source("ehentai", gallery, "sprite-3"))
        self.assertIsNone(cache.get_page_image("ehentai", gallery, 0))
        self.assertEqual(b"5678", cache.get_page_image("ehentai", gallery, 1))
        self.assertEqual(b"90ab", cache.get_page_image("ehentai", gallery, 2))


if __name__ == "__main__":
    unittest.main()
