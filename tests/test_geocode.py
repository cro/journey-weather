"""Geocoding tests: recorded 5-city fixtures + handcrafted fallback/override cases."""

import httpx
import pytest

from journey_weather import geocode
from journey_weather.cache import Cache
from conftest import fixture, mock_client

CITY_FIXTURES = {
    "Layton, UT": ("geocoding-layton-ut.json", "Layton, Utah, US", 41.06, -111.97, "America/Denver"),
    "Kanab, UT": ("geocoding-kanab-ut.json", "Kanab, Utah, US", 37.05, -112.53, "America/Denver"),
    "Flagstaff, AZ": ("geocoding-flagstaff-az.json", "Flagstaff, Arizona, US", 35.20, -111.65, "America/Phoenix"),
    "Phoenix, AZ": ("geocoding-phoenix-az.json", "Phoenix, Arizona, US", 33.45, -112.07, "America/Phoenix"),
    "Tucson, AZ": ("geocoding-tucson-az.json", "Tucson, Arizona, US", 32.22, -110.93, "America/Phoenix"),
}


def geo_url(name: str, count: int) -> str:
    return str(geocode.GEOCODE_BASE.copy_with(params={"name": name, "count": count}))


@pytest.fixture
async def gcache(tmp_path):
    routes = {geo_url(city, 5): fixture(fx) for city, (fx, *_) in CITY_FIXTURES.items()}
    async with mock_client(routes) as http:
        c = Cache(tmp_path / "g.db", http)
        await c.open()
        yield c
        await c.close()


async def test_five_example_cities_resolve_as_planned(gcache):
    """PLAN.md §2: all five example cities resolve correctly as results[0]."""
    for city, (_fx, display, lat, lon, tz) in CITY_FIXTURES.items():
        stop = await geocode.resolve_stop(gcache, city)
        assert stop.display == display, city
        assert stop.lat == pytest.approx(lat, abs=0.01)
        assert stop.lon == pytest.approx(lon, abs=0.01)
        assert stop.tz == tz


async def test_state_suffix_fallback_picks_matching_state(tmp_path):
    """§4.2 step 2: full-name miss → query city, keep first with matching admin1."""
    routes = {
        geo_url("Springfield, UT", 5): {"results": []},
        geo_url("Springfield", 10): {
            "results": [
                {"name": "Springfield", "admin1": "Illinois", "country_code": "US",
                 "latitude": 39.8, "longitude": -75.4, "timezone": "America/New_York"},
                {"name": "Springfield", "admin1": "Utah", "country_code": "US",
                 "latitude": 39.4, "longitude": -111.6, "timezone": "America/Denver"},
            ]
        },
    }
    async with mock_client(routes) as http:
        c = Cache(tmp_path / "s.db", http)
        await c.open()
        stop = await geocode.resolve_stop(c, "Springfield, UT")
        await c.close()
    assert stop.display == "Springfield, Utah, US"
    assert stop.tz == "America/Denver"


async def test_not_found_raises(tmp_path):
    calls: list[httpx.Request] = []
    routes = {
        geo_url("Nowhereville, UT", 5): {},
        geo_url("Nowhereville", 10): {"results": []},
    }
    async with mock_client(routes, calls) as http:
        c = Cache(tmp_path / "n.db", http)
        await c.open()
        with pytest.raises(geocode.StopNotFound):
            await geocode.resolve_stop(c, "Nowhereville, UT")
        await c.close()
    assert [str(r.url) for r in calls] == [geo_url("Nowhereville, UT", 5), geo_url("Nowhereville", 10)]


async def test_country_suffix_match_without_state(tmp_path):
    """Suffix not in US_STATES falls back to country_code comparison (§4.2)."""
    routes = {
        geo_url("Springfield, GB", 5): {"results": []},
        geo_url("Springfield", 10): {
            "results": [
                {"name": "Springfield", "admin1": "Illinois", "country_code": "US",
                 "latitude": 1, "longitude": 2, "timezone": "America/Chicago"},
                {"name": "Springfield", "admin1": "Scotland", "country_code": "GB",
                 "latitude": 3, "longitude": 4, "timezone": "Europe/London"},
            ]
        },
    }
    async with mock_client(routes) as http:
        c = Cache(tmp_path / "on.db", http)
        await c.open()
        stop = await geocode.resolve_stop(c, "Springfield, GB")
        await c.close()
    assert stop.display == "Springfield, Scotland, GB"


def test_parse_override():
    stop = geocode.parse_override("My Cabin;36.9;-111.5")
    assert stop is not None
    assert stop.display == "My Cabin"
    assert (stop.lat, stop.lon) == (36.9, -111.5)
    assert stop.tz == ""  # filled by lookup_tz


def test_parse_override_rejects_bad_ranges():
    assert geocode.parse_override("plain city") is None
    with pytest.raises(geocode.CoordsOutOfRange):
        geocode.parse_override("X;95;0")
    with pytest.raises(geocode.CoordsOutOfRange):
        geocode.parse_override("X;0;-181")


async def test_override_tz_lookup(tmp_path):
    url = geocode.TZ_URL.format(lat=f"{36.9:.6f}", lon=f"{-111.5:.6f}")
    calls: list[httpx.Request] = []
    async with mock_client({url: {"timezone": "America/Phoenix"}}, calls) as http:
        c = Cache(tmp_path / "o.db", http)
        await c.open()
        stop = await geocode.resolve_stop(c, "Lookout;36.9;-111.5")
        await c.close()
    assert stop.display == "Lookout" and stop.tz == "America/Phoenix"
    assert len(calls) == 1
