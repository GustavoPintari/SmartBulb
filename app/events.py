"""In-process pub/sub that feeds the Server-Sent Events stream."""
import asyncio
import json
import threading


class Broker:
    def __init__(self):
        self._subscribers = set()
        self._lock = threading.Lock()

    def subscribe(self):
        """Register a queue on the running event loop; call from async code."""
        entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=200))
        with self._lock:
            self._subscribers.add(entry)
        return entry

    def unsubscribe(self, entry):
        with self._lock:
            self._subscribers.discard(entry)

    def publish(self, kind, data=None):
        """Thread-safe: may be called from worker threads."""
        message = json.dumps({"type": kind, "data": data}, ensure_ascii=False)
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_put, queue, message)
            except RuntimeError:  # loop closed
                self.unsubscribe((loop, queue))


def _put(queue, message):
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        pass
