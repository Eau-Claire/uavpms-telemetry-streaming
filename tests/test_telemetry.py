import math
import os
import unittest
from unittest import mock

from pymavlink import mavutil

from publisher.config import ConfigError, load_settings
from publisher.telemetry import FIELDS, TelemetryState, build_payload

M = mavutil.mavlink


def wire(msg, system=1, component=1):
    """Pack and decode like a real link so the message carries its source system id."""
    mav = M.MAVLink(None, srcSystem=system, srcComponent=component)
    return M.MAVLink(None).decode(bytearray(msg.pack(mav)))


def hb(system=1, armed=True, mode=3, mav_type=M.MAV_TYPE_QUADROTOR, autopilot=M.MAV_AUTOPILOT_ARDUPILOTMEGA):
    base = M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (M.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0)
    return wire(M.MAVLink_heartbeat_message(mav_type, autopilot, base, mode, M.MAV_STATE_ACTIVE, 3), system)


def pos(lat=10.7765, lon=106.7055, rel=40.0, msl=50.0, hdg=12345, system=1):
    return wire(M.MAVLink_global_position_int_message(1000, int(lat * 1e7), int(lon * 1e7), int(msl * 1000),
                                                       int(rel * 1000), 0, 0, 0, hdg), system)


def gps(fix=3, sats=14, eph=80, system=1):
    return wire(M.MAVLink_gps_raw_int_message(0, fix, 0, 0, 0, eph, 65535, 0, 0, sats), system)


def att(system=1):
    return wire(M.MAVLink_attitude_message(0, math.radians(5), math.radians(-2), math.radians(90), 0, 0, 0), system)


def hud(system=1):
    return wire(M.MAVLink_vfr_hud_message(1.5, 5.25, 270, 52, 50.0, -0.3), system)


def sys_status(voltage=22100, current=1250, remaining=87, system=1):
    return wire(M.MAVLink_sys_status_message(0, 0, 0, 500, voltage, current, remaining, 0, 0, 0, 0, 0, 0), system)


def full_state(**gps_kw):
    s = TelemetryState()
    for m in (hb(), pos(), gps(**gps_kw), att(), hud(), sys_status()):
        assert s.update(m, mono=100.0, wall=1_795_000_000.0)
    return s


class PayloadTests(unittest.TestCase):
    def test_has_exactly_22_fields_with_expected_values(self):
        p = build_payload(full_state(), "UAV-HCM-01", now_mono=101.0, stale_after_s=5)
        self.assertEqual(list(p), list(FIELDS))
        self.assertEqual(len(p), 22)
        self.assertEqual(p["drone_id"], "UAV-HCM-01")
        self.assertEqual(p["timestamp"], "2026-11-18T11:06:40.000Z")
        self.assertEqual(p["flight_mode"], "AUTO")
        self.assertTrue(p["armed"])
        self.assertEqual((p["lat"], p["lon"]), (10.7765, 106.7055))
        self.assertEqual((p["alt_relative_m"], p["alt_msl_m"]), (40.0, 50.0))
        self.assertEqual((p["satellites"], p["gps_fix_type"], p["hdop"]), (14, 3, 0.8))
        self.assertEqual((p["roll_deg"], p["pitch_deg"], p["yaw_deg"]), (5.0, -2.0, 90.0))
        self.assertEqual((p["ground_speed_ms"], p["air_speed_ms"], p["climb_rate_ms"]), (5.25, 1.5, -0.3))
        self.assertEqual((p["heading_deg"], p["throttle_pct"]), (270.0, 52.0))  # VFR_HUD heading wins
        self.assertEqual((p["battery_voltage_v"], p["battery_current_a"], p["battery_remaining_pct"]), (22.1, 12.5, 87))

    def test_no_gps_fix_gives_null_position(self):
        for kw in ({"fix": 1}, {"fix": 0}):
            p = build_payload(full_state(**kw), "D1", 101.0, 5)
            self.assertIsNone(p["lat"]); self.assertIsNone(p["lon"]); self.assertIsNone(p["alt_msl_m"])
            self.assertEqual(p["alt_relative_m"], 40.0)        # barometric altitude is still valid

    def test_2d_fix_follows_min_gps_fix(self):
        self.assertIsNone(build_payload(full_state(fix=2), "D1", 101.0, 5, min_gps_fix=3)["lat"])
        self.assertEqual(build_payload(full_state(fix=2), "D1", 101.0, 5, min_gps_fix=2)["lat"], 10.7765)

    def test_zero_zero_position_is_not_published_even_with_fix(self):
        s = full_state()
        s.update(pos(lat=0, lon=0), mono=100.0, wall=1.0)
        p = build_payload(s, "D1", 101.0, 5)
        self.assertIsNone(p["lat"]); self.assertIsNone(p["lon"])

    def test_without_gps_message_position_is_null(self):
        s = TelemetryState()
        s.update(hb(), mono=100.0, wall=1.0); s.update(pos(), mono=100.0, wall=1.0)
        self.assertIsNone(build_payload(s, "D1", 101.0, 5)["lat"])

    def test_stale_groups_become_null(self):
        s = full_state()
        s.update(hb(), mono=110.0, wall=1_795_000_010.0)       # only the heartbeat is recent
        p = build_payload(s, "D1", now_mono=111.0, stale_after_s=5)
        self.assertEqual(p["flight_mode"], "AUTO")
        for k in ("lat", "lon", "gps_fix_type", "roll_deg", "ground_speed_ms", "battery_voltage_v", "heading_deg"):
            self.assertIsNone(p[k], k)
        self.assertEqual(p["timestamp"], "2026-11-18T11:06:50.000Z")

    def test_unknown_sentinels_become_null(self):
        s = full_state(sats=255, eph=65535)
        s.update(sys_status(voltage=65535, current=-1, remaining=-1), mono=100.0, wall=1.0)
        s.update(pos(hdg=65535), mono=100.0, wall=1.0)
        s.entries.pop("hud")
        p = build_payload(s, "D1", 101.0, 5)
        for k in ("satellites", "hdop", "battery_voltage_v", "battery_current_a", "battery_remaining_pct", "heading_deg"):
            self.assertIsNone(p[k], k)

    def test_disarmed(self):
        s = full_state()
        s.update(hb(armed=False, mode=5), mono=100.0, wall=1.0)
        p = build_payload(s, "D1", 101.0, 5)
        self.assertFalse(p["armed"]); self.assertEqual(p["flight_mode"], "LOITER")

    def test_empty_state_has_all_fields_null(self):
        p = build_payload(TelemetryState(), "D1", 1.0, 5)
        self.assertEqual(len(p), 22)
        self.assertTrue(all(v is None for k, v in p.items() if k != "drone_id"))


