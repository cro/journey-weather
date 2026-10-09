"""Pipeline tests for stop dwell time (§10 feature 1): parsing, validation,
and the end-to-end §7 trip proving dwell shifts every later arrival."""
import json

import httpx
import pytest
from journey_weather import geocode, pipeline, routing
from journey_weather.cache import Cache
from journey_weather.schedule import local_to_epoch
from conftest import FIXTURES

CITIES = {
    "Layton, UT": "geocoding-layton-ut.json",
    "Kanab, UT": "geocoding-kanab-ut.json",
    "Flagstaff, AZ": "geocoding-flagstaff-az.json",
    "Phoenix, AZ": "geocoding-phoenix-az.json",
    "Tucson, AZ": "geocoding-tucson-az.json",
}
# Coordinates as the RECORDED geocoding fixtures resolve them (the pipeline
# builds the OSRM URL from parsed stops, not from any hand-typed list).
POINTS = []
for _fx in CITIES.values():
    _r = json.loads((FIXTURES / _fx).read_bytes())["results"][0]
    POINTS.append((_r["latitude"], _r["longitude"]))


def geo_url(name: str) -> str:
    return str(geocode.GEOCODE_BASE.copy_with(params={"name": name, "count": 5}))


def osrm_url() -> str:
    return str(
        routing.OSRM_BASE.copy_with(
            path=f"{routing.OSRM_BASE.path}/{routing._coords_param(POINTS)}",
            params=routing.OSRM_PARAMS,
        )
    )


def _forecast_element() -> dict:
    """Valid Location payload whose hours are far in the past: no row matches an
    hour → every row is nodata, so the test asserts schedule behavior only."""
    base = 100 * 3600  # 1970-01-04; never a row epoch
    return {
        "timezone": "America/Denver",
        "utc_offset_seconds": -21600,
        "hourly": {"time": [base + 3600 * i for i in range(24)]},
    }


@pytest.fixture
async def trip_cache(tmp_path):
    """Cache answering the recorded §7 geocoding + routing fixtures and any
    bulk forecast with one past-hours element per requested location."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for city, fx in CITIES.items():
            if url == geo_url(city):
                return httpx.Response(200, content=(FIXTURES / fx).read_bytes())
        if url == osrm_url():
            return httpx.Response(
                200, content=(FIXTURES / "osrm-five-stops.json").read_bytes()
            )
        if request.url.host == "api.open-meteo.com":
            n = len(request.url.params.get("latitude", "").split(","))
            return httpx.Response(200, json=[_forecast_element() for _ in range(n)])
        return httpx.Response(404, json={"error": "unmapped", "url": url})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
    c = Cache(tmp_path / "p.db", http)
    await c.open()
    yield c
    await c.close()


def signals(dwells: dict[int, str]) -> dict:
    trip = {
        f"s{i}": {"name": city, "leave": "", "dwell": dwells.get(i, "")}
        for i, city in enumerate(CITIES)
    }
    trip["s0"]["leave"] = "2026-10-10T07:00"  # drive-through: only the origin leaves
    return {"trip": trip, "interval": 60}


def test_parse_signals_keeps_dwell_raw_and_positional():
    rows, interval = pipeline.parse_signals(
        {
            "trip": {
                "s1": {"name": " B ", "leave": "L", "dwell": " 35 "},
                "s0": {"name": "A", "leave": "X"},
                "s2": {"name": "", "leave": "", "dwell": "9"},
            },
            "interval": 30,
        }
    )
    assert rows == [("A", "X", ""), ("B", "L", "35")]  # ordered, blank skipped, str()
    assert interval == 30


def test_parse_signals_coerces_numeric_dwell_signal():
    rows, _ = pipeline.parse_signals(
        {"trip": {"s0": {"name": "A", "leave": "X", "dwell": 35},
                  "s1": {"name": "B", "dwell": None}}}
    )
    assert rows == [("A", "X", "35"), ("B", "", "")]


async def test_invalid_dwell_rejected_with_exact_message(trip_cache):
    with pytest.raises(pipeline.TripInputError) as ei:
        await pipeline.plan_trip(trip_cache, signals({1: "thirty five"}))
    assert 'Stop 2: dwell must be a whole number of minutes from 0 to 1440.' in ei.value.messages


@pytest.mark.parametrize("bad", ["-5", "1441", "3.5", "35 min"])
async def test_dwell_bounds(trip_cache, bad):
    with pytest.raises(pipeline.TripInputError) as ei:
        await pipeline.plan_trip(trip_cache, signals({1: bad}))
    assert any("dwell must be" in m for m in ei.value.messages), ei.value.messages


async def test_dwell_1440_accepted(trip_cache, fixed_now):
    result = await pipeline.plan_trip(trip_cache, signals({1: "1440"}))
    assert any(r.kind == "stop" and r.dwell_min == 1440 for r in result.rows)


@pytest.fixture
def fixed_now(monkeypatch):
    """Freeze wall-clock: now = one day before the §7 departure (horizon check)."""
    depart = local_to_epoch("2026-10-10T07:00", "America/Denver")
    monkeypatch.setattr(pipeline, "time", lambda: depart - 86400)
    return depart


async def test_garden_city_dwell_shifts_every_later_arrival(trip_cache, fixed_now):
    """§10.1 repro shape: a 35-min mid-route dwell is factor into times AND weather."""
    base = await pipeline.plan_trip(trip_cache, signals({}))
    dwell = await pipeline.plan_trip(trip_cache, signals({1: "35"}))

    def arrival(result, city_index):
        stop_rows = [r for r in result.rows if r.kind == "stop"]
        return stop_rows[city_index].epoch

    # stop 1 (Kanab) arrival unchanged; its departure is 35 min later
    assert arrival(dwell, 1) == arrival(base, 1)
    kanab = [r for r in dwell.rows if r.kind == "stop"][1]
    assert kanab.dwell_min == 35
    assert kanab.local_leave is not None
    # every later row is exactly 35 min later: Flagstaff, its waypoints, Tucson
    assert arrival(dwell, 2) == arrival(base, 2) + 35 * 60
    assert arrival(dwell, 4) == arrival(base, 4) + 35 * 60
    leg1 = [r for r in dwell.rows if r.kind == "waypoint" and r.epoch > arrival(dwell, 1)]
    leg1_base = [r for r in base.rows if r.kind == "waypoint" and r.epoch > arrival(base, 1)]
    assert [w.epoch for w in leg1] == [w.epoch + 35 * 60 for w in leg1_base]

    # origin and final stops ignore dwell
    plain = await pipeline.plan_trip(trip_cache, signals({0: "30", 4: "30"}))
    assert [r.epoch for r in plain.rows] == [r.epoch for r in base.rows]
    assert all(r.dwell_min is None for r in plain.rows if r.kind == "stop")


async def test_dwell_renders_leave_and_stop_minutes_in_itinerary(trip_cache, fixed_now):
    from journey_weather import app as app_module

    result = await pipeline.plan_trip(trip_cache, signals({1: "35"}))
    html = app_module._render("_result.html", result=result)
    kanab = [r for r in result.rows if r.kind == "stop"][1]
    assert kanab.local_leave in html
    assert "35 min stop" in html

