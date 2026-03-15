from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import re
from math import asin, cos, radians, sin, sqrt
from time import monotonic, sleep
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.bootstrap import ensure_default_user, get_home_profile, get_work_profile
from app.db import get_conn

AMATEUR_VENUE_PATTERNS = (
    "sports complex",
    "sport complex",
    "recreation center",
    "rec center",
    "community center",
    "fieldhouse",
    "high school",
    "middle school",
    "elementary school",
    "athletic field",
    "athletic complex",
    "training center",
    "ymca",
)

PRO_VENUE_PATTERNS = (
    "stadium",
    "arena",
    "ballpark",
    "speedway",
    "raceway",
    "coliseum",
    "busch stadium",
    "enterprise center",
    "arrowhead stadium",
    "kauffman stadium",
)

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
GENERIC_PLACE_TYPES = {"house", "residential", "road", "service", "address"}
UNKNOWN_PLACE_NAMES = {"unknown destination", "unresolved destination"}
NOMINATIM_MIN_INTERVAL_SECONDS = 1.1
NOMINATIM_BACKOFF_SECONDS = 5.0

_next_reverse_lookup_at = 0.0


def _respect_nominatim_rate_limit(backoff_seconds: float = NOMINATIM_MIN_INTERVAL_SECONDS) -> None:
    global _next_reverse_lookup_at
    now = monotonic()
    if _next_reverse_lookup_at > now:
        sleep(_next_reverse_lookup_at - now)
    _next_reverse_lookup_at = monotonic() + backoff_seconds


@dataclass
class SimpleTrip:
    start_time: datetime
    end_time: datetime
    max_distance_km: float
    destination_lat: float
    destination_lon: float
    touched_work: bool = False


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return r * c


