"""Schedule tests (PLAN.md §4.4, §6.3): per-leg departures, monotonicity, tz case."""

from journey_weather.schedule import epoch_to_hhmm, local_to_epoch, plan_schedule

EPOCH_MIN = 1_700_000_000  # 2023-11-14, stable arbitrary base


def test_origin_and_drive_through_arrivals():
    sched = plan_schedule(
        ["America/Denver", "America/Denver", "America/Denver"],
        ["2026-10-10T07:00", "", ""],
        [3600, 7200],
    )
    assert not sched.errors
    assert sched.stops[0].departure_epoch == local_to_epoch("2026-10-10T07:00", "America/Denver")
    assert sched.stops[1].arrival_epoch == sched.stops[0].departure_epoch + 3600
    assert sched.stops[1].leave_epoch is None  # blank: keep driving on arrival
    assert sched.stops[1].departure_epoch == sched.stops[1].arrival_epoch
    assert sched.stops[2].arrival_epoch == sched.stops[1].departure_epoch + 7200
    assert len(sched.legs) == 2
    assert sched.legs[0].end_epoch == sched.legs[1].start_epoch


def test_arrivals_strictly_monotonic():
    sched = plan_schedule(
        ["America/Denver"] * 4,
        ["2026-10-10T00:00", "2026-10-10T09:00", "", ""],
        [3600, 3600, 3600],
    )
    arrivals = [s.arrival_epoch for s in sched.stops[1:]]
    assert arrivals == sorted(arrivals) and len(set(arrivals)) == len(arrivals)
    # stop 1 waits until its leave: departure(09:00) > arrival(01:00)
    assert sched.stops[1].departure_epoch > sched.stops[1].arrival_epoch


def test_kanab_to_flagstaff_local_clock_falls_back_one_hour():
    """§7 fixture: leave Kanab 08:00 MDT, 4.24 h leg → arrive Flagstaff 11:14 MST."""
    dur = round(4.24 * 3600)
    sched = plan_schedule(
        ["America/Denver", "America/Phoenix"],
        ["2026-10-10T08:00", ""],
        [dur],
    )
    arrival = sched.stops[1].arrival_epoch
    assert epoch_to_hhmm(arrival, "America/Denver") == "12:14"  # naive wall-clock
    assert epoch_to_hhmm(arrival, "America/Phoenix") == "11:14"  # MST: back 1 h


def test_leave_before_arrival_reported_and_clamped():
    """Mid-route stop (final stop's leave is ignored by design, §1.1)."""
    sched = plan_schedule(
        ["America/Denver", "America/Denver", "America/Denver"],
        ["2026-10-10T07:00", "2026-10-10T07:30", ""],
        [3600, 3600],
    )
    assert len(sched.errors) == 1
    err = sched.errors[0]
    assert err.stop == 2
    assert (err.leave_local, err.arrival_local) == ("07:30", "08:00")
    # plan proceeds as if the bad leave was blank (drive on arrival)
    assert sched.stops[1].leave_epoch is None
    assert sched.stops[1].departure_epoch == sched.stops[1].arrival_epoch
    assert sched.stops[2].arrival_epoch == sched.stops[1].arrival_epoch + 3600


def test_final_stop_leave_is_ignored():
    sched = plan_schedule(
        ["America/Denver", "America/Denver"],
        ["2026-10-10T07:00", "2026-10-20T07:00"],
        [3600],
    )
    assert not sched.errors
    assert sched.stops[1].leave_epoch is None


def test_daylight_savings_boundary_uses_stop_zone():
    """2026-03-08 02:30 doesn't exist in America/Denver; zoneinfo must fold it,
    never produce a UTC-instant before the previous hour."""
    e1 = local_to_epoch("2026-03-08T01:30", "America/Denver")
    e2 = local_to_epoch("2026-03-08T03:30", "America/Denver")
    assert e2 - e1 == 3600  # 01:30 MST → 03:30 MDT: same instant apart by 1 h
    e3 = local_to_epoch("2026-03-08T02:30", "America/Denver")
    assert e1 < e3 < e2 or e3 == e2  # folded forward; strictly after 01:30


# --- dwell time (§10 feature 1) ---

def test_dwell_delays_everything_after_the_stop():
    """Garden City 35-min gas stop: departure = arrival + 35, later arrivals shift."""
    sched = plan_schedule(
        ["America/Denver"] * 3,
        ["2026-10-10T07:00", "", ""],
        [3600, 3600],
        [0, 35, 0],
    )
    assert not sched.errors
    assert sched.stops[1].dwell_min == 35
    assert sched.stops[1].departure_epoch == sched.stops[1].arrival_epoch + 35 * 60
    assert sched.stops[2].arrival_epoch == sched.stops[1].departure_epoch + 3600
    # leg 1 starts at the post-dwell departure
    assert sched.legs[1].start_epoch == sched.stops[1].departure_epoch


def test_dwell_is_a_minimum_leave_at_can_be_later():
    sched = plan_schedule(
        ["America/Denver"] * 3,
        ["2026-10-10T07:00", "2026-10-10T09:00", ""],
        [3600, 3600],
        [0, 35, 0],
    )
    # arrive 08:00, dwell says ≥08:35, explicit leave 09:00 wins
    assert sched.stops[1].leave_epoch == local_to_epoch("2026-10-10T09:00", "America/Denver")
    assert sched.stops[1].departure_epoch == sched.stops[1].leave_epoch


def test_dwell_beats_a_leave_at_inside_the_stop():
    """Explicit leave after arrival but before the dwell ends: dwell wins, no error."""
    sched = plan_schedule(
        ["America/Denver"] * 3,
        ["2026-10-10T07:00", "2026-10-10T08:10", ""],
        [3600, 3600],
        [0, 35, 0],
    )
    assert not sched.errors  # 08:10 ≥ arrival 08:00, so not the §4.1 error case
    assert sched.stops[1].departure_epoch == local_to_epoch("2026-10-10T08:35", "America/Denver")


def test_leave_before_arrival_still_errors_even_with_dwell():
    sched = plan_schedule(
        ["America/Denver"] * 3,
        ["2026-10-10T07:00", "2026-10-10T07:30", ""],
        [3600, 3600],
        [0, 35, 0],
    )
    assert [e.stop for e in sched.errors] == [2]


def test_origin_and_final_dwell_are_ignored():
    sched = plan_schedule(
        ["America/Denver"] * 3,
        ["2026-10-10T07:00", "", ""],
        [3600, 3600],
        [30, 0, 45],
    )
    assert sched.stops[0].departure_epoch == local_to_epoch("2026-10-10T07:00", "America/Denver")
    assert sched.stops[0].dwell_min == 0
    assert sched.stops[1].departure_epoch == sched.stops[1].arrival_epoch  # mid: dwell 0
    assert sched.stops[2].dwell_min == 0  # final: ignored like its leave-at
