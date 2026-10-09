"""Severity rule tests (PLAN.md §4.7, §6.4): boundaries in isolation, nulls, window max."""

import pytest

from journey_weather import config
from journey_weather.severity import evaluate

VARS = (
    "temperature_2m apparent_temperature precipitation precipitation_probability "
    "wind_speed_10m wind_gusts_10m weather_code cape visibility"
).split()
BASE = {
    "temperature_2m": 25.0,
    "apparent_temperature": 24.0,
    "precipitation": 0.0,
    "precipitation_probability": 10,
    "wind_speed_10m": 10.0,
    "wind_gusts_10m": 15.0,
    "weather_code": 0,
    "cape": 0.0,
    "visibility": 20000.0,
}
T0 = 1_760_000_000  # arbitrary aligned epoch


def hourly(**overrides) -> dict:
    """One shared value per variable (list of 4 slots) unless list given."""
    data = {"time": [T0, T0 + 3600, T0 + 7200, T0 + 10800]}
    for var in VARS:
        val = overrides.get(var, BASE[var])
        data[var] = list(val) if isinstance(val, list) else [val] * 4
    return data


def level(**kw):
    lv, _ = evaluate(hourly(**kw), [0])
    return lv


# --- Heavy rain ---
@pytest.mark.parametrize(
    "p,expected",
    [(4.99, "clear"), (5.0, "warning"), (11.99, "warning"), (12.0, "severe"), (None, "clear")],
)
def test_rain_boundaries(p, expected):
    assert level(precipitation=p) == expected


# --- High wind ---
def test_wind_sustained_boundaries():
    assert level(wind_speed_10m=46.9) == "clear"
    assert level(wind_speed_10m=47.0) == "warning"
    assert level(wind_speed_10m=63.0) == "severe"


def test_wind_gust_boundaries():
    assert level(wind_gusts_10m=63.0) == "warning"
    assert level(wind_gusts_10m=78.0) == "severe"
    assert level(wind_speed_10m=None, wind_gusts_10m=None) == "clear"


# --- Thunderstorm (code or CAPE+precip; no thunderstorm_probability, §2) ---
@pytest.mark.parametrize("code", sorted(config.THUNDERSTORM_CODES))
def test_thunderstorm_codes_severe(code):
    assert level(weather_code=code) == "severe"


def test_thunderstorm_cape_pairs():
    assert level(cape=1000, precipitation=0.5) == "warning"
    assert level(cape=1500, precipitation=2.5) == "severe"
    assert level(cape=2000, precipitation=1.0) == "warning"  # precip below severe pair
    assert level(cape=999, precipitation=5.0) == "warning"  # rain rule fires, thunder doesn't
    assert level(cape=None, precipitation=None, weather_code=0) == "clear"


# --- Low visibility (strict <) ---
def test_visibility_boundaries():
    assert level(visibility=2000) == "clear"
    assert level(visibility=1999) == "warning"
    assert level(visibility=500) == "warning"
    assert level(visibility=499) == "severe"


# --- Winter road ---
@pytest.mark.parametrize("code", sorted(config.WINTER_WARNING_CODES))
def test_winter_warning_codes(code):
    assert level(weather_code=code) == "warning"


@pytest.mark.parametrize("code", sorted(config.WINTER_SEVERE_CODES))
def test_winter_severe_codes(code):
    assert level(weather_code=code) == "severe"


def test_winter_freezing_with_precip():
    assert level(temperature_2m=0.0, precipitation=0.1) == "warning"
    assert level(temperature_2m=0.0, precipitation=0.0) == "clear"
    assert level(temperature_2m=0.1, precipitation=1.0) == "clear"
    assert level(temperature_2m=None, precipitation=None) == "clear"


# --- Window aggregation ---
def test_row_level_is_max_across_hours_and_rules():
    h = hourly(
        precipitation=[5.0, 0.0, 12.0, 0.0],  # warning at idx0, severe at idx2
        visibility=[20000, 1000, 20000, 20000],  # warning at idx1
    )
    lv, triggers = evaluate(h, [0, 1, 2])
    assert lv == "severe"
    rules = {(t.rule, t.level) for t in triggers}
    assert ("rain", "warning") in rules and ("rain", "severe") in rules
    assert ("visibility", "warning") in rules
    assert all(t.hour in h["time"] for t in triggers)


def test_null_inputs_skip_rule_for_that_hour():
    h = hourly(cape=[None, 2000, None, None], precipitation=[None, 3.0, None, None])
    lv, triggers = evaluate(h, [0, 1])
    assert lv == "severe"
    assert [t.hour for t in triggers if t.rule == "thunderstorm"] == [T0 + 3600]


def test_empty_window_is_nodata():
    lv, triggers = evaluate(hourly(), [])
    assert lv == "nodata" and triggers == []


def test_triggers_carry_value_for_detail_text():
    _lv, triggers = evaluate(hourly(wind_gusts_10m=80.0), [0])
    t = next(t for t in triggers if t.rule == "wind")
    assert t.level == "severe" and t.value == 80.0 and t.hour == T0
