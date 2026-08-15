import unittest
from types import SimpleNamespace

from app.services.manga_title_similarity import (
    find_similar_manga,
    manga_similarity_score,
    title_fingerprint,
)


def manga(gid, english_title="", original_title="", added_time=0):
    return SimpleNamespace(
        gid=gid,
        english_title=english_title,
        original_title=original_title,
        added_time=added_time,
    )


class MangaTitleSimilarityTests(unittest.TestCase):
    def test_extracts_work_title_from_common_chapter_and_metadata_patterns(self):
        variants = (
            "[Circle (Artist)] Real Work Ch. 01 [English]",
            "Real Work Chapter 2 [Digital]",
            "Real Work 第三話",
            "Real Work Vol.4",
            "Real Work 後編",
            "Real Work [Chapter Five]",
            "Real Work (6)",
        )
        fingerprints = [title_fingerprint(title) for title in variants]

        for fingerprint in fingerprints:
            self.assertIn("real work", fingerprint.forms)

    def test_finds_chapters_duplicates_and_cross_field_titles(self):
        reference = manga(
            1,
            english_title="[Circle] The Long Adventure Ch. 1 [English]",
            original_title="長い冒険 第一話",
        )
        candidates = (
            reference,
            manga(2, english_title="The Long Adventure Chapter 2"),
            manga(3, original_title="長い冒険 第2話"),
            manga(4, english_title="The Long Adventures of Somebody Else"),
            manga(5, english_title="An Unrelated Gallery"),
        )

        matches = find_similar_manga(reference, candidates)

        self.assertEqual(1, matches[0].gid)
        self.assertEqual({1, 2, 3}, {item.gid for item in matches})
        self.assertEqual(2.0, manga_similarity_score(reference, reference))

    def test_short_generic_titles_are_not_fuzzily_grouped(self):
        self.assertEqual(
            0.0,
            manga_similarity_score(
                manga(1, english_title="Love"),
                manga(2, english_title="Lover"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
