import time
import json
import math
import paho.mqtt.client as mqtt
from pymavlink import mavutil

# ================= CẤU HÌNH =================
MP_UDP_SOURCE = "udpin:127.0.0.1:14550"

MQTT_BROKER = "fff1a88e.ala.us-east-1.emqxsl.com"
MQTT_PORT = 8883
MQTT_TOPIC = "drone/DRONE_01/telemetry"
MQTT_USER = "YOUR_EMQX_USERNAME"
MQTT_PASSWORD = "YOUR_EMQX_PASSWORD"

SEND_INTERVAL = 2  # Delay 2s mỗi lần gửi telemetry lên EMQX Cloud
DRONE_ID = "DRONE_01"
# ============================================


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[+] Đã kết nối thành công tới EMQX Cloud ({MQTT_BROKER}:{MQTT_PORT})!")
    else:
        print(f"[X] Kết nối EMQX Cloud thất bại, mã lỗi: {reason_code}")


def start_mqtt_bridge():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bridge_{DRONE_ID}")
    client.on_connect = on_connect

    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    # EMQX Cloud yêu cầu MQTT qua TLS/SSL trên port 8883.
    client.tls_set()
    client.tls_insecure_set(False)

    print("[*] Đang kết nối tới EMQX Cloud Broker...")
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    print(f"[*] Đang lắng nghe Mission Planner tại {MP_UDP_SOURCE}...")
    master = mavutil.mavlink_connection(MP_UDP_SOURCE)

    print("[*] Đang chờ tín hiệu Heartbeat từ Mission Planner...")
    master.wait_heartbeat()
    print(f"[+] Đã thông luồng với Drone (System ID: {master.target_system})!")

    last_sent_time = 0
    has_new_data = False

    # Toàn bộ các chỉ số Telemetry sẽ gửi lên EMQX Cloud
    telemetry_state = {
        "drone_id": DRONE_ID,
        "timestamp": 0,
        # 1. Trạng thái hệ thống
        "flight_mode": "UNKNOWN",
        "armed": False,
        # 2. Vị trí GPS & Vệ tinh
        "lat": 0.0,
        "lon": 0.0,
        "alt_relative_m": 0.0,   # Độ cao so với điểm cất cánh (m)
        "alt_msl_m": 0.0,        # Độ cao mực nước biển (m)
        "satellites": 0,         # Số vệ tinh bắt được
        "gps_fix_type": 0,       # 0-1: No Fix, 2: 2D, 3: 3D Fix
        "hdop": 0.0,             # Sai số GPS
        # 3. Góc nghiêng (Attitude)
        "roll_deg": 0.0,
        "pitch_deg": 0.0,
        "yaw_deg": 0.0,
        # 4. Tốc độ & Ga (HUD)
        "ground_speed_ms": 0.0,  # Vận tốc mặt đất (m/s)
        "air_speed_ms": 0.0,     # Vận tốc gió (m/s)
        "climb_rate_ms": 0.0,    # Tốc độ leo cao (m/s)
        "heading_deg": 0,        # Hướng la bàn (0-360 độ)
        "throttle_pct": 0,       # Phần trăm ga (%)
        # 5. Nguồn điện / Pin
        "battery_voltage_v": 0.0,
        "battery_current_a": 0.0,
        "battery_remaining_pct": -1
    }

    target_messages = [
        "HEARTBEAT",
        "GLOBAL_POSITION_INT",
        "GPS_RAW_INT",
        "ATTITUDE",
        "VFR_HUD",
        "SYS_STATUS"
    ]

    while True:
        msg = master.recv_match(type=target_messages, blocking=False)

        if msg is not None:
            msg_type = msg.get_type()

            if msg_type == "HEARTBEAT":
                # Bỏ qua gói heartbeat của chính Mission Planner (GCS)
                if msg.type != mavutil.mavlink.MAV_TYPE_GCS:
                    telemetry_state["flight_mode"] = mavutil.mode_string_v10(msg)
                    telemetry_state["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    has_new_data = True

            elif msg_type == "GLOBAL_POSITION_INT":
                telemetry_state["lat"] = round(msg.lat / 1e7, 7)
                telemetry_state["lon"] = round(msg.lon / 1e7, 7)
                telemetry_state["alt_relative_m"] = round(msg.relative_alt / 1000.0, 2)
                telemetry_state["alt_msl_m"] = round(msg.alt / 1000.0, 2)
                has_new_data = True

            elif msg_type == "GPS_RAW_INT":
                telemetry_state["satellites"] = msg.satellites_visible
                telemetry_state["gps_fix_type"] = msg.fix_type
                telemetry_state["hdop"] = round(msg.eph / 100.0, 2) if msg.eph != 65535 else 0.0
                has_new_data = True

            elif msg_type == "ATTITUDE":
                telemetry_state["roll_deg"] = round(math.degrees(msg.roll), 2)
                telemetry_state["pitch_deg"] = round(math.degrees(msg.pitch), 2)
                telemetry_state["yaw_deg"] = round(math.degrees(msg.yaw), 2)
                has_new_data = True

            elif msg_type == "VFR_HUD":
                telemetry_state["ground_speed_ms"] = round(msg.groundspeed, 2)
                telemetry_state["air_speed_ms"] = round(msg.airspeed, 2)
                telemetry_state["climb_rate_ms"] = round(msg.climb, 2)
                telemetry_state["heading_deg"] = msg.heading
                telemetry_state["throttle_pct"] = msg.throttle
                has_new_data = True

            elif msg_type == "SYS_STATUS":
                telemetry_state["battery_voltage_v"] = round(msg.voltage_battery / 1000.0, 2)
                telemetry_state["battery_current_a"] = round(msg.current_battery / 100.0, 2) if msg.current_battery != -1 else 0.0
                telemetry_state["battery_remaining_pct"] = msg.battery_remaining
                has_new_data = True

        current_time = time.time()

        if has_new_data and (current_time - last_sent_time >= SEND_INTERVAL):
            telemetry_state["timestamp"] = int(time.time() * 1000)
            payload_str = json.dumps(telemetry_state)

            info = client.publish(MQTT_TOPIC, payload_str, qos=0)
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                # In toàn bộ JSON ra màn hình để kiểm tra đủ tất cả chỉ số
                print(f"[OK] {payload_str}\n")
            else:
                print(f"[!] Lỗi publish MQTT (mã {info.rc})")

            last_sent_time = current_time
            has_new_data = False

        time.sleep(0.005)


if __name__ == "__main__":
    try:
        start_mqtt_bridge()
    except KeyboardInterrupt:
        print("\n[*] Đã dừng cầu nối MQTT.")