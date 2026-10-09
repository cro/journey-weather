"""FastAPI app: page, fragments (Milestone 5), static mount (PLAN.md §3)."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from .cache import Cache

HERE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(
        timeout=config.HTTP_TIMEOUT_S,
        headers={"User-Agent": config.USER_AGENT},
        follow_redirects=True,
    )
    cache = Cache(config.DB_PATH, http)
    await cache.open()
    app.state.http = http
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


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    template = jinja_env.get_template("index.html")
    return HTMLResponse(template.render())
