"""
Stáhne aktuální zastávky a dnešní jízdní řád (SIRI ProductionTimetable)
a uloží je do data/. Spouští se denně přes GitHub Actions.

API vrací jízdní řád jen pro aktuální den, proto se snímky dnů ukládají
a build_gtfs.py z nich skládá feed na další týdny.
"""

import gzip
import json
import os
from datetime import date, timedelta

import zadar_common as zc

DATA_DIR = "data"
TT_DIR = os.path.join(DATA_DIR, "timetables")
KEEP_DAYS = 35          # kolik dní snímků držet v repozitáři


def main():
    os.makedirs(TT_DIR, exist_ok=True)

    print("1. Stahuji zastávky...")
    stops = zc.fetch_json(zc.STOPS_URL)
    if stops.get("Result") != "OK" or not stops.get("TsStops"):
        raise SystemExit(f"Neplatná odpověď zastávek: {str(stops)[:300]}")
    with open(os.path.join(DATA_DIR, "stops.json"), "w", encoding="utf-8") as fh:
        json.dump(stops, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"   -> {len(stops['TsStops'])} zastávek")

    print("2. Stahuji dnešní jízdní řád...")
    tt = zc.fetch_json(zc.TIMETABLE_URL, timeout=300)
    service_date = zc.snapshot_service_date(tt)
    n_jny = sum(1 for _ in zc.iter_raw_journeys(tt))
    if n_jny == 0:
        raise SystemExit("Jízdní řád neobsahuje žádné jízdy – snímek neukládám.")
    path = os.path.join(TT_DIR, f"{service_date.isoformat()}.json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(tt, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"   -> {n_jny} jízd pro {service_date} uloženo do {path}")

    print("3. Mažu staré snímky...")
    limit = date.today() - timedelta(days=KEEP_DAYS)
    for name in sorted(os.listdir(TT_DIR)):
        try:
            d = date.fromisoformat(name[:10])
        except ValueError:
            continue
        if d < limit:
            os.remove(os.path.join(TT_DIR, name))
            print(f"   -> smazán {name}")


if __name__ == "__main__":
    main()
