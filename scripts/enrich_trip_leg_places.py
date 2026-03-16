from __future__ import annotations

import argparse

from app.trip_admin import enrich_trip_leg_places


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich cached place data for trip leg endpoints.")
    parser.add_argument("--trip-id", type=int, required=True)
    args = parser.parse_args()

    enriched = enrich_trip_leg_places(args.trip_id)
    print(f"Trip leg place enrichment complete: trip_id={args.trip_id} endpoints_processed={enriched}")


if __name__ == "__main__":
    main()
