import { test } from "node:test";
import assert from "node:assert/strict";
import { FIELDS, parseSample, toContract } from "../src/adapter.js";

const cfg = { deviceId: "PHONE-001", maxAccuracyM: 50, staleAfterS: 5 };
const pos = (o = {}) => ({ lat: 10.7769, lon: 106.7009, accuracy_m: 8, altitude_m: 12.5, speed_ms: 1.4, course_deg: 90, fix_age_ms: 400, ...o });
const sample = (o = {}) => parseSample(JSON.stringify({ type: "sample", seq: 1, position: pos(), compass_heading_deg: 45, ...o })).sample;

test("contract has the 22 fields in publisher order", () => {
  const c = toContract(sample(), Date.UTC(2026, 10, 23, 5, 0, 0), cfg, {});
  assert.deepEqual(Object.keys(c), FIELDS);
  assert.equal(FIELDS.length, 22);
  assert.equal(c.timestamp, "2026-11-23T05:00:00.000Z");
});

test("fresh accurate fix fills position; fields a phone cannot measure stay null", () => {
  const session = {};
  const c = toContract(sample(), 0, cfg, session);
  assert.equal(c.lat, 10.7769); assert.equal(c.lon, 106.7009);
  assert.equal(c.gps_fix_type, 3);
  assert.equal(c.alt_relative_m, 0);            // first fix is home
  assert.equal(c.alt_msl_m, null);              // ellipsoid height is not MSL
  assert.equal(c.ground_speed_ms, 1.4);
  assert.equal(c.heading_deg, 45);              // compass wins over GPS course
  for (const k of ["flight_mode", "armed", "satellites", "hdop", "roll_deg", "pitch_deg", "yaw_deg", "air_speed_ms",
                   "climb_rate_ms", "throttle_pct", "battery_voltage_v", "battery_current_a", "battery_remaining_pct"])
    assert.equal(c[k], null, k);
  const c2 = toContract(sample({ position: pos({ altitude_m: 15 }) }), 1, cfg, session);
  assert.equal(c2.alt_relative_m, 2.5);
});

test("stale, inaccurate, (0,0) or missing fix never yields a position", () => {
  for (const s of [sample({ position: pos({ fix_age_ms: 6000 }) }), sample({ position: pos({ accuracy_m: 120 }) }),
                   sample({ position: pos({ lat: 0, lon: 0 }) }), sample({ position: null })]) {
    const c = toContract(s, 0, cfg, {});
    assert.equal(c.lat, null); assert.equal(c.lon, null);
    assert.equal(c.gps_fix_type, 1);
    assert.equal(c.ground_speed_ms, null);
  }
});

test("no altitude gives a 2D fix; no compass falls back to GPS course", () => {
  const c = toContract(sample({ compass_heading_deg: null, position: pos({ altitude_m: null }) }), 0, cfg, {});
  assert.equal(c.gps_fix_type, 2);
  assert.equal(c.alt_relative_m, null);
  assert.equal(c.heading_deg, 90);
});

test("parseSample rejects bad input", () => {
  const bad = (m) => parseSample(typeof m === "string" ? m : JSON.stringify(m)).error;
  assert.ok(bad("not json"));
  assert.ok(bad({ type: "telemetry", seq: 1 }));
  assert.ok(bad({ type: "sample", seq: -1 }));
  assert.ok(bad({ type: "sample", seq: 1, position: { lat: 91, lon: 0, accuracy_m: 5, fix_age_ms: 0 } }));
  assert.ok(bad({ type: "sample", seq: 1, position: { lat: 10, lon: 10, fix_age_ms: 0 } }));
  assert.ok(bad({ type: "sample", seq: 1, pad: "x".repeat(5000) }));
  assert.equal(bad({ type: "sample", seq: 1, position: null }), undefined);
});

test("parseSample drops non-finite and out-of-range sensor values", () => {
  const s = parseSample(JSON.stringify({ type: "sample", seq: 2, position: pos({ speed_ms: -1, course_deg: 400 }),
    compass_heading_deg: 999, orientation: { alpha: "x", beta: 10, gamma: null } })).sample;
  assert.equal(s.position.speed_ms, null);
  assert.equal(s.position.course_deg, null);
  assert.equal(s.compass_heading_deg, null);
  assert.deepEqual(s.orientation, { alpha: null, beta: 10, gamma: null });
});
