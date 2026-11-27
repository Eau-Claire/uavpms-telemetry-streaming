
import time
import json
from generate_telemetry import generate_telemetry

def run_simulator(interval=2):
    print("Starting UAV Telemetry Simulator...")

    try:
        while True:
            telemetry = generate_telemetry()

            print(
                json.dumps(
                    telemetry,
                    indent=2
                )
            )

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    run_simulator()
