"""MQTT publisher with TLS, credentials from settings and automatic reconnection (paho-mqtt 2.x)."""
from __future__ import annotations

import json
import logging
import ssl
import threading

import paho.mqtt.client as mqtt

from .config import Settings

log = logging.getLogger("publisher.mqtt")


class MqttPublisher:
    def __init__(self, s: Settings):
        self.s = s
        self.connected = threading.Event()
        self._stopping = False
        self.stats = {"published": 0, "acked": 0, "queue_full": 0, "errors": 0, "connects": 0, "disconnects": 0}
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=s.mqtt_client_id,
                        protocol=mqtt.MQTTv311, clean_session=True)
        if s.mqtt_username:
            c.username_pw_set(s.mqtt_username, s.mqtt_password or None)
        if s.mqtt_tls:
            ca = s.mqtt_ca_cert or None
            if ca is None:
                try:
                    import certifi
                    ca = certifi.where()
                except ImportError:
                    pass
            c.tls_set(ca_certs=ca, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        # QoS 1 messages published while offline are kept (up to this many) and sent after reconnecting.
        c.max_queued_messages_set(s.mqtt_max_queued)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_publish = self._on_publish
        self.client = c

    def start(self):
        log.info("connecting to %s:%s (tls=%s) as %s", self.s.mqtt_host, self.s.mqtt_port,
                 self.s.mqtt_tls, self.s.mqtt_client_id)
        # connect_async + loop_start: the network thread connects and keeps reconnecting on its own.
        self.client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=self.s.mqtt_keepalive_s)
        self.client.loop_start()

    def stop(self):
        self._stopping = True
        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()

    def publish(self, topic: str, payload: dict) -> bool:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        info = self.client.publish(topic, body, qos=self.s.mqtt_qos)
        if info.rc == mqtt.MQTT_ERR_SUCCESS or (info.rc == mqtt.MQTT_ERR_NO_CONN and self.s.mqtt_qos > 0):
            self.stats["published"] += 1   # NO_CONN with QoS>0: queued, sent after reconnect
            return True
        if info.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
            self.stats["queue_full"] += 1
            log.warning("offline queue full (%d); telemetry dropped", self.s.mqtt_max_queued)
        else:
            self.stats["errors"] += 1
            log.warning("publish failed: %s", mqtt.error_string(info.rc))
        return False

    # --- callbacks (network thread) ---
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("broker refused connection: %s", reason_code)
            return
        self.stats["connects"] += 1
        self.connected.set()
        log.info("MQTT connected (%s)", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        if self._stopping:
            log.info("MQTT disconnected")
            return
        self.stats["disconnects"] += 1
        log.warning("MQTT disconnected (%s); reconnecting automatically", reason_code)

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self.stats["acked"] += 1
