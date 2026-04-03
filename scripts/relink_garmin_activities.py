from app.db import get_conn
from app.trip_admin import build_trip_snapshot


def relink_all_garmin() -> tuple[int, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH matches AS (
                  SELECT a.id AS activity_id,
                         (
                           SELECT t.id
                           FROM trips t
                           WHERE t.start_time <= a.end_time
                             AND t.end_time >= a.start_time
                           ORDER BY LEAST(t.end_time, a.end_time)
                                    - GREATEST(t.start_time, a.start_time) DESC NULLS LAST
                           LIMIT 1
                         ) AS trip_id
                  FROM activities a
                  WHERE a.source = 'garmin'
                    AND a.start_time IS NOT NULL
                    AND a.end_time IS NOT NULL
                )
                UPDATE activities a
                SET trip_id = matches.trip_id
                FROM matches
                WHERE a.id = matches.activity_id
                """
            )
            cur.execute(
                """
                SELECT DISTINCT trip_id
                FROM activities
                WHERE source = 'garmin'
                  AND trip_id IS NOT NULL
                """
            )
            trip_ids = [row[0] for row in cur.fetchall()]
    refreshed = 0
    for trip_id in trip_ids:
        if build_trip_snapshot(int(trip_id)):
            refreshed += 1
    return len(trip_ids), refreshed


def main() -> None:
    linked, snapshots = relink_all_garmin()
    print(f"Relinked Garmin activities. trips_linked={linked} snapshots_rebuilt={snapshots}")


if __name__ == "__main__":
    main()
