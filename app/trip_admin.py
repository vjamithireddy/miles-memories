from __future__ import annotations

from datetime import datetime, timezone
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
                    le.longitude
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
