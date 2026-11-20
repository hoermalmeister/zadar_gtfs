"""
Převod SIRI StopMonitoring (LIT Transit, /stop/announcement) na GTFS-Realtime.

Endpoint /stop/announcement/nodeundefined vrací odjezdy z celé sítě na
nejbližších ~45 minut. Každá návštěva zastávky nese jízdu
(DatedVehicleJourneyRef), pořadí zastávky (Order), plánované (Aimed)
a očekávané (Expected) časy, u sledovaných jízd i polohu vozidla.

Párování na statický feed:
  1. DatedVehicleJourneyRef -> VehicleJourneyRef v dnešním jízdním řádu,
  2. záložně (linka, pořadí, zastávka, plánovaný čas) -> jízda v jízdním řádu.
"""

import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from google.transit import gtfs_realtime_pb2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zadar_common as zc  # noqa: E402

VP = gtfs_realtime_pb2.VehiclePosition


# --- Index dnešních jízd -------------------------------------------------------
def build_trip_index(timetable, stops):
    service_date, journeys, _ = zc.parse_snapshot(timetable, stops)
    by_vj, by_call = {}, {}
    for j in journeys:
        info = {"trip_id": j["trip_id"], "route_id": f"L_{zc.safe_id(j['short_name'])}",
                "start_s": j["calls"][0][2], "short_name": j["short_name"],
                "stops": {order: stop for stop, _a, _d, order in j["calls"]}}
        by_vj[j["vj_ref"]] = info
        for order, stop, arr_s, dep_s in j["raw_keys"]:
            by_call.setdefault((j["line_ref"], order, stop), []).append((arr_s, dep_s, info))
    return {"service_date": service_date, "by_vj": by_vj, "by_call": by_call}


MATCH_TOLERANCE_S = 180   # plánované časy se mezi zdroji/dny liší o sekundy až minuty


# --- Pomocné ---------------------------------------------------------------------
def parse_ts(txt):
    return datetime.fromisoformat(txt) if txt else None


def stop_id_of(ref):
    try:
        return int(str(ref).split("/")[-1])
    except ValueError:
        return None


def secs_in_service_day(ts, service_date):
    local = ts.replace(tzinfo=None)
    return ((local.date() - service_date).days * 86400
            + local.hour * 3600 + local.minute * 60 + local.second)


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)))


# --- Seskupení návštěv podle jízd ----------------------------------------------------
def group_journeys(siri_sm):
    """DatedVehicleJourneyRef -> {mvj, recorded, visits: [(order, stop_id, call)]}"""
    out = defaultdict(lambda: {"mvj": None, "recorded": None, "visits": []})
    sd = siri_sm.get("Siri", {}).get("ServiceDelivery", {})
    for delivery in sd.get("StopMonitoringDelivery", []) or []:
        for visit in delivery.get("MonitoredStopVisit", []) or []:
            mvj = visit.get("MonitoredVehicleJourney") or {}
            ref = str((mvj.get("FramedVehicleJourneyRef") or {}).get("DatedVehicleJourneyRef") or "")
            if not ref:
                continue
            call = mvj.get("MonitoredCall") or {}
            try:
                order = int(call.get("Order"))
            except (TypeError, ValueError):
                continue
            stop = stop_id_of(visit.get("MonitoringRef") or "")
            g = out[ref]
            # uchovej verzi s nenulovou polohou, pokud nějaká je
            loc = mvj.get("VehicleLocation") or {}
            if g["mvj"] is None or (loc.get("Latitude") and not (g["mvj"].get("VehicleLocation") or {}).get("Latitude")):
                g["mvj"] = mvj
            g["recorded"] = visit.get("RecordedAtTime") or g["recorded"]
            if all(o != order for o, _s, _c in g["visits"]):
                g["visits"].append((order, stop, call))
    for g in out.values():
        g["visits"].sort(key=lambda v: v[0])
    return out


def match(ref, g, index):
    info, how, _score = match_scored(ref, g, index)
    return info, how


def match_scored(ref, g, index):
    """Vrátí (info, způsob, skóre); vyšší skóre = jistější shoda."""
    if ref in index["by_vj"]:
        return index["by_vj"][ref], "vj_ref", (2, 0, 0)
    # záložně: nejbližší jízda stejné linky se stejnou zastávkou na stejném pořadí,
    # hlasování přes všechny návštěvy jízdy
    line = str(g["mvj"].get("LineRef", ""))
    votes, infos, diffs = Counter(), {}, defaultdict(list)
    for order, stop, call in g["visits"]:
        ts = parse_ts(call.get("AimedArrivalTime")) or parse_ts(call.get("AimedDepartureTime"))
        cands = index["by_call"].get((line, order, stop)) if ts and stop is not None else None
        if not cands:
            continue
        secs = secs_in_service_day(ts, index["service_date"])
        arr_s, _dep_s, info = min(cands, key=lambda c: abs(c[0] - secs))
        if abs(arr_s - secs) <= MATCH_TOLERANCE_S:
            votes[info["trip_id"]] += 1
            infos[info["trip_id"]] = info
            diffs[info["trip_id"]].append(abs(arr_s - secs))
    if votes:
        tid, n = votes.most_common(1)[0]
        return infos[tid], "call", (1, n, -sum(diffs[tid]) / len(diffs[tid]))
    return None, None, None


