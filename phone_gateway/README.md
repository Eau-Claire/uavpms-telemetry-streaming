# Phone gateway: iPhone as a temporary telemetry source

Uses an iPhone's real GPS, compass and motion sensors to test the UAV-PMS telemetry pipeline before a drone is
available. No drone, Mac, Xcode or app install is needed. Only Safari and this Linux gateway are used.

```
iPhone Safari ──WSS (Cloudflare tunnel)──▶ phone gateway (Node, this folder) ──MQTT/TLS──▶ drone/{PHONE_DEVICE_ID}/telemetry ──▶ backend (TR-03)
                                                 ├─▶ data/telemetry-YYYY-MM-DD.jsonl   (history: raw sensors + published contract)
                                                 └─▶ /dashboard                         (live map and values)
```

The gateway is a **source adapter**. It publishes the **same 22-field contract** as the MAVLink publisher
([../README.md](../README.md#payload)) on the same topic pattern. The backend subscriber therefore handles the
phone and a real drone in the same way, and nothing has to be rebuilt when the drone arrives.

## Run

Node.js 20.12 or later.

```bash
cd phone_gateway
npm install
cp .env.example .env            # set PHONE_ACCESS_TOKEN (openssl rand -hex 16). MQTT_* also come from ../.env
npm start                       # terminal 1
cloudflared tunnel --url http://127.0.0.1:3000     # terminal 2 (Arch: sudo pacman -S cloudflared)
```

cloudflared prints `https://<random>.trycloudflare.com`. Then:

- **iPhone (Safari):** open `https://<random>.trycloudflare.com/?token=<PHONE_ACCESS_TOKEN>` and tap **Start Streaming**.
  Allow Location (Precise) and Motion & Orientation.
- **Dashboard (any browser):** `https://<random>.trycloudflare.com/dashboard?token=<PHONE_ACCESS_TOKEN>`.
- **API:** `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:3000/api/telemetry/latest`.
  Other endpoints are `/api/telemetry/history?from=<ISO>&to=<ISO>&limit=N` (default: last hour) and `/api/status`.

Safari allows GPS and motion sensors only on HTTPS. The tunnel provides HTTPS, so no certificate setup is
needed. A plain `http://192.168.x.x:3000` URL will not work on the iPhone.

If `MQTT_HOST` is empty, the gateway runs without MQTT. It still writes the history file and serves the dashboard.

## How the phone data maps to the contract

The phone sends raw samples once per second. Every `PUBLISH_INTERVAL_S` (2 s) the gateway publishes one
contract message, but only if a new sample has arrived since the last message. An old sample is never sent
again as new data. `timestamp` is the time the gateway received the sample, so `(drone_id, timestamp)`
identifies each message and the backend can deduplicate on it.

| Contract field | Phone source |
|---|---|
| `lat`, `lon` | Geolocation API. Set to `null` when there is no fix, the fix is older than `STALE_AFTER_S`, accuracy is worse than `PHONE_MAX_ACCURACY_M`, or the position is (0, 0). |
| `gps_fix_type` | **Derived.** The browser does not report the real fix type: 3 = valid fix with altitude, 2 = valid fix without altitude, 1 = no valid fix. |
| `alt_relative_m` | Altitude minus the first altitude of the session. The session start counts as "home". |
| `ground_speed_ms` | `coords.speed` |
| `heading_deg` | iOS compass (`webkitCompassHeading`, magnetic). If no compass reading is available, GPS course over ground is used. |
| `alt_msl_m` | `null`. The browser altitude is height above the WGS84 ellipsoid, not above mean sea level. |
| `flight_mode`, `armed`, `satellites`, `hdop`, `roll/pitch/yaw_deg`, `air_speed_ms`, `climb_rate_ms`, `throttle_pct`, `battery_*` | `null`. The phone does not measure these, and Safari has no Battery API. |

The raw orientation (`alpha`/`beta`/`gamma`), acceleration and rotation rate are kept in the history file and
shown on the dashboard. They are **not** mapped to `roll/pitch/yaw`, because the device angles equal aircraft
attitude only for a known mounting and axis convention.

## Behaviour

| Case | Behaviour |
|---|---|
| Wrong or missing token | WebSocket returns `401`, the API returns `401`. |
| A second phone connects | The newer connection replaces the older one (close code 4000). Only one sender is active per device ID. |
| Phone stops sending (screen locked, app switched, network lost) | After `STALE_AFTER_S`, the gateway logs `device OFFLINE` and the dashboard shows **Offline**. Nothing is published. |
| Phone network comes back | The page reconnects automatically (backoff 1 to 10 s) and keeps the screen awake with the Wake Lock API. |
| GPS lost or inaccurate | Messages continue with `lat`/`lon` = `null` and `gps_fix_type` = 1. The dashboard shows **No GPS Fix** and does not move the marker. |
| MQTT broker down | Reconnects every 2 s. Up to `MQTT_MAX_QUEUED` messages are queued, oldest dropped first. |
| Invalid sample (bad JSON, lat out of range, > 4 KB) | Rejected and counted. The phone receives `{type:"error"}`. |

## Tests

```bash
npm test     # 9 tests: adapter mapping + end-to-end WebSocket -> gateway -> MQTT broker (in-process) -> subscriber
```

The output was also checked with the repo's own validator. The gateway ran with `npm start`, connected to a
local broker, and a WebSocket client sent 12 samples, including 3 without a GPS fix.
`tools/check_subscriber.py` reported **6/6 valid**, interval 2.00 s, QoS 1, and the no-fix message had `lat`/`lon` = `null`.

## Limits (put these in the report)

- Safari pauses JavaScript when the screen locks or the user switches apps. Streaming works only while the page is in the foreground.
- A phone does not provide battery data, satellite count, HDOP, flight mode or a real GPS fix type through the browser.
- This setup validates ingestion, transmission, storage and display with real sensor data. It does **not** validate
  flight-controller accuracy or replace a flight test.
- Quick Tunnel URLs are public and temporary. The token is the only protection, so do not share the link.

> The telemetry pipeline was validated with real-time data from an iPhone, used as a temporary sensor platform.
> The values came from the phone's physical GPS, compass and motion sensors, not from generated mock data. A
> gateway adapter converted them into the same 22-field MQTT contract used by the MAVLink publisher, so the
> backend handled phone and drone telemetry in the same way. Values the phone cannot measure (battery, flight
> mode, attitude, satellite count) were published as null rather than estimated. This setup validates
> ingestion, transmission, storage and visualisation, but does not replace flight testing with an actual UAV.
