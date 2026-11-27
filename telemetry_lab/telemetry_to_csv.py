
import csv

def export_to_csv(records, filename="telemetry.csv"):
    if not records:
        print("No telemetry data to export")
        return

    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=records[0].keys()
        )

        writer.writeheader()
        writer.writerows(records)

    print(f"Exported {len(records)} records to {filename}")


if __name__ == "__main__":
    sample = [
        {
            "latitude": 10.7626,
            "longitude": 106.6601,
            "altitude": 50,
            "speed": 12
        }
    ]

    export_to_csv(sample)
