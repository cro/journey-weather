"""OSRM routing: one multi-stop request + verified per-leg split (PLAN.md §2, §4.3)."""

import json
from dataclasses import dataclass

import httpx

from . import config
from .cache import Cache, UpstreamError

OSRM_BASE = httpx.URL("https://router.project-osrm.org/route/v1/driving")
OSRM_PARAMS = {
    "overview": "full",
    "geometries": "geojson",
    "annotations": "duration,distance",
}


@dataclass
class Leg:
    """One city-pair leg. coords are (lat, lon); durations/distances align
    with the segments between consecutive coords (§2 offset rule)."""

    coords: list[tuple[float, float]]
    durations: list[float]  # seconds, len == len(coords) - 1
    distances: list[float]  # metres

    @property
    def leg_duration_s(self) -> float:
        return sum(self.durations)

    @property
    def leg_distance_m(self) -> float:
        return sum(self.distances)


class NoRoute(Exception):
    """Raised when OSRM cannot route; .pair is the failing consecutive pair
    index if it could be identified."""

    def __init__(self, pair: int | None = None):
        super().__init__(f"no route (pair={pair})")
        self.pair = pair


def _coords_param(points: list[tuple[float, float]]) -> str:
    return ";".join(f"{lon},{lat}" for lat, lon in points)


async def _request(
    cache: Cache, points: list[tuple[float, float]],
    *, hits: list | None = None, refresh: bool = False,
) -> dict:
    url = str(
        OSRM_BASE.copy_with(
            path=f"{OSRM_BASE.path}/{_coords_param(points)}",
            params=OSRM_PARAMS,
        )
    )
    hit = await cache.cached_get(url, config.TTL_ROUTE, service="OSRM",
                                 refresh=refresh, hits=hits)
    return json.loads(hit.body)


def _split_legs(route: dict) -> list[Leg]:
    """Geometry/annotation split per the verified offset rule (PLAN.md §2):
    len(geometry) == Σ len(leg.annotation.duration) + 1, leg k owns indices
    [off_k, off_k + len_k]."""
    coords = route["geometry"]["coordinates"]  # GeoJSON [lon, lat]
    legs_raw = route["legs"]
    lens = [len(l["annotation"]["duration"]) for l in legs_raw]
    if sum(lens) + 1 != len(coords):
        raise UpstreamError("OSRM", f"geometry/annotation mismatch: {len(coords)} != {sum(lens) + 1}")
    legs: list[Leg] = []
    off = 0
    for leg, n in zip(legs_raw, lens, strict=True):
        seg = coords[off : off + n + 1]
        off += n
        legs.append(
            Leg(
                coords=[(c[1], c[0]) for c in seg],
                durations=[float(d) for d in leg["annotation"]["duration"]],
                distances=[float(d) for d in leg["annotation"]["distance"]],
            )
        )
    return legs


async def route_trip(
    cache: Cache, points: list[tuple[float, float]],
    *, hits: list | None = None, refresh: bool = False,
) -> list[Leg]:
    """Route all stops in order (≥2 points). Raises NoRoute(pair) with the
    failing consecutive pair identified via pairwise probes (PLAN.md §4.3)."""
    data = await _request(cache, points, hits=hits, refresh=refresh)
    code = data.get("code")
    if code != "Ok":
        if code == "NoRoute":
            pair = await find_failing_pair(cache, points, hits=hits, refresh=refresh)
            raise NoRoute(pair)
        raise UpstreamError("OSRM", f"code={code!r}")
    return _split_legs(data["routes"][0])


async def find_failing_pair(
    cache: Cache, points: list[tuple[float, float]],
    *, hits: list | None = None, refresh: bool = False,
) -> int | None:
    """Index i of the first consecutive pair points[i]->points[i+1] that cannot
    be routed; None if every pair routes (transient bulk failure)."""
    for i in range(len(points) - 1):
        data = await _request(cache, [points[i], points[i + 1]], hits=hits, refresh=refresh)
        if data.get("code") != "Ok":
            return i
    return None
