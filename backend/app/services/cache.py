import json
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger("hextrade.cache")


class QuoteCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.quotes: dict[int, dict] = {}
        self.updated_at: datetime | None = None

    def put(self, quote: dict) -> None:
        token = quote.get("token")
        if token is None:
            return
        with self._lock:
            self.quotes[int(token)] = quote
            self.updated_at = datetime.now(timezone.utc)

    def get(self, token: int) -> dict:
        with self._lock:
            return dict(self.quotes.get(int(token), {}))

    def many(self, tokens: list[int]) -> dict[int, dict]:
        with self._lock:
            return {token: dict(self.quotes[token]) for token in tokens if token in self.quotes}


class RedisBus:
    def __init__(self, url: str) -> None:
        self.url = url
        self.ok = False
        self._client = None
        self.connect()

    def connect(self) -> bool:
        try:
            import redis

            client = redis.Redis.from_url(self.url, socket_connect_timeout=0.4, socket_timeout=0.4)
            client.ping()
            self._client = client
            self.ok = True
        except Exception:
            self._client = None
            self.ok = False
            log.info("Redis is not reachable. Automated orders stay disabled.")
        return self.ok

    def publish(self, payload: dict) -> None:
        if not self.ok or self._client is None:
            return
        try:
            self._client.publish("hextrade", json.dumps(payload, default=str))
        except Exception:
            self.ok = False
