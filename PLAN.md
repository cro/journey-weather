# Journey Weather — Plan

Road-trip weather planner: enter the cities you'll drive through, when you leave each one, and see the forecast you'll actually drive into, at every stop and at waypoints in between, with severe-weather flags on a color-coded map.

## 1. What it does (v1)

1. Enter the trip as an ordered list of stops, one form row per stop: city name plus a "leave at" date/time. The origin's leave-at is required. Later stops' leave-at is optional (blank = keep driving on arrival). The final stop's leave-at is ignored.
2. Backend: geocodes each stop, routes the whole trip with one OSRM request, places a waypoint every `interval` minutes of driving (default 60), computes each point's arrival instant, fetches the hourly forecast for all points in one Open-Meteo request, applies severity rules, and caches every upstream response.
3. UI: Leaflet map with route pieces colored by severity and numbered stop markers; severity legend; itinerary table (stops interleaved with waypoints). Clicking a table row or map piece opens that row's detail panel. Display units are imperial (°F, mph, in, mi).
4. Each stop row echoes the resolved place ("Kanab, Utah, US"). A stop entered as `Name;lat;lon` skips geocoding.

Example trip (§7): Layton, UT → Kanab, UT → Flagstaff, AZ → Phoenix, AZ → Tucson, AZ.

## 2. Stack

- **Python 3.13**, **uv 0.12.23** (installed via mise: `mise use -g uv@0.12.23`). Project: `uv init --package journey-weather`; run with `uv run uvicorn journey_weather.app:app --reload`.
- Runtime deps: `fastapi`, `uvicorn[standard]`, `httpx`, `jinja2`, `aiosqlite`, `datastar-py` (1.0.3). Dev deps: `pytest`, `pytest-asyncio`.
- Frontend: Jinja2 server-rendered HTML + **Webawesome 3.14.0** + **Datastar 1.0.4** + **Leaflet 1.9.4**. The only hand-written JS is `static/route-map.js` (one custom element, §5). No Alpine, no htmx.
- Assets are vendored by `scripts/vendor.sh` into `src/journey_weather/static/vendor/` and committed. External requests at runtime: OSM map tiles (browser) and the upstream APIs (server).

### Verified integration facts (probed 2026-10-08)

- **Datastar** runtime: `https://cdn.jsdelivr.net/gh/starfederation/datastar@v1.0.4/bundles/datastar.js`. The npm package `datastar` is an unrelated SQL library.
  - Requests: `@get(url)` sends all signals (except `_`-prefixed) as JSON in the `datastar` query parameter. Backend: `from datastar_py.fastapi import datastar_response, read_signals`.
  - Responses: `text/event-stream` carrying `datastar-patch-elements` events, built with `from datastar_py import ServerSentEventGenerator as SSE` → `SSE.patch_elements(html)`. Elements are morphed into the DOM **by element id** (default mode `outer`). Other modes: `inner|replace|prepend|append|before|after|remove`, with `selector`. There is no `data-merge` attribute.
  - Attributes used: `data-signals`, `data-bind` (works on custom elements: binds `value`, listens to `change`), `data-on`, `data-show`, `data-class`, `data-attr`, `data-indicator`. Nested signals use dot notation: `data-bind:trip.s0.name`.
- **Webawesome 3.14.0**: npm tarball `https://registry.npmjs.org/@awesome.me/webawesome/-/webawesome-3.14.0.tgz`. `package/dist-cdn/` contains `webawesome.loader.js` (autoloader; imports relative `chunks/*`), `styles/webawesome.css`, `components/*`. The default `wa-icon` library is remote Font Awesome and is **not used**. Icons use `<wa-icon src="…">` with vendored SVGs.
- **lucide-static 1.53.0** (ISC): `icons/{sun,cloud-sun,cloud,cloud-fog,cloud-drizzle,cloud-rain,cloud-snow,cloud-lightning,wind,eye-off}.svg` all exist.
- **OSRM demo** `https://router.project-osrm.org/route/v1/driving/{lon,lat;…}?overview=full&geometries=geojson&annotations=duration,distance`:
  - One request with N stops returns `routes[0].legs[0..N-2]`, each with its own `annotation.duration`/`distance` arrays.
  - `len(geometry.coordinates) == Σ len(leg.annotation.duration) + 1`. Leg k covers coordinate indices `[off_k, off_k + len_k]` with `off_k = Σ_{j<k} len_j`.
