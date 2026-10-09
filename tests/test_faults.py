"""Milestone 7 hardening (PLAN.md §6.7): fault injection per upstream through
the real clients, plus stale-badge and nodata rendering in /frag/result."""

import httpx
import pytest
from fastapi.testclient import TestClient

from journey_weather import geocode, routing, weather
from journey_weather.app import app
from journey_weather.cache import Cache, UpstreamError
from journey_weather.pipeline import TripResult
from journey_weather.severity import Trigger
from journey_weather.pipeline import Row
from conftest import mock_client
from test_endpoints import DS_HEADER, _mini_result, events_of


@pytest.fixture
async def fcache(tmp_path):
    """Cache wired to a flaky transport factory set by each test."""
    holder = {"handler": lambda r: httpx.Response(500)}

    def handler(request):
        return holder["handler"](request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "f.db", http)
        await c.open()
        yield c, holder
        await c.close()


@pytest.fixture
def no_backoff(monkeypatch):
    import journey_weather.cache as cache_mod

    async def fake_sleep(_):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)


# ---------- Geocoding: 503 then recovery; persistent failure with stale row ----------

async def test_geocode_503_retries_then_ok(fcache, no_backoff):
    c, holder = fcache
    url = str(geocode.GEOCODE_BASE.copy_with(params={"name": "Kanab, UT", "count": 5}))
    seen = {"n": 0}

    def handler(r):
        seen["n"] += 1
        if seen["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [
            {"name": "Kanab", "admin1": "Utah", "country_code": "US",
             "latitude": 37.05, "longitude": -112.53, "timezone": "America/Denver"}]})

    holder["handler"] = handler
    stop = await geocode.resolve_stop(c, "Kanab, UT")
    assert stop.display == "Kanab, Utah, US"
    assert seen["n"] == 3


async def test_geocode_persistent_failure_after_cache(fcache, no_backoff):
    """First fetch succeeds; later outage serves the stale row (§3)."""
    c, holder = fcache
    url = str(geocode.GEOCODE_BASE.copy_with(params={"name": "Kanab, UT", "count": 5}))
    good = {"results": [{"name": "Kanab", "admin1": "Utah", "country_code": "US",
                         "latitude": 37.05, "longitude": -112.53, "timezone": "America/Denver"}]}
    holder["handler"] = lambda r: httpx.Response(200, json=good)
    await geocode.resolve_stop(c, "Kanab, UT")
    holder["handler"] = lambda r: httpx.Response(500)
    stop = await geocode.resolve_stop(c, "Kanab, UT", refresh=True)  # refresh → outage → stale
    assert stop.display == "Kanab, Utah, US"


# ---------- OSRM: 429 retry; timeout with no row raises ----------

async def test_osrm_429_then_ok(fcache, no_backoff):
    c, holder = fcache
    points = [(41.0, -111.9), (37.0, -112.5)]
    seen = {"n": 0}

    def handler(r):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={
            "code": "Ok",
            "routes": [{"geometry": {"coordinates": [[-111.9, 41.0], [-112.5, 37.0]]},
                        "legs": [{"annotation": {"duration": [100.0], "distance": [50.0]}}]}],
        })

    holder["handler"] = handler
    legs = await routing.route_trip(c, points)
    assert legs[0].leg_duration_s == 100.0


async def test_osrm_timeout_raises(fcache, no_backoff):
    c, holder = fcache
    import journey_weather.cache as cache_mod
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        raise httpx.ConnectTimeout("x")

    holder["handler"] = handler
    with pytest.raises(UpstreamError) as ei:
        await routing.route_trip(c, [(1.0, 2.0), (3.0, 4.0)])
    assert ei.value.service == "OSRM"
    assert calls["n"] == 4  # 1 try + 3 retries


# ---------- Weather: persistent 503 raises; chunk-fail marks None ----------

async def test_weather_persistent_503_raises(fcache, no_backoff):
    c, holder = fcache
    holder["handler"] = lambda r: httpx.Response(503)
    with pytest.raises(UpstreamError) as ei:
        await weather.fetch_all(c, [(1.0, 2.0), (3.0, 4.0)], chunk_size=10)
    assert ei.value.service == "Open-Meteo"


async def test_weather_chunk_failure_yields_none_rows(fcache, no_backoff):
    c, holder = fcache
    points = [(37.0 + i * 0.01, -112.5) for i in range(5)]
    good = [{"timezone": "America/Denver", "utc_offset_seconds": -21600,
             "hourly": {"time": [100]}}]

    def handler(r):
        if str(r.url) == weather.build_url(points):
            return httpx.Response(400)  # non-retryable bulk failure
        if str(r.url) == weather.build_url(points[:2]):
            return httpx.Response(200, json=good * 2)
        return httpx.Response(400)

    holder["handler"] = handler
    import journey_weather.weather as wmod

    async def fake_sleep(_):
        pass

    old, wmod.asyncio.sleep = wmod.asyncio.sleep, fake_sleep
    try:
        locs = await weather.fetch_all(c, points, chunk_size=2)
    finally:
        wmod.asyncio.sleep = old
    assert [l is None for l in locs] == [False, False, True, True, True]


# ---------- Labels: outage never fails plan (fallback) — see test_labels; ----------
# endpoint-level: stale badge + nodata callout render inside #route-result

def test_stale_badge_and_nodata_callout_render():
    stale = _mini_result()
    stale.stale = True
    stale.oldest_age_s = 40000
    stale.nodata_count = 3
    stale.rows[1].level = "nodata"
    with TestClient(app) as client:
        import journey_weather.pipeline as pl
        import journey_weather.labels as lb

        async def fake_plan(cache, signals, *, refresh=False):
            return stale

        async def fake_label(cache, lat, lon, *, refresh=False):
            return "Nowhere"

        orig_plan, orig_lab = pl.plan_trip, lb.reverse_label
        pl.plan_trip, lb.reverse_label = fake_plan, fake_label
        try:
            body = client.get("/frag/result?datastar=%7B%7D", headers=DS_HEADER).text
        finally:
            pl.plan_trip, lb.reverse_label = orig_plan, orig_lab
    assert "stale data" in body
    assert "Weather unavailable for 3 points." in body
    assert 'class="level-nodata"' in body
