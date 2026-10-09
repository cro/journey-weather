"""Sampling tests (PLAN.md §4.5, §6.3): waypoint counts, interpolation, pieces, rows."""

import math
import pytest

from journey_weather.routing import Leg
from journey_weather.sampling import (
    Waypoint,
    _cumulative,
    build_rows,
    point_at,
    sample_leg,
    slice_coords,
    waypoint_count,
)


def straight_leg(n_segments: int, seconds: float) -> Leg:
    """Coords due south→north along lon 0: lat 40.0 + i*0.01."""
    coords = [(40.0 + i * 0.01, 0.0) for i in range(n_segments + 1)]
    return Leg(coords=coords, durations=[seconds] * n_segments,
               distances=[100.0] * n_segments)


def test_waypoint_counts_for_seven_example_legs():
    """§7 durations at 60-min default: 6 + 4 + 3 + 2 = 15 waypoints."""
    durs = [6.06 * 3600, 4.24 * 3600, 2.59 * 3600, 2.15 * 3600]
    counts = [waypoint_count(d, 60) for d in durs]
    assert counts == [6, 4, 3, 2]
    assert sum(counts) == 15
    # slider bounds: 1 h leg counts per interval
    assert [waypoint_count(3600, i) for i in (30, 60, 90, 120)] == [2, 1, 1, 1]
    # never zero: a tiny leg still gets one midpoint
    assert waypoint_count(60, 60) == 1


def test_point_at_interpolates_within_bracketing_pair():
    leg = straight_leg(2, 100.0)  # cum: 0, 100, 200
    cum = _cumulative(leg.durations)
    assert point_at(leg.coords, cum, leg.durations, 0) == (40.0, 0.0)  # exact endpoint
    assert point_at(leg.coords, cum, leg.durations, 200) == (40.02, 0.0)  # exact endpoint
    p = point_at(leg.coords, cum, leg.durations, 50)
    assert p[0] == pytest.approx(40.005, abs=1e-9) and p[1] == 0.0
    p = point_at(leg.coords, cum, leg.durations, 150)
    assert p[0] == pytest.approx(40.015, abs=1e-9) and p[1] == 0.0


def test_slice_coords_covers_span_with_interpolated_ends():
    leg = straight_leg(4, 100.0)  # cum 0..400
    cum = _cumulative(leg.durations)
    pts = slice_coords(leg.coords, cum, leg.durations, 50, 350)
    assert pts[0][0] == pytest.approx(40.005, abs=1e-9)
    assert pts[-1][0] == pytest.approx(40.035, abs=1e-9)
    # interior vertices strictly inside the span are kept, verbatim
    assert (40.01, 0.0) in pts and (40.02, 0.0) in pts and (40.03, 0.0) in pts
    assert (40.0, 0.0) not in pts and (40.04, 0.0) not in pts


def test_sample_leg_moments_and_continuity():
    leg = straight_leg(10, 60.0)  # 600 s
    wps = sample_leg(0, leg.coords, leg.durations, leg_start_epoch=10_000, interval_min=30)
    assert len(wps) == waypoint_count(600, 30) == 1
    w = wps[0]
    assert w.epoch == 10_000 + round(300)  # t = 0.5/1*600
    assert math.isclose(w.lat, 40.0 + 5 * 0.01, abs_tol=1e-9)  # 1 waypoint at mid-drive, 11 coords → cum[5] vertex
    # pieces tile the leg: first starts at leg start, last ends at leg end, no gaps
    wps = sample_leg(0, leg.coords, leg.durations, 10_000, 60)
    n = waypoint_count(600, 60)
    assert n == 1 and len(wps) == n
    # two-leg sanity with finer interval
    wps = sample_leg(0, leg.coords, leg.durations, 10_000, 30)
    assert wps[0].piece_coords[0] == tuple(leg.coords[0])
    assert wps[-1].piece_coords[-1] == tuple(leg.coords[-1])
    for a, b in zip(wps, wps[1:]):
        assert a.piece_coords[-1] == b.piece_coords[0]


def test_build_rows_alternate_stops_and_waypoints():
    leg0 = [Waypoint(0, j, j, 0, 0, 0, []) for j in range(2)]
    leg1 = [Waypoint(1, 0, 0, 0, 0, 0, [])]
    rows = build_rows(3, [leg0, leg1])
    kinds = [(k, v if isinstance(v, int) else v.leg) for k, v in rows]
    assert kinds == [
        ("stop", 0), ("waypoint", 0), ("waypoint", 0),
        ("stop", 1), ("waypoint", 1), ("stop", 2),
    ]


def test_leg_split_offsets_6620_case():
    """§6.3/§2: geometry 6620 == 4266 + 2353 + 1 annotation split."""
    from journey_weather.routing import _split_legs

    a, b = 4266, 2353
    coords = [[0.0, 0.0]] * (a + b + 1)
    route = {
        "geometry": {"coordinates": coords},
        "legs": [
            {"annotation": {"duration": [1.0] * a, "distance": [1.0] * a}},
            {"annotation": {"duration": [1.0] * b, "distance": [1.0] * b}},
        ],
    }
    legs = _split_legs(route)
    assert [len(l.coords) for l in legs] == [a + 1, b + 1]
    assert legs[0].coords[-1] == legs[1].coords[0]
    assert sum(len(l.coords) - 1 for l in legs) + 1 == len(coords)
