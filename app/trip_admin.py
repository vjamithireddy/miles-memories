from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from psycopg.rows import dict_row

from app.db import get_conn


def _normalize_trip(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "trip_name": row["trip_name"],
        "trip_slug": row["trip_slug"],
        "trip_type": row["trip_type"],
        "status": row["status"],
        "review_decision": row["review_decision"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "primary_destination_name": row["primary_destination_name"],
        "origin_place_name": row["origin_place_name"],
        "confidence_score": row["confidence_score"],
        "summary_text": row["summary_text"],
        "is_private": bool(row["is_private"]),
        "publish_ready": bool(row["publish_ready"]),
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
    }


LEG_LABELS = {
    "FLYING": ("air", "Air travel"),
    "IN_PASSENGER_VEHICLE": ("car", "Car travel"),
    "WALKING": ("walk", "Walking"),
    "RUNNING": ("run", "Running"),
    "HIKING": ("hike", "Hiking"),
}


def _is_placeholder_segment_summary(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return (
        normalized.endswith("inferred from timeline activity data.")
        or "rental car facility" in normalized
        or normalized.startswith("drive near ")
    )


def _trip_context_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(
        r"\s+(day trip|weekend|overnight|overnight trip|multi[ -]?day trip|trip)$",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip(" -")
    return normalized or value.strip()


def _clean_segment_place_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    lowered = cleaned.lower()
    if "rental car facility" in lowered:
        cleaned = re.sub(r"\s+rental car facility\b", "", cleaned, flags=re.IGNORECASE).strip(" -,")
        lowered = cleaned.lower()
    if lowered.endswith(" airport rental car"):
        cleaned = re.sub(r"\s+airport rental car\b", " Airport", cleaned, flags=re.IGNORECASE).strip()
        lowered = cleaned.lower()
    if lowered.endswith(" airport"):
        return cleaned
    return cleaned or None


def _preferred_segment_place(
    *names: str | None,
    fallback_trip_name: str | None = None,
) -> str | None:
    for name in names:
        cleaned = _clean_segment_place_name(name)
        if cleaned:
            return cleaned
    return _trip_context_name(fallback_trip_name)


def _is_airport_like(value: str | None) -> bool:
    return bool(value and "airport" in value.lower())


def _segment_place_phrase(*names: str | None) -> str | None:
    keywords = (
        "trailhead",
        "viewpoint",
        "overlook",
        "visitor center",
        "lodge",
        "hotel",
        "inn",
        "resort",
        "campground",
        "camp",
        "village",
    )
    for name in names:
        if not name:
            continue
        lowered = name.lower()
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def _segment_place_role(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    role_keywords = (
        ("trailhead", "trailhead"),
        ("viewpoint", "viewpoint"),
        ("overlook", "viewpoint"),
        ("visitor center", "visitor center"),
        ("lodge", "lodging"),
        ("hotel", "lodging"),
        ("inn", "lodging"),
        ("resort", "lodging"),
        ("campground", "camp"),
        ("camp", "camp"),
        ("village", "village"),
    )
    for keyword, role in role_keywords:
        if keyword in lowered:
            return role
    return None


def _leg_default_summary(
    leg: dict[str, Any],
    *,
    trip_name: str | None = None,
    trip_summary_text: str | None = None,
    origin_name: str | None = None,
    destination_name: str | None = None,
    previous_leg_type: str | None = None,
    next_leg_type: str | None = None,
) -> str:
    label = leg["label"]
    start_name = _clean_segment_place_name(leg.get("start_place_name"))
    end_name = _clean_segment_place_name(leg.get("end_place_name"))
    leg_type = leg["leg_type"]
    trip_context = _trip_context_name(trip_name)
    preferred_destination = _preferred_segment_place(
        destination_name,
        end_name,
        start_name,
        fallback_trip_name=trip_context,
    )
    preferred_origin = _preferred_segment_place(origin_name, start_name)
    specific_place = _segment_place_phrase(end_name, start_name, preferred_destination)
    specific_role = _segment_place_role(specific_place)

    if leg_type == "air":
        if preferred_origin and preferred_destination and preferred_origin != preferred_destination:
            return f"Flight from {preferred_origin} to {preferred_destination}."
        if preferred_destination:
            return f"Flight to {preferred_destination}."
        return "Flight segment inferred from timeline activity data."

    if leg_type == "car":
        if next_leg_type == "air":
            return "Drive to airport."
        if previous_leg_type == "air":
            if specific_place:
                return f"Drive from airport to {specific_place}."
            if preferred_destination and "airport" not in preferred_destination.lower():
                return f"Drive from airport toward {preferred_destination}."
            return "Drive from airport."
        if next_leg_type in {"walk", "hike", "run"}:
            if specific_place:
                return f"Drive to {specific_place}."
            if trip_context:
                return f"Drive to trailhead in {trip_context}."
            return "Drive to trailhead."
        if previous_leg_type in {"walk", "hike", "run"}:
            if specific_place:
                if specific_role == "lodging":
                    return f"Drive from trail to {specific_place}."
                if specific_role == "village":
                    return f"Drive from trail into {specific_place}."
                return f"Drive from trail toward {specific_place}."
            if trip_context:
                return f"Drive from trail in {trip_context}."
        if specific_place:
            if specific_role == "lodging":
                return f"Drive to {specific_place}."
            if specific_role == "viewpoint":
                return f"Drive to {specific_place}."
            if specific_role == "visitor center":
                return f"Drive to {specific_place}."
            if specific_role == "camp":
                return f"Drive to {specific_place}."
            if specific_role == "village":
                return f"Drive in {specific_place}."
            if specific_role == "trailhead":
                return f"Drive to {specific_place}."
        if _is_airport_like(preferred_destination) and trip_context:
            return f"Drive in {trip_context}."
        if preferred_destination:
            return f"Drive in {preferred_destination}."
        if preferred_origin:
            return f"Drive from {preferred_origin}."

    if leg_type in {"walk", "hike", "run"}:
        verb = {"walk": "Walk", "hike": "Hike", "run": "Run"}[leg_type]
        if leg_type == "hike" and trip_summary_text:
            return trip_summary_text.rstrip(".") + "."
        if trip_context:
            return f"{verb} in {trip_context}."
        if preferred_destination:
            return f"{verb} in {preferred_destination}."
        if end_name:
            return f"{verb} toward {end_name}."
        if start_name:
            return f"{verb} from {start_name}."

    if start_name and end_name:
        return f"{label} from {start_name} to {end_name}."
    if end_name:
        return f"{label} toward {end_name}."
    if start_name:
        return f"{label} leaving {start_name}."
    return f"{label} inferred from timeline activity data."


def _should_refresh_segment_summary(
    existing_summary: str | None,
    *,
    leg: dict[str, Any],
    trip_name: str | None = None,
    destination_name: str | None = None,
) -> bool:
    if _is_placeholder_segment_summary(existing_summary):
        return True
    if not existing_summary:
        return True
    normalized_existing = existing_summary.strip()
    trip_context = _trip_context_name(trip_name)
    cleaned_destination = _preferred_segment_place(
        destination_name,
        leg.get("end_place_name"),
        leg.get("start_place_name"),
        fallback_trip_name=trip_context,
    )
    if leg.get("leg_type") == "air" and trip_context and cleaned_destination:
        legacy_summary = f"Flight from {trip_context} to {cleaned_destination}."
        if normalized_existing == legacy_summary:
            return True
    return False


def _build_travel_legs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_segment: dict[int, dict[str, Any]] = {}
    for row in rows:
        raw_payload = row["raw_payload_json"]
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = None
        if not isinstance(raw_payload, dict):
            continue
        segment_index = raw_payload.get("semanticSegmentIndex")
        if segment_index is None:
            continue
        existing = by_segment.setdefault(
            int(segment_index),
            {
                "leg_type": None,
                "label": None,
                "start_time": row["event_time"],
                "end_time": row["event_time"],
                "start_latitude": None,
                "start_longitude": None,
                "end_latitude": None,
                "end_longitude": None,
                "source_event_id": None,
                "path_points": [],
            },
        )
        if row["event_time"] < existing["start_time"]:
            existing["start_time"] = row["event_time"]
        if row["event_time"] > existing["end_time"]:
            existing["end_time"] = row["event_time"]
        latitude = row.get("latitude")
        longitude = row.get("longitude")
        if latitude is not None and longitude is not None:
            point = {"lat": float(latitude), "lon": float(longitude)}
            if not existing["path_points"] or existing["path_points"][-1] != point:
                existing["path_points"].append(point)

        activity = raw_payload.get("activity")
        if not isinstance(activity, dict):
            continue
        top_candidate = activity.get("topCandidate") or {}
        movement_type = top_candidate.get("type") or row["source_event_id"]
        if movement_type not in LEG_LABELS:
            continue
        label_type, label = LEG_LABELS[movement_type]
        start = activity.get("start") or {}
        end = activity.get("end") or {}
        existing.update({
            "leg_type": label_type,
            "label": label,
            "source_event_id": movement_type,
        })
        if start.get("latLng"):
            lat, lon = start["latLng"].replace("°", "").split(",")
            existing["start_latitude"] = float(lat.strip())
            existing["start_longitude"] = float(lon.strip())
        if end.get("latLng"):
            lat, lon = end["latLng"].replace("°", "").split(",")
            existing["end_latitude"] = float(lat.strip())
            existing["end_longitude"] = float(lon.strip())
        start_time = activity.get("startTime")
        end_time = activity.get("endTime")
        if start_time:
            existing["start_time"] = datetime.fromisoformat(
                start_time.replace("Z", "+00:00")
            )
        if end_time:
            existing["end_time"] = datetime.fromisoformat(
                end_time.replace("Z", "+00:00")
            )

    legs = []
    for key in sorted(by_segment):
        leg = by_segment[key]
        if not leg["leg_type"]:
            continue
        if not leg["path_points"]:
            if leg["start_latitude"] is not None and leg["start_longitude"] is not None:
                leg["path_points"].append(
                    {"lat": leg["start_latitude"], "lon": leg["start_longitude"]}
                )
            if leg["end_latitude"] is not None and leg["end_longitude"] is not None:
                end_point = {"lat": leg["end_latitude"], "lon": leg["end_longitude"]}
                if not leg["path_points"] or leg["path_points"][-1] != end_point:
                    leg["path_points"].append(end_point)
        legs.append(leg)
    return legs


def _sync_trip_segments(
    cur: Any,
    trip_id: int,
    legs: list[dict[str, Any]],
    *,
    trip_name: str | None = None,
    trip_summary_text: str | None = None,
    origin_name: str | None = None,
    destination_name: str | None = None,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            segment_type,
            start_time,
            end_time,
            segment_name,
            notes,
            rating,
            source_event_id
        FROM trip_segments
        WHERE trip_id = %s
        ORDER BY start_time ASC, id ASC
        """,
        (trip_id,),
    )
    existing_rows = cur.fetchall()
    existing_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in existing_rows:
        key = (
            row["segment_type"],
            row["start_time"],
            row["end_time"],
            row.get("source_event_id"),
        )
        existing_by_key[key] = row

    synced = []
    for index, leg in enumerate(legs):
        key = (
            leg["leg_type"],
            leg["start_time"],
            leg["end_time"],
            leg.get("source_event_id"),
        )
        persisted = existing_by_key.get(key)
        previous_leg_type = legs[index - 1]["leg_type"] if index > 0 else None
        next_leg_type = legs[index + 1]["leg_type"] if index + 1 < len(legs) else None
        default_summary = _leg_default_summary(
            leg,
            trip_name=trip_name,
            trip_summary_text=trip_summary_text,
            origin_name=origin_name,
            destination_name=destination_name,
            previous_leg_type=previous_leg_type,
            next_leg_type=next_leg_type,
        )
        if not persisted:
            cur.execute(
                """
                INSERT INTO trip_segments (
                    trip_id,
                    segment_type,
                    start_time,
                    end_time,
                    start_place_name,
                    end_place_name,
                    notes,
                    segment_name,
                    rating,
                    source_event_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, segment_name, notes, rating
                """,
                (
                    trip_id,
                    leg["leg_type"],
                    leg["start_time"],
                    leg["end_time"],
                    leg.get("start_place_name"),
                    leg.get("end_place_name"),
                    default_summary,
                    leg["label"],
                    None,
                    leg.get("source_event_id"),
                ),
            )
            persisted = cur.fetchone()
        elif _should_refresh_segment_summary(
            persisted.get("notes"),
            leg=leg,
            trip_name=trip_name,
            destination_name=destination_name,
        ):
            cur.execute(
                """
                UPDATE trip_segments
                SET notes = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, segment_name, notes, rating
                """,
                (default_summary, persisted["id"]),
            )
            persisted = cur.fetchone()
        leg["segment_id"] = int(persisted["id"])
        leg["segment_name"] = persisted.get("segment_name") or leg["label"]
        leg["segment_summary"] = persisted.get("notes") or default_summary
        leg["segment_rating"] = persisted.get("rating")
        synced.append(leg)
    return synced


def list_trips(
    *,
    status: str | None = None,
    review_decision: str | None = None,
    include_private: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []

    if status:
        filters.append("status = %s")
        params.append(status)
    if review_decision:
        filters.append("review_decision = %s")
        params.append(review_decision)
    if not include_private:
        filters.append("is_private = FALSE")

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT
                    id,
                    trip_name,
                    trip_slug,
                    trip_type,
                    status,
                    review_decision,
                    start_time,
                    end_time,
                    start_date,
                    end_date,
                    primary_destination_name,
                    origin_place_name,
                    confidence_score,
                    summary_text,
                    is_private,
                    publish_ready,
                    published_at,
                    updated_at
                FROM trips
                {where_sql}
                ORDER BY start_time DESC, id DESC
                LIMIT %s
                """,
                [*params, limit],
            )
            return [_normalize_trip(row) for row in cur.fetchall()]


def get_trip(trip_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    trip_name,
                    trip_slug,
                    trip_type,
                    status,
                    review_decision,
                    start_time,
                    end_time,
                    start_date,
                    end_date,
                    primary_destination_name,
                    origin_place_name,
                    confidence_score,
                    summary_text,
                    is_private,
                    publish_ready,
                    published_at,
                    updated_at
                FROM trips
                WHERE id = %s
                """,
                (trip_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            trip = _normalize_trip(row)

            cur.execute(
                """
                SELECT event_type, COUNT(*)::BIGINT AS total
                FROM trip_events
                WHERE trip_id = %s
                GROUP BY event_type
                ORDER BY event_type ASC
                """,
                (trip_id,),
            )
            trip["event_counts"] = [
                {"event_type": item["event_type"], "total": int(item["total"])}
                for item in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    te.event_type,
                    te.event_ref_id,
                    te.event_time,
                    te.sort_order,
                    te.day_index,
                    te.timeline_label,
                    le.latitude,
                    le.longitude,
                    le.source_event_id,
                    le.raw_payload_json
                FROM trip_events te
                LEFT JOIN location_events le
                    ON te.event_type = 'location_event'
                   AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                ORDER BY te.sort_order ASC NULLS LAST, te.event_time ASC, te.id ASC
                LIMIT 200
                """,
                (trip_id,),
            )
            trip["timeline"] = [
                {
                    "event_type": item["event_type"],
                    "event_ref_id": int(item["event_ref_id"]),
                    "event_time": item["event_time"],
                    "sort_order": item["sort_order"],
                    "day_index": item["day_index"],
                    "timeline_label": item["timeline_label"],
                    "latitude": float(item["latitude"]) if item["latitude"] is not None else None,
                    "longitude": float(item["longitude"]) if item["longitude"] is not None else None,
                }
                for item in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT
                    te.event_time,
                    le.source_event_id,
                    le.raw_payload_json,
                    le.latitude,
                    le.longitude
                FROM trip_events te
                JOIN location_events le
                    ON te.event_type = 'location_event'
                   AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            trip["travel_legs"] = _sync_trip_segments(
                cur,
                trip_id,
                _build_travel_legs(cur.fetchall()),
                trip_name=trip.get("trip_name"),
                trip_summary_text=trip.get("summary_text"),
                origin_name=trip.get("origin_place_name"),
                destination_name=trip.get("primary_destination_name"),
            )

            cur.execute(
                """
                SELECT
                    reviewer_name,
                    review_action,
                    review_notes,
                    reviewed_at
                FROM admin_reviews
                WHERE trip_id = %s
                ORDER BY reviewed_at DESC, id DESC
                LIMIT 20
                """,
                (trip_id,),
            )
            trip["review_history"] = [
                {
                    "reviewer_name": item["reviewer_name"],
                    "review_action": item["review_action"],
                    "review_notes": item["review_notes"],
                    "reviewed_at": item["reviewed_at"],
                }
                for item in cur.fetchall()
            ]

            return trip


def get_trip_neighbors(trip_id: int) -> dict[str, dict[str, Any] | None]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, trip_name, start_time
                FROM trips
                WHERE id = %s
                """,
                (trip_id,),
            )
            current = cur.fetchone()
            if not current:
                return {"previous": None, "next": None}

            cur.execute(
                """
                SELECT id, trip_name
                FROM trips
                WHERE (start_time > %s)
                   OR (start_time = %s AND id > %s)
                ORDER BY start_time ASC, id ASC
                LIMIT 1
                """,
                (current["start_time"], current["start_time"], trip_id),
            )
            previous_row = cur.fetchone()

            cur.execute(
                """
                SELECT id, trip_name
                FROM trips
                WHERE (start_time < %s)
                   OR (start_time = %s AND id < %s)
                ORDER BY start_time DESC, id DESC
                LIMIT 1
                """,
                (current["start_time"], current["start_time"], trip_id),
            )
            next_row = cur.fetchone()

    return {
        "previous": (
            {"id": int(previous_row["id"]), "trip_name": previous_row["trip_name"]}
            if previous_row
            else None
        ),
        "next": (
            {"id": int(next_row["id"]), "trip_name": next_row["trip_name"]}
            if next_row
            else None
        ),
    }


def record_review(
    trip_id: int,
    *,
    action: str,
    reviewer_name: str | None,
    review_notes: str | None,
    trip_name: str | None,
    summary_text: str | None,
    primary_destination_name: str | None,
    is_private: bool | None,
    publish_ready: bool | None,
) -> dict[str, Any] | None:
    action_map = {
        "confirm": ("confirmed", "confirmed"),
        "reject": ("ignored", "rejected"),
        "ignore": ("ignored", "ignored"),
        "publish": ("published", "confirmed"),
        "mark_private": ("confirmed", "confirmed"),
    }
    if action not in action_map:
        raise ValueError(f"Unsupported review action: {action}")

    next_status, next_review_decision = action_map[action]

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT id, is_private, publish_ready FROM trips WHERE id = %s", (trip_id,))
            existing = cur.fetchone()
            if not existing:
                return None

            final_is_private = bool(existing["is_private"]) if is_private is None else is_private
            final_publish_ready = (
                bool(existing["publish_ready"]) if publish_ready is None else publish_ready
            )

            if action == "publish":
                final_publish_ready = True
                final_is_private = False
            elif action == "mark_private":
                final_is_private = True
                final_publish_ready = False

            published_at: datetime | None = (
                datetime.now(timezone.utc) if action == "publish" else None
            )

            cur.execute(
                """
                UPDATE trips
                SET trip_name = COALESCE(%s, trip_name),
                    summary_text = COALESCE(%s, summary_text),
                    primary_destination_name = COALESCE(%s, primary_destination_name),
                    is_private = %s,
                    publish_ready = %s,
                    status = %s,
                    review_decision = %s,
                    published_at = CASE
                        WHEN %s::timestamptz IS NULL THEN published_at
                        ELSE %s::timestamptz
                    END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    trip_name,
                    summary_text,
                    primary_destination_name,
                    final_is_private,
                    final_publish_ready,
                    next_status,
                    next_review_decision,
                    published_at,
                    published_at,
                    trip_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO admin_reviews (trip_id, reviewer_name, review_action, review_notes)
                VALUES (%s, %s, %s, %s)
                """,
                (trip_id, reviewer_name, action, review_notes),
            )

    return get_trip(trip_id)


def set_publish_ready(
    trip_id: int,
    *,
    publish_ready: bool,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trips
                SET publish_ready = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (publish_ready, trip_id),
            )
            if cur.rowcount == 0:
                return None

    return get_trip(trip_id)


def update_trip_segment(
    trip_id: int,
    segment_id: int,
    *,
    segment_name: str | None,
    summary_text: str | None,
    rating: int | None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE trip_segments
                SET segment_name = COALESCE(%s, segment_name),
                    notes = COALESCE(%s, notes),
                    rating = %s,
                    updated_at = NOW()
                WHERE id = %s AND trip_id = %s
                RETURNING id
                """,
                (segment_name, summary_text, rating, segment_id, trip_id),
            )
            updated = cur.fetchone()
            if not updated:
                return None
    return get_trip(trip_id)
