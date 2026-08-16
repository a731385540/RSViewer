import json
import os
from datetime import datetime
from pathlib import Path


class GalleryUpdateStateRepository:
    """Atomic per-folder checkpoints; actual files remain the source of truth."""

    FILE_NAME = "new.json"

    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / self.FILE_NAME

    @staticmethod
    def task_key(gid, token):
        return f"{int(gid)}:{str(token)}"

    def load(self):
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("new.json is unreadable; refusing to overwrite it") from error
        if not isinstance(value, dict):
            raise ValueError("new.json must contain a JSON object")
        return value

    def record(self, gid, token):
        value = self.load().get(self.task_key(gid, token), {})
        return dict(value) if isinstance(value, dict) else {}

    def write(self, gid, token, status, **values):
        state = self.load()
        key = self.task_key(gid, token)
        current = state.get(key, {})
        current = dict(current) if isinstance(current, dict) else {}
        current.update(values)
        current["status"] = max(0, min(6, int(status)))
        current["update"] = datetime.now().astimezone().isoformat(timespec="seconds")
        state[key] = current
        self._atomic_write(state)
        return current

    def _atomic_write(self, value):
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".part")
        data = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(self.path))
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
