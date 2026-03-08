from trip_engine.detector import detect_trips


def main() -> None:
    created, linked = detect_trips()
    print(f"Trip detection complete: trips_created={created} linked_events={linked}")


if __name__ == "__main__":
    main()
