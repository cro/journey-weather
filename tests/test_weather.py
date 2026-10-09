"""Weather fetch tests (§4.6, §6.4): URL build, hour indexing, horizon, chunk fallback."""

import json

import httpx
import pytest

from journey_weather import config, weather
from journey_weather.cache import Cache, UpstreamError
from journey_weather.weather import Location

POINTS = [(37.05, -112.53), (35.20, -111.65)]


def make_loc(hours: list[int], **vars_) -> dict:
    data = {"time": hours}
    for var in (v.strip() for v in weather.HOURLY_VARS.split(",")):
        data[var] = [vars_.get(var)] * len(hours)
    return {"timezone": "America/Denver", "utc_offset_seconds": -21600, "hourly": data}


def test_build_url_rounds_and_uses_verified_params():
    url = weather.build_url([(37.04742, -112.52623)])
    assert "latitude=37.05" in url and "longitude=-112.53" in url
    assert "timezone=auto" in url and "timeformat=unixtime" in url
    assert "forecast_days=16" in url
    assert "weather_code" in url and "cape" in url and "visibility" in url
    assert "thunderstorm_probability" not in url


def test_floor_hour_and_indexing():
    e0 = 1_760_000_400  # exact multiple of 3600 → a true hour boundary
    assert weather.floor_hour(e0) == e0
    assert weather.floor_hour(e0 + 3599) == e0
    loc = Location("America/Denver", -21600, {"time": [e0, e0 + 3600, e0 + 7200]})
    assert weather.hour_index(loc, e0 + 123) == 0
    assert weather.hour_index(loc, e0 + 3600) == 1
    assert weather.hour_index(loc, e0 + 10800) is None  # absent → nodata (post-check)

def test_window_indices_present_hours_only():
    e0 = 1_760_000_400
    times = [e0, e0 + 3600, e0 + 7200, e0 + 10800]
    assert weather.window_indices(times, e0 + 3600) == [0, 1, 2, 3]
    hole = [e0, e0 + 7200, e0 + 10800]  # e0+3600 hour missing from data
    assert weather.window_indices(hole, e0 + 7200) == [1, 2]  # h−3600 and h+7200 absent

def test_horizon_boundary():
    now = 1_760_000_000
    inside = now + config.HORIZON_MAX_S - config.HORIZON_MARGIN_S  # +2h == now+15d
    outside = inside + 1
    assert not weather.horizon_exceeded(inside, now=now)
    assert weather.horizon_exceeded(outside, now=now)
    assert weather.latest_plannable(now) == outside - 1


async def test_single_location_parses_dict_response(tmp_path):
    url = weather.build_url([POINTS[0]])
    body = make_loc([100, 200], temperature_2m=20.0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    ) as http:
        c = Cache(tmp_path / "w.db", http)
        await c.open()
        locs = await weather.fetch_all(c, [POINTS[0]])
        await c.close()
    assert len(locs) == 1 and locs[0].timezone == "America/Denver"
    assert locs[0].hourly["temperature_2m"][0] == 20.0


async def test_bulk_4xx_falls_back_to_chunks(tmp_path, monkeypatch):
    """Non-retryable 4xx bulk → sequential chunks; failing chunk → None rows (§4.6)."""
    import journey_weather.weather as wmod

    async def no_sleep(_):
        pass

    monkeypatch.setattr(wmod.asyncio, "sleep", no_sleep)
    points = [(37.0 + i * 0.01, -112.5) for i in range(5)]
    c1 = points[:2]
    c2 = points[2:4]
    c3 = points[4:]
    urls_seen: list[str] = []

    def handler(request):
        url = str(request.url)
        urls_seen.append(url)
        if url == weather.build_url(points):
            return httpx.Response(400, json={"reason": "too many locations"})
        if url == weather.build_url(c1):
            return httpx.Response(200, json=[make_loc([100]), make_loc([100])])
        if url == weather.build_url(c2):
            return httpx.Response(400)
        if url == weather.build_url(c3):  # single-point chunk 4xx → raise→None
            return httpx.Response(400)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "c.db", http)
        await c.open()
        locs = await weather.fetch_all(c, points, chunk_size=2)
        await c.close()
    assert [l is not None for l in locs] == [True, True, False, False, False]
    assert urls_seen[0] == weather.build_url(points)  # bulk attempted first
    assert weather.build_url(c1) in urls_seen and weather.build_url(c2) in urls_seen


async def test_retryable_bulk_failure_still_raises(tmp_path, monkeypatch):
    import journey_weather.cache as cache_mod

    async def no_sleep(_):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)

    def handler(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "r.db", http)
        await c.open()
        with pytest.raises(UpstreamError):
            await weather.fetch_all(c, POINTS, chunk_size=10)
        await c.close()
