import argparse
from pathlib import Path

from ingestion.garmin_parser import parse_activities, save_activity
from ingestion.imports import complete_import, create_import, fail_import, get_import_summary


def _candidate_paths(path_str: str) -> list[Path]:
    path = Path(path_str).expanduser()
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"Garmin path not found: {path}")

    summary_files = sorted(path.rglob("*_summarizedActivities.json"))
    if summary_files:
        return summary_files

    activity_files = sorted(
        file_path
        for file_path in path.rglob("*")
        if file_path.is_file() and file_path.suffix.lower() in {".gpx", ".json"}
    )
    if activity_files:
        return activity_files

    raise FileNotFoundError(f"No supported Garmin activity files found under {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Garmin export file or export directory")
    parser.add_argument("--file", required=True, help="Path to Garmin export file or directory")
    args = parser.parse_args()
    activity_id = None
    inserted_count = 0
    updated_count = 0
    trip_ids: set[int] = set()
    imported_files = 0

    for candidate in _candidate_paths(args.file):
        import_id = create_import("garmin_export", "garmin", str(candidate))
        try:
            activities = parse_activities(str(candidate))
            if not activities:
                raise ValueError(f"No activities found in {candidate}")
            for activity in activities:
                activity_id, inserted, trip_id = save_activity(import_id, activity)
                if inserted:
                    inserted_count += 1
                else:
                    updated_count += 1
                if trip_id:
                    trip_ids.add(trip_id)
            complete_import(import_id)
            imported_files += 1
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
        f"files={imported_files} activity_id={activity_id} inserted={inserted_count} updated={updated_count}"
    )


if __name__ == "__main__":
    main()
