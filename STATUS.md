# journey-weather — STATUS

Road-trip weather planner. Design & rationale live in **[PLAN.md](PLAN.md)** — read it first; this file is *state + traps*, not a spec restatement.

## Where it stands (2026-10-09)

**All 7 milestones complete and verified. §10.1 dwell time shipped 2026-10-09** (see session log at the bottom). Latest change: the Trap 1 boot assertion — `base.html` now *proves* Webawesome registration before loading Datastar (`await import`), closing the last follow-up item; 127 tests pass (`uv run pytest -q`, ~1 s). Earlier fixes `d69f7b9` (Trap 4) re-verified in the same live browser run: Plan → edit Leave-at → Plan updates table/map. Dev server runs as the `journey-dev` service on `0.0.0.0:8000` (LAN-reachable; `curl localhost:8000/healthz` → `{"ok":true}`). Live browser E2E done end-to-end: idle, loading, result + map, row selection, validation error, horizon error, repeat-planning.

| Milestone | Commit | What |
|---|---|---|
| 1 Scaffold | `2d4f785` | uv package, deps, static mount, `/healthz`, vendored assets |
| 2 Cache+clients | `20b3f1c` | SQLite HTTP cache, geocode/routing/labels |
| 3 Schedule+sampling | `6acca66` | per-leg departures, waypoint math (pure) |
| 4 Weather+severity+units | `71af41f` | bulk forecast, §4.7 rules, imperial |
| 5 Endpoints | `7e6e47b` | `/frag/stop-row`, `/frag/result` SSE, pipeline |
| 6 UI | `a80123c` | templates, `route-map.js`, CSS, E2E |
| 7 Hardening | `555085d` | fault-injection, stale/nodata, add-stop limit |
| — map fix | `4051ab9` | map survived only one plan — see Trap 3 |
| — re-Plan fix | `d69f7b9` | second Plan silently no-op'd — see Trap 4 |
| — dwell time | — | §10.1: per-stop dwell shifts later arrivals + forecast hours (this session) |

## The four integration traps (each cost real debugging; do not regress)

These were found **in a live browser**, not from docs — the plan's "verified facts" (§2) were correct but incomplete. All are documented in PLAN.md §5/§8; they are the most likely things a maintainer will trip on.

**1. Webawesome must register before Datastar (commit `e11ab5d`).**
`data-bind` reads/writes the element's `value` property. Webawesome's `webawesome.loader.js` defines custom elements *after* Datastar has processed the DOM, so on an undefined `wa-input` the binding connects to nothing — typed text never reaches the signals, form appears to work but sends blanks. `webawesome.js` does NOT help (it only re-exports the loader). Fix in force: `base.html` has an inline module script importing the 8 used components directly (`components/<name>/<name>.js`), asserting `customElements.get()` for each, and only then booting Datastar via `await import` — there is no static `datastar.js` tag, so the ordering is a hard barrier now: a broken guard throws loudly at boot instead of binding to nothing (pinned by `test_datastar_loads_only_behind_the_registration_guard`). SSE-patched rows are fine (they arrive post-upgrade).

**2. Use the value form of `data-bind`, never the colon shorthand (commit `e11ab5d`).**
`data-bind:trip.s0.name` (dotted path via colon modifier) silently fails to write back on web components. `data-bind="trip.s0.name"` works. PLAN.md §2 was corrected to reflect this.

**3. Imperative DOM (Leaflet) must live OUTSIDE any Datastar-patched region (commit `4051ab9`).**
Datastar's `patch-elements` morph **reuses** the existing DOM node by id/position and syncs its attributes/children to the incoming markup. For `<route-map>` that meant stripping Leaflet's injected classes/panes/tiles *without* re-firing `connectedCallback` — the map rendered on the first plan and was a dead empty box on every re-plan. Symptom: table + weather fine, map gone, **no console error, no server error** (so it looks like rate limiting — it is not). Fix: `<route-map id="trip-map">` is a **persistent** element in `index.html`, outside `#route-result`; its data arrives as a `datastar-patch-signals` event setting the `_routeJson` signal (underscore: see Trap 4), consumed via `data-attr:data-route="$_routeJson"`. `ResizeObserver → invalidateSize()` handles the hidden→visible transition (`data-show="$planning || $_routeJson != ''"`).
**Rule:** any library that injects its own child nodes goes in a persistent element driven by signals, never in patched markup.

