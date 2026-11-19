"""
Převod SIRI VehicleMonitoring (LIT Transit) na GTFS-Realtime.

Párování na statický feed:
  VehicleJourneyRef z realtime dat -> stejná jízda v dnešním jízdním řádu
  (SIRI ProductionTimetable) -> trip_id spočítaný stejně jako v build_gtfs.py.
Záložně podle linky + plánovaného odjezdu z výchozí zastávky.
"""

import os
import re
import sys
import time
from collections import Counter

from google.transit import gtfs_realtime_pb2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import zadar_common as zc  # noqa: E402


# --- Index dnešních jízd -------------------------------------------------------
def build_trip_index(timetable, stops):
    service_date, journeys, _ = zc.parse_snapshot(timetable, stops)
    by_vj, by_line_start = {}, {}
    for j in journeys:
        info = {"trip_id": j["trip_id"], "route_id": f"L_{zc.safe_id(j['short_name'])}",
                "start_s": j["calls"][0][2], "calls": j["calls"]}
        by_vj[j["vj_ref"]] = info
        by_line_start.setdefault((j["line_ref"], j["calls"][0][2]), info)
    return {"service_date": service_date, "by_vj": by_vj, "by_line_start": by_line_start}


# --- Pomocné pro SIRI JSON -------------------------------------------------------
def val(x):
    """SIRI JSON občas balí hodnoty do {"value": ...} nebo do seznamu."""
    if isinstance(x, list):
        x = x[0] if x else None
    if isinstance(x, dict) and "value" in x:
        x = x["value"]
    return x


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


_DUR = re.compile(r"^(-)?P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def iso_duration_secs(txt):
    """'PT2M30S' -> 150, '-PT45S' -> -45."""
    if not txt:
        return None
    m = _DUR.match(str(txt).strip())
    if not m:
        return None
    sign, d, h, mi, s = m.groups()
    total = int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    return int(-total if sign else total)


def iter_activities(siri):
    sd = siri.get("Siri", siri).get("ServiceDelivery", {})
    for vmd in as_list(sd.get("VehicleMonitoringDelivery")):
        for act in as_list(vmd.get("VehicleActivity")):
            yield act


# --- Párování ----------------------------------------------------------------------
def match(mvj, index):
    framed = mvj.get("FramedVehicleJourneyRef") or {}
    for ref in (val(framed.get("DatedVehicleJourneyRef")), val(mvj.get("VehicleJourneyRef")),
                val(mvj.get("DatedVehicleJourneyRef"))):
        if ref and str(ref) in index["by_vj"]:
            return index["by_vj"][str(ref)], "vj_ref"
    origin = val(mvj.get("OriginAimedDepartureTime"))
    line = val(mvj.get("LineRef"))
    if origin and line:
        from datetime import datetime
        ts = datetime.fromisoformat(origin).replace(tzinfo=None)
        secs = (ts.date() - index["service_date"]).days * 86400 + ts.hour * 3600 + ts.minute * 60 + ts.second
        hit = index["by_line_start"].get((str(line), secs))
        if hit:
            return hit, "line_start"
    return None, None


# --- Stavba feedů --------------------------------------------------------------------
def new_feed():
    fm = gtfs_realtime_pb2.FeedMessage()
    fm.header.gtfs_realtime_version = "2.0"
    fm.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    fm.header.timestamp = int(time.time())
    return fm


def build_feeds(siri_vm, index):
    vp, tu = new_feed(), new_feed()
    stats = Counter()
    now = int(time.time())

    for n, act in enumerate(iter_activities(siri_vm)):
        mvj = act.get("MonitoredVehicleJourney") or {}
        stats["total"] += 1
        info, how = match(mvj, index)
        stats[f"match_{how}" if info else "unmatched"] += 1

        loc = mvj.get("VehicleLocation") or {}
        lat, lon = val(loc.get("Latitude")), val(loc.get("Longitude"))
        vehicle_ref = val(mvj.get("VehicleRef"))
        ent_id = str(vehicle_ref or (info and info["trip_id"]) or f"v{n}")

        if lat is not None and lon is not None:
            e = vp.entity.add()
            e.id = ent_id
            v = e.vehicle
            if info:
                v.trip.trip_id = info["trip_id"]
                v.trip.route_id = info["route_id"]
                v.trip.start_date = index["service_date"].strftime("%Y%m%d")
                v.trip.start_time = zc.fmt_time(info["start_s"])
            v.position.latitude = float(lat)
            v.position.longitude = float(lon)
            bearing = val(mvj.get("Bearing"))
            if bearing is not None:
                v.position.bearing = float(bearing)
            if vehicle_ref:
                v.vehicle.id = str(vehicle_ref)
            line_name = val(mvj.get("PublishedLineName")) or val(mvj.get("LineRef"))
            if line_name:
                v.vehicle.label = str(line_name)
            call = mvj.get("MonitoredCall") or {}
            stop_ref = val(call.get("StopPointRef"))
            if stop_ref:
                v.stop_id = str(stop_ref).split("/")[-1]
                at_stop = val(call.get("VehicleAtStop"))
                v.current_status = (gtfs_realtime_pb2.VehiclePosition.STOPPED_AT if at_stop in (True, "true")
                                    else gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO)
            v.timestamp = _ts(val(act.get("RecordedAtTime"))) or now
            stats["positioned"] += 1

        delay = iso_duration_secs(val(mvj.get("Delay")))
        if info and delay is not None:
            e = tu.entity.add()
            e.id = ent_id
            t = e.trip_update
            t.trip.trip_id = info["trip_id"]
            t.trip.route_id = info["route_id"]
            t.trip.start_date = index["service_date"].strftime("%Y%m%d")
            t.trip.start_time = zc.fmt_time(info["start_s"])
            if vehicle_ref:
                t.vehicle.id = str(vehicle_ref)
            t.delay = delay
            # zpoždění promítnuté od aktuální zastávky do konce jízdy
            call = mvj.get("MonitoredCall") or {}
            cur = str(val(call.get("StopPointRef")) or "").split("/")[-1]
            seqs = [i for i, (s, _, _) in enumerate(info["calls"], 1) if str(s) == cur]
            start_seq = seqs[0] if seqs else 1
            for seq, (stop_id, _a, _d) in enumerate(info["calls"], 1):
                if seq < start_seq:
                    continue
                stu = t.stop_time_update.add()
                stu.stop_sequence = seq
                stu.stop_id = str(stop_id)
                stu.arrival.delay = delay
                stu.departure.delay = delay
            t.timestamp = now

    return vp, tu, dict(stats)


def _ts(txt):
    if not txt:
        return None
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(txt)).timestamp())
    except ValueError:
        return None
