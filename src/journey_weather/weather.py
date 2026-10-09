"""Bulk Open-Meteo forecast fetch + hour indexing (PLAN.md §4.6)."""

import asyncio
import json
from dataclasses import dataclass
from time import time

import httpx

from . import config
from .cache import Cache, UpstreamError

FORECAST_BASE = httpx.URL("https://api.open-meteo.com/v1/forecast")
HOURLY_VARS = (
    "temperature_2m,apparent_temperature,precipitation,precipitation_probability,"
    "wind_speed_10m,wind_gusts_10m,weather_code,cape,visibility"
)


@dataclass
class Location:
    timezone: str
    utc_offset_seconds: int
    hourly: dict[str, list]  # "time" epoch seconds (unixtime), plus HOURLY_VARS


def floor_hour(epoch: int) -> int:
    return epoch - epoch % 3600


def horizon_exceeded(max_row_epoch: int, now: int | None = None) -> bool:
    """§4.6 pre-check: reject if last instant + 2 h > now + 15 days."""
    now = now if now is not None else int(time())
    return max_row_epoch + config.HORIZON_MARGIN_S > now + config.HORIZON_MAX_S


def latest_plannable(now: int | None = None) -> int:
    """Epoch rendered by the pipeline for the horizon error message (§4.6)."""
    now = now if now is not None else int(time())
    return now + config.HORIZON_MAX_S - config.HORIZON_MARGIN_S


def build_url(points: list[tuple[float, float]]) -> str:
    """One request; coords rounded to 2 decimals (§4.6)."""
    lats = ",".join(f"{lat:.2f}" for lat, _ in points)
    lons = ",".join(f"{lon:.2f}" for _, lon in points)
    return str(
        FORECAST_BASE.copy_with(
            params={
                "latitude": lats,
                "longitude": lons,
                "hourly": HOURLY_VARS,
                "timezone": "auto",
                "timeformat": "unixtime",
                "forecast_days": config.FORECAST_DAYS,
            }
        )
    )


def _parse(data: dict | list) -> list[Location]:
    if isinstance(data, dict):  # single location → object, not array
        data = [data]
    return [
        Location(
            timezone=d["timezone"],
            utc_offset_seconds=int(d["utc_offset_seconds"]),
            hourly=d["hourly"],
        )
        for d in data
    ]


async def fetch_all(
    cache: Cache,
    points: list[tuple[float, float]],
    *,
    chunk_size: int = 10,
    chunk_pause_s: float = 0.5,
) -> list[Location | None]:
    """Weather for every row coordinate. None marks `nodata` rows.

    Bulk 4xx (non-retryable) with >1 location → sequential chunks of
    `chunk_size`, 500 ms apart; a failing chunk yields None rows (§4.6).
    Retryable failure with nothing cached raises UpstreamError."""
    try:
        hit = await cache.cached_get(
            build_url(points), config.TTL_FORECAST, service="Open-Meteo"
        )
    except UpstreamError as exc:
        retryable = exc.detail.startswith("HTTP 429") or exc.detail.startswith("HTTP 5")
        if retryable or len(points) == 1:
            raise
        out: list[Location | None] = []
        for i in range(0, len(points), chunk_size):
            if i:
                await asyncio.sleep(chunk_pause_s)
            chunk = points[i : i + chunk_size]
            try:
                hit = await cache.cached_get(
                    build_url(chunk), config.TTL_FORECAST, service="Open-Meteo"
                )
            except UpstreamError:
                out.extend([None] * len(chunk))
                continue
            out.extend(_parse(json.loads(hit.body)))
        return out
    return _parse(json.loads(hit.body))


def hour_index(loc: Location, epoch: int) -> int | None:
    """Index of the row's arrival hour, or None → nodata (§4.6 post-check)."""
    times = loc.hourly["time"]
    return next((i for i, t in enumerate(times) if t == floor_hour(epoch)), None)


def window_indices(times: list[int], h: int) -> list[int]:
    """Severity window [h−1 h, h, +1 h, +2 h], hours present in data (§4.6)."""
    wanted = (h - 3600, h, h + 3600, h + 7200)
    pos = {t: i for i, t in enumerate(times)}
    return [pos[w] for w in wanted if w in pos]
