import argparse
from datetime import datetime

from app import trip_admin
from ingestion.imports import complete_import, create_import, fail_import, get_import_summary
from ingestion.location_takeout import parse_location_history, save_location_events
from trip_engine.detector import detect_trips, get_detection_since_ts


def _filter_recent_events(events: list, since_ts: datetime | None) -> list:
    if since_ts is None:
        return events
    return [event for event in events if event.timestamp >= since_ts]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely ingest a Google Timeline export and build only the latest trips."
    )
    parser.add_argument("--file", required=True, help="Path to Timeline.json")
    parser.add_argument(
        "--overlap-hours",
        type=int,
        default=6,
        help="Overlap window to preserve around the latest built trip.",
    )
    args = parser.parse_args()

    since_ts = get_detection_since_ts(overlap_hours=args.overlap_hours)
    import_id = create_import("google_takeout_location", "google_timeline", args.file)
    try:
        events = parse_location_history(args.file)
        recent_events = _filter_recent_events(events, since_ts)
        inserted = save_location_events(import_id, recent_events)
        created, linked = detect_trips(since_ts=since_ts, overlap_hours=args.overlap_hours)
        rebuilt = trip_admin.rebuild_recent_snapshots(hours=24)
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise

    summary = get_import_summary(import_id)
    print(
        "Latest timeline build complete: "
        f"id={summary['id']} status={summary['status']} "
        f"events_seen={len(events)} events_considered={len(recent_events)} events_inserted={inserted} "
        f"trips_created={created} linked_events={linked} snapshots_rebuilt={rebuilt}"
    )


if __name__ == "__main__":
    main()
