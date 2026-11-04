from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Простой лимитер под ограничение Telegram (~30 сообщений в секунду)."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(rate, 0.1)
        self.capacity = capacity if capacity is not None else max(rate, 1.0)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)
