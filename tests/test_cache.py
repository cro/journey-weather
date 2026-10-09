"""Cache tests: freshness, key normalization, retry schedule, stale serving."""

import json
import time

import httpx
import pytest

from journey_weather import config
from journey_weather.cache import Cache, UpstreamError, cache_key
from conftest import mock_client

URL = "https://example.test/api?a=1&b=2"


async def test_hit_fetches_once_then_serves_from_cache(tmp_path, no_sleep):
    calls: list[httpx.Request] = []
    async with mock_client({URL: b"payload"}, calls) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        hit = await c.cached_get(URL, 100, service="X")
        assert (hit.body, hit.stale) == (b"payload", False)
        hit2 = await c.cached_get(URL, 100, service="X")
        assert hit2.body == b"payload" and not hit2.stale
        assert len(calls) == 1  # second call never touched the network
        await c.close()


async def test_key_ignores_query_param_order(tmp_path, no_sleep):
    calls: list[httpx.Request] = []
    shuffled = "https://example.test/api?b=2&a=1"
    assert cache_key(shuffled) == cache_key(URL)
    async with mock_client({URL: b"v"}, calls) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        await c.cached_get(URL, 100, service="X")
        hit = await c.cached_get(shuffled, 100, service="X")  # normalized to same row
        assert hit.body == b"v"
        assert len(calls) == 1
        await c.close()


async def test_refresh_forces_refetch(tmp_path, no_sleep):
    calls: list[httpx.Request] = []
    async with mock_client({URL: b"v2"}, calls) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        await c.cached_get(URL, 100, service="X")
        hit = await c.cached_get(URL, 100, service="X", refresh=True)
        assert hit.body == b"v2" and hit.age_s == 0
        assert len(calls) == 2
        await c.close()


async def test_expired_row_refetches(tmp_path, no_sleep):
    async with mock_client({URL: b"fresh"}) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        await c.cached_get(URL, 100, service="X")
        # force the row stale by back-dating fetched_at
        assert c._db is not None
        await c._db.execute(
            "UPDATE http_cache SET fetched_at = ? WHERE key = ?",
            (int(time.time()) - 500, cache_key(URL)),
        )
        await c._db.commit()
        hit = await c.cached_get(URL, 100, service="X")
        assert not hit.stale and hit.body == b"fresh"
        await c.close()


async def test_retry_schedule_1_2_4_then_success(tmp_path, monkeypatch):
    import journey_weather.cache as cache_mod

    slept: list[float] = []
    attempts = {"n": 0}

    async def fake_sleep(s):
        slept.append(s)

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)  # kill jitter
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        hit = await c.cached_get(URL, 100, service="X")
        assert hit.body == b"ok"
        assert slept == [1.0, 2.0, 4.0]  # 3 retries: 1 s, 2 s, 4 s
        assert attempts["n"] == 4
        await c.close()


async def test_429_is_retryable(tmp_path, monkeypatch):
    import journey_weather.cache as cache_mod

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        return httpx.Response(429 if seen["n"] == 1 else 200, content=b"late ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        hit = await c.cached_get(URL, 100, service="X")
        assert hit.body == b"late ok"
        await c.close()


async def test_4xx_fails_immediately(tmp_path, no_sleep):
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        with pytest.raises(UpstreamError) as ei:
            await c.cached_get(URL, 100, service="SVC")
        assert len(calls) == 1  # no retries on non-429 4xx
        assert ei.value.service == "SVC"
        assert "400" in ei.value.detail
        await c.close()


async def test_exhausted_retries_serve_stale_row(tmp_path, monkeypatch):
    import journey_weather.cache as cache_mod

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)

    live = {"ok": True}

    def handler(request):
        if live["ok"]:
            return httpx.Response(200, content=b"cached body")
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        await c.cached_get(URL, 0, service="X")  # ttl 0: always expired
        live["ok"] = False
        hit = await c.cached_get(URL, 0, service="X")
        assert hit.stale and hit.body == b"cached body"
        assert hit.age_s >= 0
        await c.close()


async def test_timeout_then_no_row_raises_upstream_error(tmp_path, monkeypatch):
    import journey_weather.cache as cache_mod

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        with pytest.raises(UpstreamError) as ei:
            await c.cached_get(URL, 10, service="DOWN")
        assert ei.value.service == "DOWN"
        assert calls["n"] == config.RETRY_MAX + 1
        await c.close()


async def test_peek_never_fetches(tmp_path, no_sleep):
    calls: list[httpx.Request] = []
    async with mock_client({URL: b"p"}, calls) as http:
        c = Cache(tmp_path / "t.db", http)
        await c.open()
        assert await c.peek(URL, 100) is None
        await c.cached_get(URL, 100, service="X")
        hit = await c.peek(URL, 100)
        assert hit is not None and hit.body == b"p" and not hit.stale
        assert len(calls) == 1
        await c.close()