def _normalize_text(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _is_address_like(value: Optional[str]) -> bool:
    if not value:
        return True
    text = value.strip()
    if not text:
        return True
    if text.lower() in UNKNOWN_PLACE_NAMES:
        return True
    if re.match(r"^\d+", text):
        return True
    return False


def _meaningful_destination_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if not text or text.lower() in UNKNOWN_PLACE_NAMES:
        return None
    return text


def _meaningful_locality(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    if not text or text.lower() in UNKNOWN_PLACE_NAMES:
        return None
    return text


def _is_stale_cached_place(
    place_name: Optional[str],
    place_type: Optional[str],
    city: Optional[str],
) -> bool:
    normalized_name = _normalize_text(place_name)
    if normalized_name in UNKNOWN_PLACE_NAMES:
        return True
    if _meaningful_locality(city):
        return False
    if not _meaningful_destination_name(place_name):
        return True
    if _is_address_like(place_name) and _normalize_text(place_type) in GENERIC_PLACE_TYPES:
        return True
    return False


def _classify_destination(
    name: Optional[str],
    category: Optional[str],
    display_name: Optional[str],
) -> Optional[str]:
    haystack = " ".join(part for part in (name, category, display_name) if part).lower()
    if not haystack:
        return None
    if any(pattern in haystack for pattern in PRO_VENUE_PATTERNS):
        return "pro_sports_venue"
    if any(pattern in haystack for pattern in AMATEUR_VENUE_PATTERNS):
        return "amateur_sports_venue"
    return None


def _fetch_destination_profile(latitude: float, longitude: float) -> Dict[str, Optional[str]]:
    params = urlencode(
        {
            "lat": f"{latitude:.6f}",
            "lon": f"{longitude:.6f}",
            "format": "jsonv2",
            "zoom": "18",
            "addressdetails": "1",
        }
    )
    request = Request(
        f"{NOMINATIM_REVERSE_URL}?{params}",
        headers={"User-Agent": "MilesMemories/0.1"},
    )
    try:
        _respect_nominatim_rate_limit()
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 429:
            _respect_nominatim_rate_limit(NOMINATIM_BACKOFF_SECONDS)
        return {"name": None, "category": None, "display_name": None, "locality": None}
    except Exception:
        return {"name": None, "category": None, "display_name": None, "locality": None}

    address = payload.get("address") or {}
    name = (
        payload.get("name")
        or address.get("stadium")
        or address.get("arena")
        or address.get("building")
        or address.get("amenity")
        or address.get("leisure")
        or address.get("tourism")
    )
    category = payload.get("type") or payload.get("category")
    locality = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("county")
        or address.get("state")
    )
    return {
        "name": name,
        "category": category,
        "display_name": payload.get("display_name"),
        "locality": locality,
    }


def _resolve_destination_profile(latitude: float, longitude: float) -> Dict[str, Optional[str]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT place_name, place_type, source, city
                FROM places
                WHERE round(latitude::numeric, 3) = round(%s::numeric, 3)
                  AND round(longitude::numeric, 3) = round(%s::numeric, 3)
                ORDER BY id DESC
                LIMIT 1
                """,
                (latitude, longitude),
            )
            row = cur.fetchone()
            if row:
                place_name, place_type, source, city = row
                if _is_stale_cached_place(place_name, place_type, city):
                    row = None
                else:
                    name = _meaningful_destination_name(place_name)
                    locality = _meaningful_locality(city)
                    if name and _is_address_like(name) and locality:
                        name = None
                    return {
                        "name": name,
                        "category": place_type,
                        "display_name": locality,
                        "locality": locality,
                        "classification": source,
                    }

    profile = _fetch_destination_profile(latitude, longitude)
    classification = _classify_destination(
        profile.get("name"),
        profile.get("category"),
        profile.get("display_name"),
    )

    resolved_name = _meaningful_destination_name(profile.get("name"))
    resolved_locality = _meaningful_locality(profile.get("locality")) or _meaningful_locality(
        profile.get("display_name")
    )
    if resolved_name and _is_address_like(resolved_name) and resolved_locality:
        resolved_name = None

    if resolved_name or resolved_locality or classification:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO places (
                        place_name, city, latitude, longitude, place_type, source
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        resolved_name or resolved_locality or "Unresolved destination",
                        resolved_locality,
                        latitude,
                        longitude,
                        profile.get("category"),
                        classification or "nominatim",
                    ),
                )

    return {
        "name": resolved_name,
        "category": profile.get("category"),
        "display_name": profile.get("display_name"),
        "locality": resolved_locality,
        "classification": classification,
    }


def _apply_destination_override(
    latitude: float,
    longitude: float,
    profile: Dict[str, Optional[str]],
) -> Dict[str, Optional[str]]:
    haystack = " ".join(
        part for part in (profile.get("name"), profile.get("category"), profile.get("display_name")) if part
    ).lower()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT match_pattern, latitude, longitude, radius_meters, classification, keep_trip, ignore_trip
                FROM destination_overrides
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()

    for row in rows:
        pattern, rule_lat, rule_lon, radius_meters, classification, keep_trip, ignore_trip = row
        matched = False
        if pattern and pattern.lower() in haystack:
            matched = True
        elif rule_lat is not None and rule_lon is not None:
            if _haversine_km(latitude, longitude, float(rule_lat), float(rule_lon)) <= (int(radius_meters or 1000) / 1000.0):
                matched = True
        if matched:
            updated = dict(profile)
            updated["classification"] = classification
            updated["keep_trip"] = bool(keep_trip)
            updated["ignore_trip"] = bool(ignore_trip)
            return updated

    updated = dict(profile)
    updated["keep_trip"] = False
    updated["ignore_trip"] = False
    return updated


def _destination_title(profile: Dict[str, Optional[str]]) -> Optional[str]:
    name = _meaningful_destination_name(profile.get("name"))
    locality = _meaningful_locality(profile.get("locality"))
    category = _normalize_text(profile.get("category"))

    if name and not _is_address_like(name) and category not in GENERIC_PLACE_TYPES:
        return name.strip()
    if locality:
        return locality.strip()
    if name and not _is_address_like(name):
        return name.strip()
    return None


def _generate_trip_name(
    profile: Dict[str, Optional[str]],
    trip_type: str,
    start_time: datetime,
    end_time: datetime,
) -> str:
    destination = _destination_title(profile)
    classification = profile.get("classification")

    if not destination:
        return f"Trip on {start_time.date()}"

    if classification == "pro_sports_venue":
        if trip_type == "day_trip":
            return f"{destination} Day Trip"
        return f"{destination} Trip"

    if trip_type == "day_trip":
        return f"{destination} Day Trip"
    if trip_type == "overnight_trip":
        if start_time.weekday() in {4, 5} and (end_time.date() - start_time.date()).days <= 2:
            return f"{destination} Weekend"
        return f"{destination} Overnight"
    return destination


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
    current_destination_lat = home_lat
    current_destination_lon = home_lon
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
            if dist >= current_max_dist:
                current_max_dist = dist
                current_destination_lat = lat
                current_destination_lon = lon
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
                            destination_lat=current_destination_lat,
                            destination_lon=current_destination_lon,
                            touched_work=current_touched_work,
                        )
                    )
                current_start = None
                current_end = None
                current_max_dist = 0.0
                current_destination_lat = home_lat
                current_destination_lon = home_lon
                current_touched_work = False

    if current_start and current_end and (current_end - current_start) >= timedelta(hours=3):
        candidates.append(
            SimpleTrip(
                start_time=current_start,
                end_time=current_end,
                max_distance_km=current_max_dist,
                destination_lat=current_destination_lat,
                destination_lon=current_destination_lon,
                touched_work=current_touched_work,
            )
        )

    created = 0
    linked_events = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for idx, trip in enumerate(candidates, start=1):
                destination_profile = _resolve_destination_profile(
                    trip.destination_lat,
                    trip.destination_lon,
                )
                destination_profile = _apply_destination_override(
                    trip.destination_lat,
                    trip.destination_lon,
                    destination_profile,
                )
                destination_class = destination_profile.get("classification")
                if (
                    not destination_profile.get("keep_trip")
                    and (
                    trip.touched_work
                    and home_work_distance_km is not None
                    and trip.max_distance_km <= max(home_work_distance_km + 5.0, local_radius_km + 5.0)
                    )
                ):
                    continue
                if not destination_profile.get("keep_trip") and (
                    destination_profile.get("ignore_trip") or destination_class == "amateur_sports_venue"
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
                title = _generate_trip_name(
                    destination_profile,
                    trip_type,
                    trip.start_time,
                    trip.end_time,
                )

                cur.execute(
                    """
                    INSERT INTO trips (
                        user_id, trip_name, trip_slug, trip_type, status, review_decision,
                        start_time, end_time, start_date, end_date,
                        primary_destination_name,
                        confidence_score, is_private, publish_ready, created_by, detection_version
                    )
                    VALUES (%s, %s, %s, %s, 'needs_review', 'pending', %s, %s, %s, %s, %s, %s, TRUE, FALSE, 'system', 'v0')
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
                        _destination_title(destination_profile),
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