class SourceFilterTests(unittest.TestCase):
    def test_gcs_heartbeat_and_other_systems_are_ignored(self):
        s = TelemetryState()
        self.assertFalse(s.update(hb(system=255, mav_type=M.MAV_TYPE_GCS, autopilot=M.MAV_AUTOPILOT_INVALID)))
        self.assertFalse(s.update(pos(system=1)))              # nothing locked yet
        self.assertTrue(s.update(hb(system=1)))
        self.assertEqual(s.system_id, 1)
        self.assertTrue(s.update(pos(system=1)))
        self.assertFalse(s.update(pos(lat=21.0, system=2)))    # another vehicle on the same link
        self.assertEqual(s.entries["position"].msg.lat, int(10.7765 * 1e7))

    def test_fixed_target_system(self):
        s = TelemetryState(target_system=2)
        self.assertFalse(s.update(hb(system=1)))
        self.assertTrue(s.update(hb(system=2)))

    def test_version_counts_accepted_messages_only(self):
        s = TelemetryState()
        s.update(hb()); s.update(att()); s.update(att(system=9))
        self.assertEqual(s.version, 2)


class ConfigTests(unittest.TestCase):
    BASE = {"DRONE_ID": "UAV-HCM-01", "MQTT_HOST": "broker.example"}

    def load(self, **env):
        with mock.patch.dict(os.environ, {**self.BASE, **env}, clear=True):
            return load_settings(env_file=None)

    def test_defaults(self):
        s = self.load()
        self.assertEqual(s.topic, "drone/UAV-HCM-01/telemetry")
        self.assertEqual((s.mqtt_port, s.mqtt_tls, s.mqtt_qos, s.publish_interval_s), (8883, True, 1, 2.0))
        self.assertEqual(s.mqtt_client_id, "uavpms-telemetry-UAV-HCM-01")

    def test_drone_id_cannot_break_the_topic(self):
        for bad in ("", "a/b", "+", "#", "UAV 1"):
            with self.assertRaises(ConfigError):
                self.load(DRONE_ID=bad)

    def test_password_is_not_logged(self):
        self.assertEqual(self.load(MQTT_PASSWORD="secret").redacted()["mqtt_password"], "***")

    def test_invalid_numbers(self):
        with self.assertRaises(ConfigError):
            self.load(PUBLISH_INTERVAL_S="abc")
        with self.assertRaises(ConfigError):
            self.load(MQTT_QOS="3")


if __name__ == "__main__":
    unittest.main()
