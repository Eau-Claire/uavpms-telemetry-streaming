# Raspberry Pi 3 as companion computer

The Pi sits on the drone, reads MAVLink from the flight controller over a serial port and publishes over
Wi-Fi (or a 4G dongle / phone hotspot) to the MQTT broker. It replaces Mission Planner as the source.

```
Flight controller UART ──MAVLink──▶ Raspberry Pi 3 (publisher) ──Wi-Fi/4G, MQTT/TLS──▶ broker ──▶ UAV-PMS
```

## 1. Wiring

| Flight controller | Raspberry Pi 3 (GPIO header) |
|---|---|
| TX | pin 10 (GPIO15, RXD) |
| RX | pin 8 (GPIO14, TXD), optional (only needed to request data streams) |
| GND | pin 6 (GND) |

Both sides are 3.3 V UART. Do **not** connect the FC 5 V pin to the Pi 3.3 V pins.
You can also use USB instead (`/dev/ttyACM0`). USB is simpler but powers/resets some flight controllers.

Flight controller setting:
- **ArduPilot:** on that SERIALx port set `SERIALx_PROTOCOL = 2` (MAVLink2) and `SERIALx_BAUD = 57` (57600).
- **Betaflight:** Ports tab → that UART → Telemetry output **MAVLink**, 57600. Betaflight MAVLink telemetry
  is one-way and sends the six messages the publisher uses (HEARTBEAT, SYS_STATUS, GPS_RAW_INT,
  GLOBAL_POSITION_INT, ATTITUDE, VFR_HUD). Set `MAVLINK_REQUEST_STREAMS=false`. `flight_mode` may show a
  generic value because Betaflight does not use ArduPilot mode numbers.

## 2. Enable the Pi UART

```bash
sudo raspi-config        # Interface Options -> Serial Port: login shell = No, serial hardware = Yes
echo "dtoverlay=disable-bt" | sudo tee -a /boot/firmware/config.txt   # older OS: /boot/config.txt
sudo systemctl disable hciuart
sudo reboot
ls -l /dev/serial0       # should point to ttyAMA0
```

On the Pi 3, Bluetooth uses the stable PL011 UART. `disable-bt` gives that UART to GPIO14/15, so MAVLink at
57600 baud is reliable.

## 3. Install

Raspberry Pi OS Bookworm (Python 3.11) as user `pi`:

```bash
sudo apt update && sudo apt install -y git python3-venv
cd ~ && git clone https://github.com/Eau-Claire/uavpms-telemetry-streaming.git
cd uavpms-telemetry-streaming
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
```

In `.env`, set at least:

```env
DRONE_ID=UAV-HCM-01                 # UavCode in UAV-PMS
MAVLINK_CONNECTION=/dev/serial0     # or /dev/ttyACM0 over USB
MAVLINK_BAUD=57600
MQTT_HOST=...                       # broker host
MQTT_USERNAME=...
MQTT_PASSWORD=...
```

```bash
chmod 600 .env
sudo usermod -aG dialout pi           # serial port access (log out and in again)
.venv/bin/python -m publisher         # test in the foreground first, Ctrl+C to stop
```

The log must show `MQTT connected` and `autopilot heartbeat from system N`.

## 4. Run at boot

```bash
sudo cp deploy/raspberry-pi/uavpms-telemetry.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now uavpms-telemetry
journalctl -u uavpms-telemetry -f      # live log
```

If your user or folder is not `pi` / `/home/pi/uavpms-telemetry-streaming`, edit `User=`,
`WorkingDirectory=` and `ExecStart=` in the service file first. The service restarts automatically after a
crash. It does not restart on a config error (exit code 2): fix `.env`, then run
`sudo systemctl restart uavpms-telemetry`.

## Without a flight controller (bench test)

```bash
# .env: MAVLINK_CONNECTION=udpin:127.0.0.1:14550
.venv/bin/python tools/fake_autopilot.py &      # simulated drone
.venv/bin/python -m publisher
```

## Notes

- **Clock:** `timestamp` uses the Pi clock. The Pi 3 has no RTC, so it needs internet (NTP) before
  flying. Check with `timedatectl` (`System clock synchronized: yes`).
- **Wi-Fi range:** telemetry stops when the drone leaves Wi-Fi range. The publisher pauses and resumes on
  its own; up to 5 minutes of messages are queued while the broker is unreachable. For real flights, use a
  4G dongle or a phone hotspot carried on the drone.
- **Power:** feed the Pi a stable 5 V / 2.5 A from a BEC. Under-voltage (`vcgencmd get_throttled` ≠ `0x0`)
  causes random resets.