- **Open-Meteo forecast** `https://api.open-meteo.com/v1/forecast`:
  - Multi-location via comma-joined `latitude=`/`longitude=`; the response is a JSON array in input order.
  - With `timezone=auto&timeformat=unixtime`, each element has `timezone` (IANA), `utc_offset_seconds`, and `hourly.time` as epoch seconds.
  - `forecast_days` max is 16 (17 is rejected).
  - `cape` and `visibility` are supported. `thunderstorm_probability` is **not** supported (unit `"undefined"`, all null).
- **Open-Meteo geocoding** `https://geocoding-api.open-meteo.com/v1/search?name=…&count=5`:
  - `"Layton, UT"`, `"Kanab, UT"`, `"Flagstaff, AZ"`, `"Phoenix, AZ"`, `"Tucson, AZ"` each resolve correctly as `results[0]`.
  - Results carry `name`, `admin1`, `country_code`, `latitude`, `longitude`, `timezone`.
  - There is no reverse endpoint (404).
- **Nominatim reverse** `https://nominatim.openstreetmap.org/reverse?lat=…&lon=…&format=jsonv2&zoom=10` works. Usage policy: identifying `User-Agent`, at most 1 request/second.

## 3. Architecture

```
Browser (Jinja HTML + Webawesome + Datastar + <route-map>)
   │  GET /                 full page: form (2 stop rows) + empty #route-result + #form-errors
   │  GET /frag/stop-row    SSE: append one stop row to #stops (reads signals for next index)
   │  GET /frag/result      SSE: validate → patch #form-errors or #route-result, then stream label patches
   ▼
pipeline.plan_trip(signals) — all async, one shared httpx.AsyncClient (timeout 15 s)
   parse_stops → geocode (asyncio.gather) → route (1 OSRM call) → schedule (per-leg departures)
   → sample waypoints → weather (1 bulk call) → severity → render
   → labels (Nominatim, serialized, streamed as follow-up patches)
```

Package layout:

```
src/journey_weather/
  app.py        FastAPI app, routes, static mount, Jinja env
  config.py     constants: TTLs, thresholds, USER_AGENT="journey-weather/0.1", MAX_STOPS=12, DB path
  cache.py      cached_get + retry policy
  geocode.py    forward geocoding, US state map, override parsing
  routing.py    OSRM client + leg split
  schedule.py   per-leg departure/arrival math (pure)
  sampling.py   waypoint interpolation + map pieces (pure)
  weather.py    bulk forecast client, horizon checks, hour indexing
  severity.py   rule table + window scan (pure)
  labels.py     Nominatim reverse, rate-limited
  units.py      metric → imperial display conversions (pure)
  pipeline.py   orchestration; returns a TripResult dataclass
  templates/    base.html, index.html, _stop_row.html, _result.html, _errors.html
  static/       app.css, route-map.js, vendor/{datastar,webawesome,leaflet,icons}/
scripts/vendor.sh
tests/          unit tests for pure modules + recorded-fixture tests (tests/fixtures/*.json)
data/journey.db (gitignored, created on startup)
```

### Cache (`data/journey.db`)

- One table: `http_cache(key TEXT PRIMARY KEY, body BLOB NOT NULL, fetched_at INTEGER NOT NULL, ttl INTEGER NOT NULL)`. The key is the full URL with query params sorted.
- `async def cached_get(url: str, ttl: int, *, refresh: bool = False) -> CacheHit` where `CacheHit(body: bytes, age_s: int, stale: bool)`:
  - Fresh hit and not `refresh` → return the cached body.
  - Otherwise fetch. On success, upsert and return `stale=False`.
  - On failure after retries: if any row exists for the key, return it with `stale=True`; else raise `UpstreamError(service, detail)`.
- Retries, for every upstream: on 429/5xx/timeouts, up to 3 retries with sleeps of 1 s, 2 s, 4 s plus 0–250 ms jitter. Other 4xx fail immediately.
- TTLs: geocoding 30 d; tz lookup for override stops 30 d; Nominatim reverse 30 d (coords rounded to 2 decimals before building the URL); OSRM 7 d; forecast 3 h (coords rounded to 2 decimals).
- No other tables. Plans are stateless: the same signals recompute from cache.

## 4. Core algorithms

### 4.1 Input (signals)

