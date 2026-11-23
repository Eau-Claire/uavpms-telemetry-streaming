import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { loadEnvFiles, readConfig } from "../src/config.js";

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "phone-cfg-"));
const write = (name, body) => { const f = path.join(dir, name); fs.writeFileSync(f, body); return f; };

test("empty MQTT_HOST in phone_gateway/.env does not hide the repo .env value", () => {
  const own = write("own.env", "PHONE_ACCESS_TOKEN=0123456789abcdef\nMQTT_HOST=\nMQTT_PORT=\n");
  const repo = write("repo.env", "MQTT_HOST=broker.example\nMQTT_PORT=8883\nMQTT_USERNAME=u\nDRONE_ID=UAV-HCM-01\n");
  const env = {};
  loadEnvFiles([own, repo], env);
  const cfg = readConfig(env);
  assert.equal(cfg.mqtt.host, "broker.example");
  assert.equal(cfg.mqtt.port, 8883);
  assert.equal(cfg.mqtt.username, "u");
  assert.equal(cfg.deviceId, "PHONE-001");               // publisher's DRONE_ID is not reused
  assert.match(cfg.mqtt.clientId, /^uavpms-phone-/);      // never the publisher's MQTT_CLIENT_ID
});

test("own file wins over repo file; real environment wins over both; PHONE_MQTT=off disables MQTT", () => {
  const own = write("own2.env", "PHONE_ACCESS_TOKEN=0123456789abcdef\nMQTT_HOST=own.example\n");
  const repo = write("repo2.env", "MQTT_HOST=repo.example\n");
  const env = { MQTT_PORT: "1883" };
  loadEnvFiles([own, repo], env);
  assert.equal(env.MQTT_HOST, "own.example");
  assert.equal(readConfig(env).mqtt.port, 1883);
  assert.equal(readConfig({ ...env, PHONE_MQTT: "off" }).mqtt, null);
});
