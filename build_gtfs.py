"""
Sestaví statický GTFS (zadar_gtfs.zip) z uložených denních snímků.

Pro každý den v horizontu HORIZON_DAYS se vybere snímek:
  1. přesně ten den, pokud byl stažen,
  2. jinak poslední stažený stejný den v týdnu (bez svátků),
     u pracovních dnů případně libovolný poslední pracovní den,
  3. svátky a neděle se berou z posledního snímku neděle/svátku.
Jízdy se slučují podle trip_id (identický vzor = stejný spoj), dny provozu
jdou do calendar_dates.txt.
"""

import csv
import os
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import zadar_common as zc

TT_DIR = os.path.join("data", "timetables")
STOPS_PATH = os.path.join("data", "stops.json")
OUT_ZIP = "zadar_gtfs.zip"
HORIZON_DAYS = 45
TZ = ZoneInfo("Europe/Zagreb")

AGENCY = {
    "agency_id": "LIBURNIJA",
    "agency_name": "Liburnija d.o.o. Zadar",
    "agency_url": "https://www.liburnija-zadar.hr",
    "agency_timezone": "Europe/Zagreb",
    "agency_lang": "hr",
}


def available_snapshots():
    snaps = {}
    for name in os.listdir(TT_DIR):
        try:
            snaps[date.fromisoformat(name[:10])] = os.path.join(TT_DIR, name)
        except ValueError:
            pass
    return snaps


def day_kind(d):
    if zc.is_holiday(d) or d.weekday() == 6:
        return "sunday"
    if d.weekday() == 5:
        return "saturday"
    return "weekday"


def pick_snapshot(target, snaps):
    """Vrátí (datum snímku, způsob výběru) nebo (None, důvod)."""
    if target in snaps:
        return target, "přesný"
    past = sorted((d for d in snaps if d < target), reverse=True)
    kind = day_kind(target)
    if kind == "weekday":
        for d in past:
            if day_kind(d) == "weekday" and d.weekday() == target.weekday():
                return d, "stejný den v týdnu"
        for d in past:
            if day_kind(d) == "weekday":
                return d, "jiný pracovní den"
    else:
        for d in past:
            if day_kind(d) == kind:
                return d, "stejný typ dne"
    return None, "chybí snímek tohoto typu dne"


def write_csv(zf, name, header, rows):
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    zf.writestr(name, buf.getvalue())


def main():
    stops = zc.load_stops(STOPS_PATH)
    snaps = available_snapshots()
    if not snaps:
        raise SystemExit("V data/timetables nejsou žádné snímky – spusť nejdřív fetch.py.")
    print(f"Snímků k dispozici: {len(snaps)} ({min(snaps)} – {max(snaps)})")

    today = datetime.now(TZ).date()
    parsed = {}
    trips = {}                       # trip_id -> journey
    trip_dates = defaultdict(set)    # trip_id -> {date}
    dropped_all = set()
    plan = Counter()

    for offset in range(HORIZON_DAYS):
        target = today + timedelta(days=offset)
        src, how = pick_snapshot(target, snaps)
        plan[how] += 1
        if src is None:
            print(f"   {target} ({day_kind(target)}): {how} – bez provozu ve feedu")
            continue
        if src not in parsed:
            sd, jnys, dropped = zc.parse_snapshot(zc.load_json_any(snaps[src]), stops)
            parsed[src] = jnys
            dropped_all |= dropped
        for j in parsed[src]:
            trips.setdefault(j["trip_id"], j)
            trip_dates[j["trip_id"]].add(target)

    print(f"Výběr snímků pro {HORIZON_DAYS} dní: {dict(plan)}")
    if dropped_all:
        print(f"Varování: {len(dropped_all)} zastávek z jízdního řádu chybí v seznamu "
              f"zastávek a byly vynechány: {sorted(map(str, dropped_all))[:15]}")

    # služby = skupiny trip se stejnou množinou dní
    services = {}
    for tid in sorted(trips, key=lambda t: (min(trip_dates[t]), t)):
        key = frozenset(trip_dates[tid])
        services.setdefault(key, f"S{len(services) + 1:03d}")

    # linky
    variants = defaultdict(Counter)
    for j in trips.values():
        variants[j["short_name"]][j["line_name"]] += 1

    def route_id(short):
        return f"L_{zc.safe_id(short)}"

    def natural(s):
        return (0, int(s), s) if s.isdigit() else (1, 0, s)

    route_rows = []
    for short in sorted(variants, key=natural):
        name = variants[short].most_common(1)[0][0]
        long_name = name[len(short):].strip(" -") if name.startswith(short) else name
        route_rows.append([route_id(short), AGENCY["agency_id"], short, long_name, 3])

    used_stops = {s for j in trips.values() for s, _, _ in j["calls"]}
    stop_rows = []
    for sid in sorted(used_stops):
        s = stops[sid]
        stop_rows.append([sid, s.get("Code") or "", s["Name"], s["Latitude"], s["Longitude"],
                          s.get("WheelchairBoarding") or 0])

    trip_rows, st_rows = [], []
    for tid in sorted(trips):
        j = trips[tid]
        sid = services[frozenset(trip_dates[tid])]
        headsign = j["direction"] or j["line_name"]
        trip_rows.append([route_id(j["short_name"]), sid, tid, headsign])
        for seq, (stop_id, a, d) in enumerate(j["calls"], 1):
            st_rows.append([tid, zc.fmt_time(a), zc.fmt_time(d), stop_id, seq])

    cd_rows = []
    for days, sid in sorted(services.items(), key=lambda kv: kv[1]):
        for d in sorted(days):
            cd_rows.append([sid, d.strftime("%Y%m%d"), 1])

    all_days = sorted({d for days in services for d in days})
    feed_rows = [["Petr Novak", "https://github.com/hoermalmeister/zadar_gtfs/", "hr",
                  all_days[0].strftime("%Y%m%d"), all_days[-1].strftime("%Y%m%d"),
                  today.strftime("%Y%m%d"),
                  "https://github.com/hoermalmeister/zadar_gtfs/issues"]]

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        write_csv(zf, "agency.txt", list(AGENCY), [list(AGENCY.values())])
        write_csv(zf, "routes.txt",
                  ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
                  route_rows)
        write_csv(zf, "stops.txt",
                  ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon", "wheelchair_boarding"],
                  stop_rows)
        write_csv(zf, "trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign"], trip_rows)
        write_csv(zf, "stop_times.txt",
                  ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"], st_rows)
        write_csv(zf, "calendar_dates.txt", ["service_id", "date", "exception_type"], cd_rows)
        write_csv(zf, "feed_info.txt",
                  ["feed_publisher_name", "feed_publisher_url", "feed_lang",
                   "feed_start_date", "feed_end_date", "feed_version", "feed_contact_url"], feed_rows)

    print(f"Hotovo: {OUT_ZIP} – linek {len(route_rows)}, zastávek {len(stop_rows)}, "
          f"spojů {len(trip_rows)}, služeb {len(services)}, "
          f"platnost {all_days[0]} – {all_days[-1]}")


if __name__ == "__main__":
    main()
