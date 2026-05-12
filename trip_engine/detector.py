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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.bootstrap import ensure_default_user, get_home_profile, get_user_timezone, get_work_profile
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
DOWNRANKED_DESTINATION_KEYWORDS = (
    "county",
    "road",
    "boulevard",
    "avenue",
    "street",
    "drive",
    "lane",
    "highway",
    "freeway",
    "parkway",
    "route",
    "lot",
)
GENERIC_ACTIVITY_DESTINATION_KEYWORDS = (
    "county",
    "state",
    "region",
    "freeway",
    "highway",
    "interstate",
    "route",
    "shelter",
    "parking",
    "parking lot",
    "lot",
)
SPECIFIC_ACTIVITY_KEYWORDS = (
    "national park",
    "nps",
    "trail",
    "trailhead",
    "falls",
    "dune",
    "canyon",
    "mesa",
    "mount",
    "mt ",
    "mountain",
    "peak",
    "ridge",
    "arch",
    "grove",
    "meadow",
    "beach",
    "lake",
    "river",
    "cave",
    "viewpoint",
    "overlook",
    "visitor center",
    "state park",
    "monument",
    "preserve",
    "forest",
    "waterfall",
    "dunes",
)
NOMINATIM_MIN_INTERVAL_SECONDS = 1.1
NOMINATIM_BACKOFF_SECONDS = 5.0

_next_reverse_lookup_at = 0.0
LOCALITY_ADDRESS_FIELDS = (
    "city",
    "town",
    "village",
    "municipality",
    "hamlet",
    "suburb",
    "city_district",
    "borough",
    "neighbourhood",
)
REGIONAL_ADDRESS_FIELDS = ("county", "state")


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


@dataclass
class GarminTripCandidate:
    start_time: datetime
    end_time: datetime
    destination_lat: float
    destination_lon: float
    activity_ids: list[int]
    activity_names: list[str]
    max_distance_km: float


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


def _title_case_phrase(value: str) -> str:
    words = []
    for raw_word in re.split(r"(\s+)", value.strip()):
        if not raw_word or raw_word.isspace():
            words.append(raw_word)
            continue
        upper = raw_word.upper()
        lower = raw_word.lower()
        if upper in {"NPS", "USA", "US"}:
            words.append(upper)
            continue
        if lower in {"and", "of", "the", "in", "at", "to", "for"}:
            words.append(lower)
            continue
        if lower == "mt":
            words.append("Mt")
            continue
        words.append(raw_word[:1].upper() + raw_word[1:].lower())
    return "".join(words)


def _select_locality(address: Dict[str, Any]) -> Optional[str]:
    for field in LOCALITY_ADDRESS_FIELDS:
        locality = _meaningful_locality(address.get(field))
        if locality:
            return locality
    for field in REGIONAL_ADDRESS_FIELDS:
        locality = _meaningful_locality(address.get(field))
        if locality:
            return locality
    return None


def _is_downranked_destination_name(value: Optional[str]) -> bool:
    if not value:
        return False
    text = value.strip().lower()
    if not text:
        return False
    return any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in DOWNRANKED_DESTINATION_KEYWORDS)


def _is_generic_activity_destination(value: Optional[str]) -> bool:
    if not value:
        return True
    text = value.strip().lower()
    if not text:
        return True
    return any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in GENERIC_ACTIVITY_DESTINATION_KEYWORDS)


def _clean_activity_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" ,.-")
    if not cleaned:
        return None
    return cleaned


