// UAV-PMS phone gateway.
//
//   iPhone Safari --WSS (tunnel)--> this gateway --MQTT/TLS--> drone/{PHONE_DEVICE_ID}/telemetry --> backend (TR-03)
//                                        |--> data/telemetry-YYYY-MM-DD.jsonl (history, raw + contract)
//                                        '--> /dashboard viewers (live WebSocket)
//
// The phone sends raw sensor samples about once per second. Every PUBLISH_INTERVAL_S the gateway publishes
// one contract message, only if a new sample arrived since the last one and it is not older than STALE_AFTER_S.
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, WebSocket } from "ws";
import { ConfigError, loadEnvFiles, readConfig } from "./config.js";
import { parseSample, toContract } from "./adapter.js";
import { createMqttSink } from "./mqtt.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(here, "..", "public");
const PAGES = { "/": "index.html", "/index.html": "index.html", "/dashboard": "dashboard.html", "/dashboard.html": "dashboard.html" };

const ts = () => new Date().toISOString();
const defaultLog = (msg) => console.log(`${ts()} ${msg}`);

function tokenOk(given, expected) {
  if (typeof given !== "string") return false;
  const a = crypto.createHash("sha256").update(given).digest();
  const b = crypto.createHash("sha256").update(expected).digest();
  return crypto.timingSafeEqual(a, b);
}

function requestToken(req, url) {
  const auth = req.headers.authorization || "";
  return auth.startsWith("Bearer ") ? auth.slice(7) : url.searchParams.get("token");
}

function sendJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(body));
}

function historyFile(dir, ms) {
  return path.join(dir, `telemetry-${new Date(ms).toISOString().slice(0, 10)}.jsonl`);
}

/** Read history lines between from/to (ms, inclusive) from the daily JSONL files. Newest last. */
function readHistory(dir, fromMs, toMs, limit) {
  if (!fs.existsSync(dir)) return [];
  const fromDay = new Date(fromMs).toISOString().slice(0, 10);
  const toDay = new Date(toMs).toISOString().slice(0, 10);
  const files = fs
    .readdirSync(dir)
    .filter((f) => /^telemetry-\d{4}-\d{2}-\d{2}\.jsonl$/.test(f))
    .filter((f) => f.slice(10, 20) >= fromDay && f.slice(10, 20) <= toDay)
    .sort();
  const out = [];
  for (const f of files) {
    for (const line of fs.readFileSync(path.join(dir, f), "utf8").split("\n")) {
      if (!line) continue;
      try {
        const rec = JSON.parse(line);
        const t = Date.parse(rec.received_at);
        if (t >= fromMs && t <= toMs) out.push(rec);
      } catch {
        /* a partially written last line is skipped */
      }
    }
  }
  return out.slice(-limit);
}

