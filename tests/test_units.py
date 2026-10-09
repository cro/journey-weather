"""Unit conversion tests (PLAN.md §4.9, §6.4)."""

import pytest

from journey_weather import units


@pytest.mark.parametrize(
    "c,f", [(0, 32), (20, 68), (-40, -40), (37.8, 100), (-17.8, 0)]
)
def test_celsius_to_fahrenheit_rounds_to_int(c, f):
    assert units.c_to_f(c) == f


@pytest.mark.parametrize("kmh,mph", [(47, 29), (63, 39), (78, 48), (10, 6), (0, 0)])
def test_kmh_to_mph(kmh, mph):
    """Legend values: 29 / 39 sustained, 39 / 48 gust (§4.9)."""
    assert units.kmh_to_mph(kmh) == mph


@pytest.mark.parametrize("mm,inhr", [(5, 0.2), (12, 0.47), (0, 0.0), (1, 0.04)])
def test_mmh_to_inh_two_decimals(mm, inhr):
    assert units.mmh_to_inh(mm) == inhr


@pytest.mark.parametrize("km,mi", [(537, 334), (332, 206), (231, 144), (187, 116), (1288, 800)])
def test_km_to_mi_matches_worked_example(km, mi):
    assert units.km_to_mi(km) == mi


@pytest.mark.parametrize("m,mi", [(2000, 1.2), (500, 0.3), (1609, 1.0), (20000, 12.4)])
def test_meters_to_miles_one_decimal(m, mi):
    assert units.m_to_mi(m) == mi


def test_none_passthrough():
    assert units.c_to_f(None) is None
    assert units.kmh_to_mph(None) is None
    assert units.mmh_to_inh(None) is None
    assert units.km_to_mi(None) is None
    assert units.m_to_mi(None) is None
