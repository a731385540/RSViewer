import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Optional

from app.common.app_paths import ONLINE_THUMBNAIL_CACHE_DIR


DEFAULT_THUMBNAIL_CACHE_DIR = ONLINE_THUMBNAIL_CACHE_DIR


class OnlineThumbnailCache:
    """Small file cache for encoded online gallery thumbnails.

    Files are isolated by site and use their modification time as the expiry
    timestamp, so no database or sidecar index is required.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else DEFAULT_THUMBNAIL_CACHE_DIR

    def get(self, site: str, url: str, max_age_hours: int) -> Optional[bytes]:
        path = self.path_for(site, url)
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return None

        max_age_seconds = max(1, int(max_age_hours)) * 60 * 60
        if time.time() - stat.st_mtime > max_age_seconds:
            self._remove(path)
            return None

        try:
            data = path.read_bytes()
        except OSError:
            self._remove(path)
            return None
        if not data:
            self._remove(path)
            return None
        return data

    def put(self, site: str, url: str, data: bytes) -> bool:
        if not data:
            return False
        path = self.path_for(site, url)
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(data)
            os.replace(temporary, path)
        except OSError:
            self._remove(temporary)
            return False
        return True

    def discard(self, site: str, url: str):
        self._remove(self.path_for(site, url))

    def path_for(self, site: str, url: str) -> Path:
        safe_site = "exhentai" if site == "exhentai" else "ehentai"
        digest = hashlib.sha256(url.encode("utf-8", errors="surrogatepass")).hexdigest()
        return self.root / safe_site / f"{digest}.img"

    @staticmethod
    def _remove(path: Path):
        try:
            path.unlink()
        except (FileNotFoundError, OSError):
            pass
