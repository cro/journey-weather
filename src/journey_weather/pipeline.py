"""Trip orchestration (PLAN.md §3): signals → TripResult, or structured errors."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import time
from zoneinfo import ZoneInfo

import httpx

from . import config, geocode, routing, sampling, schedule as sched_mod, severity, weather
from .cache import Cache, CacheHit, UpstreamError
from .units import c_to_f, kmh_to_mph, m_to_mi, mmh_to_inh

DISPLAY_FMT = "%a %m-%d %H:%M"


class TripInputError(Exception):
    """User-facing validation failures (§4.1); messages render in #form-errors."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


@dataclass
class Row:
    n: int  # row index (map ids, table rows, detail panels share it)
    kind: str  # "stop" | "waypoint"
    place: str  # stop display; waypoint label (§4.8 fallback until streamed)
    label_id: str | None  # "label-{n}" for waypoint rows, else None
    epoch: int
    local_time: str  # arrival (origin: departure), local to the row's tz
    local_leave: str | None  # stop rows that have a set leave-at
    temp_f: int | None
    feels_f: int | None
    precip_in: float | None
    precip_prob: int | None
    wind_mph: int | None
    gust_mph: int | None
    icon: str | None
    level: str  # clear | warning | severe | nodata
    triggers: list[severity.Trigger] = field(default_factory=list)
    window: list[dict] = field(default_factory=list)  # detail-panel hour rows (§5)
    sentences: list[str] = field(default_factory=list)  # triggered-rule sentences
    leg_mi: int | None = None  # distance of the leg arriving at this row
    drive_str: str | None = None  # drive time into this row
    dwell_min: int | None = None  # stop rows: minutes stopped here before continuing


@dataclass
class TripResult:
    rows: list[Row]
    map_json: str  # {"pieces": [...], "stops": [...]} for <route-map> (§5)
    legend_counts: dict[str, int]
    generated_at: int
    oldest_age_s: int
    stale: bool
    nodata_count: int
    pending_labels: list[tuple[int, float, float]] = field(default_factory=list)


def parse_signals(signals: dict) -> tuple[list[tuple[str, str, str]], int]:
    """trip.{sN}.{name,leave,dwell} rows ordered by N, blank names skipped (§4.1).

    `dwell` is the raw string from the form; "" = unset. Validated in plan_trip."""
    trip = signals.get("trip") or {}
    numbered: list[tuple[int, dict]] = []
    for key, val in trip.items():
        if key.startswith("s") and key[1:].isdigit() and isinstance(val, dict):
            numbered.append((int(key[1:]), val))
    numbered.sort(key=lambda kv: kv[0])
    rows = [
        (
            (v.get("name") or "").strip(),
            (v.get("leave") or "").strip(),
            str(v.get("dwell") or "").strip(),
        )
        for _, v in numbered
    ]
    rows = [(n, lv, dw) for n, lv, dw in rows if n]
    try:
        interval = int(float(signals.get("interval") or 60))
    except (TypeError, ValueError):
        interval = 60
    if interval not in (30, 60, 90, 120):
        interval = 60
    return rows, interval


def _fmt(epoch: int, tzname: str | None) -> str:
    if tzname:
        return datetime.fromtimestamp(epoch, ZoneInfo(tzname)).strftime(DISPLAY_FMT)
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(DISPLAY_FMT) + " Z"


def _hhmm(epoch: int, tzname: str) -> str:
    return datetime.fromtimestamp(epoch, ZoneInfo(tzname)).strftime("%H:%M %Z")


