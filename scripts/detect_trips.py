from app import trip_admin
from trip_engine.detector import detect_trips, get_detection_since_ts


def main() -> None:
    since_ts = get_detection_since_ts(overlap_hours=6)
    created, linked = detect_trips(since_ts=since_ts, overlap_hours=6)
    rebuilt = trip_admin.rebuild_recent_snapshots(hours=24)
    print(
        f"Trip detection complete: trips_created={created} linked_events={linked} snapshots_rebuilt={rebuilt}"
    )


if __name__ == "__main__":
    main()
