
def calculate_statistics(records):
    if not records:
        return {}

    speeds = [r["speed"] for r in records]
    altitudes = [r["altitude"] for r in records]

    return {
        "average_speed": round(sum(speeds) / len(speeds), 2),
        "max_speed": max(speeds),
        "min_speed": min(speeds),
        "average_altitude": round(sum(altitudes) / len(altitudes), 2),
        "max_altitude": max(altitudes)
    }


if __name__ == "__main__":
    records = [
        {"speed": 10, "altitude": 30},
        {"speed": 15, "altitude": 50},
        {"speed": 8, "altitude": 40}
    ]

    print(calculate_statistics(records))
