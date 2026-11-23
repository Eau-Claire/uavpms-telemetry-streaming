"""Settings from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

DRONE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")  # no MQTT wildcards or '/'


class ConfigError(ValueError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _num(name: str, default, cast, low, high):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    drone_id: str
    mavlink_connection: str
    mavlink_baud: int
    mavlink_target_system: int
    mavlink_request_streams: bool
    mavlink_stream_rate_hz: int
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    mqtt_ca_cert: str
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    mqtt_qos: int
    mqtt_keepalive_s: int
    mqtt_max_queued: int
    publish_interval_s: float
    stale_after_s: float
    heartbeat_timeout_s: float
    min_gps_fix: int
    log_level: str

    @property
    def topic(self) -> str:
        return f"drone/{self.drone_id}/telemetry"

    def redacted(self) -> dict:
        """Settings safe to log (no password)."""
        d = dict(self.__dict__)
        d["mqtt_password"] = "***" if self.mqtt_password else ""
        return d


def load_settings(env_file: str | None = ".env") -> Settings:
    if env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)  # real environment variables win
        except ImportError:
            pass

    drone_id = _str("DRONE_ID")
    if not DRONE_ID_RE.match(drone_id):
        raise ConfigError("DRONE_ID is required and may contain only letters, digits, '.', '_' and '-' "
                          f"(it is used in the topic drone/{{DRONE_ID}}/telemetry), got {drone_id!r}")
    host = _str("MQTT_HOST")
    if not host:
        raise ConfigError("MQTT_HOST is required")
    tls = _bool("MQTT_TLS", True)
    ca = _str("MQTT_CA_CERT")
    if ca and not os.path.isfile(ca):
        raise ConfigError(f"MQTT_CA_CERT file not found: {ca}")
    stale = _num("STALE_AFTER_S", 5.0, float, 0.5, 300)
    interval = _num("PUBLISH_INTERVAL_S", 2.0, float, 0.2, 60)

    return Settings(
        drone_id=drone_id,
        mavlink_connection=_str("MAVLINK_CONNECTION", "udpin:0.0.0.0:14550"),
        mavlink_baud=_num("MAVLINK_BAUD", 57600, int, 1200, 3_000_000),
        mavlink_target_system=_num("MAVLINK_TARGET_SYSTEM", 0, int, 0, 255),
        mavlink_request_streams=_bool("MAVLINK_REQUEST_STREAMS", True),
        mavlink_stream_rate_hz=_num("MAVLINK_STREAM_RATE_HZ", 4, int, 1, 50),
        mqtt_host=host,
        mqtt_port=_num("MQTT_PORT", 8883 if tls else 1883, int, 1, 65535),
        mqtt_tls=tls,
        mqtt_ca_cert=ca,
        mqtt_username=_str("MQTT_USERNAME"),
        mqtt_password=os.environ.get("MQTT_PASSWORD", ""),
        mqtt_client_id=_str("MQTT_CLIENT_ID") or f"uavpms-telemetry-{drone_id}",
        mqtt_qos=_num("MQTT_QOS", 1, int, 0, 2),
        mqtt_keepalive_s=_num("MQTT_KEEPALIVE_S", 30, int, 5, 1200),
        mqtt_max_queued=_num("MQTT_MAX_QUEUED", 150, int, 0, 100_000),
        publish_interval_s=interval,
        stale_after_s=max(stale, interval),
        heartbeat_timeout_s=_num("HEARTBEAT_TIMEOUT_S", 5.0, float, 1, 300),
        min_gps_fix=_num("MIN_GPS_FIX", 3, int, 2, 6),
        log_level=_str("LOG_LEVEL", "INFO").upper() or "INFO",
    )
