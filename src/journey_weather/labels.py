"""Waypoint labels: Nominatim reverse, 1 req/s, never fails a plan (PLAN.md §4.8)."""

import asyncio
import json
import time

from . import config
from .cache import Cache, UpstreamError

REVERSE_URL = (
    "https://nominatim.openstreetmap.org/reverse"
    "?lat={lat}&lon={lon}&format=jsonv2&zoom=10"
)
MIN_SPACING_S = 1.1  # policy: at most 1 request/second (PLAN.md §2)
_LABEL_FIELDS = ("city", "town", "village", "hamlet", "county")

_lock = asyncio.Lock()
_last_fetch = 0.0


def _fallback(lat: float, lon: float) -> str:
    return f"near {lat:.2f}, {lon:.2f}"


def _extract(body: bytes, lat: float, lon: float) -> str:
    data = json.loads(body)
    address = data.get("address") or {}
    for field in _LABEL_FIELDS:
        value = address.get(field)
        if value:
            return str(value)
    return _fallback(lat, lon)


async def reverse_label(
    cache: Cache, lat: float, lon: float, *, refresh: bool = False
) -> str:
    """Label for one waypoint. Cosmetic: any upstream failure degrades to
    "near {lat}, {lon}" instead of failing the plan (PLAN.md §8)."""
    url = REVERSE_URL.format(lat=f"{lat:.2f}", lon=f"{lon:.2f}")
    if not refresh:
        hit = await cache.peek(url, config.TTL_REVERSE)
        if hit is not None:
            return _extract(hit.body, lat, lon)

    global _last_fetch
    async with _lock:
        if not refresh:
            hit = await cache.peek(url, config.TTL_REVERSE)  # raced?
            if hit is not None:
                return _extract(hit.body, lat, lon)
        wait = _last_fetch + MIN_SPACING_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            hit = await cache.cached_get(
                url,
                config.TTL_REVERSE,
                service="Nominatim",
                headers={"User-Agent": config.USER_AGENT},
                refresh=refresh,
            )
        except UpstreamError:
            return _fallback(lat, lon)
        _last_fetch = time.monotonic()
    return _extract(hit.body, lat, lon)
