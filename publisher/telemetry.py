"""Collect MAVLink messages from one autopilot and turn them into the 22-field telemetry payload.

Only the autopilot's own messages are used: Mission Planner forwards its GCS heartbeat (and other
vehicles) on the same link, so heartbeats from GCS / non-autopilot components are ignored and every
other message must come from the locked system id.

A value is published only when it is real and fresh:
  - a message group not received for `stale_after_s` is published as null;
  - MAVLink "unknown" sentinels (UINT16_MAX, -1, ...) and non-finite numbers become null;
  - lat/lon are null without a GPS fix >= min_gps_fix, at (0, 0) or out of range, so the map never
    shows a false position (AC-08).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pymavlink import mavutil

M = mavutil.mavlink

# Message type -> group name. A group is fresh while its latest message is younger than stale_after_s.
GROUPS = {
    "HEARTBEAT": "heartbeat",
    "GLOBAL_POSITION_INT": "position",
    "GPS_RAW_INT": "gps",
    "ATTITUDE": "attitude",
    "VFR_HUD": "hud",
    "SYS_STATUS": "battery",
}
WANTED_TYPES = list(GROUPS)

FIELDS = (
    "drone_id", "timestamp",
    "flight_mode", "armed",
    "lat", "lon", "alt_relative_m", "alt_msl_m", "satellites", "gps_fix_type", "hdop",
    "roll_deg", "pitch_deg", "yaw_deg",
    "ground_speed_ms", "air_speed_ms", "climb_rate_ms", "heading_deg", "throttle_pct",
    "battery_voltage_v", "battery_current_a", "battery_remaining_pct",
)
assert len(FIELDS) == 22

UINT16_MAX = 65535
NON_AUTOPILOT_TYPES = {M.MAV_TYPE_GCS, M.MAV_TYPE_ONBOARD_CONTROLLER, M.MAV_TYPE_GIMBAL,
                       M.MAV_TYPE_ADSB, M.MAV_TYPE_CAMERA, M.MAV_TYPE_ANTENNA_TRACKER}


@dataclass
class _Entry:
    msg: object
    mono: float      # time.monotonic() at reception, for freshness
    wall: float      # time.time() at reception, for the payload timestamp


@dataclass
class TelemetryState:
    target_system: int = 0              # 0 = lock onto the first autopilot heartbeat
    entries: dict = field(default_factory=dict)
    version: int = 0                    # increases on every accepted message
    ignored: int = 0

    @property
    def system_id(self) -> int:
        return self.target_system

    def update(self, msg, mono: float | None = None, wall: float | None = None) -> bool:
        """Store a message if it belongs to the tracked autopilot. Returns True when accepted."""
        mtype = msg.get_type()
        group = GROUPS.get(mtype)
        if group is None:
            return False
        src = msg.get_srcSystem()
        if mtype == "HEARTBEAT":
            if msg.type in NON_AUTOPILOT_TYPES or msg.autopilot == M.MAV_AUTOPILOT_INVALID:
                self.ignored += 1
                return False
            if self.target_system == 0:
                self.target_system = src
        if self.target_system == 0 or src != self.target_system:
            self.ignored += 1
            return False
        self.entries[group] = _Entry(msg, time.monotonic() if mono is None else mono,
                                     time.time() if wall is None else wall)
        self.version += 1
        return True

    def fresh(self, group: str, now_mono: float, stale_after_s: float):
        e = self.entries.get(group)
        return e if e is not None and now_mono - e.mono <= stale_after_s else None

    def heartbeat_age(self, now_mono: float) -> float | None:
        e = self.entries.get("heartbeat")
        return None if e is None else now_mono - e.mono


def _finite(v, digits: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def flight_mode(hb) -> str | None:
    try:
        mode = mavutil.mode_string_v10(hb)
    except Exception:
        return None
    return mode or None


def build_payload(state: TelemetryState, drone_id: str, now_mono: float, stale_after_s: float,
                  min_gps_fix: int = 3) -> dict:
    """Build the 22-field payload from the fresh groups of `state`. Missing/stale values are None."""
    p = dict.fromkeys(FIELDS)
    p["drone_id"] = drone_id
    used = []

    hb = state.fresh("heartbeat", now_mono, stale_after_s)
    if hb:
        used.append(hb)
        p["flight_mode"] = flight_mode(hb.msg)
        p["armed"] = bool(hb.msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)

    gps = state.fresh("gps", now_mono, stale_after_s)
    fix = None
    if gps:
        used.append(gps)
        g = gps.msg
        fix = int(g.fix_type)
        p["gps_fix_type"] = fix
        p["satellites"] = None if g.satellites_visible == 255 else int(g.satellites_visible)
        p["hdop"] = None if g.eph == UINT16_MAX else _finite(g.eph / 100.0)

    pos = state.fresh("position", now_mono, stale_after_s)
    if pos:
        used.append(pos)
        g = pos.msg
        p["alt_relative_m"] = _finite(g.relative_alt / 1000.0)
        lat, lon = g.lat / 1e7, g.lon / 1e7
        has_fix = fix is not None and fix >= min_gps_fix
        valid = (has_fix and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90
                 and -180 <= lon <= 180 and not (g.lat == 0 and g.lon == 0))
        if valid:
            p["lat"], p["lon"] = round(lat, 7), round(lon, 7)
            p["alt_msl_m"] = _finite(g.alt / 1000.0)   # MSL altitude also needs a GPS fix to mean anything
        if g.hdg != UINT16_MAX:
            p["heading_deg"] = _finite(g.hdg / 100.0)

    att = state.fresh("attitude", now_mono, stale_after_s)
    if att:
        used.append(att)
        a = att.msg
        p["roll_deg"] = _finite(math.degrees(a.roll))
        p["pitch_deg"] = _finite(math.degrees(a.pitch))
        p["yaw_deg"] = _finite(math.degrees(a.yaw))

    hud = state.fresh("hud", now_mono, stale_after_s)
    if hud:
        used.append(hud)
        h = hud.msg
        p["ground_speed_ms"] = _finite(h.groundspeed)
        p["air_speed_ms"] = _finite(h.airspeed)
        p["climb_rate_ms"] = _finite(h.climb)
        p["heading_deg"] = _finite(h.heading)          # VFR_HUD heading wins over GLOBAL_POSITION_INT.hdg
        p["throttle_pct"] = _finite(h.throttle)

    bat = state.fresh("battery", now_mono, stale_after_s)
    if bat:
        used.append(bat)
        s = bat.msg
        p["battery_voltage_v"] = None if s.voltage_battery in (0, UINT16_MAX) else _finite(s.voltage_battery / 1000.0)
        p["battery_current_a"] = None if s.current_battery == -1 else _finite(s.current_battery / 100.0)
        p["battery_remaining_pct"] = None if s.battery_remaining == -1 else int(s.battery_remaining)

    # Source timestamp: when the newest message in this payload was received from the autopilot.
    p["timestamp"] = _iso(max(e.wall for e in used)) if used else None
    return p
