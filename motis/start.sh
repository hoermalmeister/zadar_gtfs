#!/usr/bin/env bash
# MOTIS 2 as a long-running service (Railway / Docker).
#
# 1. downloads the OSM extract and clips it to the area of the GTFS stops,
# 2. downloads the current GTFS from GitHub,
# 3. imports and starts the MOTIS server (web UI + API on $PORT),
# 4. every REFRESH_HOURS checks for a new GTFS; if it changed, re-imports
#    (only the timetable step is recomputed) and restarts the server.
#
# Environment:
#   GTFS_URL        GTFS zip (required)
#   OSM_URL         OSM .osm.pbf extract (required)
#   NAME            dataset name (default: feed)
#   RT_URL          GTFS-RT TripUpdates URL (optional)
#   DATA_DIR        persistent storage (default: /data – mount a volume here)
#   CLIP            1 = clip the map to the GTFS stops bounding box (default 1)
#   CLIP_MARGIN     margin around the stops in degrees (default 0.15)
#   REFRESH_HOURS   how often to check for a new GTFS (default 6)
#   OSM_MAX_AGE_DAYS  re-download the map after this many days (default 30)
#   PORT            set by Railway
set -euo pipefail

: "${GTFS_URL:?GTFS_URL is required}"
: "${OSM_URL:?OSM_URL is required}"
NAME="${NAME:-feed}"
RT_URL="${RT_URL:-}"
DATA_DIR="${DATA_DIR:-/data}"
CLIP="${CLIP:-1}"
CLIP_MARGIN="${CLIP_MARGIN:-0.15}"
REFRESH_HOURS="${REFRESH_HOURS:-6}"
OSM_MAX_AGE_DAYS="${OSM_MAX_AGE_DAYS:-30}"
PORT="${PORT:-8080}"
MOTIS_HOME="${MOTIS_HOME:-$(cd "$(dirname "$0")" && pwd)}"

IN="$DATA_DIR/input"
GTFS="$IN/${NAME}_gtfs.zip"
REGION="$IN/region.osm.pbf"
mkdir -p "$IN"
cd "$MOTIS_HOME"

log() { echo "[start] $(date -u +%FT%TZ) $*"; }

fetch_gtfs() {   # returns 0 if the GTFS changed
  curl -fsSL --retry 3 -o "$GTFS.tmp" "$GTFS_URL" || { log "GTFS download failed"; rm -f "$GTFS.tmp"; return 1; }
  if [ -f "$GTFS" ] && cmp -s "$GTFS" "$GTFS.tmp"; then
    rm -f "$GTFS.tmp"; return 1
  fi
  mv "$GTFS.tmp" "$GTFS"; return 0
}

stops_bbox() {   # minlon,minlat,maxlon,maxlat of GTFS stops + margin
  python3 - "$GTFS" "$CLIP_MARGIN" <<'PY'
import csv, io, sys, zipfile
z = zipfile.ZipFile(sys.argv[1]); m = float(sys.argv[2])
rows = list(csv.DictReader(io.TextIOWrapper(z.open("stops.txt"), encoding="utf-8-sig")))
lat = [float(r["stop_lat"]) for r in rows if r.get("stop_lat")]
lon = [float(r["stop_lon"]) for r in rows if r.get("stop_lon")]
print(f"{min(lon)-m:.5f},{min(lat)-m:.5f},{max(lon)+m:.5f},{max(lat)+m:.5f}")
PY
}

prepare_osm() {
  if [ -f "$REGION" ] && [ -z "$(find "$REGION" -mtime +"$OSM_MAX_AGE_DAYS" 2>/dev/null)" ]; then
    return
  fi
  log "downloading map $OSM_URL"
  curl -fSL --retry 3 -o "$IN/source.osm.pbf" "$OSM_URL"
  if [ "$CLIP" = "1" ]; then
    BBOX="$(stops_bbox)"
    log "clipping map to $BBOX"
    osmium extract --overwrite -s smart -b "$BBOX" -o "$REGION" "$IN/source.osm.pbf"
    rm -f "$IN/source.osm.pbf"
  else
    mv "$IN/source.osm.pbf" "$REGION"
  fi
}

write_config() {
  {
    cat <<EOF
server:
  port: ${PORT}
  web_folder: ${MOTIS_HOME}/ui
osm: ${REGION}
tiles:
  profile: ${MOTIS_HOME}/tiles-profiles/full.lua
  db_size: 10737418240
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
      path: ${GTFS}
EOF
    if [ -n "$RT_URL" ]; then
      printf '      rt:\n        - url: %s\n' "$RT_URL"
    fi
    cat <<EOF
street_routing: true
osr_footpath: true
geocoding: true
reverse_geocoding: true
EOF
  } > "$DATA_DIR/config.yml"
}

run_import() {
  log "import"
  ./motis import -c "$DATA_DIR/config.yml" -d "$DATA_DIR/data"
}

SERVER_PID=""
start_server() {
  log "server on port $PORT"
  ./motis server -d "$DATA_DIR/data" &
  SERVER_PID=$!
}
stop_server() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID"; wait "$SERVER_PID" || true
  fi
  SERVER_PID=""
}
trap 'stop_server; exit 0' TERM INT

fetch_gtfs || true
prepare_osm
write_config
run_import
start_server

last_check=$(date +%s)
while true; do
  sleep 60
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    log "server exited, stopping container"; exit 1
  fi
  now=$(date +%s)
  if [ $((now - last_check)) -ge $((REFRESH_HOURS * 3600)) ]; then
    last_check=$now
    if fetch_gtfs; then
      log "new GTFS, re-importing"
      prepare_osm
      write_config
      stop_server
      run_import
      start_server
    fi
  fi
done
