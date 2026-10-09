"""SQLite HTTP cache with retry + stale serving (PLAN.md §3)."""

import asyncio
import random
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiosqlite
import httpx

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache(
    key         TEXT PRIMARY KEY,
    body        BLOB NOT NULL,
    fetched_at  INTEGER NOT NULL,
    ttl         INTEGER NOT NULL
)
"""


class UpstreamError(Exception):
    """An upstream failed with no cached row to fall back to."""

    def __init__(self, service: str, detail: str):
        super().__init__(f"{service}: {detail}")
        self.service = service
        self.detail = detail


@dataclass
class CacheHit:
    body: bytes
    age_s: int
    stale: bool


def cache_key(url: str) -> str:
    """Full URL with query params sorted (PLAN.md §3)."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    params = sorted(parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


class Cache:
    def __init__(self, db_path: Path, client: httpx.AsyncClient):
        self._db_path = db_path
        self._client = client
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        assert self._db is not None
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _row(self, key: str) -> tuple[bytes, int, int] | None:
        assert self._db is not None, "Cache.open() not called"
        async with self._db.execute(
            "SELECT body, fetched_at, ttl FROM http_cache WHERE key = ?", (key,)
        ) as cur:
            return await cur.fetchone()

    async def peek(self, url: str, ttl: int) -> CacheHit | None:
        """Fresh cached body, if any; never hits the network."""
        row = await self._row(cache_key(url))
        if row is None:
            return None
        body, fetched_at, _stored_ttl = row
        age = int(time.time()) - fetched_at
        return CacheHit(bytes(body), age, False) if age < ttl else None

    async def cached_get(
        self,
        url: str,
        ttl: int,
        *,
        service: str,
        refresh: bool = False,
        headers: dict[str, str] | None = None,
        hits: list[CacheHit] | None = None,
    ) -> CacheHit:
        """`hits`, when given, receives every CacheHit this call returns so the
        pipeline can report the oldest cache age among responses used (§5)."""
        key = cache_key(url)
        row = await self._row(key)
        now = int(time.time())

        def _ret(hit: CacheHit) -> CacheHit:
            if hits is not None:
                hits.append(hit)
            return hit

        if row is not None and not refresh:
            body, fetched_at, _stored_ttl = row
            age = now - fetched_at
            if age < ttl:
                return _ret(CacheHit(bytes(body), age, False))

        failure: str | None = None
        for attempt in range(config.RETRY_MAX + 1):
            if attempt:
                sleep = config.RETRY_BASE_SLEEP_S * 2 ** (attempt - 1)
                sleep += random.uniform(0, config.RETRY_JITTER_MAX_MS / 1000)
                await asyncio.sleep(sleep)
            try:
                resp = await self._client.get(url, headers=headers)
            except httpx.TimeoutException:
                failure = "timeout"
                continue
            except httpx.HTTPError as exc:  # connect/read errors: retryable
                failure = f"{type(exc).__name__}"
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                failure = f"HTTP {resp.status_code}"
                continue
            if resp.status_code >= 400:  # other 4xx: fail immediately
                failure = f"HTTP {resp.status_code}"
                break
            assert self._db is not None
            await self._db.execute(
                "INSERT OR REPLACE INTO http_cache(key, body, fetched_at, ttl)"
                " VALUES(?,?,?,?)",
                (key, resp.content, now, ttl),
            )
            await self._db.commit()
            return _ret(CacheHit(resp.content, 0, False))

        if row is not None:  # serve stale (PLAN.md §3)
            body, fetched_at, _stored_ttl = row
            return _ret(CacheHit(bytes(body), now - fetched_at, True))
        raise UpstreamError(service, failure or "request failed")
