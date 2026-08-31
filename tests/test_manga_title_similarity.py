import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.services.manga_title_similarity import (
    find_similar_manga,
    manga_similarity_score,
    title_fingerprint,
)
from app.repositories.user_library_repository import UserLibraryRepository
from app.workers.similar_manga_worker import SelectedTitleSearchWorker


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

    def test_selected_text_search_is_literal_excludes_source_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = UserLibraryRepository(Path(directory) / "rsviewer.db")
            items = (
                manga(1, english_title="Alpha Part One", added_time=1),
                manga(2, english_title="ALPHA Part Two", added_time=3),
                manga(3, original_title="前传 alpha part", added_time=2),
                manga(4, english_title="Alphabet Soup", added_time=4),
            )
            found = []
            worker = SelectedTitleSearchWorker(
                repository, 1, "alpha part", items
            )
            worker.signals.found.connect(
                lambda emitted_worker, result: found.append(
                    (emitted_worker, result)
                )
            )
            worker.run()

            emitted_worker, (record, matches) = found[0]
            self.assertIs(worker, emitted_worker)
            self.assertEqual((2, 3), tuple(item.gid for item in matches))
            self.assertEqual((2, 3), record.result_gids)
            self.assertEqual(record, repository.latest_similar_search())


if __name__ == "__main__":
    unittest.main()
