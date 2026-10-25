"""Who changed what and when: a capped log persisted as JSON."""
from datetime import datetime


class ActivityLog:
    def __init__(self, store, broker, timezone, limit=300):
        self._store = store
        self._broker = broker
        self._tz = timezone
        self._limit = limit

    def add(self, actor, action, target=None, detail=None):
        entry = {
            "ts": datetime.now(self._tz).isoformat(timespec="seconds"),
            "actor": actor,
            "action": action,
            "target": target,
            "detail": detail,
        }
        with self._store.lock:
            self._store.data.append(entry)
            del self._store.data[:-self._limit]
            self._store.save()
        self._broker.publish("activity", entry)

    def list(self, limit=50):
        with self._store.lock:
            return list(reversed(self._store.data[-limit:]))
