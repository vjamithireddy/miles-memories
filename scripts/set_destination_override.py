import argparse

from app.db import get_conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update a destination override rule")
    parser.add_argument("--name", required=True, help="Rule name")
    parser.add_argument("--classification", required=True, help="Classification label")
    parser.add_argument("--ignore-trip", action="store_true", help="Ignore matching trips")
    parser.add_argument("--pattern", help="Case-insensitive name/display pattern to match")
    parser.add_argument("--lat", type=float, help="Latitude for coordinate-based match")
    parser.add_argument("--lon", type=float, help="Longitude for coordinate-based match")
    parser.add_argument(
        "--radius-meters",
        type=int,
        default=1000,
        help="Radius for coordinate-based match",
    )
    args = parser.parse_args()

    if not args.pattern and (args.lat is None or args.lon is None):
        raise SystemExit("Provide either --pattern or both --lat and --lon")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO destination_overrides (
                    rule_name, match_pattern, latitude, longitude, radius_meters,
                    classification, ignore_trip, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    args.name,
                    args.pattern,
                    args.lat,
                    args.lon,
                    args.radius_meters,
                    args.classification,
                    args.ignore_trip,
                ),
            )

    print(
        "Destination override saved: "
        f"name={args.name} classification={args.classification} ignore_trip={args.ignore_trip}"
    )


if __name__ == "__main__":
    main()
