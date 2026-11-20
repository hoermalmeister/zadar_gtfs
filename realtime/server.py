"""
GTFS-Realtime server pro Zadar (Liburnija) – nasazení na Railway.

  * každých POLL_INTERVAL s stáhne odjezdy celé sítě (SIRI StopMonitoring),
  * každých TIMETABLE_REFRESH_MIN min obnoví dnešní jízdní řád kvůli párování,
  * servíruje /vehicle_positions.pb, /trip_updates.pb (+ .json na ladění).

Proměnné prostředí:
  ZADAR_API_KEY          API klíč (povinné)
  SIRI_SM_URL            URL realtime endpointu (výchozí /LitTrasnit/stop/announcement/nodeundefined)
  POLL_INTERVAL          s mezi dotazy na realtime (výchozí 30)
  TIMETABLE_REFRESH_MIN  min mezi obnovou jízdního řádu (výchozí 15)
"""

import os
import sys
import threading
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify
from google.protobuf.json_format import MessageToDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zadar_rt as rt  # noqa: E402
zc = rt.zc

PORT = int(os.environ.get("PORT", "8080"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))
TIMETABLE_REFRESH_MIN = int(os.environ.get("TIMETABLE_REFRESH_MIN", "15"))
SIRI_SM_URL = (os.environ.get("SIRI_SM_URL", "").strip()
               or f"{zc.API_BASE}/LitTrasnit/stop/announcement/nodeundefined")

app = Flask(__name__)
_lock = threading.Lock()
_state = {"index": None, "index_at": 0.0, "stops": None,
          "vp_pb": rt.new_feed().SerializeToString(), "tu_pb": rt.new_feed().SerializeToString(),
          "vp_dict": {}, "tu_dict": {}, "last_poll_at": 0.0, "stats": {}, "error": None,
          "last_positions": {}}


def refresh_index():
    if _state["index"] and time.time() - _state["index_at"] < TIMETABLE_REFRESH_MIN * 60:
        return
    stops_raw = zc.fetch_json(zc.STOPS_URL)
    stops = {s["Id"]: s for s in stops_raw["TsStops"]}
    timetable = zc.fetch_json(zc.TIMETABLE_URL, timeout=300)
    index = rt.build_trip_index(timetable, stops)
    with _lock:
        _state["index"], _state["index_at"] = index, time.time()
    print(f"Jízdní řád obnoven: {len(index['by_vj'])} jízd pro {index['service_date']}")


def poll_once():
    refresh_index()
    siri = zc.fetch_json(SIRI_SM_URL, timeout=60)
    vp, tu, stats = rt.build_feeds(siri, _state["index"], _state["last_positions"])
    with _lock:
        _state.update(vp_pb=vp.SerializeToString(), tu_pb=tu.SerializeToString(),
                      vp_dict=MessageToDict(vp), tu_dict=MessageToDict(tu),
                      last_poll_at=time.time(), stats=stats, error=None)
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {stats} | VP={len(vp.entity)} TU={len(tu.entity)}")


def poller_loop():
    while True:
        try:
            poll_once()
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state["error"] = f"{type(e).__name__}: {e}"
            print(f"CHYBA: {type(e).__name__}: {e}")
        time.sleep(POLL_INTERVAL)


def _pb(key):
    with _lock:
        return Response(_state[key], mimetype="application/x-protobuf")


@app.route("/vehicle_positions.pb")
def vp_pb():
    return _pb("vp_pb")


@app.route("/trip_updates.pb")
def tu_pb():
    return _pb("tu_pb")


@app.route("/vehicle_positions.json")
def vp_json():
    with _lock:
        return jsonify(_state["vp_dict"])


@app.route("/trip_updates.json")
def tu_json():
    with _lock:
        return jsonify(_state["tu_dict"])


@app.route("/healthz")
def healthz():
    """Liveness pro Railway: proces běží a odpovídá (nezávisle na zdrojovém API)."""
    return jsonify({"alive": True})


@app.route("/status")
def status():
    """Stav dat: 200 = feed čerstvý, 503 = data stará nebo chyba zdroje."""
    with _lock:
        age = time.time() - _state["last_poll_at"] if _state["last_poll_at"] else None
        err = _state["error"]
        stats = _state["stats"]
    ok = age is not None and age < POLL_INTERVAL * 3 and err is None
    return (jsonify({"ok": ok, "age_s": age, "error": err, "stats": stats,
                     "source": SIRI_SM_URL}), 200 if ok else 503)


@app.route("/")
def index():
    with _lock:
        s, err, last = _state["stats"], _state["error"], _state["last_poll_at"]
        n = len(_state["index"]["by_vj"]) if _state["index"] else 0
    last_txt = datetime.fromtimestamp(last, timezone.utc).isoformat() if last else "zatím nic"
    html = f"""<!doctype html><html lang="cs"><meta charset="utf-8"><title>Zadar GTFS-RT</title>
<body style="font-family:system-ui;max-width:640px;margin:40px auto;line-height:1.5">
<h1>Zadar (Liburnija) GTFS-Realtime</h1>
<p>Dnešních jízd v indexu: <b>{n}</b></p>
<p>Poslední aktualizace (UTC): <b>{last_txt}</b></p>
<p>Poslední cyklus: {s}</p><p>Chyba: {err or "žádná"}</p>
<ul><li><a href="/vehicle_positions.pb">/vehicle_positions.pb</a></li>
<li><a href="/trip_updates.pb">/trip_updates.pb</a></li>
<li><a href="/vehicle_positions.json">/vehicle_positions.json</a></li>
<li><a href="/trip_updates.json">/trip_updates.json</a></li></ul></body></html>"""
    return Response(html, mimetype="text/html")


threading.Thread(target=poller_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
