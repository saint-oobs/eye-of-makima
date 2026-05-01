"""
In-memory TTL cache with optional per-guild namespacing.

Provides:
    TTLCache       — generic key/value store with per-entry TTL
    GuildCache     — thin wrapper that namespaces keys by guild_id
    timed_lru      — decorator for caching coroutine/function results
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, Generic, TypeVar

V = TypeVar("V")


class _Entry(Generic[V]):
    __slots__ = ("value", "expires_at")

    def __init__(self, value: V, ttl: float) -> None:
        self.value      = value
        self.expires_at = time.monotonic() + ttl


class TTLCache(Generic[V]):
    """
    Thread-safe (asyncio-safe) TTL key/value cache.

    Parameters
    ----------
    default_ttl:
        Seconds until an entry expires when no per-set TTL is given.
    max_size:
        If set, the oldest entries are evicted when the cache exceeds
        this size. ``None`` means unbounded.
    """

    def __init__(
        self,
        default_ttl: float = 60.0,
        max_size:    int | None = None,
    ) -> None:
        self._default_ttl = default_ttl
        self._max_size    = max_size
        self._store:  dict[Any, _Entry[V]] = {}

    # ── Core operations ────────────────────────────────────────

    def set(
        self,
        key:   Any,
        value: V,
        ttl:   float | None = None,
    ) -> None:
        """Store *value* under *key*, expiring after *ttl* seconds."""
        self._evict_expired()
        if self._max_size and len(self._store) >= self._max_size:
            # Evict the entry that expires soonest
            oldest = min(self._store, key=lambda k: self._store[k].expires_at)
            del self._store[oldest]
        self._store[key] = _Entry(value, ttl if ttl is not None else self._default_ttl)

    def get(self, key: Any, default: V | None = None) -> V | None:
        """Return the cached value or *default* if missing / expired."""
        entry = self._store.get(key)
        if entry is None:
            return default
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return default
        return entry.value

    def delete(self, key: Any) -> bool:
        """Remove a key. Returns True if it existed."""
        return self._store.pop(key, None) is not None

    def has(self, key: Any) -> bool:
        """Return True if *key* exists and has not expired."""
        return self.get(key) is not None

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()

    def invalidate_prefix(self, prefix: Any) -> int:
        """
        Remove all keys whose first element equals *prefix*.
        Useful for clearing all entries belonging to a guild.
        Returns number of removed entries.
        """
        to_remove = [
            k for k in self._store
            if isinstance(k, tuple) and k[0] == prefix
        ]
        for k in to_remove:
            del self._store[k]
        return len(to_remove)

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)

    def __contains__(self, key: Any) -> bool:
        return self.has(key)

    # ── Internal ───────────────────────────────────────────────

    def _evict_expired(self) -> None:
        now     = time.monotonic()
        expired = [k for k, e in self._store.items() if now > e.expires_at]
        for k in expired:
            del self._store[k]


class GuildCache(TTLCache[V]):
    """
    TTLCache that namespaces every key under a guild_id so different
    guilds never collide.

    Usage:
        cache = GuildCache(default_ttl=300)
        cache.set(guild_id, "prefix", "!")
        cache.get(guild_id, "prefix")
        cache.delete(guild_id, "prefix")
        cache.clear_guild(guild_id)
    """

    def set(self, guild_id: int, key: Any, value: V, ttl: float | None = None) -> None:  # type: ignore[override]
        super().set((guild_id, key), value, ttl)

    def get(self, guild_id: int, key: Any, default: V | None = None) -> V | None:  # type: ignore[override]
        return super().get((guild_id, key), default)

    def delete(self, guild_id: int, key: Any) -> bool:  # type: ignore[override]
        return super().delete((guild_id, key))

    def has(self, guild_id: int, key: Any) -> bool:  # type: ignore[override]
        return super().has((guild_id, key))

    def clear_guild(self, guild_id: int) -> int:
        """Remove all cached entries for a specific guild."""
        return self.invalidate_prefix(guild_id)


# ── timed_lru decorator ────────────────────────────────────────

def timed_lru(ttl: float = 60.0) -> Callable:
    """
    Decorator that caches the return value of a sync or async function
    for *ttl* seconds, keyed on its positional arguments.

    Usage:
        @timed_lru(ttl=300)
        async def get_guild_prefix(bot, guild_id: int) -> str:
            ...
    """
    _cache: TTLCache[Any] = TTLCache(default_ttl=ttl)

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                key = (args, tuple(sorted(kwargs.items())))
                hit = _cache.get(key)
                if hit is not None:
                    return hit
                result = await func(*args, **kwargs)
                _cache.set(key, result)
                return result

            async_wrapper.cache_clear = _cache.clear  # type: ignore[attr-defined]
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                key = (args, tuple(sorted(kwargs.items())))
                hit = _cache.get(key)
                if hit is not None:
                    return hit
                result = func(*args, **kwargs)
                _cache.set(key, result)
                return result

            sync_wrapper.cache_clear = _cache.clear  # type: ignore[attr-defined]
            return sync_wrapper

    return decorator


# ── Module-level shared instances ──────────────────────────────
# Import these in cogs that need lightweight caching.

config_cache:  GuildCache[Any] = GuildCache(default_ttl=120)
premium_cache: GuildCache[bool] = GuildCache(default_ttl=60)
prefix_cache:  GuildCache[str]  = GuildCache(default_ttl=300)