import argparse

from ingestion.garmin_parser import parse_activities, save_activity
from ingestion.imports import complete_import, create_import, fail_import, get_import_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Garmin export file")
    parser.add_argument("--file", required=True, help="Path to Garmin export file")
    args = parser.parse_args()
    import_id = create_import("garmin_export", "garmin", args.file)
    inserted_count = 0
    updated_count = 0
    try:
        activities = parse_activities(args.file)
        if not activities:
            raise ValueError(f"No activities found in {args.file}")
        activity_id = None
        trip_ids: set[int] = set()
        for activity in activities:
            activity_id, inserted, trip_id = save_activity(import_id, activity)
            if inserted:
                inserted_count += 1
            else:
                updated_count += 1
            if trip_id:
                trip_ids.add(trip_id)
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise
    if trip_ids:
        from app import trip_admin

        for trip_id in sorted(trip_ids):
            trip_admin.build_trip_snapshot(trip_id)
    summary = get_import_summary(import_id)
    print(
        f"Garmin import complete: id={summary['id']} status={summary['status']} "
        f"activity_id={activity_id} inserted={inserted_count} updated={updated_count}"
    )


if __name__ == "__main__":
    main()
