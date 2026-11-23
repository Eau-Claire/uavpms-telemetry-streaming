# Drone Telemetry Streaming Integration Requirements

**Feature:** Real-Time UAV Telemetry Monitoring  
**Project:** UAV-PMS  
**Status:** Planned / Not Implemented  
**Priority:** High

## 1. Objective

Implement a real-time telemetry streaming pipeline that receives flight data from Mission Planner through MAVLink, publishes it to EMQX Cloud using MQTT, and delivers telemetry updates to the UAV-PMS Web Portal through the backend.

The system must allow authorized users to monitor drone location, flight status, altitude, speed, heading, and battery level during an active mission.

## 2. System Architecture

Mission Planner → Python MAVLink Publisher → EMQX Cloud (MQTT/TLS) → .NET MQTT Subscriber → Database + SignalR → Angular Web Portal

## 3. Functional Requirements

### TR-01: MAVLink Telemetry Collection

**Responsible:** Python / Drone Integration

- Implement a Python service using `pymavlink`.
- Connect to the MAVLink telemetry stream provided by Mission Planner.
- Collect GPS, flight mode, attitude, speed, battery, and drone status.
- Convert MAVLink messages into a standardized JSON payload.
- Publish telemetry approximately every 2 seconds when fresh data is available.
- Handle missing or invalid telemetry values without generating false coordinates.

### TR-02: MQTT Telemetry Publishing

**Responsible:** Python / Drone Integration

- Connect to EMQX Cloud using MQTT over TLS on port `8883`.
- Publish telemetry to `drone/{droneId}/telemetry`.
- Use MQTT QoS 1 for telemetry delivery, subject to integration testing.
- Implement automatic reconnection after broker or network disconnection.
- Store MQTT credentials securely in environment variables.
- Support configurable Drone ID, broker address, and publish interval.

### TR-03: MQTT Telemetry Subscription

**Responsible:** Backend

- Implement a .NET `BackgroundService` using MQTTnet.
- Subscribe to `drone/+/telemetry`.
- Deserialize incoming JSON into a strongly typed telemetry DTO.
- Validate Drone ID, timestamp, GPS coordinates, and numerical values.
- Prevent duplicate or out-of-order messages from overwriting newer telemetry.
- Log connection failures, invalid messages, and processing errors.
- Reconnect automatically when the MQTT connection is interrupted.

### TR-04: Mission and Drone Association

**Responsible:** Backend

- Identify the UAV associated with each telemetry message.
- Resolve the currently active mission assigned to that UAV.
- Associate valid telemetry records with the corresponding mission.
- Handle telemetry received from unregistered drones or drones without active missions.
- Prevent telemetry from being assigned to an unrelated mission.

### TR-05: Telemetry Persistence

**Responsible:** Backend / Database

- Store the latest valid telemetry state for each drone.
- Store telemetry history for mission tracking and playback.
- Record both the source timestamp and backend reception timestamp.
- Support querying telemetry history by Drone ID, Mission ID, and time range.
- Define retention and cleanup policies for historical telemetry.

### TR-06: Real-Time SignalR Streaming

**Responsible:** Backend

- Implement a SignalR Hub for telemetry updates.
- Broadcast telemetry to authorized clients monitoring the corresponding mission.
- Provide a REST API for retrieving the latest telemetry snapshot.
- Restrict telemetry access based on existing UAV-PMS authentication and authorization.
- Detect telemetry timeout and notify clients when a drone becomes offline.

### TR-07: Real-Time Drone Monitoring UI

**Responsible:** Frontend

- Connect to the Backend SignalR Hub.
- Display the drone's latest GPS position on the Leaflet map.
- Update drone location without refreshing the page.
- Display altitude, ground speed, heading, battery percentage, flight mode, and armed status.
- Display the last telemetry update time.
- Show Online, Offline, and No GPS Fix states.
- Handle SignalR disconnection and reconnection.
- Support monitoring multiple drones without mixing their telemetry.

## 4. Telemetry Data Contract

The MQTT JSON payload must contain the following 22 fields:

| Group | Fields |
|---|---|
| Identity | `drone_id`, `timestamp` |
| Flight Status | `flight_mode`, `armed` |
| GPS | `lat`, `lon`, `alt_relative_m`, `alt_msl_m`, `satellites`, `gps_fix_type`, `hdop` |
| Attitude | `roll_deg`, `pitch_deg`, `yaw_deg` |
| Speed | `ground_speed_ms`, `air_speed_ms`, `climb_rate_ms`, `heading_deg`, `throttle_pct` |
| Battery | `battery_voltage_v`, `battery_current_a`, `battery_remaining_pct` |

## 5. MQTT Configuration

| Parameter | Configuration |
|---|---|
| Broker | EMQX Cloud |
| Host | `fff1a88e.ala.us-east-1.emqxsl.com` |
| Port | `8883` |
| Protocol | MQTT over TLS |
| Topic | `drone/{droneId}/telemetry` |
| Publish Interval | Approximately 2 seconds |
| QoS | 1 (proposed; confirm before implementation) |
| Authentication | Username/password from secure configuration |

Credentials must not be hardcoded or committed to the repository.

## 6. Non-Functional Requirements

- **Latency:** Target telemetry visibility on the Web Portal within 3 seconds of publication under normal network conditions.
- **Reliability:** MQTT and SignalR clients must support automatic reconnection.
- **Scalability:** The architecture must support multiple drone IDs.
- **Security:** MQTT connections must use TLS, and telemetry APIs/SignalR must enforce authorization.
- **Data Integrity:** Invalid, duplicated, or out-of-order messages must not corrupt the latest drone state.
- **Observability:** Record MQTT connection status, message processing errors, and telemetry freshness.
- **Maintainability:** MQTT broker settings and telemetry timeout thresholds must be configurable.

## 7. Acceptance Criteria

| ID | Acceptance Criteria |
|---|---|
| AC-01 | Python Publisher receives MAVLink data from Mission Planner and publishes valid telemetry JSON to EMQX Cloud. |
| AC-02 | Backend receives and validates telemetry from EMQX Cloud. |
| AC-03 | Valid telemetry is stored and associated with the correct UAV and mission. |
| AC-04 | Authorized Frontend clients receive updates through SignalR without page refresh. |
| AC-05 | The map displays the correct drone GPS position and updates as telemetry arrives. |
| AC-06 | Flight mode, altitude, speed, heading, and battery are displayed correctly. |
| AC-07 | MQTT/SignalR disconnection and reconnection are handled without restarting the system. |
| AC-08 | Missing GPS data does not cause the drone marker to appear at invalid coordinates. |
| AC-09 | Telemetry from multiple drones remains isolated. |
| AC-10 | Unauthorized users cannot access another mission's telemetry. |

## 8. Implementation Scope

**In scope:** MAVLink collection, MQTT publishing/subscription, telemetry validation, persistence, mission association, SignalR delivery, and real-time Web Portal visualization.

**Out of scope:** Remote flight control, autonomous mission execution, drone command publishing, and changes to the existing AI defect-detection pipeline.

**Completion condition:** The feature is complete when a running Mission Planner telemetry source can update the corresponding drone's position and flight information on the UAV-PMS Web Portal in real time, with successful integration tests and recorded evidence.