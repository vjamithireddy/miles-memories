from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_conn
from ingestion.garmin_parser import parse_activity, save_activity
from ingestion.imports import complete_import, create_import, fail_import

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Missing MCP SDK. Install with: .venv/bin/pip install mcp"
    ) from exc

mcp = FastMCP("MilesMemories Garmin MCP")


@mcp.tool()
def ingest_garmin_export(file_path: str) -> dict[str, Any]:
    """Ingest a Garmin export file (GPX/FIT/etc.) into the local activities table."""
    import_id = create_import("garmin_export", "garmin", file_path)
    try:
        activity = parse_activity(file_path)
        activity_id, _, _ = save_activity(import_id, activity)
        complete_import(import_id)
    except Exception as exc:
        fail_import(import_id, str(exc))
        raise

    return {
        "import_id": import_id,
        "activity_id": activity_id,
        "activity_name": activity.activity_name,
        "activity_type": activity.activity_type,
    }


@mcp.tool()
def list_activities(limit: int = 20, offset: int = 0, activity_type: str | None = None) -> list[dict[str, Any]]:
    """List Garmin activities from the local database."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    query = """
        SELECT id, activity_name, activity_type, start_time, end_time,
               distance_meters, elevation_gain_meters, trip_id
        FROM activities
    """
    params: list[Any] = []

    if activity_type:
        query += " WHERE activity_type = %s"
        params.append(activity_type)

    query += " ORDER BY start_time DESC NULLS LAST, id DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        {
            "id": int(r[0]),
            "activity_name": r[1],
            "activity_type": r[2],
            "start_time": r[3].isoformat() if r[3] else None,
            "end_time": r[4].isoformat() if r[4] else None,
            "distance_meters": float(r[5]) if r[5] is not None else None,
            "elevation_gain_meters": float(r[6]) if r[6] is not None else None,
            "trip_id": int(r[7]) if r[7] is not None else None,
        }
        for r in rows
    ]


@mcp.tool()
def get_activity(activity_id: int) -> dict[str, Any] | None:
    """Get one Garmin activity by ID."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, import_id, source_activity_id, activity_name, activity_type,
                       start_time, end_time, duration_seconds, distance_meters,
                       elevation_gain_meters, elevation_loss_meters,
                       start_latitude, start_longitude, end_latitude, end_longitude, trip_id
                FROM activities
                WHERE id = %s
                """,
                (activity_id,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "id": int(row[0]),
        "import_id": int(row[1]) if row[1] is not None else None,
        "source_activity_id": row[2],
        "activity_name": row[3],
        "activity_type": row[4],
        "start_time": row[5].isoformat() if row[5] else None,
        "end_time": row[6].isoformat() if row[6] else None,
        "duration_seconds": int(row[7]) if row[7] is not None else None,
        "distance_meters": float(row[8]) if row[8] is not None else None,
        "elevation_gain_meters": float(row[9]) if row[9] is not None else None,
        "elevation_loss_meters": float(row[10]) if row[10] is not None else None,
        "start_latitude": float(row[11]) if row[11] is not None else None,
        "start_longitude": float(row[12]) if row[12] is not None else None,
        "end_latitude": float(row[13]) if row[13] is not None else None,
        "end_longitude": float(row[14]) if row[14] is not None else None,
        "trip_id": int(row[15]) if row[15] is not None else None,
    }


@mcp.tool()
def activity_stats(days: int = 30) -> dict[str, Any]:
    """Return aggregate activity stats for the last N days."""
    days = max(1, min(days, 3650))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(distance_meters), 0) AS total_distance_m,
                    COALESCE(SUM(duration_seconds), 0) AS total_duration_s,
                    COALESCE(SUM(elevation_gain_meters), 0) AS total_gain_m
                FROM activities
                WHERE start_time >= %s
                """,
                (since,),
            )
            total, dist_m, dur_s, gain_m = cur.fetchone()

            cur.execute(
                """
                SELECT activity_type, COUNT(*)
                FROM activities
                WHERE start_time >= %s
                GROUP BY activity_type
                ORDER BY COUNT(*) DESC, activity_type ASC
                """,
                (since,),
            )
            by_type = [{"activity_type": r[0], "count": int(r[1])} for r in cur.fetchall()]

    return {
        "window_days": days,
        "total_activities": int(total),
        "total_distance_meters": float(dist_m),
        "total_duration_seconds": int(dur_s),
        "total_elevation_gain_meters": float(gain_m),
        "by_activity_type": by_type,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
