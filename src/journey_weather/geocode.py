"""Geocoding: Open-Meteo forward search, state-suffix fallback, coord overrides (PLAN.md §4.2)."""

import json
import re
from dataclasses import dataclass

import httpx

from . import config
from .cache import Cache

GEOCODE_BASE = httpx.URL("https://geocoding-api.open-meteo.com/v1/search")
TZ_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&timezone=auto&forecast_days=1"
)

OVERRIDE_RE = re.compile(r"^(.+);\s*(-?\d+(?:\.\d+)?)\s*;\s*(-?\d+(?:\.\d+)?)$")

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


@dataclass
class Stop:
    display: str  # "Kanab, Utah, US" (or the given name for coord overrides)
    lat: float
    lon: float
    tz: str  # IANA, e.g. "America/Denver"
    raw: str  # what the user typed, for error messages


class StopNotFound(Exception):
    def __init__(self, raw: str):
        super().__init__(raw)
        self.raw = raw


class CoordsOutOfRange(Exception):
    def __init__(self, raw: str):
        super().__init__(raw)
        self.raw = raw


def parse_override(raw: str) -> Stop | None:
    """`Name;lat;lon` skips geocoding (PLAN.md §1.4); tz filled by lookup_tz."""
    m = OVERRIDE_RE.match(raw.strip())
    if not m:
        return None
    name, lat_s, lon_s = m.groups()
    lat, lon = float(lat_s), float(lon_s)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise CoordsOutOfRange(raw)
    return Stop(display=name.strip(), lat=lat, lon=lon, tz="", raw=raw)


async def lookup_tz(cache: Cache, lat: float, lon: float) -> str:
    """IANA zone for override coords via a 1-day forecast (PLAN.md §4.2 step 5)."""
    url = TZ_URL.format(lat=f"{lat:.6f}", lon=f"{lon:.6f}")
    hit = await cache.cached_get(url, config.TTL_GEOCODE, service="Open-Meteo")
    return json.loads(hit.body)["timezone"]


async def _results(cache: Cache, name: str, count: int) -> list[dict]:
    url = str(GEOCODE_BASE.copy_with(params={"name": name, "count": count}))
    hit = await cache.cached_get(url, config.TTL_GEOCODE, service="Geocoding")
    return json.loads(hit.body).get("results") or []


def _to_stop(raw: str, r: dict) -> Stop:
    admin1 = r.get("admin1") or ""
    cc = r.get("country_code") or ""
    return Stop(
        display=f"{r['name']}, {admin1}, {cc}",
        lat=float(r["latitude"]),
        lon=float(r["longitude"]),
        tz=r.get("timezone") or "",
        raw=raw,
    )


async def resolve_stop(cache: Cache, raw: str) -> Stop:
    """Resolve one stop row (PLAN.md §4.2). Callers run these via asyncio.gather."""
    override = parse_override(raw)
    if override is not None:
        override.tz = await lookup_tz(cache, override.lat, override.lon)
        return override

    query = raw.strip()
    results = await _results(cache, query, count=5)
    if not results and ", " in query:
        # Step 2: split at the last ", " into city, suffix; retry by city.
        city, suffix = query.rsplit(", ", 1)
        suffix = suffix.strip().upper()
        want_admin = US_STATES.get(suffix)
        for r in await _results(cache, city, count=10):
            if (want_admin and r.get("admin1") == want_admin) or (
                (r.get("country_code") or "").upper() == suffix
            ):
                return _to_stop(raw, r)
    if not results:
        raise StopNotFound(query)
    return _to_stop(raw, results[0])
