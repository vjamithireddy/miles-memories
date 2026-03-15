from __future__ import annotations

from typing import Any, Optional

from psycopg.rows import dict_row

from app.db import get_conn


def list_overrides() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    rule_name,
                    match_pattern,
                    latitude,
                    longitude,
                    radius_meters,
                    classification,
                    keep_trip,
                    ignore_trip,
                    created_at,
                    updated_at
                FROM destination_overrides
                ORDER BY updated_at DESC, id DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": int(row["id"]),
            "rule_name": row["rule_name"],
            "match_pattern": row["match_pattern"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "radius_meters": int(row["radius_meters"] or 1000),
            "classification": row["classification"],
            "keep_trip": bool(row["keep_trip"]),
            "ignore_trip": bool(row["ignore_trip"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def create_override(
    *,
    rule_name: str,
    classification: str,
    keep_trip: bool,
    ignore_trip: bool,
    match_pattern: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    radius_meters: int,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO destination_overrides (
                    rule_name, match_pattern, latitude, longitude, radius_meters,
                    classification, keep_trip, ignore_trip, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    rule_name,
                    match_pattern,
                    latitude,
                    longitude,
                    radius_meters,
                    classification,
                    keep_trip,
                    ignore_trip,
                ),
            )


def delete_override(override_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM destination_overrides WHERE id = %s", (override_id,))
