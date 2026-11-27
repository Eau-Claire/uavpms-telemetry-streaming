
def validate_telemetry(data):
    required_fields = [
        "latitude",
        "longitude",
        "altitude",
        "speed",
        "battery_voltage",
        "battery_remaining",
        "timestamp"
    ]

    for field in required_fields:
        if field not in data:
            return False, f"Missing field: {field}"

    if not -90 <= data["latitude"] <= 90:
        return False, "Invalid latitude"

    if not -180 <= data["longitude"] <= 180:
        return False, "Invalid longitude"

    if data["speed"] < 0:
        return False, "Invalid speed"

    if not 0 <= data["battery_remaining"] <= 100:
        return False, "Invalid battery percentage"

    return True, "Telemetry data is valid"


if __name__ == "__main__":
    sample = {
        "latitude": 10.762622,
        "longitude": 106.660172,
        "altitude": 50,
        "speed": 12,
        "battery_voltage": 11.8,
        "battery_remaining": 85,
        "timestamp": "2026-11-26T10:00:00Z"
    }

    print(validate_telemetry(sample))
