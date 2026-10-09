"""Severity rules (PLAN.md §4.7). Pure; metric thresholds from config."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import config
# WMO weather_code → vendored lucide icon (PLAN.md §5 table).
WMO_ICON: dict[int, str] = {
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


LEVELS = ("clear", "warning", "severe")


@dataclass(frozen=True)
class Trigger:
    rule: str
    level: str  # "warning" | "severe"
    hour: int  # epoch seconds of the offending hour
    value: float | None  # representative metric for the detail sentence



def hour_level(h: Mapping) -> str | None:
    """Max level across rules for a single hour dict (detail-panel rows)."""
    return _max_level([fn(h) for fn in _RULES.values()])

def _max_level(levels: Sequence[str | None]) -> str:
    best = 0
    for lv in levels:
        if lv is not None and lv != "nodata":
            best = max(best, LEVELS.index(lv))
    return LEVELS[best]


def _rule_rain(h: Mapping) -> str | None:
    p = h["precipitation"]
    if p is None:
        return None
    return "severe" if p >= config.RAIN_SEVERE_MMH else ("warning" if p >= config.RAIN_WARNING_MMH else None)


def _rule_wind(h: Mapping) -> str | None:
    s, g = h["wind_speed_10m"], h["wind_gusts_10m"]
    if s is None and g is None:
        return None
    if (s is not None and s >= config.WIND_SEVERE_KMH) or (g is not None and g >= config.WIND_GUST_SEVERE_KMH):
        return "severe"
    if (s is not None and s >= config.WIND_WARNING_KMH) or (g is not None and g >= config.WIND_GUST_WARNING_KMH):
        return "warning"
    return None


def _rule_thunder(h: Mapping) -> str | None:
    code, cape, precip = h["weather_code"], h["cape"], h["precipitation"]
    if code is not None and code in config.THUNDERSTORM_CODES:
        return "severe"
    if cape is None or precip is None:
        return None
    if cape >= config.CAPE_SEVERE and precip >= config.PRECIP_THUNDER_SEVERE_MMH:
        return "severe"
    if cape >= config.CAPE_WARNING and precip >= config.PRECIP_THUNDER_WARNING_MMH:
        return "warning"
    return None


def _rule_visibility(h: Mapping) -> str | None:
    v = h["visibility"]
    if v is None:
        return None
    return "severe" if v < config.VIS_SEVERE_M else ("warning" if v < config.VIS_WARNING_M else None)


def _rule_winter(h: Mapping) -> str | None:
    code, temp, precip = h["weather_code"], h["temperature_2m"], h["precipitation"]
    if code is not None and code in config.WINTER_SEVERE_CODES:
        return "severe"
    icy = temp is not None and precip is not None and temp <= config.FREEZING_C and precip > 0
    if (code is not None and code in config.WINTER_WARNING_CODES) or icy:
        return "warning"
    return None


_RULES = {
    "rain": _rule_rain,
    "wind": _rule_wind,
    "thunderstorm": _rule_thunder,
    "visibility": _rule_visibility,
    "winter": _rule_winter,
}

# Representative value per rule for the detail sentence (§4.7 triggers).
_VALUE_KEYS = {
    "rain": "precipitation",
    "wind": "wind_gusts_10m",
    "thunderstorm": "cape",
    "visibility": "visibility",
    "winter": "temperature_2m",
}


def _hour_at(hourly: Mapping, idx: int) -> dict:
    return {var: series[idx] for var, series in hourly.items() if var != "time"}


def evaluate(hourly: Mapping, indices: Sequence[int]) -> tuple[str, list[Trigger]]:
    """Row level = max over rules and hours; empty/None → nodata (§4.7).
    `hourly` is a Location.hourly mapping; `indices` are window rows in it."""
    if not indices:
        return "nodata", []
    times = hourly["time"]
    triggers: list[Trigger] = []
    levels: list[str] = []
    for i in indices:
        h = _hour_at(hourly, i)
        for rule, fn in _RULES.items():
            level = fn(h)
            levels.append(level)
            if level:
                triggers.append(Trigger(rule, level, times[i], h.get(_VALUE_KEYS[rule])))
    return _max_level(levels), triggers
