
def filter_by_speed(records, min_speed=5):
    return [
        record for record in records
        if record["speed"] >= min_speed
    ]


if __name__ == "__main__":
    records = [
        {"speed": 2.5},
        {"speed": 8.2},
        {"speed": 15.0},
        {"speed": 3.1}
    ]

    result = filter_by_speed(records)

    print("Filtered telemetry:")
    for item in result:
        print(item)
