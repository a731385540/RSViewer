import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.domain.manga import MangaItem
from app.services.manga_classification_index import MangaClassificationIndex


def make_item(root, gid, category="", playlists=(), taxonomy=()):
    return MangaItem(
        gid=gid,
        english_title=f"Gallery {gid}",
        original_title="",
        category=0,
        category_name="",
        primary_label=category,
        multiple_labels=tuple(playlists),
        tags=(),
        folder=root / str(gid),
        cover_path=root / f"{gid}.jpg",
        thumbnail_path=None,
        page_paths=(),
        page_count=0,
        taxonomy_label_ids=tuple(taxonomy),
    )


class MangaClassificationIndexTests(unittest.TestCase):
    def test_all_and_three_classification_kinds_share_one_gid_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = MangaClassificationIndex()
            index.rebuild(
                (
                    make_item(root, 1, "A", ("Queue",), (11,)),
                    make_item(root, 2, "", ("Queue", "Read"), (12,)),
                    make_item(root, 3, "B", (), (20,)),
                ),
                (
                    (10, None, "Parent", 0),
                    (11, 10, "Child", 0),
                    (12, 11, "Grandchild", 0),
                    (20, None, "Other", 0),
                ),
            )

            self.assertEqual({1, 2, 3}, set(index.all_gids()))
            self.assertEqual(
                {2},
                set(index.gids_for(index.CATEGORY, index.UNCLASSIFIED)),
            )
            self.assertEqual({1, 2}, set(index.gids_for(index.PLAYLIST, "Queue")))
            self.assertEqual({1, 2}, set(index.gids_for(index.TAXONOMY, 10)))

    def test_upsert_moves_gid_without_leaving_stale_memberships(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = MangaClassificationIndex()
            item = make_item(root, 7, "A", ("Old",), (1,))
            index.rebuild((item,), ((1, None, "One", 0), (2, None, "Two", 0)))

            index.upsert(
                replace(
                    item,
                    primary_label="B",
                    multiple_labels=("New",),
                    taxonomy_label_ids=(2,),
                )
            )

            self.assertFalse(index.gids_for(index.CATEGORY, "A"))
            self.assertFalse(index.gids_for(index.PLAYLIST, "Old"))
            self.assertFalse(index.gids_for(index.TAXONOMY, 1))
            self.assertEqual({7}, set(index.gids_for(index.CATEGORY, "B")))
            self.assertEqual({7}, set(index.gids_for(index.PLAYLIST, "New")))
            self.assertEqual({7}, set(index.gids_for(index.TAXONOMY, 2)))


if __name__ == "__main__":
    unittest.main()
