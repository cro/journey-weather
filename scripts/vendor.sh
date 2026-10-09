#!/usr/bin/env bash
# Vendor frontend assets into src/journey_weather/static/vendor/ (PLAN.md §2, §6.1).
# Idempotent: rebuilds the vendor tree from scratch. Requires: npm, curl.
set -euo pipefail

cd "$(dirname "$0")/.."
VENDOR=src/journey_weather/static/vendor
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

rm -rf "$VENDOR"
mkdir -p "$VENDOR/webawesome" "$VENDOR/leaflet" "$VENDOR/icons" "$VENDOR/datastar"

unpack() { # unpack <pkg-spec> <dest-dir-name>
  local spec="$1" dir="$2"
  (cd "$TMP" && npm pack --silent "$spec" >/dev/null)
  mkdir -p "$TMP/$dir"
  (cd "$TMP/$dir" && tar -xzf "$TMP"/*.tgz)
  rm -f "$TMP"/*.tgz
}

# Webawesome: whole dist-cdn tree (loader imports relative chunks/*, PLAN.md §2).
unpack '@awesome.me/webawesome@3.14.0' webawesome
cp -r "$TMP/webawesome/package/dist-cdn/." "$VENDOR/webawesome/"

# Leaflet: dist build + marker images.
unpack 'leaflet@1.9.4' leaflet
cp "$TMP/leaflet/package/dist/leaflet.js" \
   "$TMP/leaflet/package/dist/leaflet.css" "$VENDOR/leaflet/"
cp -r "$TMP/leaflet/package/dist/images" "$VENDOR/leaflet/images"

# Lucide icons (ISC): the ten WMO/badge icons from PLAN.md §2.
unpack 'lucide-static@1.53.0' lucide
for icon in sun cloud-sun cloud cloud-fog cloud-drizzle cloud-rain \
            cloud-snow cloud-lightning wind eye-off; do
  cp "$TMP/lucide/package/icons/$icon.svg" "$VENDOR/icons/$icon.svg"
done

# Datastar bundle (jsdelivr; the npm package `datastar` is an unrelated SQL lib).
curl -fsSL 'https://cdn.jsdelivr.net/gh/starfederation/datastar@v1.0.4/bundles/datastar.js' \
  -o "$VENDOR/datastar/datastar.js"

echo "Vendored to $VENDOR:"
du -sh "$VENDOR"/*
