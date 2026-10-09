"""FastAPI app: page, fragments (Milestone 5), static mount (PLAN.md §3)."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent

app = FastAPI(title="Journey Weather")

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