def resolve_matches(groups, index):
    """Každý trip smí dostat nejvýš jednu živou jízdu – vyhrává nejjistější shoda."""
    cands = []
    for ref, g in groups.items():
        info, how, score = match_scored(ref, g, index)
        if info:
            cands.append((score, ref, info, how))
    cands.sort(key=lambda c: c[0], reverse=True)
    taken, result = set(), {}
    for score, ref, info, how in cands:
        if info["trip_id"] in taken:
            result[ref] = (None, "conflict")
            continue
        taken.add(info["trip_id"])
        result[ref] = (info, how)
    return result


def deviation(call):
    for a, e in (("AimedArrivalTime", "ExpectedArrivalTime"), ("AimedDepartureTime", "ExpectedDepartureTime")):
        at, et = parse_ts(call.get(a)), parse_ts(call.get(e))
        if at and et:
            return int((et - at).total_seconds())
    return None


# --- Stavba feedů --------------------------------------------------------------------
def new_feed():
    fm = gtfs_realtime_pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    fm.header.timestamp = int(time.time())
    return fm


def build_feeds(siri_sm, index, last_positions=None):
    """
    Vrátí (vehicle_positions, trip_updates, stats).
    last_positions: dict vozidlo -> (lat, lon, bearing) z minulého cyklu (kvůli směru jízdy);
    funkce ho průběžně aktualizuje.
    """
    if last_positions is None:
        last_positions = {}
    vp, tu = new_feed(), new_feed()
    stats = Counter()
    now = int(time.time())
    start_date = index["service_date"].strftime("%Y%m%d")

    groups = group_journeys(siri_sm)
    matches = resolve_matches(groups, index)
    for ref, g in groups.items():
        mvj = g["mvj"]
        stats["journeys"] += 1
        info, how = matches.get(ref, (None, None))
        if not info:
            stats["conflict" if how == "conflict" else "unmatched"] += 1
            continue
        stats[f"match_{how}"] += 1
        monitored = bool(mvj.get("Monitored"))
        stats["monitored"] += monitored
        vehicle_ref = (mvj.get("VehicleRef") or "").strip()
        recorded = parse_ts(g["recorded"])
        ts = int(recorded.timestamp()) if recorded else now
        first_order, first_stop, first_call = g["visits"][0]

        def trip_descriptor(td):
            td.trip_id = info["trip_id"]
            td.route_id = info["route_id"]
            td.start_date = start_date
            td.start_time = zc.fmt_time(info["start_s"])

        # ---- VehiclePosition: jen jízdy se skutečnou polohou ----
        loc = mvj.get("VehicleLocation") or {}
        lat, lon = loc.get("Latitude") or 0, loc.get("Longitude") or 0
        if lat and lon:
            e = vp.entity.add()
            e.id = vehicle_ref or info["trip_id"]
            v = e.vehicle
            trip_descriptor(v.trip)
            v.position.latitude = float(lat)
            v.position.longitude = float(lon)
            prev = last_positions.get(e.id)
            bearing = prev[2] if prev else None
            if prev and dist_m(prev[0], prev[1], lat, lon) > 15:
                bearing = bearing_deg(prev[0], prev[1], lat, lon)
            if bearing is not None:
                v.position.bearing = float(bearing)
            last_positions[e.id] = (lat, lon, bearing)
            if vehicle_ref:
                v.vehicle.id = vehicle_ref
                v.vehicle.license_plate = vehicle_ref
            v.vehicle.label = info["short_name"]
            v.current_stop_sequence = first_order
            if first_stop is not None:
                v.stop_id = str(first_stop)
            v.current_status = VP.STOPPED_AT if first_call.get("VehicleAtStop") else VP.IN_TRANSIT_TO
            v.timestamp = ts
            stats["positions"] += 1

        # ---- TripUpdate: sledované jízdy a jízdy s predikcí odlišnou od plánu ----
        devs = [deviation(c) for _o, _s, c in g["visits"]]
        if not monitored and not any(d for d in devs if d):
            stats["schedule_only"] += 1
            continue
        e = tu.entity.add()
        e.id = info["trip_id"]
        t = e.trip_update
        trip_descriptor(t.trip)
        if vehicle_ref:
            t.vehicle.id = vehicle_ref
            t.vehicle.license_plate = vehicle_ref
        t.timestamp = ts
        for order, stop, call in g["visits"]:
            if info["stops"].get(order) != stop:
                stats["skipped_stop_updates"] += 1   # zastávka ve statickém feedu není
                continue
            ea = parse_ts(call.get("ExpectedArrivalTime"))
            ed = parse_ts(call.get("ExpectedDepartureTime")) or ea
            if not ea and not ed:
                continue
            ea = ea or ed
            ed = max(ed, ea)
            aa = parse_ts(call.get("AimedArrivalTime"))
            ad = parse_ts(call.get("AimedDepartureTime")) or aa
            stu = t.stop_time_update.add()
            stu.stop_sequence = order
            stu.stop_id = str(stop)
            stu.arrival.time = int(ea.timestamp())
            if aa:
                stu.arrival.delay = int((ea - aa).total_seconds())
            stu.departure.time = int(ed.timestamp())
            if ad:
                stu.departure.delay = int((ed - ad).total_seconds())
        if not t.stop_time_update:
            del tu.entity[-1]
            continue
        stats["trip_updates"] += 1

    return vp, tu, dict(stats)
