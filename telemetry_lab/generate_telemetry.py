
import random
import json
from datetime import datetime, timezone

def generate_telemetry():
    data = {
        "latitude": round(10.762622 + random.uniform(-0.001, 0.001), 6),
        "longitude": round(106.660172 + random.uniform(-0.001, 0.001), 6),
        "altitude": round(random.uniform(10, 100), 2),
        "speed": round(random.uniform(0, 20), 2),
        "battery_voltage": round(random.uniform(10.5, 12.6), 2),
        "battery_remaining": random.randint(0, 100),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    return data

if __name__ == "__main__":
    telemetry = generate_telemetry()
    print(json.dumps(telemetry, indent=4))
