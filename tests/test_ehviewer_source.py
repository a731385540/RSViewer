import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.sources.ehviewer_source import EhViewerDataSource


class EhViewerDataSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database = root / "eh.db"
        self.manga_root = root / "漫画"
        self.manga_root.mkdir()
        folder = self.manga_root / "42-sample"
        folder.mkdir()
        (folder / ".thumb").write_bytes(b"thumbnail")
        (folder / "10.webp").write_bytes(b"ten")
        (folder / "2.webp").write_bytes(b"two")
        (folder / "1.webp").write_bytes(b"one")
        (folder / "note.txt").write_text("ignored", encoding="utf-8")

        tag_columns = ", ".join(f'"{name}" TEXT' for name in (
            "ARTIST", "COSPLAYER", "CHARACTER", "FEMALE", "GROUP",
            "LANGUAGE", "MALE", "MISC", "MIXED", "OTHER", "PARODY",
            "RECLASS",
        ))
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(f"""
                CREATE TABLE DOWNLOADS (
                    GID INTEGER PRIMARY KEY, TITLE TEXT, TITLE_JPN TEXT,
                    CATEGORY INTEGER NOT NULL, LABEL TEXT, TIME INTEGER NOT NULL
                );
                CREATE TABLE DOWNLOAD_DIRNAME (GID INTEGER PRIMARY KEY, DIRNAME TEXT);
                CREATE TABLE Gallery_Tags (GID INTEGER PRIMARY KEY, {tag_columns});
                CREATE TABLE DOWNLOAD_LABELS (_id INTEGER PRIMARY KEY, LABEL TEXT, TIME INTEGER);
                INSERT INTO DOWNLOADS VALUES (42, 'English', '原标题', 4, '主标签', 1);
                INSERT INTO DOWNLOAD_DIRNAME VALUES (42, '42-sample');
                INSERT INTO Gallery_Tags (GID, ARTIST) VALUES (42, 'artist name');
                INSERT INTO DOWNLOAD_LABELS VALUES (1, '主标签', 1);
            """)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_library_listing_is_lazy_and_database_stays_read_only(self):
        source = EhViewerDataSource(self.database, self.manga_root)
        before = hashlib.sha256(self.database.read_bytes()).digest()
        items = source.list_local_manga()
        after = hashlib.sha256(self.database.read_bytes()).digest()

        self.assertEqual(1, len(items))
        self.assertEqual((), items[0].page_paths)
        self.assertEqual(0, items[0].page_count)
        self.assertEqual(items[0].folder / ".thumb", items[0].cover_image_path)
        self.assertEqual(1, items[0].added_time)
        self.assertEqual(before, after)

    def test_library_is_ordered_by_added_time_descending(self):
        second_folder = self.manga_root / "84-newer"
        second_folder.mkdir()
        (second_folder / ".thumb").write_bytes(b"thumbnail")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO DOWNLOADS VALUES (?, ?, ?, ?, ?, ?)",
                (84, "Newer", "", 4, "主标签", 99),
            )
            connection.execute(
                "INSERT INTO DOWNLOAD_DIRNAME VALUES (?, ?)",
                (84, "84-newer"),
            )
            connection.commit()

        items = EhViewerDataSource(
            self.database, self.manga_root
        ).list_local_manga()

        self.assertEqual([84, 42], [item.gid for item in items])
        self.assertEqual([99, 1], [item.added_time for item in items])

    def test_primary_label_write_updates_only_requested_downloads(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO DOWNLOADS VALUES (?, ?, ?, ?, ?, ?)",
                (84, "Second", "", 4, "", 2),
            )
            connection.commit()
            schema_before = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )

        source = EhViewerDataSource(self.database, self.manga_root)
        source.set_primary_label([42, 84], "主标签")

        with closing(sqlite3.connect(self.database)) as connection:
            labels = dict(
                connection.execute(
                    "SELECT GID, LABEL FROM DOWNLOADS WHERE GID IN (42, 84)"
                )
            )
            schema_after = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        self.assertEqual({42: "主标签", 84: "主标签"}, labels)
        self.assertEqual(schema_before, schema_after)

        with self.assertRaisesRegex(ValueError, "不存在分类标签"):
            source.set_primary_label([42], "不存在")
        with closing(sqlite3.connect(self.database)) as connection:
            unchanged = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = 42"
            ).fetchone()[0]
        self.assertEqual("主标签", unchanged)

    def test_clearing_primary_label_keeps_label_and_external_schema(self):
        with closing(sqlite3.connect(self.database)) as connection:
            schema_before = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        source = EhViewerDataSource(self.database, self.manga_root)
        source.clear_primary_label((42,))

        with closing(sqlite3.connect(self.database)) as connection:
            manga_label = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = 42"
            ).fetchone()[0]
            saved_label = connection.execute(
                "SELECT LABEL FROM DOWNLOAD_LABELS WHERE _id = 1"
            ).fetchone()[0]
            schema_after = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        self.assertEqual("", manga_label)
        self.assertEqual("主标签", saved_label)
        self.assertEqual(schema_before, schema_after)

    def test_creating_primary_label_changes_data_but_not_external_schema(self):
        with closing(sqlite3.connect(self.database)) as connection:
            schema_before = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        source = EhViewerDataSource(self.database, self.manga_root)
        source.create_primary_label("新分类")
        source.create_primary_label("新分类")

        with closing(sqlite3.connect(self.database)) as connection:
            labels = list(
                connection.execute(
                    "SELECT LABEL FROM DOWNLOAD_LABELS WHERE LABEL = '新分类'"
                )
            )
            schema_after = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        self.assertEqual([("新分类",)], labels)
        self.assertEqual(schema_before, schema_after)

    def test_deleting_primary_label_uncategorizes_manga_without_schema_change(self):
        with closing(sqlite3.connect(self.database)) as connection:
            schema_before = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        EhViewerDataSource(self.database, self.manga_root).delete_primary_label(
            "主标签"
        )

        with closing(sqlite3.connect(self.database)) as connection:
            manga_label = connection.execute(
                "SELECT LABEL FROM DOWNLOADS WHERE GID = 42"
            ).fetchone()[0]
            labels = list(connection.execute("SELECT LABEL FROM DOWNLOAD_LABELS"))
            schema_after = list(
                connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )
            )
        self.assertEqual("", manga_label)
        self.assertEqual([], labels)
        self.assertEqual(schema_before, schema_after)

    def test_missing_thumbnail_falls_back_to_first_natural_page(self):
        (self.manga_root / "42-sample" / ".thumb").unlink()
        source = EhViewerDataSource(self.database, self.manga_root)
        item = source.list_local_manga()[0]

        self.assertEqual("1.webp", source.find_cover_path(item).name)

    def test_pages_are_loaded_on_demand_and_naturally_sorted(self):
        source = EhViewerDataSource(self.database, self.manga_root)
        item = source.list_local_manga()[0]
        loaded = source.load_pages(item)

        self.assertEqual(["1.webp", "2.webp", "10.webp"], [p.name for p in loaded.page_paths])
        self.assertEqual(3, loaded.page_count)
        self.assertEqual("1.webp", loaded.cover_path.name)

    def test_sidecar_total_pages_detects_partial_download_even_if_db_says_finished(self):
        folder = self.manga_root / "42-sample"
        tokens = "".join(f"{index} {index + 1:010x}\n" for index in range(12))
        (folder / ".ehviewer").write_text(
            "VERSION2\n00000000\n42\ngallerytoken\n1\n1\n20\n12\n" + tokens,
            encoding="ascii",
        )
        source = EhViewerDataSource(self.database, self.manga_root)
        loaded = source.load_pages(source.list_local_manga()[0])

        self.assertEqual(12, loaded.page_count)
        self.assertEqual(3, loaded.downloaded_page_count)
        self.assertFalse(loaded.download_complete)
        self.assertEqual("gallerytoken", loaded.gallery_token)
        self.assertEqual(12, len(loaded.page_tokens))

    def test_missing_configuration_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "EhViewer 数据库"):
            EhViewerDataSource("", "").list_local_manga()


if __name__ == "__main__":
    unittest.main()
