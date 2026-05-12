from __future__ import annotations

import argparse

from app.bootstrap import get_home_profile
from app.db import get_conn
from app.trip_admin import build_trip_snapshot, enrich_trip_leg_places, get_trip
from trip_engine.detector import (
    _apply_destination_override,
    _best_activity_destination,
    _destination_title,
    _format_trip_name_from_destination,
    _generate_trip_name,
    _generate_trip_summary,
    _get_local_zone,
    _haversine_km,
    _is_generic_activity_destination,
    _is_downranked_destination_name,
    _resolve_destination_profile,
)


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


def _fetch_trip_activity_names(trip_id: int) -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.activity_name
                FROM trip_events te
                JOIN activities a
                  ON te.event_type = 'garmin_activity'
                 AND a.id = te.event_ref_id
                WHERE te.trip_id = %s
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            return [str(row[0]) for row in cur.fetchall() if row and row[0]]


def _compute_destination(lat_lon: list[tuple[float, float]], home_lat: float, home_lon: float) -> tuple[str | None, dict]:
    if not lat_lon:
        return (None, {})
    max_point = max(lat_lon, key=lambda p: _haversine_km(home_lat, home_lon, p[0], p[1]))
    profile = _resolve_destination_profile(max_point[0], max_point[1])
    profile = _apply_destination_override(max_point[0], max_point[1], profile)
    return (_destination_title(profile), profile)


def _is_replaceable_summary(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip()
    return (
        normalized.startswith("Built from Garmin activities:")
        or normalized.startswith("Detected from Google Timeline")
        or normalized.startswith("Detected from non-local Garmin activities")
    )


def _is_replaceable_trip_name(
    current_name: str | None,
    *,
    current_destination: str | None,
    trip_type: str | None,
    start_time,
    end_time,
) -> bool:
    if not current_name:
        return True
    if not trip_type or not start_time or not end_time:
        return False
    current_name = current_name.strip()
    if current_name.startswith("Trip on "):
        return True
    generated = _format_trip_name_from_destination(
        current_destination,
        trip_type,
        start_time,
        end_time,
    )
    return current_name == generated


def _should_update_destination(current: str | None, proposed: str | None) -> bool:
    if not proposed or proposed == current:
        return False
    if not current:
        return True
    if "," in proposed:
        return False
    if _is_downranked_destination_name(current) or _is_generic_activity_destination(current):
        return True
    return False


def _trip_context_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    normalized = normalized.removesuffix(" Day Trip")
    normalized = normalized.removesuffix(" Weekend")
    normalized = normalized.removesuffix(" Overnight")
    normalized = normalized.removesuffix(" Trip")
    return normalized.strip(" -,") or value.strip()


def _summary_destination(
    *,
    current_title: str | None,
    replaceable_title: bool,
    proposed_destination: str | None,
    current_destination: str | None,
) -> str | None:
    if not replaceable_title:
        title_context = _trip_context_name(current_title)
        if title_context:
            return title_context
    return proposed_destination or current_destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh names, destinations, and summaries for recent auto-built trips."
    )
    parser.add_argument("--min-trip-id", type=int, required=True)
    parser.add_argument("--max-trip-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
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
                    primary_destination_name,
                    summary_text,
                    start_time,
                    end_time,
                    status,
                    review_decision,
                    publish_ready,
                    published_at,
                    detection_version
                FROM trips
                WHERE id >= %s
                  AND (%s::int IS NULL OR id <= %s)
                  AND detection_version IN ('v0', 'garmin_v1')
                ORDER BY id ASC
                """,
                (args.min_trip_id, args.max_trip_id, args.max_trip_id),
            )
            columns = [desc.name for desc in cur.description]
            trips = [dict(zip(columns, row)) for row in cur.fetchall()]

    local_zone = _get_local_zone()
    updated = 0
    snapshot_rebuilt = 0

    for trip in trips:
        trip_id = int(trip["id"])
        trip_type = trip.get("trip_type")
        start_time = trip.get("start_time")
        end_time = trip.get("end_time")
        if not trip_type or not start_time or not end_time:
            continue

        local_start = start_time.astimezone(local_zone)
        local_end = end_time.astimezone(local_zone)
        current_destination = trip.get("primary_destination_name")
        next_destination = current_destination
        next_title = trip.get("trip_name")
        next_summary = trip.get("summary_text")
        replaceable_title = _is_replaceable_trip_name(
            trip.get("trip_name"),
            current_destination=current_destination,
            trip_type=trip_type,
            start_time=local_start,
            end_time=local_end,
        )

        if trip.get("detection_version") == "garmin_v1":
            activity_names = _fetch_trip_activity_names(trip_id)
            primary_destination, title_destination, cleaned_activity_names = _best_activity_destination(
                activity_names,
                current_destination,
            )
            proposed_title = _generate_trip_name(
                {"classification": None},
                trip_type,
                local_start,
                local_end,
                preferred_destination=title_destination,
            )
            proposed_summary = _generate_trip_summary(
                source="garmin",
                destination=_summary_destination(
                    current_title=trip.get("trip_name"),
                    replaceable_title=replaceable_title,
                    proposed_destination=primary_destination,
                    current_destination=current_destination,
                ),
                trip_type=trip_type,
                activity_names=cleaned_activity_names,
            )
            if _should_update_destination(current_destination, primary_destination):
                next_destination = primary_destination
            if replaceable_title:
                next_title = proposed_title
            if _is_replaceable_summary(trip.get("summary_text")):
                next_summary = proposed_summary
        else:
            lat_lon = _fetch_trip_events(trip_id)
            proposed_destination, profile = _compute_destination(lat_lon, home_lat, home_lon)
            proposed_title = _generate_trip_name(
                profile,
                trip_type,
                local_start,
                local_end,
            )
            proposed_summary = _generate_trip_summary(
                source="timeline",
                destination=_summary_destination(
                    current_title=trip.get("trip_name"),
                    replaceable_title=replaceable_title,
                    proposed_destination=proposed_destination,
                    current_destination=current_destination,
                ),
                trip_type=trip_type,
            )
            if _should_update_destination(current_destination, proposed_destination):
                next_destination = proposed_destination
            if replaceable_title:
                next_title = proposed_title
            if _is_replaceable_summary(trip.get("summary_text")):
                next_summary = proposed_summary

        changed = (
            next_title != trip.get("trip_name")
            or next_destination != trip.get("primary_destination_name")
            or next_summary != trip.get("summary_text")
        )
        if not changed:
            continue

        if args.dry_run:
            print(
                f"[dry-run] trip_id={trip_id} "
                f"title={trip.get('trip_name')!r}->{next_title!r} "
                f"dest={trip.get('primary_destination_name')!r}->{next_destination!r} "
                f"summary={trip.get('summary_text')!r}->{next_summary!r}"
            )
            continue

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trips
                    SET trip_name = %s,
                        primary_destination_name = %s,
                        summary_text = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        next_title,
                        next_destination,
                        next_summary,
                        trip_id,
                    ),
                )
        updated += 1

        enrich_trip_leg_places(trip_id)
        refreshed_trip = get_trip(trip_id)
        if refreshed_trip:
            build_trip_snapshot(trip_id)
            snapshot_rebuilt += 1

        print(
            f"updated trip_id={trip_id} "
            f"title={next_title!r} dest={next_destination!r}"
        )

    print(f"Refresh complete: trips_updated={updated} snapshots_rebuilt={snapshot_rebuilt}")


if __name__ == "__main__":
    main()
