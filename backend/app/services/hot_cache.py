"""Small project-scoped hot cache with optional Redis backend.

Siming 2.1 keeps the database authoritative. This cache only stores derived
read payloads such as outline trees and lightweight indexes. It is safe to drop
at any time and is invalidated after project writes refresh the file mirror.
"""
from __future__ import annotations

import fnmatch
import json
import threading
import time
from typing import Any

from ..core.legacy_env import get_compatible_env

DEFAULT_TTL_SECONDS = 60
_MEMORY_LOCK = threading.RLock()
_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_REDIS_CLIENT: Any | None | bool = None


def _redis_client() -> Any | None:
    """Return an optional Redis client, or None when unavailable."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is False:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    url = get_compatible_env("SIMING_REDIS_URL", "REDIS_URL")
    if not url:
        _REDIS_CLIENT = False
        return None
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        return client
    except Exception:
        _REDIS_CLIENT = False
        return None


def project_cache_key(project_id: str, namespace: str, variant: str = "default") -> str:
    return f"siming:project:{project_id}:{namespace}:{variant}"


def get_json(key: str) -> Any | None:
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    now = time.time()
    with _MEMORY_LOCK:
        item = _MEMORY_CACHE.get(key)
        if not item:
            return None
        expires_at, raw = item
        if expires_at < now:
            _MEMORY_CACHE.pop(key, None)
            return None
        try:
            return json.loads(raw)
        except Exception:
            _MEMORY_CACHE.pop(key, None)
            return None


def set_json(key: str, value: Any, *, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    client = _redis_client()
    if client is not None:
        try:
            client.setex(key, max(1, ttl), raw)
            return
        except Exception:
            pass

    with _MEMORY_LOCK:
        _MEMORY_CACHE[key] = (time.time() + max(1, ttl), raw)


def delete_pattern(pattern: str) -> int:
    """Delete cached keys matching a glob-like pattern."""
    deleted = 0
    client = _redis_client()
    if client is not None:
        try:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                deleted += int(client.delete(*keys))
            return deleted
        except Exception:
            return 0

    with _MEMORY_LOCK:
        for key in list(_MEMORY_CACHE.keys()):
            if fnmatch.fnmatch(key, pattern):
                _MEMORY_CACHE.pop(key, None)
                deleted += 1
    return deleted


def invalidate_project(project_id: str) -> int:
    if not project_id:
        return 0
    return delete_pattern(f"siming:project:{project_id}:*")
