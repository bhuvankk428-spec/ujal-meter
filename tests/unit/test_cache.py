from __future__ import annotations

import asyncio

from app.services.cache import CacheState, TTLCache


async def test_fresh_hit_does_not_reload() -> None:
    cache = TTLCache(jitter_ratio=0)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    first = await cache.get_or_load("k", loader, fresh_ttl=10, stale_ttl=20)
    second = await cache.get_or_load("k", loader, fresh_ttl=10, stale_ttl=20)
    assert first.state is CacheState.MISS
    assert second.state is CacheState.HIT
    assert first.value == second.value == 1
    assert calls == 1


async def test_stale_is_served_then_background_refresh() -> None:
    cache = TTLCache(jitter_ratio=0)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_load("k", loader, fresh_ttl=0.1, stale_ttl=2)
    await asyncio.sleep(0.12)
    stale = await cache.get_or_load("k", loader, fresh_ttl=0.1, stale_ttl=2)
    assert stale.state is CacheState.STALE
    assert stale.value == 1

    await asyncio.sleep(0.03)
    fresh = await cache.get_or_load("k", loader, fresh_ttl=0.1, stale_ttl=2)
    assert fresh.state is CacheState.HIT
    assert fresh.value == 2
    assert calls == 2


async def test_single_flight_coalesces_concurrent_misses() -> None:
    cache = TTLCache(jitter_ratio=0)
    calls = 0

    async def loader() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "value"

    results = await asyncio.gather(
        *[cache.get_or_load("k", loader, fresh_ttl=10, stale_ttl=20) for _ in range(10)]
    )
    assert calls == 1
    assert {result.value for result in results} == {"value"}
    assert {result.state for result in results} == {CacheState.MISS}


async def test_stale_survives_background_failure() -> None:
    cache = TTLCache(jitter_ratio=0)
    fail = False

    async def loader() -> str:
        if fail:
            raise RuntimeError("portal down")
        return "cached"

    await cache.get_or_load("k", loader, fresh_ttl=0.01, stale_ttl=1)
    await asyncio.sleep(0.03)
    fail = True
    stale = await cache.get_or_load("k", loader, fresh_ttl=0.01, stale_ttl=1)
    assert stale.state is CacheState.STALE
    assert stale.value == "cached"
    await asyncio.sleep(0.03)
    assert cache.background_errors == 1
    again = await cache.get_or_load("k", loader, fresh_ttl=0.01, stale_ttl=1)
    assert again.state is CacheState.STALE
    assert again.value == "cached"


async def test_invalidate_and_close() -> None:
    cache = TTLCache(jitter_ratio=0)

    async def loader() -> int:
        return 1

    await cache.get_or_load("a", loader, fresh_ttl=10, stale_ttl=20)
    await cache.get_or_load("b", loader, fresh_ttl=10, stale_ttl=20)
    assert await cache.invalidate("a") == 1
    assert cache.stats()["entries"] == 1
    assert await cache.invalidate() == 1
    await cache.aclose()
