"""Routing tests: recorded 5-stop OSRM fixture + synthetic NoRoute/mismatch cases."""

import copy

import pytest

from journey_weather import routing
from journey_weather.cache import Cache, UpstreamError
from conftest import fixture_json, mock_client

# Layton → Kanab → Flagstaff → Phoenix → Tucson (coords from recorded geocoding fixtures)
POINTS = [
    (41.06022, -111.97105),
    (37.04742, -112.52623),
    (35.19819, -111.65131),
    (33.44838, -112.07404),
    (32.22174, -110.97069),
]
# PLAN.md §7 verified legs (hours, km)
EXPECTED_H = [6.06, 4.24, 2.59, 2.15]
EXPECTED_KM = [537, 332, 231, 187]


def bulk_url(points=POINTS) -> str:
    return str(
        routing.OSRM_BASE.copy_with(
            path=f"{routing.OSRM_BASE.path}/{routing._coords_param(points)}",
            params=routing.OSRM_PARAMS,
        )
    )


@pytest.fixture
async def rcache(tmp_path):
    body = fixture_json("osrm-five-stops.json")
    async with mock_client({bulk_url(): body}) as http:
        c = Cache(tmp_path / "r.db", http)
        await c.open()
        yield c
        await c.close()


async def test_route_trip_splits_into_verified_legs(rcache):
    legs = await routing.route_trip(rcache, POINTS)
    assert len(legs) == 4
    for leg, exp_h, exp_km in zip(legs, EXPECTED_H, EXPECTED_KM, strict=True):
        assert leg.leg_duration_s / 3600 == pytest.approx(exp_h, abs=0.05)
        assert leg.leg_distance_m / 1000 == pytest.approx(exp_km, abs=5)
        # local invariant: coords and segment annotations align (§2 offset rule)
        assert len(leg.coords) == len(leg.durations) + 1 == len(leg.distances) + 1
        assert all(-90 <= lat <= 90 and -180 <= lon <= 180 for lat, lon in leg.coords)


async def test_legs_stitch_and_cover_geometry(rcache):
    legs = await routing.route_trip(rcache, POINTS)
    # consecutive legs share their endpoint (leg k owns [off_k, off_k+len_k])
    for a, b in zip(legs, legs[1:], strict=False):
        assert a.coords[-1] == b.coords[0]
    # endpoints land on the stops (within OSRM snap tolerance)
    starts = [legs[0].coords[0]] + [leg.coords[-1] for leg in legs]
    for (lat, lon), (slat, slon) in zip(POINTS, starts, strict=True):
        assert abs(lat - slat) < 0.05 and abs(lon - slon) < 0.05
    body = fixture_json("osrm-five-stops.json")
    total = sum(len(l.coords) - 1 for l in legs)
    assert total + 1 == len(body["routes"][0]["geometry"]["coordinates"])


async def test_geometry_annotation_mismatch_raises(tmp_path):
    body = copy.deepcopy(fixture_json("osrm-five-stops.json"))
    coords = body["routes"][0]["geometry"]["coordinates"]
    body["routes"][0]["geometry"]["coordinates"] = coords[:-5]  # break Σ+1 rule
    async with mock_client({bulk_url(): body}) as http:
        c = Cache(tmp_path / "m.db", http)
        await c.open()
        with pytest.raises(UpstreamError) as ei:
            await routing.route_trip(c, POINTS)
        await c.close()
    assert ei.value.service == "OSRM"


async def test_no_route_reports_failing_pair(tmp_path):
    """§4.3: bulk NoRoute → pairwise probes identify the bad consecutive pair."""
    ok = {"code": "Ok"}
    routes = {bulk_url(): {"code": "NoRoute"}}
    pair_url = routing.OSRM_BASE.copy_with(params=routing.OSRM_PARAMS)
    for i in range(4):
        pts = [POINTS[i], POINTS[i + 1]]
        url = str(pair_url.copy_with(path=f"{pair_url.path}/{routing._coords_param(pts)}"))
        routes[url] = {"code": "NoRoute"} if i == 2 else ok
    async with mock_client(routes) as http:
        c = Cache(tmp_path / "nr.db", http)
        await c.open()
        with pytest.raises(routing.NoRoute) as ei:
            await routing.route_trip(c, POINTS)
        await c.close()
    assert ei.value.pair == 2


async def test_unknown_error_code_raises_upstream(tmp_path):
    async with mock_client({bulk_url(): {"code": "InvalidInput"}}) as http:
        c = Cache(tmp_path / "e.db", http)
        await c.open()
        with pytest.raises(UpstreamError) as ei:
            await routing.route_trip(c, POINTS)
        await c.close()
    assert "InvalidInput" in ei.value.detail
