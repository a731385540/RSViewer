import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication


APP_NAME = "RSViewer"
ORGANIZATION_NAME = ""


@dataclass(frozen=True)
class ApplicationPaths:
    project_root: Path
    bundle_root: Path
    runtime_root: Path
    data_root: Path
    config_path: Path
    database_path: Path
    online_thumbnail_cache_dir: Path
    qss_root: Path
    legacy_config_paths: tuple
    legacy_database_paths: tuple


def _legacy_local_root(value=None):
    if value is not None:
        return Path(value).expanduser().resolve()
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if not local_app_data:
        return None
    return Path(local_app_data).expanduser().resolve() / APP_NAME


def resolve_application_paths(
    *,
    frozen=None,
    project_root=None,
    bundle_root=None,
    runtime_root=None,
    legacy_local_root=None,
):
    """Resolve bundled resources and portable writable application state."""

    project_root = Path(
        project_root or Path(__file__).resolve().parents[2]
    ).resolve()
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if bundle_root is None:
        bundle_root = getattr(sys, "_MEIPASS", project_root) if frozen else project_root
    bundle_root = Path(bundle_root).resolve()
    if runtime_root is None:
        runtime_root = Path(sys.executable).resolve().parent if frozen else project_root
    runtime_root = Path(runtime_root).resolve()
    data_root = runtime_root / "data"

    legacy_config_paths = []
    legacy_database_paths = []
    if frozen:
        local_root = _legacy_local_root(legacy_local_root)
        if local_root is not None:
            legacy_config_paths.append(local_root / "config.json")
            legacy_database_paths.append(local_root / "data" / "rsviewer.db")
    else:
        legacy_config_paths.append(project_root / "app" / "config" / "config.json")
        legacy_database_paths.append(project_root / "app" / "data" / "rsviewer.db")

    return ApplicationPaths(
        project_root=project_root,
        bundle_root=bundle_root,
        runtime_root=runtime_root,
        data_root=data_root,
        config_path=data_root / "config.json",
        database_path=data_root / "rsviewer.db",
        online_thumbnail_cache_dir=data_root / "cache" / "online_thumbnails",
        qss_root=bundle_root / "app" / "resource" / "qss",
        legacy_config_paths=tuple(legacy_config_paths),
        legacy_database_paths=tuple(legacy_database_paths),
    )


def migrate_state_file(target, legacy_candidates):
    """Copy the first existing legacy file when the portable target is absent."""

    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return False
    for candidate in legacy_candidates:
        candidate = Path(candidate).resolve()
        if candidate == target or not candidate.is_file():
            continue
        temporary = target.with_name(f".{target.name}.{os.getpid()}.migrating")
        try:
            shutil.copy2(candidate, temporary)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return True
    return False


def prepare_config_path():
    return migrate_state_file(CONFIG_PATH, PATHS.legacy_config_paths)


def prepare_database_path():
    return migrate_state_file(DATABASE_PATH, PATHS.legacy_database_paths)


QCoreApplication.setApplicationName(APP_NAME)
QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
PATHS = resolve_application_paths()
PROJECT_ROOT = PATHS.project_root
BUNDLE_ROOT = PATHS.bundle_root
RUNTIME_ROOT = PATHS.runtime_root
DATA_ROOT = PATHS.data_root
CONFIG_PATH = PATHS.config_path
DATABASE_PATH = PATHS.database_path
ONLINE_THUMBNAIL_CACHE_DIR = PATHS.online_thumbnail_cache_dir
QSS_ROOT = PATHS.qss_root