**4. Every signal is echoed back in the GET URL; big display-only data MUST be `_`-prefixed (found 2026-10-09).**
Datastar's `@get` serializes ALL signals (default `filterSignals` exclude regex `/(^|\.)_/`, confirmed in the vendored 1.0.4 bundle) into the `datastar` query parameter of every request. The map geometry was parked in a plain `routeJson` signal (~94 KB for the 2-stop Layton→Kanab repro; the §7 route is bigger), so the *first* Plan sent `routeJson:""` fine, and every *re-Plan* put the whole geometry into the URL. uvicorn's h11 caps the request line at 65536 B → the request died as a raw `400 Bad Request` before FastAPI ran, Datastar got no SSE stream, and **the second Plan click silently did nothing** — no spinner stall, no error, no server-side pipeline involvement. Fix: `routeJson` → `_routeJson` (initial signal in `index.html`, `data-attr:data-route`/`data-show` refs, `SSE.patch_signals` in `app.py`) — the store keeps working, the echo skips it. Regression pins: `test_map_signal_is_excluded_from_request_echo` (all patch-signals keys underscore-prefixed) + the `$_routeJson` template assertion.
**Rule:** anything the server pushes to the client that the client must never send back (payloads, blobs, geometry) lives in an underscore-prefixed signal.

## Also non-obvious

