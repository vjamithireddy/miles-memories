from dataclasses import dataclass
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt

from app.bootstrap import ensure_default_user, get_home_profile, get_work_profile
from app.db import get_conn


@dataclass
class SimpleTrip:
    start_time: datetime
    end_time: datetime
    max_distance_km: float
    touched_work: bool = False


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return r * c


def _fetch_location_events() -> list[tuple[int, datetime, float, float]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, event_timestamp, latitude, longitude
                FROM location_events
                ORDER BY event_timestamp ASC
                """
            )
            return [(int(r[0]), r[1], float(r[2]), float(r[3])) for r in cur.fetchall()]


def detect_trips() -> tuple[int, int]:
    user_id = ensure_default_user()
    home_lat, home_lon, local_radius_m = get_home_profile()
    work_lat, work_lon, work_radius_m = get_work_profile()
    if home_lat is None or home_lon is None:
        return (0, 0)

    events = _fetch_location_events()
    if not events:
        return (0, 0)

    local_radius_km = local_radius_m / 1000.0
    work_radius_km = work_radius_m / 1000.0
    home_work_distance_km = None
    if work_lat is not None and work_lon is not None:
        home_work_distance_km = _haversine_km(home_lat, home_lon, work_lat, work_lon)
    candidates: list[SimpleTrip] = []
    current_start = None
    current_end = None
    current_max_dist = 0.0
    current_touched_work = False

    for _, ts, lat, lon in events:
        dist = _haversine_km(home_lat, home_lon, lat, lon)
        away = dist > local_radius_km
        work_dist = None
        if work_lat is not None and work_lon is not None:
            work_dist = _haversine_km(work_lat, work_lon, lat, lon)
        if away:
            if current_start is None:
                current_start = ts
            current_end = ts
            current_max_dist = max(current_max_dist, dist)
            if work_dist is not None and work_dist <= work_radius_km:
                current_touched_work = True
        else:
            if current_start and current_end:
                if (current_end - current_start) >= timedelta(hours=3):
                    candidates.append(
                        SimpleTrip(
                            start_time=current_start,
                            end_time=current_end,
                            max_distance_km=current_max_dist,
                            touched_work=current_touched_work,
                        )
                    )
                current_start = None
                current_end = None
                current_max_dist = 0.0
                current_touched_work = False

    if current_start and current_end and (current_end - current_start) >= timedelta(hours=3):
        candidates.append(
            SimpleTrip(
                start_time=current_start,
                end_time=current_end,
                max_distance_km=current_max_dist,
                touched_work=current_touched_work,
            )
        )

    created = 0
    linked_events = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for idx, trip in enumerate(candidates, start=1):
                if (
                    trip.touched_work
                    and home_work_distance_km is not None
                    and trip.max_distance_km <= max(home_work_distance_km + 5.0, local_radius_km + 5.0)
                ):
                    continue
                duration = trip.end_time - trip.start_time
                if trip.start_time.date() != trip.end_time.date():
                    if duration < timedelta(days=2):
                        trip_type = "overnight_trip"
                    else:
                        trip_type = "multi_day_trip"
                elif duration < timedelta(hours=24):
                    trip_type = "day_trip"
                else:
                    trip_type = "multi_day_trip"
                score = min(100, int(40 + min(trip.max_distance_km, 400) / 5))
                slug = f"detected-{trip.start_time.date()}-{idx}"
                title = f"Detected Trip {trip.start_time.date()}"

                cur.execute(
                    """
                    INSERT INTO trips (
                        user_id, trip_name, trip_slug, trip_type, status, review_decision,
                        start_time, end_time, start_date, end_date,
                        confidence_score, is_private, publish_ready, created_by, detection_version
                    )
                    VALUES (%s, %s, %s, %s, 'needs_review', 'pending', %s, %s, %s, %s, %s, TRUE, FALSE, 'system', 'v0')
                    ON CONFLICT (trip_slug) DO NOTHING
                    RETURNING id
                    """,
                    (
                        user_id,
                        title,
                        slug,
                        trip_type,
                        trip.start_time,
                        trip.end_time,
                        trip.start_time.date(),
                        trip.end_time.date(),
                        score,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    continue
                trip_id = int(row[0])
                created += 1

                cur.execute(
                    """
                    SELECT id, event_timestamp
                    FROM location_events
                    WHERE event_timestamp BETWEEN %s AND %s
                    ORDER BY event_timestamp ASC
                    """,
                    (trip.start_time, trip.end_time),
                )
                trip_rows = cur.fetchall()
                for order, ev in enumerate(trip_rows, start=1):
                    cur.execute(
                        """
                        INSERT INTO trip_events (
                            trip_id, event_type, event_ref_id, event_time, sort_order, day_index, timeline_label
                        )
                        VALUES (%s, 'location_event', %s, %s, %s, 0, 'Location point')
                        """,
                        (trip_id, int(ev[0]), ev[1], order),
                    )
                    linked_events += 1

    return (created, linked_events)
