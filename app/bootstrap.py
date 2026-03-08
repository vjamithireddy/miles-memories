from psycopg import sql

from app.db import get_conn


def ensure_default_user() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (display_name, timezone)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                ("MilesMemories User", "America/Chicago"),
            )
            cur.execute(
                "SELECT id FROM users ORDER BY id ASC LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Unable to create or fetch default user")
            return int(row[0])


def get_home_profile() -> tuple[float | None, float | None, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT home_latitude, home_longitude, home_local_radius_meters
                FROM users
                ORDER BY id ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return (None, None, 16093)
            return (row[0], row[1], int(row[2] or 16093))


def set_home_profile(latitude: float, longitude: float, local_radius_meters: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET home_latitude = %s,
                    home_longitude = %s,
                    home_local_radius_meters = %s,
                    updated_at = NOW()
                WHERE id = (
                    SELECT id FROM users ORDER BY id ASC LIMIT 1
                )
                """,
                (latitude, longitude, local_radius_meters),
            )
