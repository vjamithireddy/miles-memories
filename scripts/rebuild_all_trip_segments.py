from __future__ import annotations

import argparse

from app.db import get_conn
from app.trip_admin import enrich_trip_leg_places, get_trip


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-enrich trip leg places and rebuild synced trip segments for all trips."
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trips ORDER BY id ASC")
            trip_ids = [int(row[0]) for row in cur.fetchall()]

    if args.limit is not None:
        trip_ids = trip_ids[: args.limit]

    rebuilt = 0
    enriched_endpoints = 0
    total_legs = 0

    for trip_id in trip_ids:
        enriched_endpoints += enrich_trip_leg_places(trip_id)
        trip = get_trip(trip_id)
        if not trip:
            continue
        rebuilt += 1
        total_legs += len(trip.get("travel_legs", []))
        print(
            f"rebuilt trip_id={trip_id} legs={len(trip.get('travel_legs', []))} "
            f"name={trip.get('trip_name')!r}"
        )

    print(
        f"Trip segment rebuild complete: trips={rebuilt} "
        f"legs={total_legs} endpoints_processed={enriched_endpoints}"
    )


if __name__ == "__main__":
    main()
