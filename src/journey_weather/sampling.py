"""Waypoint interpolation + map pieces (PLAN.md §4.5). Pure; no upstreams."""

from dataclasses import dataclass


@dataclass
class Waypoint:
    """A sampled point along a leg, `t_s` of drive-time into it."""

    leg: int
    j: int  # index within the leg
    t_s: float  # drive-time offset from leg start
    epoch: int  # arrival instant = leg start + t_s
    lat: float
    lon: float
    piece_coords: list[tuple[float, float]]  # map slice for this waypoint


def waypoint_count(duration_s: float, interval_min: int) -> int:
    """§4.5: n_k = max(1, round(leg_duration_s / (interval * 60)))."""
    return max(1, round(duration_s / (interval_min * 60)))


def _cumulative(durations: list[float]) -> list[float]:
    cum = [0.0]
    for d in durations:
        cum.append(cum[-1] + d)
    return cum


def point_at(
    coords: list[tuple[float, float]], cum: list[float], durations: list[float], t: float
) -> tuple[float, float]:
    """Interpolated (lat, lon) at drive-time t: find the coordinate pair whose
    cumulative durations bracket t, lerp within it (§4.5)."""
    total = cum[-1]
    if t <= 0:
        return coords[0]
    if t >= total:
        return coords[-1]
    lo, hi = 0, len(durations) - 1
    while lo < hi:  # binary search for the bracketing segment
        mid = (lo + hi) // 2
        if cum[mid + 1] < t:
            lo = mid + 1
        else:
            hi = mid
    i = lo
    d = durations[i]
    frac = 0.0 if d <= 0 else (t - cum[i]) / d
    (a_lat, a_lon), (b_lat, b_lon) = coords[i], coords[i + 1]
    return (a_lat + (b_lat - a_lat) * frac, a_lon + (b_lon - a_lon) * frac)


def slice_coords(
    coords: list[tuple[float, float]], cum: list[float], durations: list[float],
    t0: float, t1: float,
) -> list[tuple[float, float]]:
    """Coordinate slice between drive-times t0 and t1 with interpolated
    endpoints, keeping interior vertices (§4.5 map pieces)."""
    start = point_at(coords, cum, durations, t0)
    end = point_at(coords, cum, durations, t1)
    pts = [start]
    eps = 1e-9
    for i in range(1, len(cum) - 1):
        if t0 + eps < cum[i] < t1 - eps:
            pts.append(coords[i])
    if end != start:
        pts.append(end)
    return pts


def sample_leg(
    leg: int,
    coords: list[tuple[float, float]],
    durations: list[float],
    leg_start_epoch: int,
    interval_min: int,
) -> list[Waypoint]:
    """All waypoints of one leg with their map pieces (§4.5)."""
    cum = _cumulative(durations)
    total = cum[-1]
    n = waypoint_count(total, interval_min)
    out: list[Waypoint] = []
    for j in range(n):
        t0 = j / n * total
        t1 = (j + 1) / n * total
        t = (j + 0.5) / n * total
        lat, lon = point_at(coords, cum, durations, t)
        out.append(
            Waypoint(
                leg=leg, j=j, t_s=t, epoch=leg_start_epoch + round(t),
                lat=lat, lon=lon,
                piece_coords=slice_coords(coords, cum, durations, t0, t1),
            )
        )
    return out


def build_rows(stop_count: int, leg_samples: list[list[Waypoint]]) -> list[tuple]:
    """Row order per §4.5: stop 0, waypoints of leg 0, stop 1, …, last stop.
    Rows: ("stop", k) | ("waypoint", Waypoint)."""
    rows: list[tuple] = [("stop", 0)]
    for k, samples in enumerate(leg_samples):
        rows.extend(("waypoint", w) for w in samples)
        rows.append(("stop", k + 1))
    assert len(rows) == stop_count + sum(len(s) for s in leg_samples)
    return rows