- Initial page signals: `{trip: {s0: {name: '', leave: ''}, s1: {name: '', leave: ''}}, interval: 60, selected: -1, planning: false}`.
- Each stop row `#stop-{i}` holds:
  - `<wa-input data-bind:trip.s{i}.name label="Stop {i+1}">`
  - `<wa-input type="datetime-local" data-bind:trip.s{i}.leave label="Leave at">`
- `/frag/stop-row`:
  - next index = number of keys in `trip`; patches a new row with `selector="#stops"`, `mode="append"`.
  - When the count reaches `MAX_STOPS` (12), it also patches the `#add-stop` button with `disabled`.
- Parsing in `/frag/result`:
  - Rows are ordered by numeric index; rows with a blank name are skipped.
  - A name matching `^(.+);\s*(-?\d+(\.\d+)?)\s*;\s*(-?\d+(\.\d+)?)$` is an override (lat in [-90, 90], lon in [-180, 180]).
- Validation errors are collected (not first-fail) and rendered as one `<wa-callout variant="danger">` in `#form-errors`. Exact messages:
  - `Need at least 2 stops.`
  - `Stop {n}: "{name}" not found.`
  - `Stop {n}: coordinates out of range.`
  - `Stop 1 needs a leave-at time.`
  - `Stop {n}: leave time {hh:mm} is before arrival {hh:mm}.`
  - `No drivable route between stop {n} and stop {n+1}.`
  - `Trip extends past the 16-day forecast horizon (latest plannable: {date time tz}).`
- A successful plan patches `#form-errors` empty.

### 4.2 Geocoding

1. Query the full name, `count=5`, and take `results[0]`.
2. If there are no results and the name contains `", "`, split at the last `", "` into `city, suffix`. Query `city` with `count=10` and take the first result where `admin1 == US_STATES[suffix.upper()]` (51-entry map: 50 states + DC) or `country_code == suffix.upper()`.
3. If still nothing → `not found` error.
4. Stop display = `f"{name}, {admin1}, {country_code}"`; keep `lat`, `lon`, `tz` (IANA).
5. Override stops: display = given name. Their `tz` comes from `forecast?latitude=…&longitude=…&timezone=auto&forecast_days=1` (`timezone` field; cached 30 d).
6. All stops are geocoded concurrently with `asyncio.gather`.

### 4.3 Routing

- One OSRM request with all stops in order. If `code == "NoRoute"`, find the failing pair by routing consecutive pairs and report it with the message from §4.1. Any other non-`Ok` code → `UpstreamError`.
- Split geometry and annotations per leg using the verified offset rule (§2). Leg k: `coords_k`, `dur_k[]`, `dist_k[]`, `leg_duration_s`, `leg_distance_m`.

### 4.4 Schedule (per-leg departures)

- Each `leave` string (`YYYY-MM-DDTHH:MM`) is interpreted in **that stop's** tz with `zoneinfo.ZoneInfo(tz)` and converted to UTC.
- Leg 0 starts at stop 0's leave time.
- Leg k > 0 starts at stop k's leave time if set, else at stop k's arrival. A leave time earlier than the arrival is a validation error.
- `arrive(stop k+1) = start(leg k) + leg_duration_s`.
- The UI labels every time "static speeds, no traffic".

### 4.5 Waypoint sampling and map pieces

- `n_k = max(1, round(leg_duration_s / (interval * 60)))`.
- Waypoint j of leg k sits at drive-time `t = (j + 0.5) / n_k * leg_duration_s`. Find the annotation index where cumulative `dur_k` crosses `t` and linearly interpolate lat/lon within that coordinate pair. Arrival instant = `start(leg k) + t`.
- Map piece j of leg k = the coordinate slice between drive-times `j/n_k` and `(j+1)/n_k` (endpoints interpolated). It is colored by waypoint j's level.
- Rows, in order: stop 0, waypoints of leg 0, stop 1, waypoints of leg 1, …, last stop.
  - Stop 0's weather instant = its leave time.
  - Stop k > 0's weather instant = its arrival.
  - Stop rows show arrival and (if set) leave times.

### 4.6 Weather

- One request for all row coordinates (rounded to 2 decimals):
  `hourly=temperature_2m,apparent_temperature,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m,weather_code,cape,visibility&timezone=auto&timeformat=unixtime&forecast_days=16`. Units stay at the API defaults (°C, km/h, mm, m).
