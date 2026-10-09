#!/usr/bin/env uv run python
"""Milestone 4 live smoke (PLAN.md §6.4): full §7 chain → itinerary table.

Run: uv run scripts/smoke_milestone4.py   (live upstreams; uses data/journey.db)
"""

import asyncio
from datetime import datetime, timezone

import httpx

from journey_weather import config, geocode, routing, schedule as sched_mod
from journey_weather import sampling, severity, units, weather
from journey_weather.cache import Cache

# §7 test input: Layton leave D 07:00; Kanab D+1 08:00; Flagstaff D+2 09:00;
# Phoenix blank; Tucson final (leave ignored).
TRIP = [
    ("Layton, UT", "2026-10-10T07:00"),
    ("Kanab, UT", "2026-10-11T08:00"),
    ("Flagstaff, AZ", "2026-10-12T09:00"),
    ("Phoenix, AZ", ""),
    ("Tucson, AZ", ""),
]
INTERVAL_MIN = 60

WMO_ICON = {
    0: "sun", 1: "cloud-sun", 2: "cloud-sun", 3: "cloud",
    45: "cloud-fog", 48: "cloud-fog",
    51: "cloud-drizzle", 53: "cloud-drizzle", 55: "cloud-drizzle",
    56: "cloud-drizzle", 57: "cloud-drizzle",
    61: "cloud-rain", 63: "cloud-rain", 65: "cloud-rain",
    66: "cloud-rain", 67: "cloud-rain",
    80: "cloud-rain", 81: "cloud-rain", 82: "cloud-rain",
    71: "cloud-snow", 73: "cloud-snow", 75: "cloud-snow",
    77: "cloud-snow", 85: "cloud-snow", 86: "cloud-snow",
    95: "cloud-lightning", 96: "cloud-lightning", 99: "cloud-lightning",
}


def fmt_local(epoch: int, offset_s: int) -> str:
    return datetime.fromtimestamp(epoch + offset_s, timezone.utc).strftime("%a %m-%d %H:%M")


async def main() -> None:
    async with httpx.AsyncClient(
        timeout=config.HTTP_TIMEOUT_S, headers={"User-Agent": config.USER_AGENT}
    ) as http:
        cache = Cache(config.DB_PATH, http)
        await cache.open()
        try:
            stops = await asyncio.gather(
                *(geocode.resolve_stop(cache, name) for name, _ in TRIP)
            )
            legs = await routing.route_trip(cache, [(s.lat, s.lon) for s in stops])
            plan = sched_mod.plan_schedule(
                [s.tz for s in stops], [lv for _, lv in TRIP],
                [leg.leg_duration_s for leg in legs],
            )
            for err in plan.errors:
                print(f"!! Stop {err.stop}: leave {err.leave_local} before arrival {err.arrival_local}")
            leg_samples = [
                sampling.sample_leg(k, leg.coords, leg.durations, sl.start_epoch, INTERVAL_MIN)
                for k, (leg, sl) in enumerate(zip(legs, plan.legs, strict=True))
            ]
            rows = sampling.build_rows(len(stops), leg_samples)

            row_points, row_epochs = [], []
            for kind, val in rows:
                if kind == "stop":
                    st = plan.stops[val]
                    row_points.append((stops[val].lat, stops[val].lon))
                    row_epochs.append(st.arrival_epoch if st.arrival_epoch is not None else st.departure_epoch)
                else:
                    row_points.append((val.lat, val.lon))
                    row_epochs.append(val.epoch)
            locs = await weather.fetch_all(cache, row_points)  # one bulk request (§4.6)

            print(f"{'#':>3} {'kind':<8} {'place':<22} {'local(arr)':<15} {'icon':<15} "
                  f"{'F':>3} {'in/h':>5} {'gust':>4} {'level':<8} flags")
            for i, ((kind, val), epoch, loc) in enumerate(zip(rows, row_epochs, locs, strict=True)):
                place = stops[val].display if kind == "stop" else f"wp leg{val.leg} #{val.j + 1}"
                if loc is None:
                    print(f"{i:>3} {'':<8} {place:<22} {'':<15} {'':<15} {'':>3} {'':>5} {'':>4} nodata   (chunk failed)")
                    continue
                h = weather.floor_hour(epoch)
                idx = weather.hour_index(loc, epoch)
                if idx is None:
                    print(f"{i:>3} {'':<8} {place:<22} {fmt_local(epoch, loc.utc_offset_seconds):<15} "
                          f"{'':<15} {'':>3} {'':>5} {'':>4} nodata   (hour past horizon)")
                    continue
                win = weather.window_indices(loc.hourly["time"], h)
                level, triggers = severity.evaluate(loc.hourly, win)
                temp = units.c_to_f(loc.hourly["temperature_2m"][idx])
                prec = units.mmh_to_inh(loc.hourly["precipitation"][idx])
                gust = units.kmh_to_mph(loc.hourly["wind_gusts_10m"][idx])
                icon = WMO_ICON.get(loc.hourly["weather_code"][idx], "?")
                flags = ", ".join(f"{t.rule}:{t.level}" for t in triggers) or "—"
                print(f"{i:>3} {kind:<8} {place:<22} {fmt_local(epoch, loc.utc_offset_seconds):<15} "
                      f"{icon:<15} {temp:>3} {prec:>5} {gust:>4} {level:<8} {flags}")
            print(f"\nrows: {len(rows)} (5 stops + {len(rows) - 5} waypoints); "
                  f"bulk weather locations: {len(row_points)}")
        finally:
            await cache.close()


if __name__ == "__main__":
    asyncio.run(main())
