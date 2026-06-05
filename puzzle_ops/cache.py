from __future__ import annotations

import time
from typing import Any


class CacheProvider:
    def get(self, key: str) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        raise NotImplementedError


class InMemoryCache(CacheProvider):
    def __init__(self):
        self._values: dict[str, tuple[Any, float | None]] = {}

    def get(self, key: str) -> Any:
        item = self._values.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at < time.time():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = time.time() + ttl if ttl else None
        self._values[key] = (value, expires_at)


class RedisCache(CacheProvider):
    def __init__(self, backend: CacheProvider):
        self.backend = backend

    @classmethod
    def from_url(cls, url: str) -> "RedisCache":
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(url, socket_connect_timeout=0.1, socket_timeout=0.1)
            client.ping()
            return cls(_RedisBackend(client))
        except Exception:
            return cls(InMemoryCache())

    def get(self, key: str) -> Any:
        return self.backend.get(key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.backend.set(key, value, ttl)


class _RedisBackend(CacheProvider):
    def __init__(self, client):
        self.client = client

    def get(self, key: str) -> Any:
        import json

        raw = self.client.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        import json

        self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
