# journey-weather

Road-trip weather planner: forecast along your route at every stop and waypoint.
Design and rationale: [PLAN.md](PLAN.md).

## Run

```sh
uv sync
uv run scripts/vendor.sh   # only needed to refresh static/vendor/ (committed)
uv run uvicorn journey_weather.app:app --reload
```

Open http://127.0.0.1:8000

## Test

```sh
uv run pytest
```
