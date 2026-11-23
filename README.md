# UAV-PMS Telemetry Publisher

Python service for **TR-01** (MAVLink collection) and **TR-02** (MQTT publishing) of
[docs/requirement.md](docs/requirement.md):

```
Mission Planner ──MAVLink──▶ publisher (this repo) ──MQTT/TLS 8883──▶ EMQX Cloud ──▶ UAV-PMS backend
```

Every `PUBLISH_INTERVAL_S` (default 2 s) it publishes one JSON message with the 22 contract fields to
`drone/{DRONE_ID}/telemetry`, QoS 1. A message is sent only when new MAVLink data arrived since the last one
and the autopilot heartbeat is alive.

## Setup

Python 3.11+ (Windows, Linux or macOS). To run on a Raspberry Pi on the drone, see [deploy/raspberry-pi](deploy/raspberry-pi/README.md).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
cp .env.example .env                              # then fill in DRONE_ID and the MQTT credentials
```

`DRONE_ID` must be the drone's `UavCode` in UAV-PMS (for example `UAV-HCM-01`), because the backend looks the
drone up by the topic. MQTT credentials are read from `.env` or the environment only. `.env` is git-ignored.

### Mission Planner

1. Connect Mission Planner to the drone (or SITL) as usual.
2. Press **Ctrl+F**, then click **MAVLink** (MAVLink mirror).
3. Choose **UDP Outbound**, host `127.0.0.1`, port `14550`. Tick **Write access** only if you want the
   publisher to request data streams.
4. Keep `MAVLINK_CONNECTION=udpin:0.0.0.0:14550` in `.env`.

Other sources: `tcp:127.0.0.1:5762` (SITL), or a serial port (`COM3`, `/dev/ttyUSB0`) with `MAVLINK_BAUD`.

## Run

```bash
.venv/bin/python -m publisher            # uses .env; Ctrl+C to stop
```

Every publish prints the raw MAVLink position received and the coordinates sent (`LOG_TELEMETRY=coords`,
the default; `full` also prints the JSON payload, `off` turns it off):

```
RECV sys=1 GLOBAL_POSITION_INT lat=107770362 lon=1067055550 (deg*1e7) alt=50000mm relative_alt=40000mm hdg=9576cdeg age=0.1s | GPS_RAW_INT fix=3 sats=14 eph=80 age=0.1s
SENT drone/UAV-HCM-01/telemetry lat=10.7770362 lon=106.705555 alt_rel=40.0m alt_msl=50.0m fix=3 sats=14 mode=AUTO armed=True bat=99%
RECV sys=1 GLOBAL_POSITION_INT lat=0 lon=0 (deg*1e7) alt=50000mm relative_alt=40000mm hdg=10725cdeg age=0.1s | GPS_RAW_INT fix=1 sats=0 eph=65535 age=0.1s
SENT drone/UAV-HCM-01/telemetry lat=null lon=null alt_rel=40.0m alt_msl=null fix=1 sats=0 mode=AUTO armed=True bat=99%
```

The log also shows the MQTT connection, the autopilot heartbeat, link loss and a stats line every 60 s
(`published`, `acked`, `queue_full`, `skipped_no_data`, `skipped_link_lost`). The password is never logged.

## Payload

Topic `drone/{DRONE_ID}/telemetry`, JSON object with exactly these 22 keys in this order. A value is `null`
when it is unknown, invalid or older than `STALE_AFTER_S` (default 5 s).

| Field | Unit / type | MAVLink source | `null` when |
|---|---|---|---|
| `drone_id` | string | `DRONE_ID` | never |
| `timestamp` | ISO 8601 UTC, ms (`2026-11-23T03:54:53.925Z`) | reception time of the newest message used | no data |
| `flight_mode` | string (`AUTO`, `LOITER`, `RTL`, ...) | `HEARTBEAT.custom_mode` | heartbeat stale |
| `armed` | bool | `HEARTBEAT.base_mode` | heartbeat stale |
| `lat`, `lon` | degrees, 7 decimals | `GLOBAL_POSITION_INT` | GPS fix < `MIN_GPS_FIX` (default 3D), `(0,0)`, out of range |
| `alt_relative_m` | m above home | `GLOBAL_POSITION_INT.relative_alt` | stale |
| `alt_msl_m` | m above MSL | `GLOBAL_POSITION_INT.alt` | same as `lat`/`lon` |
| `satellites` | int | `GPS_RAW_INT.satellites_visible` | 255 (unknown) |
| `gps_fix_type` | int (0 none, 1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK float, 6 RTK fixed) | `GPS_RAW_INT.fix_type` | stale |
| `hdop` | unitless | `GPS_RAW_INT.eph / 100` | 65535 (unknown) |
| `roll_deg`, `pitch_deg`, `yaw_deg` | degrees (yaw −180..180) | `ATTITUDE` | stale |
| `ground_speed_ms`, `air_speed_ms`, `climb_rate_ms` | m/s | `VFR_HUD` | stale |
| `heading_deg` | degrees 0..360 | `VFR_HUD.heading` (fallback `GLOBAL_POSITION_INT.hdg`) | unknown |
| `throttle_pct` | % | `VFR_HUD.throttle` | stale |
| `battery_voltage_v` | V | `SYS_STATUS.voltage_battery / 1000` | 0 or 65535 |
| `battery_current_a` | A | `SYS_STATUS.current_battery / 100` | −1 |
| `battery_remaining_pct` | int % | `SYS_STATUS.battery_remaining` | −1 |

Only messages from the autopilot are used. Mission Planner's own GCS heartbeat is ignored, and so are other
vehicles on the same link. The publisher locks onto the first autopilot system id, or uses
`MAVLINK_TARGET_SYSTEM` if it is set.

### Notes for the backend subscriber (TR-03)

- Subscribe to `drone/+/telemetry`. The topic segment equals `drone_id`.
- Messages can arrive **late and in a burst** after a broker outage: up to `MQTT_MAX_QUEUED` (150 = 5 min)
  QoS 1 messages are queued and sent after reconnecting. Order them by `timestamp`, and do not let an older
  `timestamp` overwrite the latest state.
- QoS 1 is at-least-once, so the same message can arrive twice. Use `(drone_id, timestamp)` to deduplicate.
- `timestamp` is the time the publisher received the data from Mission Planner (the GCS clock in UTC).
  The backend should also store its own reception time.

## Reliability

| Case | Behaviour |
|---|---|
| Broker or network down | paho reconnects automatically (backoff 1 to 10 s). QoS 1 messages are queued (`MQTT_MAX_QUEUED`) and sent after reconnecting. |
| Wrong credentials | Logged as `broker refused connection: Not authorized`. Retries continue. |
| No autopilot heartbeat for `HEARTBEAT_TIMEOUT_S` | Publishing pauses. It resumes on the next heartbeat. |
| TCP/serial link silent for 30 s or more | The MAVLink connection is reopened. |
| GPS lost | `lat`/`lon`/`alt_msl_m` become `null`. Other fields continue. |
| Invalid `.env` | The program exits with code 2 and names the bad variable. |

## Testing

```bash
.venv/bin/python -m unittest discover -s tests -t .      # 17 unit tests, no network needed
```

End-to-end without a drone. Run each command in its own terminal:

```bash
.venv/bin/python tools/check_subscriber.py --seconds 30 --out evidence.jsonl   # checks every message
.venv/bin/python -m publisher
.venv/bin/python tools/fake_autopilot.py                  # options: --no-gps, --gps-loss-after 20, --stop-after 30
```

`tools/check_subscriber.py` uses the same `.env` and works against EMQX Cloud. It checks the 22 keys,
types, topic/`drone_id`, and that no position is published without a fix. It exits 1 if any message is
invalid. Results of the local run (Mosquitto with TLS and password) are in
[evidence/2026-11-23_local_mosquitto](evidence/2026-11-23_local_mosquitto/README.md).