- **Horizon pre-check** (before any weather call): reject if `max(row instant) + 2 h > now_utc + 15 days`. The message's "latest plannable" = `now_utc + 15 days − 2 h`, rendered in stop 0's tz.
- **Hour indexing**: `h = instant floored to the hour` (epoch seconds), `idx = hourly.time.index(h)`.
  - Severity window = hours `h − 3600`, `h`, `h + 3600`, `h + 7200` (those present in `hourly.time`).
  - If `h` is absent (post-check), the row becomes `nodata`.
  - "Weather at arrival" values = hour `h`.
- Local display time = `instant + utc_offset_seconds` of that row's response element.
- Failure handling:
  - If the bulk request fails with a non-retryable 4xx and there is more than 1 location, retry in chunks of 10 locations, sequentially, 500 ms apart.
  - Any chunk that still fails marks its rows `nodata` (grey).
  - The result is still rendered, with a warning callout: `Weather unavailable for {k} points.`

### 4.7 Severity (metric internally; thresholds baked in, not user-adjustable)

| Rule | Warning (amber) | Severe (red) | Inputs |
|---|---|---|---|
| Heavy rain | precip ≥ 5 mm/h | precip ≥ 12 mm/h | `precipitation` |
| High wind | sustained ≥ 47 km/h or gust ≥ 63 km/h | sustained ≥ 63 km/h or gust ≥ 78 km/h | `wind_speed_10m`, `wind_gusts_10m` |
| Thunderstorm | CAPE ≥ 1000 J/kg and precip ≥ 0.5 mm/h | `weather_code` ∈ {95, 96, 99}, or CAPE ≥ 1500 J/kg and precip ≥ 2.5 mm/h | `weather_code`, `cape`, `precipitation` |
| Low visibility | < 2000 m | < 500 m | `visibility` |
| Winter road | `weather_code` ∈ {71, 73, 77, 85}, or temp ≤ 0 °C with precip > 0 | `weather_code` ∈ {56, 57, 66, 67, 75, 86} | `weather_code`, `temperature_2m`, `precipitation` |

- Each rule is evaluated over every hour in the window. Null inputs skip that rule for that hour.
- Row level = max over rules and hours: `clear < warning < severe`. If all hours are missing → `nodata`.
- Each triggered rule records `(rule, level, hour, value)` for the badges and detail text.
- Map piece color, table row class, stop marker color, and detail panel all derive from row level.

### 4.8 Labels (waypoint rows only)

- Nominatim reverse `zoom=10`. Label = first present of `address.city`, `town`, `village`, `hamlet`, `county`. Fallback `f"near {lat:.2f}, {lon:.2f}"`.
- Uncached requests are serialized with one `asyncio.Lock`, ≥ 1.1 s apart, with header `User-Agent: journey-weather/0.1`.
- `/frag/result` first patches `#route-result` with fallback labels, then for each resolved label patches `<span id="label-{row}">…</span>` in the same SSE stream. Cached labels are rendered directly in the first patch.

### 4.9 Units (display only, `units.py`)

- °F = °C × 9/5 + 32 (rounded to int)
- mph = km/h × 0.621371 (int)
- in/h = mm / 25.4 (2 decimals)
- mi = km × 0.621371 (int); visibility m → mi (1 decimal)
- The legend shows thresholds converted: rain 0.20 / 0.47 in/h; wind 29 / 39 mph sustained, gust 39 / 48 mph; visibility 1.2 / 0.3 mi; freezing at 32 °F.

## 5. UI

**`base.html`** loads, all from `/static/vendor/`:
- `webawesome/styles/webawesome.css`
- `<script type="module" src="…/webawesome/webawesome.loader.js">`
- `<script type="module" src="…/datastar/datastar.js">`
- `leaflet/leaflet.css`
- `leaflet/leaflet.js`
- `/static/route-map.js` (module)

**Form** (`index.html`):
- `#stops` (2 rows), then `<wa-button id="add-stop" data-on:click="@get('/frag/stop-row')">Add stop</wa-button>`.
- Interval control: `<wa-select data-bind:interval>` with options 30/60/90/120 ("Waypoint every … min").
- Plan button: `<wa-button variant="brand" data-on:click="@get('/frag/result')" data-indicator:planning>Plan</wa-button>`, followed by `<wa-spinner data-show="$planning">`.
- `<div id="form-errors">`, `<div id="route-result">`.

