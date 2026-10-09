#!/usr/bin/env uv run python
"""Milestone 2 live smoke (PLAN.md §6.2): resolve the §7 trip and print legs.

Run: uv run scripts/smoke_milestone2.py   (hits live upstreams; uses data/journey.db)
"""

import asyncio

import httpx

from journey_weather import config, geocode, routing
from journey_weather.cache import Cache

TRIP = ["Layton, UT", "Kanab, UT", "Flagstaff, AZ", "Phoenix, AZ", "Tucson, AZ"]


async def main() -> None:
    async with httpx.AsyncClient(
        timeout=config.HTTP_TIMEOUT_S, headers={"User-Agent": config.USER_AGENT}
    ) as http:
        cache = Cache(config.DB_PATH, http)
        await cache.open()
        try:
            stops = await asyncio.gather(*(geocode.resolve_stop(cache, n) for n in TRIP))
            for s in stops:
                print(f"{s.display:<28} lat={s.lat:8.4f} lon={s.lon:9.4f} tz={s.tz}")
            legs = await routing.route_trip(cache, [(s.lat, s.lon) for s in stops])
            durations = [f"{leg.leg_duration_s / 3600:.2f}" for leg in legs]
            print("legs:", " / ".join(durations), "h")
            for i, leg in enumerate(legs):
                print(
                    f"  {TRIP[i]} -> {TRIP[i + 1]}: {leg.leg_duration_s / 3600:.2f} h, "
                    f"{leg.leg_distance_m / 1000:.0f} km, {len(leg.coords)} coords"
                )
        finally:
            await cache.close()


if __name__ == "__main__":
    asyncio.run(main())
