from __future__ import annotations

from typing import Any, Iterable

from psycopg.rows import dict_row

from app.db import get_conn


def list_parks() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT park_code, name, state, city, lat, lon, visited, planned
                FROM national_parks
                ORDER BY name
                """
            )
            rows = cur.fetchall()
    for row in rows:
        row["visited"] = bool(row["visited"])
        row["planned"] = bool(row["planned"])
    return rows


def park_counts(parks_list: Iterable[dict[str, Any]]) -> dict[str, int]:
    total = 0
    visited = 0
    planned = 0
    for park in parks_list:
        total += 1
        if park.get("visited"):
            visited += 1
        if park.get("planned"):
            planned += 1
    return {"total": total, "visited": visited, "planned": planned}


def update_park_status(park_code: str, *, visited: bool | None, planned: bool | None) -> dict[str, Any] | None:
    if visited is None and planned is None:
        return None
    fields: list[str] = []
    values: list[Any] = []
    if visited is not None:
        fields.append("visited = %s")
        values.append(bool(visited))
    if planned is not None:
        fields.append("planned = %s")
        values.append(bool(planned))
    values.append(park_code)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE national_parks
                SET {", ".join(fields)}, updated_at = NOW()
                WHERE park_code = %s
                RETURNING park_code, name, state, city, lat, lon, visited, planned
                """,
                values,
            )
            row = cur.fetchone()
    if not row:
        return None
    row["visited"] = bool(row["visited"])
    row["planned"] = bool(row["planned"])
    return row


def bulk_update_parks(park_codes: list[str], *, field: str, value: bool) -> int:
    if not park_codes:
        return 0
    if field not in {"visited", "planned"}:
        raise ValueError("Invalid bulk field")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE national_parks
                SET {field} = %s, updated_at = NOW()
                WHERE park_code = ANY(%s)
                """,
                (value, park_codes),
            )
            return cur.rowcount
