"""Small JSON documents under DATA_DIR, written atomically."""
import json
import os
import threading


class JsonStore:
    """A JSON document loaded once and saved on demand.

    Hold `lock` while reading or changing `data`, then call save().
    """

    def __init__(self, path, default):
        self.path = path
        self.lock = threading.RLock()
        try:
            with open(path) as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = default

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
