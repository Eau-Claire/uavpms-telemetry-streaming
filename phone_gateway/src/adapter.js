// Phone adapter: turns raw browser sensor samples into the 22-field contract used by the MAVLink publisher
// (docs/requirement.md section 4). The backend subscriber then handles phone and drone the same way.
//
// Only values a phone really measures are filled. Everything else is null, never invented:
//   flight_mode, armed, satellites, hdop, roll/pitch/yaw, air_speed, climb_rate, throttle, battery -> null
//   alt_msl_m -> null: the browser altitude is height above the WGS84 ellipsoid, not above mean sea level.
//   alt_relative_m -> altitude minus the first valid altitude of the session (the session start is "home").
//   gps_fix_type -> derived: 3 with altitude, 2 without, 1 when the fix is missing, stale or too inaccurate.
//     The browser does not expose the real fix type. lat/lon are null whenever gps_fix_type < 2 (AC-08).
//   heading_deg -> iOS compass heading (webkitCompassHeading) when available, else GPS course over ground.

export const FIELDS = [
  "drone_id", "timestamp",
  "flight_mode", "armed",
  "lat", "lon", "alt_relative_m", "alt_msl_m", "satellites", "gps_fix_type", "hdop",
  "roll_deg", "pitch_deg", "yaw_deg",
  "ground_speed_ms", "air_speed_ms", "climb_rate_ms", "heading_deg", "throttle_pct",
  "battery_voltage_v", "battery_current_a", "battery_remaining_pct",
];

const MAX_MESSAGE_BYTES = 4096;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const numOrNull = (v) => (isNum(v) ? v : null);
const round = (v, d = 2) => (isNum(v) ? Number(v.toFixed(d)) : null);

/** Parse and sanity-check one message from the phone. Returns {sample} or {error}. */
export function parseSample(raw) {
  if (raw.length > MAX_MESSAGE_BYTES) return { error: "message too large" };
  let m;
  try {
    m = JSON.parse(raw.toString());
  } catch {
    return { error: "not JSON" };
  }
  if (!m || typeof m !== "object" || m.type !== "sample") return { error: "type must be 'sample'" };
  if (!Number.isInteger(m.seq) || m.seq < 0) return { error: "seq must be a non-negative integer" };

  let position = null;
  const p = m.position;
  if (p != null) {
    if (typeof p !== "object") return { error: "position must be an object or null" };
    if (!isNum(p.lat) || !isNum(p.lon) || Math.abs(p.lat) > 90 || Math.abs(p.lon) > 180)
      return { error: "position lat/lon out of range" };
    if (!isNum(p.accuracy_m) || p.accuracy_m < 0) return { error: "position.accuracy_m required" };
    if (!isNum(p.fix_age_ms) || p.fix_age_ms < 0) return { error: "position.fix_age_ms required" };
    position = {
      lat: p.lat,
      lon: p.lon,
      accuracy_m: p.accuracy_m,
      altitude_m: numOrNull(p.altitude_m),
      altitude_accuracy_m: numOrNull(p.altitude_accuracy_m),
      speed_ms: isNum(p.speed_ms) && p.speed_ms >= 0 ? p.speed_ms : null,
      course_deg: isNum(p.course_deg) && p.course_deg >= 0 && p.course_deg < 360 ? p.course_deg : null,
      fix_age_ms: p.fix_age_ms,
      fix_time: typeof p.fix_time === "string" ? p.fix_time.slice(0, 40) : null,
    };
  }

  const triple = (o, keys) =>
    o && typeof o === "object" ? Object.fromEntries(keys.map((k) => [k, numOrNull(o[k])])) : null;
  const compass = numOrNull(m.compass_heading_deg);
  return {
    sample: {
      seq: m.seq,
      position,
      compass_heading_deg: compass !== null && compass >= 0 && compass <= 360 ? compass : null,
      compass_accuracy_deg: numOrNull(m.compass_accuracy_deg),
      orientation: triple(m.orientation, ["alpha", "beta", "gamma"]),
      acceleration: triple(m.acceleration, ["x", "y", "z"]),
      rotation_rate: triple(m.rotation_rate, ["alpha", "beta", "gamma"]),
    },
  };
}

/**
 * Build the contract payload from the latest sample.
 * @param sample      parsed sample (parseSample().sample)
 * @param receivedAt  gateway clock (ms) when the sample arrived; it is the payload timestamp, so the same
 *                    sample always yields the same (drone_id, timestamp) and the backend can deduplicate.
 * @param session     {homeAltitudeM} mutable per-connection state
 */
export function toContract(sample, receivedAt, { deviceId, maxAccuracyM, staleAfterS }, session) {
  const out = Object.fromEntries(FIELDS.map((k) => [k, null]));
  out.drone_id = deviceId;
  out.timestamp = new Date(receivedAt).toISOString();

  const p = sample.position;
  const usable =
    p !== null && p.fix_age_ms <= staleAfterS * 1000 && p.accuracy_m <= maxAccuracyM && !(p.lat === 0 && p.lon === 0);

  if (usable) {
    out.lat = round(p.lat, 7);
    out.lon = round(p.lon, 7);
    out.gps_fix_type = p.altitude_m !== null ? 3 : 2;
    if (p.altitude_m !== null) {
      if (session.homeAltitudeM == null) session.homeAltitudeM = p.altitude_m;
      out.alt_relative_m = round(p.altitude_m - session.homeAltitudeM);
    }
    out.ground_speed_ms = round(p.speed_ms);
  } else {
    out.gps_fix_type = 1;
  }

  const heading = sample.compass_heading_deg ?? (usable ? p.course_deg : null);
  out.heading_deg = heading === null ? null : round(heading % 360);
  return out;
}