def _drive_str(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    return f"{h} h {rem // 60:02d} min" if h else f"{rem // 60} min"


def _sentence(rule: str, value: float | None, when: str, place: str) -> str:
    if rule == "rain" and value is not None:
        phrase = f"Rain {mmh_to_inh(value):.2f} in/h"
    elif rule == "wind":
        phrase = f"Gusts {kmh_to_mph(value)} mph" if value is not None else "High wind"
    elif rule == "thunderstorm":
        phrase = "Thunderstorm"
    elif rule == "visibility" and value is not None:
        phrase = f"Visibility {m_to_mi(value)} mi"
    elif rule == "winter":
        phrase = (
            f"Freezing at {c_to_f(value)} °F"
            if value is not None and value <= 0
            else "Snow"
        )
    else:
        phrase = rule.capitalize()
    suffix = place if place.startswith("near ") else f"near {place}"
    return f"{phrase} at {when} {suffix}"


async def plan_trip(cache: Cache, raw_signals: dict, *, refresh: bool = False) -> TripResult:
    trip, interval = parse_signals(raw_signals)
    now = int(time())
    hits: list[CacheHit] = []

    if len(trip) < 2:
        raise TripInputError(["Need at least 2 stops."])
    if not trip[0][1]:
        raise TripInputError(["Stop 1 needs a leave-at time."])

    # Dwell (PLAN.md §4.1): minutes stopped at each stop before continuing;
    # "" = 0. Mid-route only — origin/final dwell is ignored by the schedule.
    errors: list[str] = []
    dwells: list[int] = []
    for i, (_, _, dwell_raw) in enumerate(trip):
        dwell = 0
        if dwell_raw:
            try:
                parsed = int(dwell_raw)
            except ValueError:
                parsed = -1
            if 0 <= parsed <= config.MAX_DWELL_MIN:
                dwell = parsed
            else:
                errors.append(
                    f"Stop {i + 1}: dwell must be a whole number of minutes "
                    f"from 0 to {config.MAX_DWELL_MIN}."
                )
        dwells.append(dwell)

    # Geocode concurrently (§4.2 step 6), collecting all failures (§4.1).

    async def resolve(i: int, name: str) -> geocode.Stop | None:
        try:
            return await geocode.resolve_stop(cache, name, hits=hits, refresh=refresh)
        except geocode.StopNotFound:
            errors.append(f'Stop {i + 1}: "{name}" not found.')
        except geocode.CoordsOutOfRange:
            errors.append(f"Stop {i + 1}: coordinates out of range.")
        return None

    stops_t = await asyncio.gather(*(resolve(i, n) for i, (n, _, _) in enumerate(trip)))
    if errors:
        raise TripInputError(errors)
    stops: list[geocode.Stop] = [s for s in stops_t if s is not None]

    # Route (§4.3).
    try:
        legs = await routing.route_trip(
            cache, [(s.lat, s.lon) for s in stops], hits=hits, refresh=refresh
        )
    except routing.NoRoute as nr:
        if nr.pair is not None:
            raise TripInputError(
                [f"No drivable route between stop {nr.pair + 1} and stop {nr.pair + 2}."]
            ) from None
        raise

    # Schedule (§4.4); leave-before-arrival is a validation error.
    plan = sched_mod.plan_schedule(
        [s.tz for s in stops],
        [lv for _, lv, _ in trip],
        [leg.leg_duration_s for leg in legs],
        dwells,
    )
    if plan.errors:
        raise TripInputError(
            [
                f"Stop {e.stop}: leave time {e.leave_local} is before arrival {e.arrival_local}."
                for e in plan.errors
            ]
        )

    # Sampling + row order (§4.5).
    leg_samples = [
        sampling.sample_leg(k, leg.coords, leg.durations, sl.start_epoch, interval)
        for k, (leg, sl) in enumerate(zip(legs, plan.legs, strict=True))
    ]
    row_spec = sampling.build_rows(len(stops), leg_samples)
    row_points: list[tuple[float, float]] = []
    row_epochs: list[int] = []
    for kind, val in row_spec:
        if kind == "stop":
            st = plan.stops[val]
            row_points.append((stops[val].lat, stops[val].lon))
            row_epochs.append(st.arrival_epoch if st.arrival_epoch is not None else st.departure_epoch)
        else:
            row_points.append((val.lat, val.lon))
            row_epochs.append(val.epoch)

    # Horizon pre-check (§4.6) before any weather call.
    if weather.horizon_exceeded(max(row_epochs), now=now):
        latest = weather.latest_plannable(now)
        tz0 = stops[0].tz
        raise TripInputError(
            [
                "Trip extends past the 16-day forecast horizon "
                f"(latest plannable: {_fmt(latest, tz0)}, {tz0})."
            ]
        )

    # Weather (§4.6): one bulk request; chunk failures mark nodata rows.
    try:
        locs = await weather.fetch_all(cache, row_points, hits=hits, refresh=refresh)
    except httpx.HTTPError as exc:  # defensive: transport leak past cache
        raise UpstreamError("Open-Meteo", type(exc).__name__) from exc

    rows: list[Row] = []
    counts = {"clear": 0, "warning": 0, "severe": 0, "nodata": 0}
    pending: list[tuple[int, float, float]] = []
    stop_idx_of_row: dict[int, int] = {}
    next_stop = 0
    for n, ((kind, val), epoch, loc) in enumerate(
        zip(row_spec, row_epochs, locs, strict=True)
    ):
        tzname = loc.timezone if loc else None
        if kind == "stop":
            si = next_stop
            next_stop += 1
            stop_idx_of_row[n] = si
            st = plan.stops[si]
            mid = 0 < si < len(plan.stops) - 1
            row = Row(
                n=n, kind="stop", place=stops[si].display, label_id=None,
                epoch=epoch, local_time=_fmt(epoch, tzname),
                local_leave=(
                    _fmt(st.departure_epoch, tzname)
                    if mid and st.departure_epoch > st.arrival_epoch
                    else None
                ),
                dwell_min=st.dwell_min if mid and st.dwell_min else None,
                leg_mi=int(legs[si - 1].leg_distance_m / 1609.344) if si else None,
                drive_str=_drive_str(legs[si - 1].leg_duration_s) if si else None,
                temp_f=None, feels_f=None, precip_in=None, precip_prob=None,
                wind_mph=None, gust_mph=None, icon=None, level="nodata",
            )
        else:
            row = Row(
                n=n, kind="waypoint",
                place=f"near {val.lat:.2f}, {val.lon:.2f}",  # fallback until streamed
                label_id=f"label-{n}",
                epoch=epoch, local_time=_fmt(epoch, tzname), local_leave=None,
                leg_mi=int(legs[val.leg].leg_distance_m / 1609.344),
                drive_str=_drive_str(val.t_s),
                temp_f=None, feels_f=None, precip_in=None, precip_prob=None,
                wind_mph=None, gust_mph=None, icon=None, level="nodata",
            )
        if loc is not None:
            idx = weather.hour_index(loc, epoch)
            if idx is not None:
                hv = loc.hourly
                row.temp_f = c_to_f(hv["temperature_2m"][idx])
                row.feels_f = c_to_f(hv["apparent_temperature"][idx])
                row.precip_in = mmh_to_inh(hv["precipitation"][idx])
                prob = hv["precipitation_probability"][idx]
                row.precip_prob = int(prob) if prob is not None else None
                row.wind_mph = kmh_to_mph(hv["wind_speed_10m"][idx])
                row.gust_mph = kmh_to_mph(hv["wind_gusts_10m"][idx])
                row.icon = severity.WMO_ICON.get(hv["weather_code"][idx])
                win = weather.window_indices(hv["time"], weather.floor_hour(epoch))
                row.level, row.triggers = severity.evaluate(hv, win)
                for i in win:  # detail-panel window table (§5)
                    hdict = {k: series[i] for k, series in hv.items() if k != "time"}
                    row.window.append({
                        "t": _hhmm(hv["time"][i], loc.timezone),
                        "temp": c_to_f(hdict["temperature_2m"]),
                        "feels": c_to_f(hdict["apparent_temperature"]),
                        "precip": (
                            f"{mmh_to_inh(hdict['precipitation']):.2f}"
                            if hdict["precipitation"] is not None else "—"
                        ),
                        "prob": hdict["precipitation_probability"],
                        "wind": kmh_to_mph(hdict["wind_speed_10m"]),
                        "gust": kmh_to_mph(hdict["wind_gusts_10m"]),
                        "icon": severity.WMO_ICON.get(hdict["weather_code"]),
                        "level": severity.hour_level(hdict) or "clear",
                    })
                row.sentences = [
                    f"{t.rule}: "
                    + _sentence(t.rule, t.value, _hhmm(t.hour, loc.timezone), row.place)
                    for t in row.triggers
                ]
        counts[row.level] += 1
        if row.kind == "waypoint":
            pending.append((n, row_points[n][0], row_points[n][1]))
        rows.append(row)

    # Map JSON (§5 shape).
    pieces = []
    for samples in leg_samples:
        for w in samples:
            row_n = row_spec.index(("waypoint", w))
            pieces.append({
                "row": row_n, "level": rows[row_n].level,
                "coords": [[round(c[0], 5), round(c[1], 5)] for c in w.piece_coords],
            })
    map_stops = [
        {
            "row": n, "n": si + 1, "lat": stops[si].lat, "lon": stops[si].lon,
            "level": rows[n].level, "title": f"{si + 1}. {stops[si].display}",
        }
        for n, si in stop_idx_of_row.items()
    ]
    map_json = json.dumps({"pieces": pieces, "stops": map_stops}, separators=(",", ":"))

    return TripResult(
        rows=rows, map_json=map_json, legend_counts=counts, generated_at=now,
        oldest_age_s=max((h.age_s for h in hits), default=0),
        stale=any(h.stale for h in hits),
        nodata_count=counts["nodata"], pending_labels=pending,
    )
