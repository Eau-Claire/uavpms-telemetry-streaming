"""Main loop: read MAVLink, publish one telemetry message every PUBLISH_INTERVAL_S when there is fresh data."""
from __future__ import annotations

import json
import logging
import signal
import threading
import time

from pymavlink import mavutil

from .config import Settings
from .mqtt_client import MqttPublisher
from .telemetry import WANTED_TYPES, TelemetryState, build_payload, describe_received

log = logging.getLogger("publisher")

RECONNECT_DELAY_S = 3.0
STATS_EVERY_S = 60.0


class MavlinkSource:
    """MAVLink connection that is reopened when it fails or the autopilot stays silent."""

    def __init__(self, s: Settings):
        self.s = s
        self.conn = None
        self.opened_at = 0.0

    def open(self):
        self.close()
        log.info("opening MAVLink connection %s", self.s.mavlink_connection)
        self.conn = mavutil.mavlink_connection(self.s.mavlink_connection, baud=self.s.mavlink_baud,
                                               source_system=255, source_component=190,
                                               autoreconnect=True)
        self.opened_at = time.monotonic()

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def request_streams(self, target_system: int):
        if not self.s.mavlink_request_streams or self.conn is None:
            return
        try:
            self.conn.mav.request_data_stream_send(target_system, 0, mavutil.mavlink.MAV_DATA_STREAM_ALL,
                                                   self.s.mavlink_stream_rate_hz, 1)
            log.info("requested data streams at %d Hz from system %d", self.s.mavlink_stream_rate_hz, target_system)
        except Exception as exc:
            log.warning("could not request data streams: %s", exc)

    def recv(self, timeout: float):
        return self.conn.recv_match(type=WANTED_TYPES, blocking=True, timeout=timeout)


def log_telemetry(s: Settings, state: TelemetryState, payload: dict, now: float, sent: bool) -> None:
    """Console line per publish: raw MAVLink position received -> coordinates sent (LOG_TELEMETRY)."""
    if s.log_telemetry == "off":
        return
    status = "SENT" if sent else "NOT SENT"
    log.info("RECV %s", describe_received(state, now, s.stale_after_s))
    def v(key, unit=""):
        x = payload[key]
        return "null" if x is None else f"{x}{unit}"
    log.info("%s %s lat=%s lon=%s alt_rel=%s alt_msl=%s fix=%s sats=%s mode=%s armed=%s bat=%s",
             status, s.topic, v("lat"), v("lon"), v("alt_relative_m", "m"), v("alt_msl_m", "m"),
             v("gps_fix_type"), v("satellites"), v("flight_mode"), v("armed"), v("battery_remaining_pct", "%"))
    if s.log_telemetry == "full":
        log.info("JSON %s", json.dumps(payload, ensure_ascii=False))


def run(s: Settings, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: stop.set())
            except ValueError:  # not in the main thread (tests)
                pass

    mqtt = MqttPublisher(s)
    mqtt.start()
    source = MavlinkSource(s)
    state = TelemetryState(target_system=s.mavlink_target_system)
    stats = {"skipped_no_data": 0, "skipped_link_lost": 0}
    published_version = -1
    link_up = False
    streams_requested_for = None
    next_publish = time.monotonic() + s.publish_interval_s
    next_stats = time.monotonic() + STATS_EVERY_S

    try:
        while not stop.is_set():
            if source.conn is None:
                try:
                    source.open()
                except Exception as exc:
                    log.error("MAVLink connection failed: %s; retrying in %.0f s", exc, RECONNECT_DELAY_S)
                    stop.wait(RECONNECT_DELAY_S)
                    continue

            now = time.monotonic()
            try:
                msg = source.recv(timeout=max(0.05, min(0.5, next_publish - now)))
            except Exception as exc:
                log.error("MAVLink read error: %s; reopening", exc)
                source.close()
                stop.wait(RECONNECT_DELAY_S)
                continue
            if msg is not None and msg.get_type() != "BAD_DATA":
                if state.update(msg) and state.system_id and streams_requested_for != state.system_id:
                    streams_requested_for = state.system_id
                    source.request_streams(state.system_id)

            now = time.monotonic()
            hb_age = state.heartbeat_age(now)
            alive = hb_age is not None and hb_age <= s.heartbeat_timeout_s
            if alive != link_up:
                link_up = alive
                if alive:
                    log.info("autopilot heartbeat from system %d", state.system_id)
                else:
                    log.warning("no autopilot heartbeat for %.0f s: telemetry paused", s.heartbeat_timeout_s)

            # Serial/TCP links can die silently: reopen when nothing arrives for a long time.
            silent_for = hb_age if hb_age is not None else now - source.opened_at
            if silent_for > max(30.0, 3 * s.heartbeat_timeout_s) and now - source.opened_at > 30.0 \
                    and not s.mavlink_connection.startswith("udpin"):
                log.warning("MAVLink silent for %.0f s; reopening %s", silent_for, s.mavlink_connection)
                source.close()
                continue

            if now >= next_publish:
                next_publish = max(next_publish + s.publish_interval_s, now)
                if not alive:
                    stats["skipped_link_lost"] += 1
                elif state.version == published_version:
                    stats["skipped_no_data"] += 1      # nothing new since the last message
                else:
                    published_version = state.version
                    payload = build_payload(state, s.drone_id, now, s.stale_after_s, s.min_gps_fix)
                    sent = mqtt.publish(s.topic, payload)
                    log_telemetry(s, state, payload, now, sent)

            if now >= next_stats:
                next_stats = now + STATS_EVERY_S
                log.info("stats mqtt=%s connected=%s link=%s ignored_msgs=%d %s", mqtt.stats,
                         mqtt.connected.is_set(), "up" if link_up else "down", state.ignored, stats)
    finally:
        log.info("stopping; mqtt=%s %s", mqtt.stats, stats)
        source.close()
        mqtt.stop()
