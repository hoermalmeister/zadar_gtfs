# Zadar GTFS Feed 🇭🇷

Automatically generated GTFS and GTFS-Realtime data for city, suburban and island bus lines in Zadar operated by Liburnija d.o.o. The static feed is updated every day.

## Feeds

| Feed | URL |
|---|---|
| GTFS (static) | [`zadar_gtfs.zip`](https://github.com/hoermalmeister/zadar_gtfs/raw/main/zadar_gtfs.zip) |
| GTFS-RT Vehicle Positions | `https://<railway-domain>/vehicle_positions.pb` |
| GTFS-RT Trip Updates | `https://<railway-domain>/trip_updates.pb` |

## How it works

The source API (zadarcity.hr) only provides the timetable for the current day. A GitHub Action therefore downloads a snapshot every night (`data/timetables/YYYY-MM-DD.json.gz`) and the feed is assembled from the collected snapshots for the next 45 days:

1. the exact day, if a snapshot exists,
2. weekdays: the latest snapshot of the same weekday, otherwise any other weekday,
3. Saturdays only from the latest Saturday, Sundays only from the latest Sunday,
4. Croatian public holidays from the latest holiday snapshot, otherwise from the latest Sunday.

Snapshots taken on a public holiday are never used for regular days.

Days without a suitable snapshot (e.g. the first weekend after setup) have no service in the feed until a matching snapshot is collected.

- `fetch.py` – downloads stops and today's timetable (needs `ZADAR_API_KEY`)
- `build_gtfs.py` – builds `zadar_gtfs.zip` from the stored snapshots
- `zadar_common.py` – shared parsing and `trip_id` logic (static and realtime use identical IDs). `trip_id` = line + variant + first departure + first stop (e.g. `3_4_0915_1545`) and stays the same every day, so realtime matches even an older copy of the static feed.
- `realtime/` – GTFS-RT server (SIRI StopMonitoring → GTFS-RT), deployed on Railway

## Setup

1. **Settings → Secrets and variables → Actions → New repository secret**: `ZADAR_API_KEY` = API key used by zadarcity.hr.
2. **Actions → Update GTFS Feed → Run workflow** once; afterwards it runs daily.

### GTFS-RT on Railway

1. Railway → **New Project → Deploy from GitHub repo** → `zadar_gtfs`.
2. **Settings → Build**: builder `Railpack`. **Settings → Deploy**: start command `python realtime/server.py`, healthcheck path `/healthz`.
3. **Variables**: `ZADAR_API_KEY`, optionally `POLL_INTERVAL` (default 30 s) and `SIRI_SM_URL` (default `/LitTrasnit/stop/announcement/nodeundefined`).
4. **Settings → Networking → Generate Domain**.

`/healthz` only checks that the server is running; `/status` reports data freshness and source errors.

### What the realtime feed contains

The source is SIRI StopMonitoring for the whole network (departures in the next ~45 minutes). Only some vehicles are actively tracked (`Monitored`), so:

- **Vehicle Positions** – vehicles that report a GPS position (plate number as vehicle id, next stop, bearing computed from consecutive positions),
- **Trip Updates** – tracked trips and trips whose predicted times differ from the schedule; expected arrival/departure for the upcoming stops.

Trips are matched to the static feed via today's timetable (`VehicleJourneyRef`), with a fallback on line + stop order + stop + scheduled time (±3 min).

## Local test with MOTIS

`motis/` contains a script that runs a local [MOTIS 2](https://github.com/motis-project/motis) routing server on this feed: it downloads MOTIS, the OpenStreetMap extract from Geofabrik and the current `zadar_gtfs.zip`, writes `config.yml` and starts the server.

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File motis\run.ps1
powershell -ExecutionPolicy Bypass -File motis\run.ps1 -RtUrl https://<railway-domain>/trip_updates.pb
```

```bash
# Linux / macOS
./motis/run.sh
RT_URL=https://<railway-domain>/trip_updates.pb ./motis/run.sh
```

Then open http://localhost:8080. Everything is stored in `motis/work/` (ignored by git). The first import of the Croatia extract takes a while; later runs only redo the steps whose inputs changed. MOTIS uses GTFS-RT **Trip Updates** only (vehicle positions are ignored), so pass the `trip_updates.pb` URL.

### MOTIS on Railway (web)

`motis/Dockerfile` + `motis/start.sh` run MOTIS as a public web service. On start it downloads the map, imports the current GTFS and serves the web UI and API. Every 6 hours it checks for a new GTFS and re-imports only the timetable.

Railway cannot reach download.geofabrik.de, so the map is prepared by the **Update OSM extract for MOTIS** workflow: it downloads the Croatia extract, clips it to the area of the GTFS stops and publishes it as the release asset `osm/region.osm.pbf` (monthly).

0. **Actions → Update OSM extract for MOTIS → Run workflow** once before the first deploy.
1. In the same Railway project: **+ New → GitHub Repo → `zadar_gtfs`** (a second service next to the realtime one).
2. **Variables**: `RAILWAY_DOCKERFILE_PATH` = `/motis/Dockerfile`, `PORT` = `8080`, optionally `RT_URL` = `https://<realtime-domain>/trip_updates.pb`.
3. **Volume**: attach a volume mounted at `/data` (map and import survive redeploys).
4. **Settings**: no healthcheck (the first import takes a few minutes); Watch Paths `/motis/**` so daily data commits do not rebuild the image.
5. **Networking → Generate Domain** (port 8080).

Optional variables: `REFRESH_HOURS` (6), `OSM_MAX_AGE_DAYS` (30), `OSM_URL` (another map source; set `CLIP=1` to clip it on start).

## License

This data is provided under the **CC BY-NC-ND 4.0** license.

[![CC BY-NC-ND 4.0](https://i.creativecommons.org/l/by-nc-nd/4.0/88x31.png)](http://creativecommons.org/licenses/by-nc-nd/4.0/)

* **BY (Attribution):** You must give appropriate credit to the author.
* **NC (NonCommercial):** You may not use the material for commercial purposes.
* **ND (NoDerivatives):** If you remix, transform, or build upon the material, you may not distribute the modified material.
