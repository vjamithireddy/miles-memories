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
        for activity in activities:
            activity_id, inserted = save_activity(import_id, activity)
            if inserted:
                inserted_count += 1
            else:
                updated_count += 1
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise
    summary = get_import_summary(import_id)
    print(
        f"Garmin import complete: id={summary['id']} status={summary['status']} "
        f"activity_id={activity_id} inserted={inserted_count} updated={updated_count}"
    )


if __name__ == "__main__":
    main()