export function startGateway(cfg, { log = defaultLog } = {}) {
  fs.mkdirSync(cfg.historyDir, { recursive: true });
  const topic = `drone/${cfg.deviceId}/telemetry`;
  const mqttSink = createMqttSink(cfg.mqtt, log);

  const state = {
    sender: null, // the one active phone connection
    latest: null, // {sample, receivedAt}
    lastPublishedAt: 0, // receivedAt of the sample used in the last publish
    lastContract: null,
    session: { homeAltitudeM: null },
    online: false,
    counts: { samples: 0, rejected: 0, published: 0 },
  };
  const viewers = new Set();
  const broadcast = (msg) => {
    const body = JSON.stringify(msg);
    for (const v of viewers) if (v.readyState === WebSocket.OPEN) v.send(body);
  };
  const statusMsg = () => ({
    type: "status",
    device_id: cfg.deviceId,
    online: state.online,
    sender_connected: state.sender !== null,
    last_sample_at: state.latest ? new Date(state.latest.receivedAt).toISOString() : null,
    mqtt: mqttSink.stats(),
    counts: state.counts,
  });

  // ---------- HTTP ----------
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (req.method !== "GET") return sendJson(res, 405, { error: "method not allowed" });
    if (PAGES[url.pathname]) {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
      return fs.createReadStream(path.join(PUBLIC, PAGES[url.pathname])).pipe(res);
    }
    if (url.pathname === "/healthz") return sendJson(res, 200, { ok: true });
    if (!url.pathname.startsWith("/api/")) return sendJson(res, 404, { error: "not found" });
    if (!tokenOk(requestToken(req, url), cfg.token)) return sendJson(res, 401, { error: "invalid token" });

    if (url.pathname === "/api/telemetry/latest") return sendJson(res, 200, state.lastContract);
    if (url.pathname === "/api/status") return sendJson(res, 200, statusMsg());
    if (url.pathname === "/api/telemetry/history") {
      const now = Date.now();
      const from = url.searchParams.has("from") ? Date.parse(url.searchParams.get("from")) : now - 3600_000;
      const to = url.searchParams.has("to") ? Date.parse(url.searchParams.get("to")) : now;
      const limit = Math.min(Number(url.searchParams.get("limit")) || 1000, 20000);
      if (!Number.isFinite(from) || !Number.isFinite(to)) return sendJson(res, 400, { error: "from/to must be ISO 8601" });
      return sendJson(res, 200, readHistory(cfg.historyDir, from, to, limit));
    }
    return sendJson(res, 404, { error: "not found" });
  });

  // ---------- WebSocket ----------
  const wss = new WebSocketServer({ noServer: true, maxPayload: 8192 });
  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url, "http://localhost");
    const role = url.searchParams.get("role");
    if (url.pathname !== "/ws" || !["sender", "viewer"].includes(role) || !tokenOk(url.searchParams.get("token"), cfg.token)) {
      socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      return socket.destroy();
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      ws.isAlive = true;
      ws.on("pong", () => (ws.isAlive = true));
      role === "sender" ? onSender(ws, req) : onViewer(ws);
    });
  });

  function onSender(ws, req) {
    if (state.sender) {
      log("new phone connection replaces the previous one");
      state.sender.close(4000, "replaced by a newer connection");
    }
    state.sender = ws;
    state.session = { homeAltitudeM: null }; // new session = new "home" for alt_relative_m
    log(`phone connected from ${req.headers["cf-connecting-ip"] || req.socket.remoteAddress}`);
    broadcast(statusMsg());

    ws.on("message", (raw) => {
      const { sample, error } = parseSample(raw);
      if (error) {
        state.counts.rejected++;
        if (state.counts.rejected <= 20 || state.counts.rejected % 100 === 0) log(`rejected sample: ${error}`);
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "error", error }));
        return;
      }
      const receivedAt = Date.now();
      state.counts.samples++;
      state.latest = { sample, receivedAt };
      broadcast({ type: "sample", received_at: new Date(receivedAt).toISOString(), sample });
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ack", seq: sample.seq }));
    });
    ws.on("close", (code) => {
      if (state.sender === ws) {
        state.sender = null;
        log(`phone disconnected (code ${code})`);
        broadcast(statusMsg());
      }
    });
  }

  function onViewer(ws) {
    viewers.add(ws);
    ws.send(JSON.stringify({ ...statusMsg(), type: "hello", topic, latest: state.lastContract }));
    ws.on("close", () => viewers.delete(ws));
  }

  // ---------- loops ----------
  const publishTimer = setInterval(() => {
    const l = state.latest;
    if (!l || l.receivedAt <= state.lastPublishedAt) return; // nothing new: never resend old data as new
    if (Date.now() - l.receivedAt > cfg.staleAfterS * 1000) return;
    state.lastPublishedAt = l.receivedAt;

    const contract = toContract(l.sample, l.receivedAt, cfg, state.session);
    const mqtt = mqttSink.publish(topic, contract);
    state.lastContract = contract;
    state.counts.published++;
    const record = { received_at: contract.timestamp, topic, mqtt, contract, raw: l.sample };
    fs.appendFile(historyFile(cfg.historyDir, l.receivedAt), JSON.stringify(record) + "\n", (err) => {
      if (err) log(`history write failed: ${err.message}`);
    });
    broadcast({ type: "telemetry", mqtt, contract });
    const p = l.sample.position;
    log(
      `SENT ${topic} [${mqtt}] lat=${contract.lat} lon=${contract.lon} acc=${p ? p.accuracy_m.toFixed(1) : "-"}m ` +
        `fix=${contract.gps_fix_type} alt_rel=${contract.alt_relative_m} spd=${contract.ground_speed_ms} hdg=${contract.heading_deg}`,
    );
  }, cfg.publishIntervalS * 1000);

  const statusTimer = setInterval(() => {
    const online = state.sender !== null && state.latest !== null && Date.now() - state.latest.receivedAt <= cfg.staleAfterS * 1000;
    if (online !== state.online) {
      state.online = online;
      log(online ? "device ONLINE" : state.sender ? `device OFFLINE (no sample for ${cfg.staleAfterS}s)` : "device OFFLINE (phone disconnected)");
    }
    broadcast(statusMsg());
  }, 1000);

  const pingTimer = setInterval(() => {
    for (const ws of wss.clients) {
      if (!ws.isAlive) {
        ws.terminate();
        continue;
      }
      ws.isAlive = false;
      ws.ping();
    }
  }, 10_000);

  const statsTimer = setInterval(() => log(`stats ${JSON.stringify({ ...state.counts, mqtt: mqttSink.stats() })}`), 60_000);

  return new Promise((resolve) => {
    server.listen(cfg.port, cfg.host, () => {
      const { port } = server.address();
      log(`phone gateway on http://${cfg.host}:${port} device=${cfg.deviceId} mqtt=${cfg.mqtt ? `${cfg.mqtt.host}:${cfg.mqtt.port}` : "off"}`);
      log(`open <tunnel URL>/?token=<PHONE_ACCESS_TOKEN> on the phone, <tunnel URL>/dashboard?token=... to watch`);
      resolve({
        port,
        async close() {
          for (const t of [publishTimer, statusTimer, pingTimer, statsTimer]) clearInterval(t);
          for (const ws of wss.clients) ws.terminate();
          await mqttSink.close();
          await new Promise((r) => server.close(r));
        },
      });
    });
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  loadEnvFiles();
  let cfg;
  try {
    cfg = readConfig();
  } catch (e) {
    if (e instanceof ConfigError) {
      console.error(`config error: ${e.message}`);
      process.exit(2);
    }
    throw e;
  }
  const gw = await startGateway(cfg);
  for (const sig of ["SIGINT", "SIGTERM"]) {
    process.on(sig, async () => {
      defaultLog("shutting down");
      await gw.close();
      process.exit(0);
    });
  }
}
