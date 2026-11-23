# Local end-to-end run, 2026-11-23

**Setup**
- `tools/fake_autopilot.py` sends ArduCopter MAVLink (4 Hz) over UDP, plus a GCS heartbeat from system 255.
- `python -m publisher` reads it (`udpin:127.0.0.1:14550`).
- The broker is Mosquitto 2.1.4 with **TLS on 8883** (test CA) and **username/password** (anonymous access refused).
- `tools/check_subscriber.py` checks every message against the 22-field contract.
- Settings: DRONE_ID `UAV-HCM-01`, interval 2 s, QoS 1.

EMQX Cloud has not been tested yet: no credentials were available. The same commands work against it once
`.env` holds the EMQX host, user and password.

| # | Scenario | Result | Files |
|---|---|---|---|
| 1 | Normal flight, 30 s | **Pass.** 15/15 valid messages, interval avg 2.00 s (max 2.01 s), QoS 1, all 22 fields filled, GCS heartbeat ignored | `basic_30s.jsonl`, `basic_30s_publisher.log` |
| 2 | GPS fix lost after 10 s (fix 1, lat/lon 0 from the autopilot) | **Pass.** `lat`/`lon`/`alt_msl_m` = `null`, `gps_fix_type` = 1; no message with a false position | `part1_before_outage.jsonl`, `part2_after_outage.jsonl` |
| 3 | Broker stopped for 7 s (`broker_events.txt`) | **Pass.** Publisher logged the disconnect, reconnected automatically within 1 s of the broker coming back, `published 21 = acked 21` | `publisher.log` |
| 4 | QoS 1 messages sent while the broker was down | **Pass** (earlier run). 5 messages from the outage (timestamps 03:55:51 to 03:55:58) were delivered together right after reconnecting, in order. In the final run the checker reconnected after that burst, so it did not see it there. | `queue_delivery_earlier_run.jsonl`, `queue_delivery_earlier_run_publisher.log` |
| 5 | MAVLink link lost for 8 s, then back | **Pass.** "telemetry paused" logged after 5 s, no messages while down, publishing resumed on the next heartbeat | `publisher.log`, `part2_after_outage.jsonl` |
| 6 | Wrong MQTT password | **Pass.** `broker refused connection: Not authorized` logged, retries with backoff, nothing crashes | `wrong_password_publisher.log` |
| 7 | Invalid config (`MQTT_PORT=x`, `DRONE_ID=a/b`) | **Pass.** Exit code 2 with the variable name | console only |
| 8 | Unit tests | **Pass.** 16/16 | `python -m unittest discover -s tests -t .` |

The earlier run in row 4 used the same code, except for two changes made afterwards: the maximum
reconnect backoff went from 30 s to 10 s, and a shutdown log message was fixed. Passwords are masked (`***`)
in every log.
