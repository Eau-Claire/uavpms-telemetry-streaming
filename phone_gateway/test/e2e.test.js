// End-to-end: WebSocket "phone" -> gateway -> real MQTT broker (aedes, in-process) -> subscriber.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { once } from "node:events";
import aedes from "aedes";
import mqtt from "mqtt";
import { WebSocket } from "ws";
import { startGateway } from "../src/server.js";
import { FIELDS } from "../src/adapter.js";

const TOKEN = "test-token-0123456789";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function setup() {
  const broker = await aedes.createBroker();
  const brokerServer = net.createServer(broker.handle);
  await new Promise((r) => brokerServer.listen(0, "127.0.0.1", r));
  const mqttPort = brokerServer.address().port;
  const historyDir = fs.mkdtempSync(path.join(os.tmpdir(), "phone-gw-"));
  const logs = [];
  const gw = await startGateway({
    deviceId: "PHONE-001", token: TOKEN, host: "127.0.0.1", port: 0, publishIntervalS: 0.3, staleAfterS: 1, maxAccuracyM: 50, historyDir,
    mqtt: { host: "127.0.0.1", port: mqttPort, tls: false, ca: "", username: "", password: "", clientId: "gw-test", qos: 1, keepaliveS: 30, maxQueued: 10 },
  }, { log: (m) => logs.push(m) });
  const sub = mqtt.connect(`mqtt://127.0.0.1:${mqttPort}`);
  await once(sub, "connect");
  await new Promise((r) => sub.subscribe("drone/+/telemetry", { qos: 1 }, r));
  const received = [];
  sub.on("message", (topic, buf) => received.push({ topic, p: JSON.parse(buf) }));
  await sleep(300); // gateway's own MQTT connect
  return {
    gw, received, historyDir, logs,
    url: (q) => `ws://127.0.0.1:${gw.port}/ws?${q}`,
    async close() { await gw.close(); sub.end(true); await new Promise((r) => broker.close(r)); brokerServer.close(); },
  };
}

const sample = (seq, position) => JSON.stringify({ type: "sample", seq, position, compass_heading_deg: 180,
  orientation: { alpha: 1, beta: 2, gamma: 3 } });
const fix = { lat: 21.0285, lon: 105.8542, accuracy_m: 6, altitude_m: 20, speed_ms: 0.8, course_deg: 10, fix_age_ms: 200 };

test("phone samples reach MQTT as the 22-field contract, once per new sample", async () => {
  const t = await setup();
  try {
    const phone = new WebSocket(t.url(`role=sender&token=${TOKEN}`));
    await once(phone, "open");
    phone.send(sample(1, fix));
    await sleep(900);  // 3 publish ticks but only one new sample
    assert.equal(t.received.length, 1, "an old sample must not be republished");
    const { topic, p } = t.received[0];
    assert.equal(topic, "drone/PHONE-001/telemetry");
    assert.deepEqual(Object.keys(p), FIELDS);
    assert.equal(p.lat, 21.0285); assert.equal(p.gps_fix_type, 3); assert.equal(p.heading_deg, 180);

    phone.send(sample(2, { ...fix, fix_age_ms: 9000 }));   // stale GPS fix
    await sleep(500);
    assert.equal(t.received.length, 2);
    assert.equal(t.received[1].p.lat, null);
    assert.equal(t.received[1].p.gps_fix_type, 1);

    // history file holds raw + contract
    const lines = fs.readdirSync(t.historyDir).flatMap((f) => fs.readFileSync(path.join(t.historyDir, f), "utf8").trim().split("\n"));
    assert.equal(lines.length, 2);
    const rec = JSON.parse(lines[0]);
    assert.equal(rec.mqtt, "sent"); assert.equal(rec.raw.position.accuracy_m, 6);

    const r = await fetch(`http://127.0.0.1:${t.gw.port}/api/telemetry/history`, { headers: { authorization: `Bearer ${TOKEN}` } });
    assert.equal((await r.json()).length, 2);
    phone.close();
  } finally { await t.close(); }
});

test("viewer sees status go offline when the phone stops sending", async () => {
  const t = await setup();
  try {
    const viewer = new WebSocket(t.url(`role=viewer&token=${TOKEN}`));
    const msgs = [];
    viewer.on("message", (d) => msgs.push(JSON.parse(d)));
    await once(viewer, "open");
    const phone = new WebSocket(t.url(`role=sender&token=${TOKEN}`));
    await once(phone, "open");
    phone.send(sample(1, fix));
    await sleep(1200);
    assert.ok(msgs.some((m) => m.type === "status" && m.online), "online while samples arrive");
    assert.ok(msgs.some((m) => m.type === "telemetry"));
    await sleep(1500);  // staleAfterS = 1, no new samples
    assert.equal(msgs.filter((m) => m.type === "status").at(-1).online, false);
    assert.ok(t.logs.some((l) => l.includes("OFFLINE")));
    phone.close(); viewer.close();
  } finally { await t.close(); }
});

test("wrong token is refused for WebSocket and API", async () => {
  const t = await setup();
  try {
    const ws = new WebSocket(t.url("role=sender&token=wrong"));
    const [err] = await once(ws, "error");
    assert.match(err.message, /401/);
    const r = await fetch(`http://127.0.0.1:${t.gw.port}/api/telemetry/latest?token=wrong`);
    assert.equal(r.status, 401);
    assert.equal(t.received.length, 0);
  } finally { await t.close(); }
});