**Result fragment** (`_result.html`, root `<div id="route-result">`):
- Status strip: generated time, oldest cache age among responses used (`stale` → amber badge "stale data"), Refresh button `@get('/frag/result?refresh=1')`.
- Legend: four chips (Clear green, Warning amber, Severe red, No data grey) and the converted thresholds.
- Map element: `<route-map data-route='{json}' data-attr:selected="$selected" data-on:row-select="$selected = evt.detail.row" style="height:420px">`.
  - JSON shape: `{"pieces":[{"row":int,"level":str,"coords":[[lat,lon],…]}],"stops":[{"row":int,"n":int,"lat":float,"lon":float,"level":str,"title":str}]}`.
- Itinerary table, one `<tr>` per row:
  - `data-on:click="$selected = {row}"`, `data-class:selected="$selected == {row}"`.
  - Columns: #, place (stop rows bold with resolved display; waypoint rows `<span id="label-{row}">`), local arrival (and leave for stops), icon `<wa-icon src="/static/vendor/icons/{name}.svg" label="{condition}">`, temp °F, precip in/h (+ %), wind/gust mph, rule badges `<wa-badge variant=warning|danger>`.
- Detail panels: one `<div data-show="$selected == {row}">` per row, all rendered (no extra endpoint). Each shows:
  - the window hours table;
  - triggered rules as sentences, e.g. "Gusts 51 mph at 16:00 MST near Ash Fork";
  - leg distance (mi) and drive time to this row.

**WMO code → icon:**

| Codes | Icon |
|---|---|
| 0 | sun |
| 1, 2 | cloud-sun |
| 3 | cloud |
| 45, 48 | cloud-fog |
| 51, 53, 55, 56, 57 | cloud-drizzle |
| 61, 63, 65, 66, 67, 80, 81, 82 | cloud-rain |
| 71, 73, 75, 77, 85, 86 | cloud-snow |
| 95, 96, 99 | cloud-lightning |

Badges use `wind` (wind rule) and `eye-off` (visibility).

**`static/route-map.js`** defines `customElements.define('route-map', …)`:
- `observedAttributes = ['data-route', 'selected']`.
- `connectedCallback`:
  - create `L.map(this)`;
  - add the tile layer `https://tile.openstreetmap.org/{z}/{x}/{y}.png` with attribution `© OpenStreetMap contributors`;
  - render.
- Render (on connect and on `data-route` change):
  - clear the layer group;
  - one `L.polyline` per piece (weight 5; colors: clear `#2e7d32`, warning `#f9a825`, severe `#c62828`, nodata `#9e9e9e`);
  - one `L.circleMarker` per stop (radius 9, fill = level color, permanent tooltip with stop number `n`);
  - `fitBounds` over all coords.
- Clicking a piece or marker dispatches `new CustomEvent('row-select', {detail: {row}, bubbles: true})`.
- On `selected` change: reset all weights to 5, set that row's piece weight to 9, `panTo` its midpoint.
- `disconnectedCallback`: `map.remove()`.

**Errors:** every pipeline failure ends in a patched callout in `#form-errors`, and `#route-result` is left untouched (never half-drawn). `UpstreamError` → `"{service} is unavailable right now; try again."` with a Retry button doing `@get('/frag/result')`.

## 6. Milestones

1. **Scaffold**
   - `uv init --package journey-weather`, deps from §2, `app.py` serving `index.html`, static mount, `GET /healthz` → `{"ok": true}`.
   - `scripts/vendor.sh` builds `static/vendor/`:
     - `npm pack @awesome.me/webawesome@3.14.0` → extract `package/dist-cdn/*` into `vendor/webawesome/`;
     - `npm pack leaflet@1.9.4` → `package/dist/{leaflet.js,leaflet.css,images/}` into `vendor/leaflet/`;
     - `npm pack lucide-static@1.53.0` → the 10 icons from §2 into `vendor/icons/`;
     - curl the Datastar bundle into `vendor/datastar/datastar.js`.
   - *Verify:* the page loads with zero 404s in the server log; a `<wa-button>` renders styled.
2. **Cache + clients**: `cache.py`, `geocode.py`, `routing.py`, `labels.py`.
   - *Verify:* pytest with recorded fixtures (geocoding the 5 example cities; the 5-stop OSRM response); a live smoke script prints the resolved stops and leg durations 6.06 / 4.24 / 2.59 / 2.15 h.
3. **Schedule + sampling** (pure).
   - *Verify:* pytest — leg split offsets (6620 = 4266 + 2353 + 1 case); arrival monotonicity; leave-before-arrival error; waypoint count per interval; the Kanab (America/Denver) → Flagstaff (America/Phoenix) local-clock case from §7.
