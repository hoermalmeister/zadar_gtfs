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

## License

This data is provided under the **CC BY-NC-ND 4.0** license.

[![CC BY-NC-ND 4.0](https://i.creativecommons.org/l/by-nc-nd/4.0/88x31.png)](http://creativecommons.org/licenses/by-nc-nd/4.0/)

* **BY (Attribution):** You must give appropriate credit to the author.
* **NC (NonCommercial):** You may not use the material for commercial purposes.
* **ND (NoDerivatives):** If you remix, transform, or build upon the material, you may not distribute the modified material.
