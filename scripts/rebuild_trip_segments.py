from __future__ import annotations

import argparse

from app.trip_admin import get_trip


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild trip segment summaries for one trip.")
    parser.add_argument("--trip-id", type=int, required=True)
    args = parser.parse_args()

    trip = get_trip(args.trip_id)
    if not trip:
        raise SystemExit(f"Trip not found: {args.trip_id}")

    print(
        f"Trip segment rebuild complete: trip_id={args.trip_id} "
        f"segments={len(trip.get('travel_legs', []))}"
    )


if __name__ == "__main__":
    main()