def _strip_activity_suffix(value: str) -> str:
    stripped = re.sub(
        r"\s+(?:walking|running|hiking|cycling|biking|strength|workout|activity)\b$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" ,.-")
    return stripped or value


def _activity_candidate_score(value: str) -> tuple[int, int, int, int]:
    text = value.strip().lower()
    specific_hits = sum(1 for keyword in SPECIFIC_ACTIVITY_KEYWORDS if keyword in text)
    generic_penalty = sum(1 for keyword in GENERIC_ACTIVITY_DESTINATION_KEYWORDS if keyword in text)
    token_count = min(len(text.split()), 8)
    return (
        specific_hits,
        -generic_penalty,
        token_count,
        len(text),
    )


def _extract_park_names(activity_name: str) -> list[str]:
    matches: list[str] = []
    lowered = activity_name.lower()
    for match in re.finditer(r"([a-z0-9'&.\- /]+?)\s*(?:national park(?:s)?|[,-]\s*nps)\b", lowered):
        prefix = match.group(1).strip(" ,.-/")
        tail = re.split(r"\s[-,/]\s", prefix)
        park = tail[-1].strip(" ,.-/")
        if not park:
            continue
        if _is_generic_activity_destination(park):
            continue
        matches.append(_title_case_phrase(park))
    return matches


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(value.strip())
    return deduped


def _best_activity_destination(
    activity_names: list[str],
    fallback_destination: Optional[str],
) -> tuple[Optional[str], Optional[str], list[str]]:
    cleaned_names = _dedupe_preserving_order(
        [name for name in (_clean_activity_name(item) for item in activity_names) if name]
    )
    park_names = _dedupe_preserving_order(
        [park for name in cleaned_names for park in _extract_park_names(name)]
    )
    if park_names:
        title_destination = ", ".join(f"{name} - NPS" for name in park_names[:3])
        primary_destination = f"{park_names[0]} - NPS"
        return primary_destination, title_destination, cleaned_names

    candidate_names: list[str] = []
    for name in cleaned_names:
        stripped = _strip_activity_suffix(name)
        if _is_generic_activity_destination(stripped):
            continue
        candidate_names.append(stripped)
    candidate_names = _dedupe_preserving_order(candidate_names)

    best_candidate = None
    if candidate_names:
        best_candidate = max(candidate_names, key=_activity_candidate_score)

    destination = fallback_destination
    if best_candidate:
        best_score = _activity_candidate_score(best_candidate)
        fallback_score = _activity_candidate_score(destination) if destination else (-1, -99, 0, 0)
        if not destination or _is_generic_activity_destination(destination) or best_score > fallback_score:
            destination = best_candidate

    title_destination = destination or fallback_destination
    return destination, title_destination, cleaned_names


def _format_trip_name_from_destination(
    destination: Optional[str],
    trip_type: str,
    start_time: datetime,
    end_time: datetime,
    *,
    classification: Optional[str] = None,
) -> str:
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


def _generate_trip_summary(
    *,
    source: str,
    destination: Optional[str],
    trip_type: str,
    activity_names: list[str] | None = None,
) -> Optional[str]:
    cleaned_destination = destination.strip() if destination else None
    if source == "garmin":
        lead = (
            f"Detected from non-local Garmin activities around {cleaned_destination}."
            if cleaned_destination
            else "Detected from non-local Garmin activities outside the St. Louis area."
        )
        cleaned_names = _dedupe_preserving_order(
            [name for name in (_clean_activity_name(item) for item in (activity_names or [])) if name]
        )
        if not cleaned_names:
            return lead
        highlights = ", ".join(cleaned_names[:3])
        if len(cleaned_names) > 3:
            highlights = f"{highlights}, and more"
        return f"{lead} Highlights: {highlights}."

    if cleaned_destination:
        if trip_type == "day_trip":
            return f"Detected from Google Timeline activity around {cleaned_destination}."
        return f"Detected from Google Timeline travel around {cleaned_destination}."
    return "Detected from Google Timeline activity data."


def _is_stale_cached_place(
    place_name: Optional[str],
    place_type: Optional[str],
    city: Optional[str],
) -> bool:
    normalized_name = _normalize_text(place_name)
    if normalized_name in UNKNOWN_PLACE_NAMES:
        return True
    normalized_city = _normalize_text(city)
    if (
        _meaningful_locality(city)
        and "county" not in normalized_city
        and "state" not in normalized_city
        and "region" not in normalized_city
    ):
        return False
    if not _meaningful_destination_name(place_name):
        return True
    if _is_downranked_destination_name(place_name) and (
        not _meaningful_locality(city)
        or "county" in normalized_city
        or "state" in normalized_city
        or "region" in normalized_city
    ):
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
    locality = _select_locality(address)
    return {
        "name": name,
        "category": category,
        "display_name": payload.get("display_name"),
        "locality": locality,
    }


def _resolve_destination_profile(
    latitude: float,
    longitude: float,
    *,
    force_refresh: bool = False,
) -> Dict[str, Optional[str]]:
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
            if row and not force_refresh:
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

    if (
        name
        and not _is_address_like(name)
        and not _is_downranked_destination_name(name)
        and category not in GENERIC_PLACE_TYPES
    ):
        return name.strip()
    if locality:
        return locality.strip()
    if name and not _is_address_like(name) and not _is_downranked_destination_name(name):
        return name.strip()
    if name and not _is_address_like(name):
        return name.strip()
    return None


def _generate_trip_name(
    profile: Dict[str, Optional[str]],
    trip_type: str,
    start_time: datetime,
    end_time: datetime,
    *,
    preferred_destination: Optional[str] = None,
) -> str:
    destination = preferred_destination or _destination_title(profile)
    classification = profile.get("classification")
    return _format_trip_name_from_destination(
        destination,
        trip_type,
        start_time,
        end_time,
        classification=classification,
    )


def _get_local_zone() -> ZoneInfo:
    zone_name = get_user_timezone()
    try:
        return ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Chicago")


def _to_local_time(value: datetime, local_zone: ZoneInfo) -> datetime:
    return value.astimezone(local_zone)


def _fetch_location_events(since_ts: datetime | None = None) -> list[tuple[int, datetime, float, float]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if since_ts:
                cur.execute(
                    """
                    SELECT id, event_timestamp, latitude, longitude
                    FROM location_events
                    WHERE event_timestamp >= %s
                    ORDER BY event_timestamp ASC
                    """,
                    (since_ts,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, event_timestamp, latitude, longitude
                    FROM location_events
                    ORDER BY event_timestamp ASC
                    """
                )
            return [(int(r[0]), r[1], float(r[2]), float(r[3])) for r in cur.fetchall()]


def _fetch_unattached_garmin_activities() -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    activity_name,
                    activity_type,
                    start_time,
                    end_time,
                    duration_seconds,
                    start_latitude,
                    start_longitude,
                    end_latitude,
                    end_longitude
                FROM activities
                WHERE source = 'garmin'
                  AND trip_id IS NULL
                  AND start_time IS NOT NULL
                  AND (
                    (start_latitude IS NOT NULL AND start_longitude IS NOT NULL)
                    OR (end_latitude IS NOT NULL AND end_longitude IS NOT NULL)
                  )
                ORDER BY start_time ASC, id ASC
                """
            )
            rows = cur.fetchall()
    activities: list[dict[str, Any]] = []
    for row in rows:
        start_lat = float(row[6]) if row[6] is not None else None
        start_lon = float(row[7]) if row[7] is not None else None
        end_lat = float(row[8]) if row[8] is not None else None
        end_lon = float(row[9]) if row[9] is not None else None
        activities.append(
            {
                "id": int(row[0]),
                "activity_name": str(row[1] or "Garmin activity"),
                "activity_type": str(row[2] or "other"),
                "start_time": row[3],
                "end_time": row[4] or (
                    row[3] + timedelta(seconds=int(row[5]))
                    if row[5] and row[3]
                    else row[3]
                ),
                "start_latitude": start_lat,
                "start_longitude": start_lon,
                "end_latitude": end_lat,
                "end_longitude": end_lon,
            }
        )
    return activities


def get_detection_since_ts(*, overlap_hours: int = 6) -> datetime | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(end_time) FROM trips")
            row = cur.fetchone()
            latest = row[0] if row else None
    if latest is None:
        return None
    return latest - timedelta(hours=overlap_hours)


def _trip_overlaps_existing(cur, start_time: datetime, end_time: datetime) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM trips
        WHERE start_time < %s AND end_time > %s
        LIMIT 1
        """,
        (end_time, start_time),
    )
    return cur.fetchone() is not None


def _cluster_nonlocal_garmin_activities(
    activities: list[dict[str, Any]],
    *,
    home_lat: float,
    home_lon: float,
    local_cutoff_km: float,
    cluster_gap_hours: int,
) -> list[GarminTripCandidate]:
    clusters: list[GarminTripCandidate] = []
    current: GarminTripCandidate | None = None
    cluster_gap = timedelta(hours=cluster_gap_hours)

    for activity in activities:
        candidate_points = [
            (activity["start_latitude"], activity["start_longitude"]),
            (activity["end_latitude"], activity["end_longitude"]),
        ]
        non_local_points: list[tuple[float, float, float]] = []
        for lat, lon in candidate_points:
            if lat is None or lon is None:
                continue
            dist = _haversine_km(home_lat, home_lon, lat, lon)
            if dist >= local_cutoff_km:
                non_local_points.append((lat, lon, dist))
        if not non_local_points:
            continue

        destination_lat, destination_lon, max_dist = max(non_local_points, key=lambda item: item[2])
        start_time = activity["start_time"]
        end_time = activity["end_time"] or activity["start_time"]
        if current and start_time - current.end_time <= cluster_gap:
            current.end_time = max(current.end_time, end_time)
            current.activity_ids.append(activity["id"])
            current.activity_names.append(activity["activity_name"])
            if max_dist >= current.max_distance_km:
                current.max_distance_km = max_dist
                current.destination_lat = destination_lat
                current.destination_lon = destination_lon
        else:
            current = GarminTripCandidate(
                start_time=start_time,
                end_time=end_time,
                destination_lat=destination_lat,
                destination_lon=destination_lon,
                activity_ids=[activity["id"]],
                activity_names=[activity["activity_name"]],
                max_distance_km=max_dist,
            )
            clusters.append(current)

    return clusters


def detect_trips(*, since_ts: datetime | None = None, overlap_hours: int = 6) -> tuple[int, int]:
    user_id = ensure_default_user()
    home_lat, home_lon, local_radius_m = get_home_profile()
    work_lat, work_lon, work_radius_m = get_work_profile()
    local_zone = _get_local_zone()
    if home_lat is None or home_lon is None:
        return (0, 0)

    if since_ts is None:
        since_ts = get_detection_since_ts(overlap_hours=overlap_hours)
    events = _fetch_location_events(since_ts)
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
                if _trip_overlaps_existing(cur, trip.start_time, trip.end_time):
                    continue
                duration = trip.end_time - trip.start_time
                local_start = _to_local_time(trip.start_time, local_zone)
                local_end = _to_local_time(trip.end_time, local_zone)
                if local_start.date() != local_end.date():
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
                    local_start,
                    local_end,
                )
                summary = _generate_trip_summary(
                    source="timeline",
                    destination=_destination_title(destination_profile),
                    trip_type=trip_type,
                )

                cur.execute(
                    """
                    INSERT INTO trips (
                        user_id, trip_name, trip_slug, trip_type, status, review_decision,
                        start_time, end_time, start_date, end_date,
                        primary_destination_name, summary_text,
                        confidence_score, is_private, publish_ready, created_by, detection_version
                    )
                    VALUES (%s, %s, %s, %s, 'needs_review', 'pending', %s, %s, %s, %s, %s, %s, %s, TRUE, FALSE, 'system', 'v0')
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
                        local_start.date(),
                        local_end.date(),
                        _destination_title(destination_profile),
                        summary,
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


def detect_garmin_trips(
    *,
    local_cutoff_km: float = 80.0,
    cluster_gap_hours: int = 36,
) -> tuple[int, int]:
    user_id = ensure_default_user()
    home_lat, home_lon, _ = get_home_profile()
    local_zone = _get_local_zone()
    if home_lat is None or home_lon is None:
        return (0, 0)

    activities = _fetch_unattached_garmin_activities()
    if not activities:
        return (0, 0)

    candidates = _cluster_nonlocal_garmin_activities(
        activities,
        home_lat=home_lat,
        home_lon=home_lon,
        local_cutoff_km=local_cutoff_km,
        cluster_gap_hours=cluster_gap_hours,
    )
    if not candidates:
        return (0, 0)

    created = 0
    linked_activities = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for idx, trip in enumerate(candidates, start=1):
                if _trip_overlaps_existing(cur, trip.start_time, trip.end_time):
                    continue

                destination_profile = _resolve_destination_profile(
                    trip.destination_lat,
                    trip.destination_lon,
                )
                destination_profile = _apply_destination_override(
                    trip.destination_lat,
                    trip.destination_lon,
                    destination_profile,
                )
                if destination_profile.get("ignore_trip"):
                    continue

                duration = trip.end_time - trip.start_time
                local_start = _to_local_time(trip.start_time, local_zone)
                local_end = _to_local_time(trip.end_time, local_zone)
                if local_start.date() != local_end.date():
                    if duration < timedelta(days=2):
                        trip_type = "overnight_trip"
                    else:
                        trip_type = "multi_day_trip"
                else:
                    trip_type = "day_trip"

                score = min(100, int(45 + min(trip.max_distance_km, 800) / 8))
                slug = f"garmin-detected-{trip.start_time.date()}-{idx}"
                fallback_destination = _destination_title(destination_profile)
                primary_destination_name, title_destination, cleaned_activity_names = _best_activity_destination(
                    trip.activity_names,
                    fallback_destination,
                )
                title = _generate_trip_name(
                    destination_profile,
                    trip_type,
                    local_start,
                    local_end,
                    preferred_destination=title_destination,
                )
                summary = _generate_trip_summary(
                    source="garmin",
                    destination=primary_destination_name or fallback_destination,
                    trip_type=trip_type,
                    activity_names=cleaned_activity_names,
                )

                cur.execute(
                    """
                    INSERT INTO trips (
                        user_id, trip_name, trip_slug, trip_type, status, review_decision,
                        start_time, end_time, start_date, end_date,
                        primary_destination_name, confidence_score, summary_text,
                        is_private, publish_ready, created_by, detection_version
                    )
                    VALUES (%s, %s, %s, %s, 'needs_review', 'pending', %s, %s, %s, %s, %s, %s, %s, TRUE, FALSE, 'system', 'garmin_v1')
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
                        local_start.date(),
                        local_end.date(),
                        primary_destination_name or fallback_destination,
                        score,
                        summary,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    continue
                trip_id = int(row[0])
                created += 1

                for sort_order, activity_id in enumerate(trip.activity_ids, start=1):
                    cur.execute(
                        "UPDATE activities SET trip_id = %s WHERE id = %s",
                        (trip_id, activity_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO trip_events (
                            trip_id, event_type, event_ref_id, event_time, sort_order, day_index, timeline_label
                        )
                        SELECT %s, 'garmin_activity', a.id, a.start_time, %s, 0, a.activity_name
                        FROM activities a
                        WHERE a.id = %s
                        """,
                        (trip_id, sort_order, activity_id),
                    )
                    linked_activities += 1

    return (created, linked_activities)
