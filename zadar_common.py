"""
Sdílené jádro pro statický GTFS i GTFS-RT (Zadar – Liburnija / LIT Transit).

Statický feed i realtime server musí počítat trip_id úplně stejně, jinak by
GTFS-RT neodkazoval na existující spoje. Proto je celá logika tady.
"""

import gzip
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

API_BASE = "https://publictransport.service.zadarcity.hr/api"
STOPS_URL = f"{API_BASE}/Liburnija/stops"
TIMETABLE_URL = f"{API_BASE}/LitTrasnit/stop/timeTable"


# --- API ---------------------------------------------------------------------
def api_headers():
    key = os.environ.get("ZADAR_API_KEY")
    if not key:
        raise SystemExit("Chybí proměnná prostředí ZADAR_API_KEY.")
    return {
        "accept": "*/*",
        "authorization": f"ApiKey {key}",
        "content-type": "application/json",
        "origin": "https://zadarcity.hr",
        "referer": "https://zadarcity.hr/",
        "user-agent": "zadar_gtfs (+https://github.com/hoermalmeister/zadar_gtfs)",
    }


def fetch_json(url, timeout=120):
    r = requests.get(url, headers=api_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


# --- Načítání uložených dat ----------------------------------------------------
def load_json_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_stops(path):
    data = load_json_any(path)
    return {s["Id"]: s for s in data["TsStops"]}


# --- Parsování SIRI ProductionTimetable ---------------------------------------
def _parse_ts(txt):
    return datetime.fromisoformat(txt) if txt else None


def snapshot_service_date(siri):
    """Provozní den snímku = nejčastější datum odjezdů."""
    counts = defaultdict(int)
    for jny in iter_raw_journeys(siri):
        for call in jny["calls"]:
            ts = call.get("AimedDepartureTime") or call.get("AimedArrivalTime")
            if ts:
                counts[ts[:10]] += 1
    if not counts:
        raise ValueError("Snímek neobsahuje žádné časy.")
    return date.fromisoformat(max(counts, key=counts.get))


def iter_raw_journeys(siri):
    sd = siri["Siri"]["ServiceDelivery"]
    for delivery in sd.get("ProductionTimetableDelivery", []):
        for frame in delivery.get("DatedTimetableVersionFrame", []):
            for vj in frame.get("DatedVehicleJourney", []):
                yield {
                    "line_ref": str(frame.get("LineRef", "")).strip(),
                    "line_name": (frame.get("PublishedLineName") or "").strip(),
                    "direction": (frame.get("DirectionRef") or "").strip(),
                    "vj_ref": str(vj.get("VehicleJourneyRef", "")),
                    "calls": [c["DatedCall"] for c in vj.get("DatedCalls", [])],
                }


def _secs_since(service_date, ts):
    """Sekundy od půlnoci provozního dne (místní čas z ISO řetězce, může být > 24 h)."""
    local = ts.replace(tzinfo=None)
    days = (local.date() - service_date).days
    return days * 86400 + local.hour * 3600 + local.minute * 60 + local.second


def parse_snapshot(siri, stops):
    """
    Vrátí (service_date, journeys, dropped_stop_ids).
    journey = {line_ref, line_name, short_name, direction, vj_ref,
               calls: [(stop_id, arr_s, dep_s, order)], raw_keys, key, trip_id}
    order = pořadí zastávky ve zdrojové jízdě (1-based) = SIRI Order = GTFS stop_sequence
    """
    service_date = snapshot_service_date(siri)
    journeys, dropped = [], set()

    for raw in iter_raw_journeys(siri):
        calls, raw_keys = [], []
        for order, c in enumerate(raw["calls"], 1):
            ref = c.get("StopPointRef", "")
            try:
                stop_id = int(ref.split("/")[-1])
            except ValueError:
                dropped.add(ref)
                continue
            if stop_id not in stops:
                dropped.add(stop_id)
                continue
            arr, dep = _parse_ts(c.get("AimedArrivalTime")), _parse_ts(c.get("AimedDepartureTime"))
            if not arr and not dep:
                continue
            arr_s = _secs_since(service_date, arr or dep)
            dep_s = _secs_since(service_date, dep or arr)
            # klíč pro párování realtime (pořadí, zastávka, plánované časy před úpravami)
            raw_keys.append((order, stop_id, arr_s, dep_s))
            if dep_s < arr_s:          # pevný odjezd časového bodu dřív než modelový příjezd
                arr_s = dep_s
            calls.append((stop_id, arr_s, dep_s, order))

        # časy musí být neklesající i mezi zastávkami
        fixed, last = [], -1
        for stop_id, a, d, order in calls:
            a = max(a, last)
            d = max(d, a)
            fixed.append((stop_id, a, d, order))
            last = d
        if len(fixed) < 2:
            continue

        short = raw["line_name"].split()[0] if raw["line_name"] else raw["line_ref"]
        journeys.append({**raw, "short_name": short, "calls": fixed, "raw_keys": raw_keys})

    assign_trip_ids(journeys)
    return service_date, journeys, dropped


def assign_trip_ids(journeys):
    """
    Stabilní trip_id napříč dny: linka + směr + výchozí zastávka + čas odjezdu
    z výchozí zastávky (ten je v jízdním řádu pevný, na celé minuty; mezilehlé
    časy se den ode dne mění o sekundy až minuty, proto do ID nevstupují).
    Příklad: 3_4_0915_1545. Jízdy se stejným začátkem se rozliší pořadím
    podle struktury (počet a sled zastávek), pak podle VehicleJourneyRef.
    """
    groups = defaultdict(list)
    for j in journeys:
        first_stop, _a, first_dep, _o = j["calls"][0]
        j["key"] = (j["line_ref"], j["direction"], first_stop, first_dep)
        groups[j["key"]].append(j)
    for (line_ref, _direction, first_stop, first_dep), items in groups.items():
        items.sort(key=lambda j: (len(j["calls"]), tuple(c[0] for c in j["calls"]), j["vj_ref"]))
        hhmm = f"{first_dep // 3600:02d}{(first_dep % 3600) // 60:02d}"
        for occ, j in enumerate(items):
            suffix = f"_{occ + 1}" if occ else ""
            j["trip_id"] = f"{safe_id(j['short_name'])}_{line_ref}_{hhmm}_{first_stop}{suffix}"


def safe_id(txt):
    return "".join(ch if ch.isalnum() else "_" for ch in txt)


def fmt_time(secs):
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


# --- Státní svátky Chorvatska ----------------------------------------------------
def easter(year):
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def croatian_holidays(year):
    fixed = ["01-01", "01-06", "05-01", "05-30", "06-22", "08-05",
             "08-15", "11-01", "11-18", "12-25", "12-26"]
    days = {date.fromisoformat(f"{year}-{md}") for md in fixed}
    e = easter(year)
    days |= {e, e + timedelta(days=1), e + timedelta(days=60)}  # Velikonoce, Vel. pondělí, Boží tělo
    return days


def is_holiday(d):
    return d in croatian_holidays(d.year)
