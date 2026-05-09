import argparse

from app import trip_admin
from trip_engine.detector import detect_garmin_trips


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build trips from unattached Garmin activities outside the local St. Louis area."
    )
    parser.add_argument(
        "--local-cutoff-km",
        type=float,
        default=80.0,
        help="Minimum distance from home before a Garmin activity is treated as non-local.",
    )
    parser.add_argument(
        "--cluster-gap-hours",
        type=int,
        default=36,
        help="Maximum gap between Garmin activities before starting a new trip.",
    )
    args = parser.parse_args()

    created, linked = detect_garmin_trips(
        local_cutoff_km=args.local_cutoff_km,
        cluster_gap_hours=args.cluster_gap_hours,
    )
    rebuilt = trip_admin.rebuild_recent_snapshots(hours=24 * 365 * 5)
    print(
        "Garmin trip build complete: "
        f"trips_created={created} activities_linked={linked} snapshots_rebuilt={rebuilt}"
    )


if __name__ == "__main__":
    main()
