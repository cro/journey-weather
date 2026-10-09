"""Endpoint tests (PLAN.md §6.5): SSE framing for stop-row and result fragments."""

import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from journey_weather import app as app_module  # noqa: F401  (ensures import)
from journey_weather import labels, pipeline
from journey_weather.app import app
from journey_weather.pipeline import Row, TripResult

DS_HEADER = {"Datastar-Request": "true"}  # real Datastar client always sends this


@pytest.fixture
def client():
    with TestClient(app) as c:  # runs lifespan → opens cache DB
        yield c


def ds(sigs: dict) -> str:
    return "?datastar=" + quote(json.dumps(sigs), safe="")


def events_of(body: str) -> list[dict]:
    """Parse SSE frames; multi-line elements data-lines accumulate."""
    out = []
    for block in body.strip().split("\n\n"):
        typ, kvs, elements = None, {}, []
        for line in block.splitlines():
            if line.startswith("event: "):
                typ = line[7:]
            elif line.startswith("data: "):
                k, _, v = line[6:].partition(" ")
                if k == "elements":
                    elements.append(v)
                else:
                    kvs[k] = v
        if elements:
            kvs["elements"] = "\n".join(elements)
        out.append({"type": typ, **kvs})
    return out


def trip_signals(n: int) -> dict:
    return {
        "trip": {f"s{i}": {"name": f"City{i}", "leave": ""} for i in range(n)},
        "interval": 60,
    }


def test_stop_row_appends_with_selector_and_mode(client):
    evs = events_of(
        client.get("/frag/stop-row" + ds(trip_signals(2)), headers=DS_HEADER).text
    )
    assert len(evs) == 1
    ev = evs[0]
    assert 'id="stop-2"' in ev["elements"] and 'data-bind="trip.s2.name"' in ev["elements"]
    assert ev["mode"] == "append" and ev["selector"] == "#stops"


def test_stop_row_disables_at_max(client):
    evs = events_of(
        client.get("/frag/stop-row" + ds(trip_signals(11)), headers=DS_HEADER).text
    )
    assert evs[0]["mode"] == "append"  # 12th row still appended
    assert any("disabled" in ev.get("elements", "") for ev in evs[1:])


def test_result_without_datastar_header_reports_need_two_stops(client):
    body = client.get("/frag/result" + ds({})).text  # no header → signals None
    assert "Need at least 2 stops." in body


def test_result_validation_error_patches_form_errors(client, monkeypatch):
    def boom(cache, signals, *, refresh=False):
        raise pipeline.TripInputError(["Need at least 2 stops."])

    monkeypatch.setattr(pipeline, "plan_trip", boom)
    evs = events_of(
        client.get("/frag/result" + ds({}), headers=DS_HEADER).text
    )
    assert len(evs) == 1
    assert evs[0]["type"] == "datastar-patch-elements"
    assert 'id="form-errors"' in evs[0]["elements"]
    assert "Need at least 2 stops." in evs[0]["elements"]


def test_result_upstream_error_offers_retry(client, monkeypatch):
    from journey_weather.cache import UpstreamError

    def boom(cache, signals, *, refresh=False):
        raise UpstreamError("OSRM", "timeout")

    monkeypatch.setattr(pipeline, "plan_trip", boom)
    body = client.get("/frag/result" + ds({}), headers=DS_HEADER).text
    assert "OSRM is unavailable right now; try again." in body
    assert "Retry" in body


def _mini_result() -> TripResult:
    row0 = Row(
        n=0, kind="stop", place="A, Ax, US", label_id=None, epoch=1,
        local_time="Sat 07:00", local_leave=None, temp_f=70, feels_f=69,
        precip_in=0.0, precip_prob=0, wind_mph=5, gust_mph=9, icon="sun", level="clear",
    )
    row1 = Row(
        n=1, kind="waypoint", place="near 1.00, 2.00", label_id="label-1", epoch=2,
        local_time="Sat 08:00", local_leave=None, temp_f=68, feels_f=67,
        precip_in=0.3, precip_prob=40, wind_mph=45, gust_mph=60, icon="cloud-rain",
        level="warning", leg_mi=50, drive_str="1 h 00 min",
        sentences=["wind: Gusts 60 mph at 08:00 XST near 1.00, 2.00"],
        window=[{"t": "08:00 XST", "temp": 68, "feels": 67, "precip": "0.30", "prob": 40,
                 "wind": 45, "gust": 60, "icon": "cloud-rain", "level": "warning"}],
    )
    return TripResult(
        rows=[row0, row1], map_json='{"pieces":[],"stops":[]}', legend_counts={},
        generated_at=1, oldest_age_s=3600, stale=False, nodata_count=0,
        pending_labels=[(1, 1.0, 2.0)],
    )


def test_result_streams_result_then_label_patches(client, monkeypatch):
    async def fake_plan(cache, signals, *, refresh=False):
        return _mini_result()

    async def fake_label(cache, lat, lon, *, refresh=False):
        assert (lat, lon) == (1.0, 2.0)
        return "Ash Fork"

    monkeypatch.setattr(pipeline, "plan_trip", fake_plan)
    monkeypatch.setattr(labels, "reverse_label", fake_label)
    evs = events_of(
        client.get("/frag/result" + ds({}), headers=DS_HEADER).text
    )
    assert [e["type"] for e in evs] == ["datastar-patch-elements"] * 3
    assert evs[0]["elements"].strip() == '<div id="form-errors"></div>'
    assert 'id="route-result"' in evs[1]["elements"]
    assert "near 1.00, 2.00" in evs[1]["elements"]  # fallback labels first (§4.8)
    assert 'id="label-1"' in evs[1]["elements"]
    assert evs[2]["elements"] == '<span id="label-1">Ash Fork</span>'


def test_result_escapes_label_html(client, monkeypatch):
    async def fake_plan(cache, signals, *, refresh=False):
        return _mini_result()

    async def fake_label(cache, lat, lon, *, refresh=False):
        return '<img src=x onerror="alert(1)">'

    monkeypatch.setattr(pipeline, "plan_trip", fake_plan)
    monkeypatch.setattr(labels, "reverse_label", fake_label)
    body = client.get("/frag/result" + ds({}), headers=DS_HEADER).text
    tail = body.split('id="label-1"')[-1]
    assert "&lt;img" in tail and "<img" not in tail


def test_index_page_has_form_containers(client):
    body = client.get("/").text
    for needle in ('id="stops"', 'id="form-errors"', 'id="route-result"',
                   'data-bind="trip.s0.name"', 'data-bind="interval"', "data-indicator:planning"):
        assert needle in body
