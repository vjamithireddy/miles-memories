import argparse

from ingestion.imports import complete_import, create_import, fail_import, get_import_summary
from ingestion.photos_takeout import parse_takeout_zip, save_photo_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Google Photos Takeout archive")
    parser.add_argument("--file", required=True, help="Path to Google Photos Takeout zip")
    args = parser.parse_args()
    import_id = create_import("google_takeout_photos", "google_photos", args.file)
    try:
        photos = parse_takeout_zip(args.file)
        inserted = save_photo_records(import_id, photos)
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise
    summary = get_import_summary(import_id)
    print(
        f"Photos import complete: id={summary['id']} status={summary['status']} "
        f"photos_inserted={inserted}"
    )


if __name__ == "__main__":
    main()
