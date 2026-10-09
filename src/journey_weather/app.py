"""FastAPI app: page, Datastar fragment endpoints, static mount (PLAN.md §3, §5)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import httpx
from datastar_py import ServerSentEventGenerator as SSE
from datastar_py.fastapi import ReadSignals, datastar_response
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, labels, pipeline
from .cache import Cache, UpstreamError

HERE = Path(__file__).resolve().parent


def _humandur(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h"


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(
        timeout=config.HTTP_TIMEOUT_S,
        headers={"User-Agent": config.USER_AGENT},
        follow_redirects=True,
    )
    cache = Cache(config.DB_PATH, http)
    await cache.open()
    app.state.cache = cache
    try:
        yield
    finally:
        await cache.close()
        await http.aclose()


app = FastAPI(title="Journey Weather", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

jinja_env = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(("html", "xml")),
)
jinja_env.filters["humandur"] = _humandur
jinja_env.filters["utc"] = _utc

jinja_env.globals["MAX_DWELL_MIN"] = config.MAX_DWELL_MIN


def _render(name: str, **ctx) -> str:
    return jinja_env.get_template(name).render(**ctx)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_render("index.html"))


@app.get("/frag/stop-row")
@datastar_response
async def frag_stop_row(signals: ReadSignals):
    """Append one stop row (#stops, §4.1); disable Add stop at MAX_STOPS."""
    trip = (signals or {}).get("trip") or {}
    idx = sum(
        1 for k in trip if isinstance(k, str) and k.startswith("s") and k[1:].isdigit()
    )
    events = []
    if idx < config.MAX_STOPS:
        events.append(
            SSE.patch_elements(_render("_stop_row.html", i=idx), selector="#stops", mode="append")
        )
    if idx + 1 >= config.MAX_STOPS:
        events.append(
            SSE.patch_elements('<wa-button id="add-stop" disabled>Add stop</wa-button>')
        )
    return events


@app.get("/frag/result")
@datastar_response
async def frag_result(request: Request, signals: ReadSignals):
    """Validate → patch #form-errors or #route-result, then stream #label-* (#4.8)."""
    cache: Cache = request.app.state.cache
    refresh = request.query_params.get("refresh") in {"1", "true"}

    async def events():
        try:
            result = await pipeline.plan_trip(cache, signals or {}, refresh=refresh)
        except pipeline.TripInputError as exc:
            yield SSE.patch_elements(_render("_errors.html", messages=exc.messages, retry=False))
            return
        except UpstreamError as exc:
            yield SSE.patch_elements(
                _render(
                    "_errors.html",
                    messages=[f"{exc.service} is unavailable right now; try again."],
                    retry=True,
                )
            )
            return
        yield SSE.patch_elements('<div id="form-errors"></div>')
        yield SSE.patch_signals({"_routeJson": result.map_json})
        yield SSE.patch_elements(_render("_result.html", result=result))
        for n, lat, lon in result.pending_labels:
            label = await labels.reverse_label(cache, lat, lon, refresh=refresh)
            yield SSE.patch_elements(f'<span id="label-{n}">{escape(label)}</span>')

    return events()
