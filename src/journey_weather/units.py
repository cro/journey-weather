"""Metric → imperial display conversions (PLAN.md §4.9). Pure; fetch stays metric."""


def c_to_f(celsius: float | None) -> int | None:
    return None if celsius is None else round(celsius * 9 / 5 + 32)


def kmh_to_mph(kmh: float | None) -> int | None:
    return None if kmh is None else round(kmh * 0.621371)


def mmh_to_inh(mm: float | None) -> float | None:
    return None if mm is None else round(mm / 25.4, 2)


def km_to_mi(km: float | None) -> int | None:
    return None if km is None else round(km * 0.621371)


def m_to_mi(m: float | None) -> float | None:
    return None if m is None else round(m / 1609.344, 1)
