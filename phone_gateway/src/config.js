// Settings come from the environment. phone_gateway/.env is loaded first, then the repo's .env, so the
// MQTT_* values already set up for the MAVLink publisher are reused. Existing variables are never overridden.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ID_RE = /^[A-Za-z0-9_.-]{1,64}$/; // same rule as the publisher: no MQTT wildcards or '/'

export function loadEnvFiles() {
  for (const f of [path.join(here, "..", ".env"), path.join(here, "..", "..", ".env")]) {
    if (fs.existsSync(f)) process.loadEnvFile(f);
  }
}

class ConfigError extends Error {}

function num(env, name, def, { min = -Infinity, max = Infinity } = {}) {
  const raw = env[name];
  if (raw === undefined || raw === "") return def;
  const v = Number(raw);
  if (!Number.isFinite(v) || v < min || v > max) throw new ConfigError(`${name}=${raw} is not a number in [${min}, ${max}]`);
  return v;
}

function bool(env, name, def) {
  const raw = env[name];
  if (raw === undefined || raw === "") return def;
  return ["1", "true", "yes", "on"].includes(raw.toLowerCase());
}

export function readConfig(env = process.env) {
  const deviceId = env.PHONE_DEVICE_ID || "PHONE-001";
  if (!ID_RE.test(deviceId)) throw new ConfigError(`PHONE_DEVICE_ID=${deviceId} may only use letters, digits, '_', '.', '-'`);
  const token = env.PHONE_ACCESS_TOKEN || "";
  if (token.length < 16) throw new ConfigError("PHONE_ACCESS_TOKEN must be set (at least 16 characters); the tunnel URL is public");
  const mqttHost = env.MQTT_HOST || "";
  return {
    deviceId,
    token,
    host: env.PHONE_HTTP_HOST || "127.0.0.1",
    port: num(env, "PHONE_HTTP_PORT", 3000, { min: 0, max: 65535 }),
    publishIntervalS: num(env, "PUBLISH_INTERVAL_S", 2, { min: 0.1, max: 60 }),
    staleAfterS: num(env, "STALE_AFTER_S", 5, { min: 1, max: 300 }),
    maxAccuracyM: num(env, "PHONE_MAX_ACCURACY_M", 50, { min: 1, max: 10000 }),
    historyDir: env.PHONE_HISTORY_DIR || path.join(here, "..", "data"),
    mqtt: mqttHost
      ? {
          host: mqttHost,
          port: num(env, "MQTT_PORT", 8883, { min: 1, max: 65535 }),
          tls: bool(env, "MQTT_TLS", true),
          ca: env.MQTT_CA_CERT || "",
          username: env.MQTT_USERNAME || "",
          password: env.MQTT_PASSWORD || "",
          clientId: env.PHONE_MQTT_CLIENT_ID || `uavpms-phone-${deviceId}-${process.pid}`,
          qos: num(env, "MQTT_QOS", 1, { min: 0, max: 2 }),
          keepaliveS: num(env, "MQTT_KEEPALIVE_S", 30, { min: 5, max: 600 }),
          maxQueued: num(env, "MQTT_MAX_QUEUED", 150, { min: 0, max: 100000 }),
        }
      : null,
  };
}

export { ConfigError };
