import tempfile
import unittest
from pathlib import Path

from app.common.app_paths import migrate_state_file, resolve_application_paths


class ApplicationPathsTests(unittest.TestCase):
    def test_source_run_uses_project_portable_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "project"
            paths = resolve_application_paths(
                frozen=False,
                project_root=project_root,
            )

        data_root = project_root.resolve() / "data"
        self.assertEqual(data_root / "config.json", paths.config_path)
        self.assertEqual(data_root / "rsviewer.db", paths.database_path)
        self.assertEqual(
            data_root / "cache" / "online_thumbnails",
            paths.online_thumbnail_cache_dir,
        )
        self.assertEqual(
            project_root.resolve() / "app" / "resource" / "qss",
            paths.qss_root,
        )
        self.assertEqual(
            (project_root.resolve() / "app" / "config" / "config.json",),
            paths.legacy_config_paths,
        )

    def test_frozen_run_uses_executable_directory_but_bundle_qss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_root = root / "source"
            bundle_root = root / "_MEI12345"
            install_root = root / "portable"
            legacy_root = root / "local-app-data" / "RSViewer"
            paths = resolve_application_paths(
                frozen=True,
                project_root=project_root,
                bundle_root=bundle_root,
                runtime_root=install_root,
                legacy_local_root=legacy_root,
            )

        data_root = install_root.resolve() / "data"
        self.assertEqual(
            bundle_root.resolve() / "app" / "resource" / "qss",
            paths.qss_root,
        )
        self.assertEqual(data_root / "config.json", paths.config_path)
        self.assertEqual(data_root / "rsviewer.db", paths.database_path)
        self.assertEqual(
            data_root / "cache" / "online_thumbnails",
            paths.online_thumbnail_cache_dir,
        )
        self.assertEqual(
            (legacy_root.resolve() / "data" / "rsviewer.db",),
            paths.legacy_database_paths,
        )

    def test_legacy_file_is_migrated_once_without_overwriting_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "data" / "config.json"
            first = root / "missing.json"
            second = root / "legacy" / "config.json"
            second.parent.mkdir(parents=True)
            second.write_bytes(b"legacy")

            self.assertTrue(migrate_state_file(target, (first, second)))
            self.assertEqual(b"legacy", target.read_bytes())
            second.write_bytes(b"changed")
            self.assertFalse(migrate_state_file(target, (second,)))
            self.assertEqual(b"legacy", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
