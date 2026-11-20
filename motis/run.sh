#!/usr/bin/env bash
# Local MOTIS 2 test instance for this feed (Linux / macOS).
#
# Usage:
#   ./motis/run.sh                                   # static GTFS only
#   RT_URL=https://<railway-domain>/trip_updates.pb ./motis/run.sh
#
# Optional env vars: OSM_URL, GTFS_URL, RT_URL, MOTIS_TARGET (e.g. macos-arm64), PORT
# Then open http://localhost:8080
set -euo pipefail

NAME="zadar"
OSM_URL="${OSM_URL:-https://download.geofabrik.de/europe/croatia-latest.osm.pbf}"
GTFS_URL="${GTFS_URL:-https://github.com/hoermalmeister/zadar_gtfs/raw/main/zadar_gtfs.zip}"
RT_URL="${RT_URL:-}"
PORT="${PORT:-8080}"

if [ -z "${MOTIS_TARGET:-}" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) MOTIS_TARGET="linux-amd64" ;;
    Linux-aarch64) MOTIS_TARGET="linux-arm64" ;;
    Darwin-arm64) MOTIS_TARGET="macos-arm64" ;;
    Darwin-x86_64) MOTIS_TARGET="macos-x86_64" ;;
    *) echo "Unknown platform, set MOTIS_TARGET (see MOTIS release list)"; exit 1 ;;
  esac
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/work"
mkdir -p "$WORK"
cd "$WORK"

if [ ! -x ./motis ]; then
  echo "== Downloading MOTIS ($MOTIS_TARGET)"
  curl -fL -o motis.tar.bz2 "https://github.com/motis-project/motis/releases/latest/download/motis-${MOTIS_TARGET}.tar.bz2"
  tar xf motis.tar.bz2 && rm motis.tar.bz2
fi

OSM_FILE="$(basename "$OSM_URL")"
if [ ! -f "$OSM_FILE" ] || [ -n "$(find "$OSM_FILE" -mtime +30 2>/dev/null)" ]; then
  echo "== Downloading map $OSM_URL"
  curl -fL -o "$OSM_FILE" "$OSM_URL"
fi

echo "== Downloading current GTFS"
curl -fL -o "${NAME}_gtfs.zip" "$GTFS_URL"

echo "== Writing config.yml"
{
  cat <<EOF
server:
  port: ${PORT}
osm: ${OSM_FILE}
tiles:
  profile: tiles-profiles/full.lua
  db_size: 274877906944
  flush_threshold: 100000
timetable:
  first_day: TODAY
  num_days: 60
  railviz: true
  with_shapes: true
  update_interval: 30
  http_timeout: 30
  datasets:
    ${NAME}:
      path: ${NAME}_gtfs.zip
EOF
  if [ -n "$RT_URL" ]; then
    cat <<EOF
      rt:
        - url: ${RT_URL}
EOF
  fi
  cat <<EOF
street_routing: true
osr_footpath: true
geocoding: true
reverse_geocoding: true
EOF
} > config.yml

echo "== Import (first run takes a while, later runs only redo what changed)"
./motis import

echo "== Server: http://localhost:${PORT}"
exec ./motis server
