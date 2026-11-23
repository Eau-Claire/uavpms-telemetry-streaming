"""Fake ArduCopter that streams MAVLink like Mission Planner's MAVLink mirror, for testing without a drone.

It flies a circle around a centre point and sends HEARTBEAT, GLOBAL_POSITION_INT, GPS_RAW_INT, ATTITUDE,
VFR_HUD and SYS_STATUS. A GCS heartbeat (system 255) is also sent to check that the publisher ignores it.

  python tools/fake_autopilot.py --out udpout:127.0.0.1:14550
  python tools/fake_autopilot.py --no-gps            # fix 1 and lat/lon 0: publisher must send lat/lon null
  python tools/fake_autopilot.py --gps-loss-after 20 # GPS fix lost after 20 s
  python tools/fake_autopilot.py --stop-after 30     # stop sending (link loss) after 30 s
"""
from __future__ import annotations

import argparse
import math
import time

from pymavlink import mavutil

M = mavutil.mavlink
COPTER_MODES = {"STABILIZE": 0, "AUTO": 3, "GUIDED": 4, "LOITER": 5, "RTL": 6, "LAND": 9}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="udpout:127.0.0.1:14550")
    p.add_argument("--system", type=int, default=1)
    p.add_argument("--lat", type=float, default=10.7765)
    p.add_argument("--lon", type=float, default=106.7055)
    p.add_argument("--radius-m", type=float, default=60.0)
    p.add_argument("--alt-m", type=float, default=40.0)
    p.add_argument("--rate-hz", type=float, default=4.0)
    p.add_argument("--mode", default="AUTO", choices=list(COPTER_MODES))
    p.add_argument("--no-gps", action="store_true")
    p.add_argument("--gps-loss-after", type=float, default=0)
    p.add_argument("--stop-after", type=float, default=0, help="stop sending after N seconds (0 = never)")
    p.add_argument("--duration", type=float, default=0, help="exit after N seconds (0 = run until Ctrl+C)")
    a = p.parse_args()

    ap = mavutil.mavlink_connection(a.out, source_system=a.system, source_component=1)
    gcs = mavutil.mavlink_connection(a.out, source_system=255, source_component=190)
    t0, n = time.time(), 0
    battery = 100.0
    print(f"fake autopilot sys={a.system} -> {a.out} ({a.rate_hz} Hz)")
    try:
        while True:
            t = time.time() - t0
            if a.duration and t >= a.duration:
                break
            if a.stop_after and t >= a.stop_after:
                time.sleep(0.25)
                continue
            ang = t * 0.05                                   # rad/s around the circle
            dlat = a.radius_m * math.cos(ang) / 111_320
            dlon = a.radius_m * math.sin(ang) / (111_320 * math.cos(math.radians(a.lat)))
            gps_ok = not a.no_gps and not (a.gps_loss_after and t >= a.gps_loss_after)
            lat, lon = (a.lat + dlat, a.lon + dlon) if gps_ok else (0.0, 0.0)
            heading = (math.degrees(ang) + 90) % 360
            battery = max(0.0, 100.0 - t / 30)                # 2 %/min

            if n % max(1, int(a.rate_hz)) == 0:              # 1 Hz heartbeats
                ap.mav.heartbeat_send(M.MAV_TYPE_QUADROTOR, M.MAV_AUTOPILOT_ARDUPILOTMEGA,
                                      M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | M.MAV_MODE_FLAG_SAFETY_ARMED,
                                      COPTER_MODES[a.mode], M.MAV_STATE_ACTIVE)
                gcs.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, 0)
            boot_ms = int(t * 1000) & 0xFFFFFFFF
            ap.mav.global_position_int_send(boot_ms, int(lat * 1e7), int(lon * 1e7),
                                            int((a.alt_m + 10) * 1000), int(a.alt_m * 1000),
                                            0, 0, 0, int(heading * 100))
            ap.mav.gps_raw_int_send(int(t * 1e6), 3 if gps_ok else 1, int(lat * 1e7), int(lon * 1e7),
                                    int((a.alt_m + 10) * 1000), 80 if gps_ok else 65535, 65535, 500,
                                    int(heading * 100), 14 if gps_ok else 0)
            ap.mav.attitude_send(boot_ms, math.radians(3 * math.sin(t)), math.radians(-2.0),
                                 math.radians(heading if heading <= 180 else heading - 360), 0, 0, 0)
            ap.mav.vfr_hud_send(0.0, 5.0, int(heading), 52, a.alt_m + 10, 0.1)
            ap.mav.sys_status_send(0, 0, 0, 500, int((22.2 - (100 - battery) * 0.04) * 1000),
                                   1250, int(battery), 0, 0, 0, 0, 0, 0)
            n += 1
            time.sleep(1.0 / a.rate_hz)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
