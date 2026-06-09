"""Process-global pub/sub for live activity (gateway turns, tool calls).

The dashboard subscribes and streams events to the browser over SSE — a live activity mirror.
Publishers must never block or fail on a slow
subscriber, so each subscriber has a bounded queue and overflow is dropped.
"""

from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:           # slow consumer — drop rather than block the agent
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


BUS = EventBus()
