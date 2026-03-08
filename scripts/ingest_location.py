import argparse

from ingestion.imports import complete_import, create_import, fail_import, get_import_summary
from ingestion.location_takeout import parse_location_history, save_location_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Google location history file")
    parser.add_argument("--file", required=True, help="Path to Google Takeout location file")
    args = parser.parse_args()
    import_id = create_import("google_takeout_location", "google_timeline", args.file)
    try:
        events = parse_location_history(args.file)
        inserted = save_location_events(import_id, events)
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise
    summary = get_import_summary(import_id)
    print(
        f"Location import complete: id={summary['id']} status={summary['status']} "
        f"events_inserted={inserted}"
    )


if __name__ == "__main__":
    main()
