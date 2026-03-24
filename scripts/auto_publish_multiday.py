from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app import trip_admin
from app.db import get_conn


def _fetch_target_trips(limit: int | None = None) -> list[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT id
                FROM trips
                WHERE trip_type = 'multi_day_trip'
                  AND (
                    status <> 'published'
                    OR publish_ready = FALSE
                    OR is_private = TRUE
                    OR review_decision <> 'confirmed'
                  )
                ORDER BY start_time ASC
            """
            if limit:
                query += " LIMIT %s"
                cur.execute(query, (limit,))
            else:
                cur.execute(query)
            return [int(row[0]) for row in cur.fetchall()]


def auto_publish_multiday(*, dry_run: bool = False, limit: int | None = None) -> int:
    trip_ids = _fetch_target_trips(limit)
    if not trip_ids:
        return 0
    if dry_run:
        return len(trip_ids)

    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trips
                SET status = 'published',
                    review_decision = 'confirmed',
                    publish_ready = TRUE,
                    published_at = COALESCE(published_at, %s),
                    is_private = FALSE,
                    updated_at = NOW()
                WHERE id = ANY(%s)
                """,
                (now, trip_ids),
            )
            cur.executemany(
                """
                INSERT INTO admin_reviews (trip_id, reviewer_name, review_action, review_notes)
                VALUES (%s, %s, %s, %s)
                """,
                [(trip_id, "system_auto", "publish", "Auto-published multi-day trip") for trip_id in trip_ids],
            )

    for trip_id in trip_ids:
        trip_admin.build_trip_snapshot(trip_id)

    return len(trip_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-publish all multi-day trips.")
    parser.add_argument("--dry-run", action="store_true", help="Report how many trips would be updated.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of trips to update.")
    args = parser.parse_args()

    updated = auto_publish_multiday(dry_run=args.dry_run, limit=args.limit)
    if args.dry_run:
        print(f"Would update {updated} multi-day trips.")
    else:
        print(f"Updated {updated} multi-day trips.")


if __name__ == "__main__":
    main()
