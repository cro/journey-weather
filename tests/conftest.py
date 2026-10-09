"""Shared fixtures: MockTransport clients + temp SQLite cache."""

import pathlib

import httpx
import pytest

from journey_weather.cache import Cache

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str):
    import json

    return json.loads(fixture(name))


def mock_client(
    routes: dict[str, bytes | str | dict | list],
    calls: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    """Client whose transport answers `routes` by exact URL; 404 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        body = routes.get(str(request.url))
        if body is None:
            return httpx.Response(404, json={"error": "unmapped", "url": str(request.url)})
        if isinstance(body, (dict, list)):
            return httpx.Response(200, json=body)
        if isinstance(body, str):
            body = body.encode()
        return httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)


@pytest.fixture
async def cache(tmp_path):
    """Real Cache over a throwaway DB with an all-404 client."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as http:
        c = Cache(tmp_path / "test.db", http)
        await c.open()
        yield c
        await c.close()


@pytest.fixture
def no_sleep(monkeypatch):
    """Neutralize backoff/rate-limit sleeps; record requested durations."""
    import journey_weather.cache as cache_mod
    import journey_weather.labels as labels_mod

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(cache_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(labels_mod.asyncio, "sleep", fake_sleep)
    return slept
