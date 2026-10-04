from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding-window in-memory limiter (per key)."""

    def __init__(self, max_events: int, per_seconds: float) -> None:
        self.max_events = max_events
        self.per_seconds = per_seconds
        self._hits: dict[object, deque[float]] = defaultdict(deque)

    def allow(self, key: object) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.per_seconds:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        if len(self._hits) > 5000:  # bound memory
            for k in [k for k, v in self._hits.items() if not v][:1000]:
                self._hits.pop(k, None)
        return True
