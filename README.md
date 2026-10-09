# journey-weather

Road-trip weather planner: forecast along your route at every stop and waypoint.
Design and rationale: [PLAN.md](PLAN.md).

## Run
```sh
cd /src/journey-weather
uv sync
uv run scripts/vendor.sh   # only needed to refresh static/vendor/ (committed)
uv run uvicorn journey_weather.app:app --reload --host 0.0.0.0 --port 8000
```
Open `http://<this machine's address>:8000` (binds all interfaces).

## Test

```sh
uv run pytest
```
