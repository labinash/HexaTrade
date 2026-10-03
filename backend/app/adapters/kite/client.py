import asyncio
import time
from collections.abc import Callable


class RateLimiter:
    """Spaces Kite REST calls. Quote ~1/s, historical ~3/s, everything else ~10/s."""

    def __init__(self) -> None:
        self._gaps = {"quote": 1.05, "historical": 0.36, "other": 0.11}
        self._next = {name: 0.0 for name in self._gaps}
        self._lock = asyncio.Lock()

    async def wait(self, kind: str) -> None:
        async with self._lock:
            gap = self._gaps.get(kind, self._gaps["other"])
            delay = self._next[kind] - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next[kind] = time.monotonic() + gap


def call_with_backoff(func: Callable, *args, retries: int = 3, **kwargs):
    delay = 0.5
    last_error = None
    for _ in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            if "429" in message or "too many" in message or "rate" in message:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last_error
