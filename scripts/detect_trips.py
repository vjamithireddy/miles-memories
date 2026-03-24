from app import trip_admin
from trip_engine.detector import detect_trips


def main() -> None:
    created, linked = detect_trips()
    rebuilt = trip_admin.rebuild_recent_snapshots(hours=24)
    print(
        f"Trip detection complete: trips_created={created} linked_events={linked} snapshots_rebuilt={rebuilt}"
    )


if __name__ == "__main__":
    main()
