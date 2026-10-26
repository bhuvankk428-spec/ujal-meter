"""In-process TTL cache with stale-while-revalidate and single-flight.

Small on purpose (~150 lines): a single-process wrapper around one operator
session does not need Redis. See README for the point at which that changes.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheState(StrEnum):
    HIT = "HIT"
    STALE = "STALE"
    MISS = "MISS"


@dataclass(frozen=True, slots=True)
class CacheResult(Generic[T]):
    value: T
    state: CacheState
    age_s: float


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    value: T
    stored_at: float
    fresh_ttl: float
    stale_ttl: float

    def age(self, now: float) -> float:
        return max(0.0, now - self.stored_at)


class TTLCache:
    def __init__(self, jitter_ratio: float = 0.05) -> None:
        self._entries: dict[str, CacheEntry[Any]] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self._jitter_ratio = max(0.0, jitter_ratio)
        self.background_errors = 0

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        *,
        fresh_ttl: float,
        stale_ttl: float,
    ) -> CacheResult[T]:
        now = time.monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                age = entry.age(now)
                if age < entry.fresh_ttl:
                    return CacheResult(entry.value, CacheState.HIT, round(age, 1))
                if age < entry.stale_ttl:
                    self._start_refresh(key, loader, fresh_ttl, stale_ttl)
                    return CacheResult(entry.value, CacheState.STALE, round(age, 1))

            task = self._inflight.get(key)
            if task is None:
                task = self._start_refresh(key, loader, fresh_ttl, stale_ttl)

        try:
            value = await task
        except asyncio.CancelledError:
            raise
        except Exception:
            raise
        return CacheResult(value, CacheState.MISS, 0.0)

    def _start_refresh(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        fresh_ttl: float,
        stale_ttl: float,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(
            self._load(key, loader, fresh_ttl, stale_ttl), name=f"cache:{key}"
        )
        self._inflight[key] = task
        task.add_done_callback(functools.partial(self._on_done, key))
        return task

    async def _load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        fresh_ttl: float,
        stale_ttl: float,
    ) -> T:
        try:
            value = await loader()
        except BaseException:
            raise
        fresh = self._jitter(fresh_ttl)
        stale = max(fresh, self._jitter(stale_ttl))
        async with self._lock:
            self._entries[key] = CacheEntry(
                value=value, stored_at=time.monotonic(), fresh_ttl=fresh, stale_ttl=stale
            )
        return value

    def _jitter(self, ttl: float) -> float:
        if ttl <= 0 or self._jitter_ratio <= 0:
            return max(0.0, ttl)
        return ttl * (1 + random.uniform(-self._jitter_ratio, self._jitter_ratio))

    def _on_done(self, key: str, task: asyncio.Task[Any]) -> None:
        self._inflight.pop(key, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.background_errors += 1
            logger.warning("cache refresh for %s failed: %s", key, exc)

    async def invalidate(self, prefix: str | None = None) -> int:
        async with self._lock:
            if prefix is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            keys = [key for key in self._entries if key.startswith(prefix)]
            for key in keys:
                del self._entries[key]
            return len(keys)

    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._entries),
            "refreshes_inflight": len(self._inflight),
            "background_errors": self.background_errors,
        }

    async def aclose(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