- **Interval control is a native `<select>`, not `wa-select`.** Webawesome `wa-select` needs `wa-option` children (plain `<option>`s don't register → `.value` stays null → the signal never carries the interval). Datastar binds native `<select>` cleanly. (Commit `a80123c`.)
- **`wa-spinner` visibility** is driven by `data-indicator:planning` on the Plan button + `data-show="$planning"`. It is only visible on genuinely slow requests (everything cached → sub-frame). Don't "fix" it by adding artificial delay.
- **Favicon.** `base.html` links `/static/favicon.svg`; browsers auto-request `/favicon.ico` which 404s. The explicit `<link rel="icon">` + SVG suppress it — required for the milestone-1 "zero 404s" check to stay true.
- **Service PATH quirk.** The `journey-dev` uvicorn service does not inherit the mise shim PATH; it must be started with the absolute path `/home/cro/.local/share/mise/shims/uv run uvicorn journey_weather.app:app --host 0.0.0.0 --port 8000` (plain `uv` → `command not found`, exit 127).
- **`hits` collector.** `Cache.cached_get(..., hits=list)` records every returned `CacheHit` so the pipeline can report "oldest cached data" / stale badge in the result status strip. Threading it through geocode/routing/weather is what feeds §5's status strip — don't drop the kwarg.

## Verified upstream facts (2026-10-08/09, re-checked 10-09) — PLAN.md §2 is the source of truth

- **No `data-merge` attribute** in Datastar; responses are `datastar-patch-elements` events, morphed **by element id** (`SSE.patch_elements(html, selector=, mode=)` — keywords confirmed against installed `datastar_py` 1.0.3). `read_signals` requires the `Datastar-Request` header — **real Datastar sends it; TestClient does NOT**, so endpoint tests pass `DS_HEADER` explicitly and one test pins the "missing header → empty signals → 'Need at least 2 stops.'" path.
- **Open-Meteo**: `thunderstorm_probability` is NOT supported (all-null) → thunder rule uses `weather_code`∈{95,96,99} + CAPE. `forecast_days` max 16. Multi-location = comma-joined `latitude=`/`longitude=` (array, input order). `timezone=auto&timeformat=unixtime` → per-row IANA tz + `utc_offset_seconds` + epoch `hourly.time`. No reverse endpoint (404).
- **OSRM**: one multi-stop request returns `legs[k].annotation`; `len(geometry.coordinates) == Σ len(leg.annotation.duration) + 1` (the `6620 = 4266 + 2353 + 1` case). Live leg durations drift ~±0.02 h from the plan's 2026-10-08 probe — routing tolerances are `abs=0.05 h / 5 km`.
- **§7 worked example** (Layton→Kanab→Flagstaff→Phoenix→Tucson): 20 rows (5 stops + 15 waypoints), one bulk weather call, 20 locations. Kanab MDT→Flagstaff MST local clock falls back 1 h. `severity.WMO_ICON` maps weather_code→vendored lucide icon.
- **Nominatim**: 1 req/s + identifying `User-Agent` enforced in `labels.py` (`asyncio.Lock`, ≥1.1 s spacing). Labels are cosmetic — an outage degrades to `near {lat}, {lon}`, never fails a plan.

## Layout / how to run

```
src/journey_weather/  app.py config.py cache.py geocode.py routing.py schedule.py
                      sampling.py weather.py severity.py labels.py units.py pipeline.py
                      templates/{base,index,_stop_row,_result,_errors}.html
                      static/{app.css,route-map.js,favicon.svg,vendor/…}
scripts/vendor.sh  scripts/smoke_milestone2.py  scripts/smoke_milestone4.py
tests/  (fixtures are recorded LIVE responses; run via httpx.MockTransport, network-free)
data/journey.db  (gitignored, created at startup; 30 d geocode / 7 d OSRM / 3 h forecast TTLs)
```

- Run: `uv run uvicorn journey_weather.app:app --reload --host 0.0.0.0 --port 8000`
- Refresh vendored assets (rare; they are committed): `uv run scripts/vendor.sh`
- Live smoke (hits real upstreams, uses `data/journey.db`): `uv run scripts/smoke_milestone2.py` / `smoke_milestone4.py`
- Tests: `uv run pytest -q` → 127. `tests/conftest.py` has `mock_client(routes)` + the `cache`/`no_sleep` fixtures; pure modules are unit-tested, I/O tested against recorded fixtures + synthetic fault handlers.

## Test map (where the trap regressions live)

- `test_endpoints.py` — SSE frame order `form-errors → patch_signals(_routeJson) → #route-result → labels`; asserts `route-map` is **absent** from the fragment and **present** in `/`; `datastar_py` `selector=`/`mode=` framing; Trap 4 pin: every patch-signals key is `_`-prefixed; Trap 1 pin: Datastar boots only behind the registration guard, and every `<wa-*>` tag in the templates is in the guarded set.
- `test_faults.py` — 503/429/timeout per upstream, chunk-of-10 weather fallback → `None`/`level-nodata`, stale serving, Refresh/stale-badge render.
- `test_schedule.py` — the §4.4 math + Kanab→Flagstaff tz case + `6620` split + the five dwell cases (§10.1: shift, leave-wins, dwell-wins, bounds, origin/final ignored).
- `test_pipeline.py` — dwell end-to-end over the §7 fixtures: `trip_cache` answers geocoding + OSRM fixtures and a past-hours synthetic forecast (all rows nodata → pure schedule assertions); pins parse/validation (0–1440) and that a mid-route 35-min dwell shifts every later arrival exactly. Its `POINTS` are **derived from the geocoding fixtures** — the live-API coords drift from the recorded ones, and a hand-typed OSRM URL 404s the mock.
- `test_weather.py` / `test_severity.py` / `test_units.py` — boundaries, null-skip, window indexing, horizon, imperial conversions.

## Intentional non-goals (v1, per PLAN.md §9)

No accounts, no saved/shared routes, no live traffic, no stop reordering/removal UI (blank rows ignored), thresholds baked in, imperial display only (fetch stays metric). Stateless: same signals recompute from cache — there is deliberately **no `runs`/`trips` table**.

## Unfinished / follow-up candidates

- Nothing blocking. If the Webawesome autoloader assumption is ever revisited, Trap 1 is the constraint.

## Session log — 2026-10-09 (second Plan click silently did nothing)

Reported: after a successful Plan, editing stop 1's Leave-at and clicking Plan again produced no visible response.

- Reproduced the failure mechanism: live browser E2E (page loads clean, zero 404s, but the `@get` payload grows) + raw-socket replay. The second click's `datastar` query param is 128 KB because the route geometry (~94 KB for Layton→Kanab, 2 stops) lives in the `routeJson` signal and Datastar echoes every non-underscore signal into the GET URL. uvicorn's h11 caps the request line at 65536 B → raw `400 Bad Request` before FastAPI; no SSE, no UI change, no pipeline error. The server-side pipeline itself was proven healthy (curl with the same signals → full result + label patches).
- Fix (`d69f7b9`): renamed the signal to `_routeJson` — excluded from the echo, unchanged as client store. Touched `templates/index.html` (initial signals, `data-show`, `data-attr:data-route`) and `app.py` (`SSE.patch_signals`). The Refresh button path (`/frag/result?refresh=1`) was equally affected and is fixed by the same rename.
- Tests: new `test_map_signal_is_excluded_from_request_echo` (asserts every patch-signals key is `_`-prefixed — fails on the old `routeJson` name); updated the two existing pins in `test_endpoints.py`. 110 → **111 passing**.
- Verified end-to-end in a live browser: typed trip → Plan (200, 304-char URL) → edited Leave-at 07:00→09:00 → Plan again (200, 304-char URL): itinerary updated (row 0 = 09:00, Kanab arrival 15:03), Leaflet map still alive (7 panes, `data-route` = the new 93 KB geometry), `#form-errors` empty; screenshot captured.
- Docs (`d69f7b9` + `67ae812` + this): PLAN.md §2 now records the bundle default `filterSignals {include: /.*/, exclude: /(^|\.)_/}`; §4.1 initial signals and §5 map refs updated; new §8 watch-out. STATUS.md gained Trap 4 ("four integration traps"), Trap 3 and the test map renamed accordingly, milestone table gained the re-Plan row.
- Environment: `journey-dev` was found down; restarted with the absolute uv path (PATH quirk, "Also non-obvious") — running on `0.0.0.0:8000` with the fix. Browser tabs from this investigation were closed; no scratch files to clean up.

## Session log — 2026-10-09 (Trap 1 boot assertion — final follow-up closed)

- `base.html`: the 8 eager Webawesome imports now **assert** `customElements.get(name)` for every component and boot Datastar via `await import` only after the check passes; the static `<script src=…/datastar.js>` tag was removed. The ordering that was convention is now mechanism: a future breakage is an uncaught `Error` at boot, not silent blank bindings.
- New pin `test_datastar_loads_only_behind_the_registration_guard` (111 → 112): no datastar script tag in `<head>`; exactly one inline module script; import set == guarded set; throw-before-`await import`; every `<wa-*>` tag across the templates ⊆ guarded set.
- Live browser re-verified (fresh tab on `journey-dev`): all 8 `wa-*` registered, `datastar.js` fetched exactly once, zero console errors, zero 4xx/5xx. Typed Layton→Kanab + Leave-at `2026-10-10T09:00` → Plan: signals echoed with typed values (Trap 1/2 binding proven over the wire), 20-row table (256 cells), 93 KB `_routeJson` into the persistent map, Leaflet panes + tiles alive, 304-char request URL (Trap 4 intact); re-Plan again clean. Screenshot reviewed.
- Negative path (guard missing → throw → Datastar never loads) is guaranteed by module semantics (top-level throw aborts before the dynamic import) and pinned statically; could not exercise it live because the harness's `tab.route` respond-override silently no-ops (reported upstream).

## Session log — 2026-10-09 (§10.1 dwell time)

Added per-stop dwell (`trip.s{i}.dwell`, minutes): validated in `pipeline.plan_trip` (0–1440 whole minutes, message in §4.1), applied in `schedule.plan_schedule` as a minimum stay at mid-route stops — departure = max(arrival + dwell, explicit leave-at); origin/final dwell ignored like the final leave-at. Everything downstream (waypoint instants, weather hour index, horizon check, map) reads the schedule, so dwell automatically factors into the forecast without touching sampling/weather/severity. Result rows show `leave …` (effective departure, now shown whenever the car rests) + `N min stop` (`Row.dwell_min`). Templates: third `wa-input type=number` per stop row + `dwell` in page/row signals (small strings — no Trap 4 risk). 112 → 127 tests.

Verified live: server A/B (curl SSE, Datastar-Request header) Layton→Kanab→Flagstaff with/without 35 min at Kanab — Kanab arrival unchanged 13:02, row shows leave 13:37 + "35 min stop", Flagstaff 16:16→16:51, 22 of 65 rows' weather changed (all at/after the dwell); origin dwell and final dwell ignored. Browser: dwell field renders on each row, typed 35 → full plan updates; invalid dwell → `#form-errors` callout. `journey-dev` was down again at session start; restarted with the absolute uv path.
