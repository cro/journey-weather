"""Constants for journey-weather (see PLAN.md §3 package layout)."""

from pathlib import Path

# Identifying User-Agent required by the Nominatim usage policy (PLAN.md §2, §4.8).
USER_AGENT = "journey-weather/0.1"

MAX_STOPS = 12
# Upper bound for a stop's dwell time, minutes (PLAN.md §4.1).
MAX_DWELL_MIN = 1440

# Cache database (created on startup; gitignored).
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "journey.db"

# Cache TTLs in seconds (PLAN.md §3).
TTL_GEOCODE = 30 * 24 * 3600          # forward geocoding + override tz lookup
TTL_REVERSE = 30 * 24 * 3600          # Nominatim reverse labels
TTL_ROUTE = 7 * 24 * 3600             # OSRM
TTL_FORECAST = 3 * 3600               # Open-Meteo bulk forecast

HTTP_TIMEOUT_S = 15.0

# Retry policy (PLAN.md §3): up to 3 retries on 429/5xx/timeouts.
RETRY_MAX = 3
RETRY_BASE_SLEEP_S = 1.0              # 1 s, 2 s, 4 s (plus 0-250 ms jitter)
RETRY_JITTER_MAX_MS = 250

# Severity thresholds, metric units as fetched (PLAN.md §4.7).
RAIN_WARNING_MMH = 5.0
RAIN_SEVERE_MMH = 12.0
WIND_WARNING_KMH = 47.0
WIND_GUST_WARNING_KMH = 63.0
WIND_SEVERE_KMH = 63.0
WIND_GUST_SEVERE_KMH = 78.0
CAPE_WARNING = 1000.0
CAPE_SEVERE = 1500.0
PRECIP_THUNDER_WARNING_MMH = 0.5
PRECIP_THUNDER_SEVERE_MMH = 2.5
VIS_WARNING_M = 2000.0
VIS_SEVERE_M = 500.0
FREEZING_C = 0.0
THUNDERSTORM_CODES = frozenset({95, 96, 99})
WINTER_WARNING_CODES = frozenset({71, 73, 77, 85})
WINTER_SEVERE_CODES = frozenset({56, 57, 66, 67, 75, 86})

# Forecast horizon (PLAN.md §4.6): forecast_days max is 16; horizon check now + 15 d.
FORECAST_DAYS = 16
HORIZON_MARGIN_S = 2 * 3600
HORIZON_MAX_S = 15 * 24 * 3600
