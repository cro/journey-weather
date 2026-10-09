"""Per-leg departure/arrival arithmetic (PLAN.md §4.4). Pure: UTC epoch seconds in/out."""

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

LEAVE_FMT = "%Y-%m-%dT%H:%M"  # datetime-local value, interpreted in the stop's tz


@dataclass
class LeaveBeforeArrival:
    """Structured error; the pipeline formats the §4.1 message from these fields."""

    stop: int  # 1-based stop number (message uses this)
    leave_local: str  # "HH:MM" at that stop's local clock
    arrival_local: str  # "HH:MM" at that stop's local clock


@dataclass
class StopSchedule:
    departure_epoch: int  # when the car leaves this stop (origin: its leave; else arrival + dwell)
    arrival_epoch: int | None  # None for the origin
    leave_epoch: int | None  # set only if the user gave a leave-at (not for final stop)
    tz: str
    dwell_min: int = 0  # dwell honored at this stop (0 for origin/final: their dwell is ignored)


@dataclass
class LegSchedule:
    start_epoch: int
    end_epoch: int


@dataclass
class Schedule:
    stops: list[StopSchedule]
    legs: list[LegSchedule]
    errors: list[LeaveBeforeArrival] = field(default_factory=list)


def local_to_epoch(leave: str, tz: str) -> int:
    """`YYYY-MM-DDTHH:MM` in stop tz → UTC epoch seconds (§4.4, §8)."""
    naive = datetime.strptime(leave, LEAVE_FMT)
    return int(naive.replace(tzinfo=ZoneInfo(tz)).timestamp())


def epoch_to_hhmm(epoch: int, tz: str) -> str:
    return datetime.fromtimestamp(epoch, ZoneInfo(tz)).strftime("%H:%M")


def plan_schedule(
    tzs: list[str],
    leaves: list[str],
    durations_s: list[float],
    dwells: list[int] | None = None,
) -> Schedule:
    """One leg per consecutive pair (len(tzs) == len(durations_s) + 1).

    `leaves` has one entry per stop; "" = unset. Origin's leave must be set
    (validated upstream). Final stop's leave is ignored (§1.1).

    `dwells` (minutes, one per stop; missing/None = all 0) is the minimum time
    spent AT that stop before continuing. It applies to mid-route stops only:
    the origin's departure is its leave-at and the final stop has no onward
    driving, so their dwell is ignored. Departure from a mid-route stop =
    max(explicit leave-at if set, arrival + dwell)."""
    n = len(tzs)
    stops: list[StopSchedule] = []
    legs: list[LegSchedule] = []
    errors: list[LeaveBeforeArrival] = []

    def dwell_of(i: int) -> int:
        if dwells is None or i >= len(dwells):
            return 0
        return dwells[i]

    origin = local_to_epoch(leaves[0], tzs[0])
    stops.append(StopSchedule(departure_epoch=origin, arrival_epoch=None,
                              leave_epoch=origin, tz=tzs[0]))
    for k, dur in enumerate(durations_s):
        start = stops[k].departure_epoch
        end = start + round(dur)
        legs.append(LegSchedule(start_epoch=start, end_epoch=end))
        tz_next = tzs[k + 1]
        is_final = k + 1 == n - 1
        dwell = 0 if is_final else dwell_of(k + 1)
        leave_raw = "" if is_final else leaves[k + 1].strip()
        leave_epoch: int | None = None
        if leave_raw:
            candidate = local_to_epoch(leave_raw, tz_next)
            if candidate < end:
                errors.append(LeaveBeforeArrival(
                    stop=k + 2,
                    leave_local=epoch_to_hhmm(candidate, tz_next),
                    arrival_local=epoch_to_hhmm(end, tz_next),
                ))
            else:
                leave_epoch = candidate
        departure = end + dwell * 60
        if leave_epoch is not None:
            departure = max(departure, leave_epoch)
        stops.append(StopSchedule(departure_epoch=departure, arrival_epoch=end,
                                  leave_epoch=leave_epoch, tz=tz_next,
                                  dwell_min=dwell))
    return Schedule(stops=stops, legs=legs, errors=errors)
