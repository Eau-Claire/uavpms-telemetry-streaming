"""Subscribe to drone/+/telemetry and check every message against the 22-field contract.

Uses the same MQTT_* settings as the publisher (.env), so it works against EMQX Cloud or a local broker.

  python tools/check_subscriber.py --seconds 30 --out evidence.jsonl
Exit code 0 when every message is valid, 1 otherwise (or when nothing arrived).
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from publisher.telemetry import FIELDS  # noqa: E402

TYPES = {
    "drone_id": (str,), "timestamp": (str,), "flight_mode": (str,), "armed": (bool,),
    "satellites": (int,), "gps_fix_type": (int,), "battery_remaining_pct": (int,),
}


def problems(topic: str, p: dict) -> list[str]:
    out = []
    if not isinstance(p, dict):
        return ["payload is not a JSON object"]
    if list(p) != list(FIELDS):
        missing, extra = set(FIELDS) - set(p), set(p) - set(FIELDS)
        out.append(f"fields differ (missing={sorted(missing)}, extra={sorted(extra)})" if missing or extra
                   else "field order differs")
    if topic.split("/")[1] != p.get("drone_id"):
        out.append(f"topic drone {topic.split('/')[1]!r} != drone_id {p.get('drone_id')!r}")
    for k in FIELDS:
        v = p.get(k)
        if v is None:
            continue
        want = TYPES.get(k, (int, float))
        if isinstance(v, bool) and bool not in want or not isinstance(v, want):
            out.append(f"{k} has type {type(v).__name__}")
    try:
        datetime.fromisoformat(str(p.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        out.append("timestamp is not ISO 8601")
    lat, lon = p.get("lat"), p.get("lon")
    if (lat is None) != (lon is None):
        out.append("only one of lat/lon is null")
    if lat is not None and (not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0)):
        out.append(f"invalid position {lat},{lon}")
    if lat is not None and (p.get("gps_fix_type") or 0) < 2:
        out.append("position published without a GPS fix")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=".env")
    ap.add_argument("--topic", default="drone/+/telemetry")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--out", type=Path, help="write received messages (JSON lines) as evidence")
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(a.env, override=False)
    except ImportError:
        pass
    env = os.environ.get
    tls = env("MQTT_TLS", "true").lower() in ("1", "true", "yes", "on")
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"uavpms-telemetry-check-{os.getpid()}")
    if env("MQTT_USERNAME"):
        c.username_pw_set(env("MQTT_USERNAME"), env("MQTT_PASSWORD") or None)
    if tls:
        ca = env("MQTT_CA_CERT") or None
        if ca is None:
            import certifi
            ca = certifi.where()
        c.tls_set(ca_certs=ca, cert_reqs=ssl.CERT_REQUIRED)

    rows, lock = [], threading.Lock()

    def on_connect(client, userdata, flags, rc, props):
        print(f"connected: {rc}")
        client.subscribe(a.topic, qos=1)

    def on_message(client, userdata, msg):
        recv = time.time()
        try:
            p = json.loads(msg.payload)
        except ValueError:
            p = None
        issues = problems(msg.topic, p) if p is not None else ["not JSON"]
        with lock:
            rows.append(dict(topic=msg.topic, qos=msg.qos, received=recv, payload=p, issues=issues))
        print(f"{msg.topic} qos={msg.qos} lat={p and p.get('lat')} lon={p and p.get('lon')} "
              f"mode={p and p.get('flight_mode')} bat={p and p.get('battery_remaining_pct')} "
              f"{'OK' if not issues else issues}")

    c.on_connect, c.on_message = on_connect, on_message
    c.connect(env("MQTT_HOST"), int(env("MQTT_PORT") or (8883 if tls else 1883)), keepalive=30)
    c.loop_start()
    time.sleep(a.seconds)
    c.loop_stop()

    by_drone = {}
    for r in rows:
        by_drone.setdefault(r["topic"], []).append(r["received"])
    print("\nsummary:")
    for topic, ts in by_drone.items():
        gaps = [b - a_ for a_, b in zip(ts, ts[1:])]
        print(f"  {topic}: {len(ts)} msgs, interval avg {sum(gaps) / len(gaps):.2f}s max {max(gaps):.2f}s"
              if gaps else f"  {topic}: {len(ts)} msg")
    bad = [r for r in rows if r["issues"]]
    print(f"  invalid: {len(bad)}/{len(rows)}")
    if a.out:
        a.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    sys.exit(0 if rows and not bad else 1)


if __name__ == "__main__":
    main()
