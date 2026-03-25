from __future__ import annotations

import argparse

from app.db import get_conn
from app.trip_admin import enrich_trip_leg_places, get_trip, build_trip_snapshot
from app.bootstrap import get_home_profile
from trip_engine.detector import (
    _apply_destination_override,
    _destination_title,
    _generate_trip_name,
    _haversine_km,
    _resolve_destination_profile,
    _get_local_zone,
)


AUTO_NAME_PREFIXES = ("Detected Trip ", "Trip on ")


def _is_published(trip: dict) -> bool:
    return bool(
        trip.get("publish_ready")
        or trip.get("status") == "published"
        or trip.get("published_at")
    )


def _is_auto_trip_name(value: str | None) -> bool:
    if not value:
        return True
    return value.startswith(AUTO_NAME_PREFIXES)


def _fetch_trip_events(trip_id: int) -> list[tuple[float, float]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT le.latitude, le.longitude
                FROM trip_events te
                JOIN location_events le
                  ON te.event_type = 'location_event'
                 AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                  AND le.latitude IS NOT NULL
                  AND le.longitude IS NOT NULL
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            return [(float(r[0]), float(r[1])) for r in cur.fetchall()]


def _compute_destination(lat_lon: list[tuple[float, float]], home_lat: float, home_lon: float) -> tuple[str | None, dict]:
    if not lat_lon:
        return (None, {})
    max_point = max(lat_lon, key=lambda p: _haversine_km(home_lat, home_lon, p[0], p[1]))
    profile = _resolve_destination_profile(max_point[0], max_point[1])
    profile = _apply_destination_override(max_point[0], max_point[1], profile)
    title = _destination_title(profile)
    return (title, profile)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh destination logic, segments, and snapshots for all trips."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-name-update", action="store_true")
    args = parser.parse_args()

    home_lat, home_lon, _ = get_home_profile()
    if home_lat is None or home_lon is None:
        raise SystemExit("Home profile is not configured.")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    trip_name,
                    trip_type,
                    status,
                    review_decision,
                    publish_ready,
                    published_at,
                    primary_destination_name,
                    start_time,
                    end_time
                FROM trips
                ORDER BY id ASC
                """
            )
            trips = [dict(zip([d.name for d in cur.description], row)) for row in cur.fetchall()]

    if args.limit is not None:
        trips = trips[: args.limit]

    updated = 0
    refreshed = 0

    for trip in trips:
        trip_id = int(trip["id"])
        lat_lon = _fetch_trip_events(trip_id)
        destination_name, profile = _compute_destination(lat_lon, home_lat, home_lon)
        is_published = _is_published(trip)

        next_trip_name = trip.get("trip_name")
        if not args.skip_name_update and not is_published and _is_auto_trip_name(next_trip_name):
            local_zone = _get_local_zone()
            local_start = trip["start_time"].astimezone(local_zone)
            local_end = trip["end_time"].astimezone(local_zone)
            next_trip_name = _generate_trip_name(profile, trip.get("trip_type"), local_start, local_end)

        if args.dry_run:
            print(
                f"[dry-run] trip_id={trip_id} published={is_published} "
                f"dest={destination_name!r} name={next_trip_name!r}"
            )
            continue

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trips
                    SET primary_destination_name = COALESCE(%s, primary_destination_name),
                        trip_name = COALESCE(%s, trip_name),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        destination_name,
                        next_trip_name if not is_published else trip.get("trip_name"),
                        trip_id,
                    ),
                )
        updated += 1

        enrich_trip_leg_places(trip_id)
        refreshed_trip = get_trip(trip_id)
        if refreshed_trip:
            build_trip_snapshot(trip_id)
            refreshed += 1

        print(
            f"refreshed trip_id={trip_id} published={is_published} "
            f"dest={destination_name!r} name={next_trip_name!r}"
        )

    print(f"Trip refresh complete: trips_updated={updated} snapshots_rebuilt={refreshed}")


if __name__ == "__main__":
    main()
