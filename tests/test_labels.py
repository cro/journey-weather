"""Label tests: recorded Nominatim fixture, precedence chain, throttling, never-fail."""

import httpx
import pytest

from journey_weather import config, labels
from journey_weather.cache import Cache
from conftest import fixture_json, mock_client


def url_for(lat: float, lon: float) -> str:
    return labels.REVERSE_URL.format(lat=f"{lat:.2f}", lon=f"{lon:.2f}")


@pytest.fixture(autouse=True)
def _reset_spacing():
    labels._last_fetch = 0.0
    yield
    labels._last_fetch = 0.0


async def test_recorded_fixture_labels_by_county(tmp_path):
    """Fixture has no city/town/village/hamlet → county is the last chain entry."""
    body = fixture_json("nominatim-midpoint.json")
    assert body["address"]["county"] == "Coconino County"
    calls: list[httpx.Request] = []
    async with mock_client({url_for(36.12, -112.09): body}, calls) as http:
        c = Cache(tmp_path / "l.db", http)
        await c.open()
        label = await labels.reverse_label(c, 36.1194, -112.0913)  # rounds to .2f
        assert label == "Coconino County"
        # second call is served from cache: still exactly one upstream request
        assert await labels.reverse_label(c, 36.1194, -112.0913) == "Coconino County"
        await c.close()
    assert len(calls) == 1
    assert calls[0].headers["user-agent"] == config.USER_AGENT  # policy requirement


async def test_label_field_precedence(tmp_path):
    routes = {
        url_for(40.00, -105.00): {"address": {"city": "Boulder", "county": "Boulder County"}},
        url_for(40.10, -105.10): {"address": {"town": "Louisville", "county": "X"}},
        url_for(40.20, -105.20): {"address": {"hamlet": "TBD", "county": "Y"}},
        url_for(40.30, -105.30): {"address": {}},
    }
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    old_sleep, labels.asyncio.sleep = labels.asyncio.sleep, fake_sleep
    try:
        async with mock_client(routes) as http:
            c = Cache(tmp_path / "p.db", http)
            await c.open()
            assert await labels.reverse_label(c, 40.0, -105.0) == "Boulder"
            assert await labels.reverse_label(c, 40.1, -105.1) == "Louisville"
            assert await labels.reverse_label(c, 40.2, -105.2) == "TBD"
            assert await labels.reverse_label(c, 40.3, -105.3) == "near 40.30, -105.30"
            await c.close()
    finally:
        labels.asyncio.sleep = old_sleep
    # three gaps were enforced (calls 2..4 each waited)
    assert len(sleeps) == 3 and all(0 < s <= labels.MIN_SPACING_S for s in sleeps)


async def test_upstream_failure_degrades_to_fallback(tmp_path, monkeypatch):
    """§8: labels are cosmetic — never fail the plan."""
    import journey_weather.cache as cache_mod

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cache_mod.random, "uniform", lambda *_: 0.0)

    def handler(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        c = Cache(tmp_path / "f.db", http)
        await c.open()
        label = await labels.reverse_label(c, 35.5, -112.5)
        await c.close()
    assert label == "near 35.50, -112.50"