4. **Weather + severity + units**.
   - *Verify:* pytest — each rule in isolation at warning/severe boundaries; null-input skip; window indexing; horizon pre-check; unit conversions. A live smoke script prints the §7 itinerary table.
5. **Endpoints**: `/frag/stop-row`, `/frag/result` (signals via `read_signals`, SSE via `datastar_response` + `SSE.patch_elements`; confirm the `selector`/`mode` keyword spelling against the installed `datastar_py` source), label streaming.
   - *Verify:* `curl -N 'http://127.0.0.1:8000/frag/result?datastar=<urlencoded signals JSON>'` shows a `datastar-patch-elements` event for `#route-result`, then `#label-*` events.
6. **UI**: templates, `route-map.js`, CSS.
   - *Verify:* scripted browser session on the real page: enter the §7 trip, click Plan, see the spinner, then the map with colored pieces and 5 markers; clicking a row highlights the piece and shows the detail; screenshots for the idle, loading, result, validation-error, and horizon-error states.
7. **Hardening**: retry/backoff paths, `nodata` rows, stale-cache badge, Add stop at 12 disabled.
   - *Verify:* fault-injection tests (mock transport returning 500/429/timeout) for each upstream.

## 7. Worked example

Layton, UT → Kanab, UT → Flagstaff, AZ → Phoenix, AZ → Tucson, AZ (OSRM, verified):

| Leg | Roads | Drive | Distance |
|---|---|---|---|
| Layton → Kanab | I-15, SR-20, US-89 | 6.06 h | 537 km (334 mi) |
| Kanab → Flagstaff | US-89 | 4.24 h | 332 km (206 mi) |
| Flagstaff → Phoenix | I-17 | 2.59 h | 231 km (144 mi) |
| Phoenix → Tucson | I-17, I-10 | 2.15 h | 187 km (116 mi) |
| **Total** | | **15.04 h** | **1,288 km (800 mi)** |

- Test input: Layton leave `D 07:00`; Kanab leave `D+1 08:00`; Flagstaff leave `D+2 09:00`; Phoenix blank; Tucson final.
- Kanab is `America/Denver` (MDT, UTC−6); Flagstaff, Phoenix, and Tucson are `America/Phoenix` (MST, UTC−7, no DST). Leaving Kanab 08:00 MDT arrives in Flagstaff ≈ 11:14 MST, i.e. the local clock moves back 1 h.
- At the default 60-min interval: legs get 6 + 4 + 3 + 2 = 15 waypoints, so 20 rows and one bulk weather request with 20 locations.
- The route is the primary fixture for Milestones 3, 4, and 6: it covers a time-zone discontinuity, the 2,100 m Flagstaff elevation (winter-road rule), and the desert night-to-day temperature swing.

## 8. Risks / watch-outs

- **Public OSRM demo** has no SLA: cached 7 d, retries with backoff. If it is persistently down, the plan fails with an `UpstreamError` callout (no alternative router in v1).
- **Open-Meteo free tier**: undocumented locations-per-request cap; handled by chunked fallback (§4.6). The 16-day horizon is a hard limit, enforced by the §4.6 pre-check.
- **Nominatim policy**: 1 req/s and identifying User-Agent are mandatory; enforced in `labels.py`. Labels are cosmetic, so their failure never fails a plan.
- **OSM tile policy**: low-volume interactive use with attribution is fine; heavy use would need a tile provider.
- **ETA accuracy**: no traffic or live speeds; stated in the UI.
- **Webawesome autoloader**: assumed to discover `wa-*` elements inserted by Datastar patches. If patched elements stay undefined (no `:defined` styling), switch `base.html` to import `webawesome.js` (registers every component) instead of the loader.
- **Timezone/DST edges**: all arithmetic in UTC epoch seconds; local times only for display, using `utc_offset_seconds` (weather) and `zoneinfo` (input).

## 9. Confirmed decisions

- Hard 16-day forecast limit; no climate fallback.
- Route = ordered stop rows (city name or `Name;lat;lon`), each with an optional leave-at; no presets.
- Weather at each point's arrival hour; the flag is the worst value in the [−1 h, +2 h] window.
- Severity thresholds baked in; display units imperial.
- Leaflet + OSM tiles via one `<route-map>` custom element; all other interactivity is Datastar attributes and server-rendered fragments.
- SQLite HTTP cache only; no accounts, no saved or shared routes, no live traffic, no stop reordering or removal UI (blank rows are ignored).
