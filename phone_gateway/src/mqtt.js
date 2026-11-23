import fs from "node:fs";
import mqtt from "mqtt";

/**
 * MQTT output with the same behaviour as the Python publisher: automatic reconnect, QoS from config, and a
 * bounded queue while the broker is unreachable (oldest dropped first). Returns a no-op sink when MQTT is off.
 */
export function createMqttSink(cfg, log) {
  if (!cfg) {
    return { publish: () => "disabled", stats: () => ({ enabled: false }), close: async () => {} };
  }
  const stats = { enabled: true, connected: false, published: 0, acked: 0, queued: 0, dropped: 0, errors: 0 };
  const queue = [];
  const client = mqtt.connect({
    protocol: cfg.tls ? "mqtts" : "mqtt",
    host: cfg.host,
    port: cfg.port,
    username: cfg.username || undefined,
    password: cfg.password || undefined,
    clientId: cfg.clientId,
    keepalive: cfg.keepaliveS,
    reconnectPeriod: 2000,
    connectTimeout: 10_000,
    clean: true,
    ca: cfg.ca ? fs.readFileSync(cfg.ca) : undefined,
    queueQoSZero: false,
  });

  const send = (topic, body) => {
    stats.published++;
    client.publish(topic, body, { qos: cfg.qos }, (err) => {
      if (err) {
        stats.errors++;
        log(`MQTT publish failed: ${err.message}`);
      } else stats.acked++;
    });
  };

  client.on("connect", () => {
    stats.connected = true;
    log(`MQTT connected to ${cfg.host}:${cfg.port} (tls=${cfg.tls}); flushing ${queue.length} queued`);
    while (queue.length) send(...queue.shift());
    stats.queued = 0;
  });
  client.on("close", () => {
    if (stats.connected) log("MQTT disconnected; reconnecting");
    stats.connected = false;
  });
  client.on("error", (err) => log(`MQTT error: ${err.message}`)); // the password is never part of the message

  return {
    publish(topic, payload) {
      const body = JSON.stringify(payload);
      if (client.connected) {
        send(topic, body);
        return "sent";
      }
      if (cfg.maxQueued === 0) {
        stats.dropped++;
        return "dropped";
      }
      queue.push([topic, body]);
      if (queue.length > cfg.maxQueued) {
        queue.shift();
        stats.dropped++;
      }
      stats.queued = queue.length;
      return "queued";
    },
    stats: () => ({ ...stats }),
    close: () => new Promise((resolve) => client.end(false, {}, resolve)),
  };
}
