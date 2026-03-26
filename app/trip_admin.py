from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.rows import dict_row

from app.bootstrap import get_home_profile, get_user_timezone
from app.db import get_conn
from trip_engine.detector import (
    _destination_title,
    _haversine_km,
    _resolve_destination_profile,
)


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


LEG_LABELS = {
    "FLYING": ("air", "Air travel"),
    "IN_PASSENGER_VEHICLE": ("car", "Car travel"),
    "WALKING": ("walk", "Walking"),
    "RUNNING": ("run", "Running"),
    "HIKING": ("hike", "Hiking"),
}

NEARBY_ENRICHMENT_OFFSETS = (
    (0.0, 0.01),
    (0.01, 0.0),
    (0.0, -0.01),
    (-0.01, 0.0),
)


def _is_placeholder_segment_summary(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return (
        normalized.endswith("inferred from timeline activity data.")
        or "rental car facility" in normalized
        or normalized.startswith("drive near ")
    )


def _is_generic_regional_segment_summary(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return bool(
        re.match(
            r"^(?:[a-z-]+\s+[a-z-]+\s+)?(?:drive|walk|hike|run) (?:in|from trail in|to trailhead in) .*(county|state|region)(?: \(\d+\))?\.?$",
            normalized,
        )
    )


def _trip_context_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(
        r"\s+(day trip|weekend|overnight|overnight trip|multi[ -]?day trip|trip)$",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip(" -")
    return normalized or value.strip()


def _clean_segment_place_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    lowered = cleaned.lower()
    if "rental car facility" in lowered:
        cleaned = re.sub(r"\s+rental car facility\b", "", cleaned, flags=re.IGNORECASE).strip(" -,")
        lowered = cleaned.lower()
    if lowered.endswith(" airport rental car"):
        cleaned = re.sub(r"\s+airport rental car\b", " Airport", cleaned, flags=re.IGNORECASE).strip()
        lowered = cleaned.lower()
    if lowered.endswith(" airport"):
        return cleaned
    return cleaned or None


def _preferred_segment_place(
    *names: str | None,
    fallback_trip_name: str | None = None,
) -> str | None:
    regional_fallback: str | None = None
    for name in names:
        cleaned = _clean_segment_place_name(name)
        if cleaned and not _is_regional_place(cleaned):
            return cleaned
        if cleaned and regional_fallback is None:
            regional_fallback = cleaned
    if regional_fallback:
        return regional_fallback
    return _trip_context_name(fallback_trip_name)


def _is_airport_like(value: str | None) -> bool:
    return bool(value and "airport" in value.lower())


def _segment_place_phrase(*names: str | None) -> str | None:
    keywords = (
        "trailhead",
        "viewpoint",
        "overlook",
        "visitor center",
        "lodge",
        "hotel",
        "inn",
        "resort",
        "campground",
        "camp",
        "village",
    )
    for name in names:
        if not name:
            continue
        lowered = name.lower()
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def _segment_place_role(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    role_keywords = (
        ("trailhead", "trailhead"),
        ("viewpoint", "viewpoint"),
        ("overlook", "viewpoint"),
        ("visitor center", "visitor center"),
        ("lodge", "lodging"),
        ("hotel", "lodging"),
        ("inn", "lodging"),
        ("resort", "lodging"),
        ("campground", "camp"),
        ("camp", "camp"),
        ("village", "village"),
    )
    for keyword, role in role_keywords:
        if keyword in lowered:
            return role
    return None


def _is_regional_place(name: str | None) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(token in lowered for token in ("county", "state", "region"))


def _prefer_locality_over_region(name: str | None, locality: str | None) -> str | None:
    cleaned_name = _clean_segment_place_name(name)
    cleaned_locality = _clean_segment_place_name(locality)
    if cleaned_name and not _is_regional_place(cleaned_name):
        return cleaned_name
    if cleaned_locality:
        return cleaned_locality
    return cleaned_name or cleaned_locality


def _place_candidate_score(place_name: str | None, locality: str | None) -> tuple[int, int, int]:
    cleaned_name = _clean_segment_place_name(place_name)
    cleaned_locality = _clean_segment_place_name(locality)
    has_specific_name = int(bool(cleaned_name and not _is_regional_place(cleaned_name)))
    has_locality = int(bool(cleaned_locality and not _is_regional_place(cleaned_locality)))
    has_any_name = int(bool(cleaned_name or cleaned_locality))
    return (has_specific_name, has_locality, has_any_name)


def _candidate_distance_sq(
    latitude: float,
    longitude: float,
    candidate_latitude: float | None,
    candidate_longitude: float | None,
) -> float:
    if candidate_latitude is None or candidate_longitude is None:
        return float("inf")
    return (float(candidate_latitude) - latitude) ** 2 + (float(candidate_longitude) - longitude) ** 2


def _best_nearby_place_candidate(
    rows: list[dict[str, Any]],
    *,
    latitude: float,
    longitude: float,
) -> dict[str, str] | None:
    shortlisted = []
    for row in rows:
        preferred_name = _prefer_locality_over_region(row.get("place_name"), row.get("city"))
        if not preferred_name or _is_regional_place(preferred_name):
            continue
        shortlisted.append(
            (
                _candidate_distance_sq(latitude, longitude, row.get("latitude"), row.get("longitude")),
                -int(row.get("id") or 0),
                preferred_name,
                row.get("place_type") or "",
            )
        )
    if not shortlisted:
        return None
    shortlisted.sort()
    return {
        "name": shortlisted[0][2],
        "place_type": shortlisted[0][3],
    }


def _best_nearby_place_name(
    rows: list[dict[str, Any]],
    *,
    latitude: float,
    longitude: float,
) -> str | None:
    candidate = _best_nearby_place_candidate(rows, latitude=latitude, longitude=longitude)
    if not candidate:
        return None
    return candidate["name"]


def _enrich_nearby_endpoint_places(latitude: float, longitude: float) -> int:
    enriched = 0
    for lat_offset, lon_offset in NEARBY_ENRICHMENT_OFFSETS:
        _resolve_destination_profile(
            latitude + lat_offset,
            longitude + lon_offset,
            force_refresh=True,
        )
        enriched += 1
    return enriched


def _drive_duration_minutes(leg: dict[str, Any]) -> int:
    start_time = leg.get("start_time")
    end_time = leg.get("end_time")
    if not start_time or not end_time:
        return 0
    return max(0, int((end_time - start_time).total_seconds() // 60))


def _specific_leg_place_name(leg: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _clean_segment_place_name(leg.get(key))
        if value and not _is_regional_place(value):
            return value
    return None


def _apply_trip_context_place_inference(legs: list[dict[str, Any]]) -> None:
    for index, leg in enumerate(legs):
        start_name = _clean_segment_place_name(leg.get("start_place_name"))
        end_name = _clean_segment_place_name(leg.get("end_place_name"))
        if not start_name or _is_regional_place(start_name):
            inferred_start = None
            if index > 0:
                inferred_start = _specific_leg_place_name(
                    legs[index - 1],
                    "end_place_name",
                    "start_place_name",
                    "context_end_name",
                    "context_start_name",
                )
            if inferred_start:
                leg["context_start_name"] = inferred_start
        if not end_name or _is_regional_place(end_name):
            inferred_end = None
            if index + 1 < len(legs):
                inferred_end = _specific_leg_place_name(
                    legs[index + 1],
                    "start_place_name",
                    "end_place_name",
                    "context_start_name",
                    "context_end_name",
                )
            if not inferred_end and index > 0:
                inferred_end = _specific_leg_place_name(
                    legs[index - 1],
                    "end_place_name",
                    "start_place_name",
                    "context_end_name",
                    "context_start_name",
                )
            if inferred_end:
                leg["context_end_name"] = inferred_end


def _merge_leg_path_points(target: dict[str, Any], extra_points: list[dict[str, Any]] | None) -> None:
    for point in extra_points or []:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        candidate = {"lat": float(lat), "lon": float(lon)}
        if not target["path_points"] or target["path_points"][-1] != candidate:
            target["path_points"].append(candidate)


def _should_merge_adjacent_legs(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("leg_type") != right.get("leg_type"):
        return False
    left_start = _clean_segment_place_name(left.get("start_place_name"))
    left_end = _clean_segment_place_name(left.get("end_place_name"))
    right_start = _clean_segment_place_name(right.get("start_place_name"))
    right_end = _clean_segment_place_name(right.get("end_place_name"))
    if not left_start or not left_end or not right_start or not right_end:
        return False
    if left_start != right_start or left_end != right_end:
        return False
    left_end_time = left.get("end_time")
    right_start_time = right.get("start_time")
    if not left_end_time or not right_start_time:
        return False
    gap_minutes = max(0, int((right_start_time - left_end_time).total_seconds() // 60))
    return gap_minutes <= 10


def _merge_adjacent_duplicate_route_legs(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for leg in legs:
        if merged and _should_merge_adjacent_legs(merged[-1], leg):
            previous = merged[-1]
            if leg.get("end_time") and leg["end_time"] > previous["end_time"]:
                previous["end_time"] = leg["end_time"]
            if leg.get("end_latitude") is not None:
                previous["end_latitude"] = leg["end_latitude"]
            if leg.get("end_longitude") is not None:
                previous["end_longitude"] = leg["end_longitude"]
            if leg.get("end_place_name"):
                previous["end_place_name"] = leg["end_place_name"]
            _merge_leg_path_points(previous, leg.get("path_points"))
            continue
        merged.append(leg)
    return merged


def _leg_default_summary(
    leg: dict[str, Any],
    *,
    trip_name: str | None = None,
    trip_summary_text: str | None = None,
    origin_name: str | None = None,
    destination_name: str | None = None,
    previous_leg_type: str | None = None,
    next_leg_type: str | None = None,
) -> str:
    label = leg["label"]
    start_name = _clean_segment_place_name(leg.get("context_start_name")) or _clean_segment_place_name(leg.get("start_place_name"))
    end_name = _clean_segment_place_name(leg.get("context_end_name")) or _clean_segment_place_name(leg.get("end_place_name"))
    leg_type = leg["leg_type"]
    trip_context = _trip_context_name(trip_name)
    preferred_destination = _preferred_segment_place(
        destination_name,
        end_name,
        start_name,
        fallback_trip_name=trip_context,
    )
    preferred_origin = _preferred_segment_place(origin_name, start_name)
    specific_place = _segment_place_phrase(end_name, start_name, preferred_destination)
    specific_role = _segment_place_role(specific_place)

    if leg_type == "air":
        if preferred_origin and preferred_destination and preferred_origin != preferred_destination:
            return f"Flight from {preferred_origin} to {preferred_destination}."
        if preferred_destination:
            return f"Flight to {preferred_destination}."
        return "Flight segment inferred from timeline activity data."

    if leg_type == "car":
        drive_minutes = _drive_duration_minutes(leg)
        if next_leg_type == "air":
            return "Drive to airport."
        if previous_leg_type == "air":
            if specific_place:
                return f"Drive from airport to {specific_place}."
            if preferred_destination and "airport" not in preferred_destination.lower():
                return f"Drive from airport toward {preferred_destination}."
            return "Drive from airport."
        if next_leg_type in {"walk", "hike", "run"}:
            if specific_place:
                return f"Drive to {specific_place}."
            if trip_context:
                return f"Drive to trailhead in {trip_context}."
            return "Drive to trailhead."
        if previous_leg_type in {"walk", "hike", "run"}:
            if specific_place:
                if specific_role == "lodging":
                    return f"Drive from trail to {specific_place}."
                if specific_role == "village":
                    return f"Drive from trail into {specific_place}."
                return f"Drive from trail toward {specific_place}."
            if trip_context:
                return f"Drive from trail in {trip_context}."
        if start_name and end_name and start_name != end_name:
            if not (_is_regional_place(start_name) and _is_regional_place(end_name)):
                return f"Drive from {start_name} to {end_name}."
        if specific_place:
            if specific_role == "lodging":
                return f"Drive to {specific_place}."
            if specific_role == "viewpoint":
                return f"Drive to {specific_place}."
            if specific_role == "visitor center":
                return f"Drive to {specific_place}."
            if specific_role == "camp":
                return f"Drive to {specific_place}."
            if specific_role == "village":
                return f"Drive in {specific_place}."
            if specific_role == "trailhead":
                return f"Drive to {specific_place}."
        if drive_minutes >= 90:
            if preferred_origin and preferred_destination and preferred_origin != preferred_destination:
                if not (_is_regional_place(preferred_origin) and _is_regional_place(preferred_destination)):
                    return f"Drive from {preferred_origin} to {preferred_destination}."
            return "Road trip drive."
        if _is_airport_like(preferred_destination) and trip_context:
            return f"Drive in {trip_context}."
        if preferred_destination:
            return f"Drive in {preferred_destination}."
        if preferred_origin:
            return f"Drive from {preferred_origin}."

    if leg_type in {"walk", "hike", "run"}:
        verb = {"walk": "Walk", "hike": "Hike", "run": "Run"}[leg_type]
        if leg_type == "hike" and trip_summary_text:
            return trip_summary_text.rstrip(".") + "."
        if trip_context:
            return f"{verb} in {trip_context}."
        if preferred_destination:
            return f"{verb} in {preferred_destination}."
        if end_name:
            return f"{verb} toward {end_name}."
        if start_name:
            return f"{verb} from {start_name}."

    if start_name and end_name:
        return f"{label} from {start_name} to {end_name}."
    if end_name:
        return f"{label} toward {end_name}."
    if start_name:
        return f"{label} leaving {start_name}."
    return f"{label} inferred from timeline activity data."


def _should_refresh_segment_summary(
    existing_summary: str | None,
    *,
    leg: dict[str, Any],
    trip_name: str | None = None,
    destination_name: str | None = None,
) -> bool:
    if _is_placeholder_segment_summary(existing_summary):
        return True
    if leg.get("leg_type") in {"car", "walk", "hike", "run"} and _is_generic_regional_segment_summary(existing_summary):
        return True
    if not existing_summary:
        return True
    normalized_existing = existing_summary.strip()
    trip_context = _trip_context_name(trip_name)
    cleaned_destination = _preferred_segment_place(
        destination_name,
        leg.get("end_place_name"),
        leg.get("start_place_name"),
        fallback_trip_name=trip_context,
    )
    if leg.get("leg_type") == "air" and trip_context and cleaned_destination:
        legacy_summary = f"Flight from {trip_context} to {cleaned_destination}."
        if normalized_existing == legacy_summary:
            return True
    return False


def _segment_local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_user_timezone())
    except Exception:
        return ZoneInfo("America/Chicago")


def _segment_time_bucket(value: datetime) -> str:
    hour = value.hour
    if hour < 5:
        return "Late-night"
    if hour < 11:
        return "Morning"
    if hour < 14:
        return "Midday"
    if hour < 18:
        return "Afternoon"
    if hour < 22:
        return "Evening"
    return "Night"


def _disambiguated_summary(summary: str, leg: dict[str, Any], sequence: int, total: int) -> str:
    base = summary.rstrip(".")
    if not base:
        return summary
    local_start = leg["start_time"].astimezone(_segment_local_zone())
    prefix = f"{local_start.strftime('%A')} {_segment_time_bucket(local_start)}"
    adjusted = f"{prefix} {base[0].lower()}{base[1:]}"
    if total > 1:
        adjusted = f"{adjusted} ({sequence})"
    return f"{adjusted}."


def _apply_duplicate_leg_summary_disambiguation(cur: Any, legs: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for leg in legs:
        summary = (leg.get("segment_summary") or "").strip()
        if not summary:
            continue
        grouped.setdefault((leg["leg_type"], summary), []).append(leg)

    for (_, summary), grouped_legs in grouped.items():
        if len(grouped_legs) < 2:
            continue
        for index, leg in enumerate(grouped_legs, start=1):
            updated_summary = _disambiguated_summary(summary, leg, index, len(grouped_legs))
            if updated_summary == leg["segment_summary"]:
                continue
            leg["segment_summary"] = updated_summary
            if leg.get("segment_summary_auto"):
                cur.execute(
                    """
                    UPDATE trip_segments
                    SET notes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (updated_summary, leg["segment_id"]),
                )


def _leg_point_place_details(latitude: float | None, longitude: float | None) -> dict[str, str] | None:
    if latitude is None or longitude is None:
        return None
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, place_name, place_type, source, city
                FROM places
                WHERE round(latitude::numeric, 3) = round(%s::numeric, 3)
                  AND round(longitude::numeric, 3) = round(%s::numeric, 3)
                ORDER BY id DESC
                """,
                (float(latitude), float(longitude)),
            )
            rows = cur.fetchall()
            cur.execute(
                """
                SELECT id, place_name, place_type, source, city, latitude, longitude
                FROM places
                WHERE ABS(latitude - %s) <= 0.15
                  AND ABS(longitude - %s) <= 0.15
                ORDER BY id DESC
                LIMIT 200
                """,
                (float(latitude), float(longitude)),
            )
            nearby_rows = cur.fetchall()
    if not rows:
        return _best_nearby_place_candidate(nearby_rows, latitude=float(latitude), longitude=float(longitude))
    row = max(
        rows,
        key=lambda candidate: (
            *_place_candidate_score(candidate["place_name"], candidate["city"]),
            int(candidate.get("id") or 0),
        ),
    )
    preferred_name = _prefer_locality_over_region(row["place_name"], row["city"])
    profile = {
        "name": preferred_name,
        "category": row["place_type"],
        "display_name": row["city"],
        "locality": row["city"],
        "classification": row["source"],
    }
    title = _destination_title(profile)
    if title and not _is_regional_place(title):
        return {
            "name": title,
            "place_type": row.get("place_type") or "",
        }
    nearby_candidate = _best_nearby_place_candidate(
        nearby_rows,
        latitude=float(latitude),
        longitude=float(longitude),
    )
    if nearby_candidate:
        return nearby_candidate
    if title:
        return {
            "name": title,
            "place_type": row.get("place_type") or "",
        }
    return None


def _leg_point_place_name(latitude: float | None, longitude: float | None) -> str | None:
    details = _leg_point_place_details(latitude, longitude)
    if not details:
        return None
    return details["name"]


def _build_travel_legs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    current_leg: dict[str, Any] | None = None
    pending_path_rows: list[dict[str, Any]] = []
    gap_split_threshold = timedelta(minutes=90)
    pending_attach_threshold = timedelta(minutes=15)

    def _new_leg(movement_type: str, event_time: datetime) -> dict[str, Any]:
        label_type, label = LEG_LABELS[movement_type]
        return {
            "leg_type": label_type,
            "label": label,
            "start_time": event_time,
            "end_time": event_time,
            "start_latitude": None,
            "start_longitude": None,
            "end_latitude": None,
            "end_longitude": None,
            "source_event_id": movement_type,
            "path_points": [],
            "start_place_name": None,
            "start_place_type": None,
            "end_place_name": None,
            "end_place_type": None,
            "_first_place_hint": None,
            "_last_place_hint": None,
        }

    def _append_point(leg: dict[str, Any], latitude: Any, longitude: Any) -> None:
        if latitude is None or longitude is None:
            return
        point = {"lat": float(latitude), "lon": float(longitude)}
        if not leg["path_points"] or leg["path_points"][-1] != point:
            leg["path_points"].append(point)

    def _append_path_row_to_leg(leg: dict[str, Any], row: dict[str, Any]) -> None:
        event_time = row["event_time"]
        if event_time < leg["start_time"]:
            leg["start_time"] = event_time
        if event_time > leg["end_time"]:
            leg["end_time"] = event_time
        _append_point(leg, row.get("latitude"), row.get("longitude"))
        if leg["start_latitude"] is None or leg["start_longitude"] is None:
            if row.get("latitude") is not None and row.get("longitude") is not None:
                leg["start_latitude"] = float(row["latitude"])
                leg["start_longitude"] = float(row["longitude"])
        if row.get("latitude") is not None and row.get("longitude") is not None:
            leg["end_latitude"] = float(row["latitude"])
            leg["end_longitude"] = float(row["longitude"])

    def _is_timeline_path_row(row: dict[str, Any]) -> bool:
        return str(row.get("source_event_id") or "").startswith("timeline_path:")

    for row in rows:
        if pending_path_rows and (row["event_time"] - pending_path_rows[-1]["event_time"]) > gap_split_threshold:
            pending_path_rows = []

        raw_payload = row["raw_payload_json"]
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = None
        raw_payload = raw_payload if isinstance(raw_payload, dict) else {}

        activity = raw_payload.get("activity")
        activity = activity if isinstance(activity, dict) else {}
        top_candidate = activity.get("topCandidate") or {}
        candidate_type = top_candidate.get("type")
        anchor_type = row["source_event_id"] if row["source_event_id"] in LEG_LABELS else None
        movement_type = anchor_type or (candidate_type if current_leg is None and candidate_type in LEG_LABELS else None)

        if current_leg is not None and (row["event_time"] - current_leg["end_time"]) > gap_split_threshold:
            legs.append(current_leg)
            current_leg = None
            if _is_timeline_path_row(row):
                pending_path_rows = [row]
                continue

        if current_leg is None and _is_timeline_path_row(row):
            pending_path_rows.append(row)
            continue

        if movement_type in LEG_LABELS and anchor_type:
            if current_leg is not None:
                legs.append(current_leg)
            current_leg = _new_leg(movement_type, row["event_time"])
            if pending_path_rows and (row["event_time"] - pending_path_rows[-1]["event_time"]) <= pending_attach_threshold:
                for pending_row in pending_path_rows:
                    _append_path_row_to_leg(current_leg, pending_row)
            pending_path_rows = []
        elif movement_type in LEG_LABELS and current_leg is None:
            current_leg = _new_leg(movement_type, row["event_time"])
            if pending_path_rows and (row["event_time"] - pending_path_rows[-1]["event_time"]) <= pending_attach_threshold:
                for pending_row in pending_path_rows:
                    _append_path_row_to_leg(current_leg, pending_row)
            pending_path_rows = []
        elif current_leg is None:
            continue

        if (
            current_leg is not None
            and row.get("source_event_id")
            and row["source_event_id"] not in LEG_LABELS
            and not str(row["source_event_id"]).startswith("timeline_path:")
        ):
            place_hint = _leg_point_place_name(row.get("latitude"), row.get("longitude"))
            if place_hint:
                if not current_leg.get("_first_place_hint"):
                    current_leg["_first_place_hint"] = place_hint
                current_leg["_last_place_hint"] = place_hint

        start = activity.get("start") or {}
        end = activity.get("end") or {}
        if row["event_time"] < current_leg["start_time"]:
            current_leg["start_time"] = row["event_time"]
        if row["event_time"] > current_leg["end_time"]:
            current_leg["end_time"] = row["event_time"]
        _append_point(current_leg, row.get("latitude"), row.get("longitude"))
        if start.get("latLng"):
            lat, lon = start["latLng"].replace("°", "").split(",")
            current_leg["start_latitude"] = float(lat.strip())
            current_leg["start_longitude"] = float(lon.strip())
        if end.get("latLng"):
            lat, lon = end["latLng"].replace("°", "").split(",")
            current_leg["end_latitude"] = float(lat.strip())
            current_leg["end_longitude"] = float(lon.strip())
        start_time = activity.get("startTime")
        end_time = activity.get("endTime")
        if start_time:
            parsed_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if parsed_start < current_leg["start_time"]:
                current_leg["start_time"] = parsed_start
        if end_time:
            parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            if parsed_end > current_leg["end_time"]:
                current_leg["end_time"] = parsed_end

    if current_leg is not None:
        legs.append(current_leg)

    for leg in legs:
        if leg["start_latitude"] is None or leg["start_longitude"] is None:
            first_point = leg["path_points"][0] if leg["path_points"] else None
            if first_point:
                leg["start_latitude"] = float(first_point["lat"])
                leg["start_longitude"] = float(first_point["lon"])
        if leg["end_latitude"] is None or leg["end_longitude"] is None:
            last_point = leg["path_points"][-1] if leg["path_points"] else None
            if last_point:
                leg["end_latitude"] = float(last_point["lat"])
                leg["end_longitude"] = float(last_point["lon"])
        if not leg["path_points"]:
            if leg["start_latitude"] is not None and leg["start_longitude"] is not None:
                leg["path_points"].append(
                    {"lat": leg["start_latitude"], "lon": leg["start_longitude"]}
                )
            if leg["end_latitude"] is not None and leg["end_longitude"] is not None:
                end_point = {"lat": leg["end_latitude"], "lon": leg["end_longitude"]}
                if not leg["path_points"] or leg["path_points"][-1] != end_point:
                    leg["path_points"].append(end_point)
        start_place = _leg_point_place_details(
            leg.get("start_latitude"),
            leg.get("start_longitude"),
        )
        end_place = _leg_point_place_details(
            leg.get("end_latitude"),
            leg.get("end_longitude"),
        )
        leg["start_place_name"] = start_place["name"] if start_place else None
        leg["start_place_type"] = start_place["place_type"] if start_place else None
        leg["end_place_name"] = end_place["name"] if end_place else None
        leg["end_place_type"] = end_place["place_type"] if end_place else None
        if not leg.get("start_place_name") and leg.get("_first_place_hint"):
            leg["start_place_name"] = leg["_first_place_hint"]
        if not leg.get("end_place_name") and leg.get("_last_place_hint"):
            leg["end_place_name"] = leg["_last_place_hint"]
        leg.pop("_first_place_hint", None)
        leg.pop("_last_place_hint", None)
    legs = _merge_adjacent_duplicate_route_legs(legs)
    _apply_trip_context_place_inference(legs)
    return legs


def get_trip_route_points(trip_id: int, *, append_home_if_close: bool = False) -> list[dict[str, float]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT le.latitude, le.longitude
                FROM trip_events te
                JOIN location_events le
                    ON te.event_type = 'location_event'
                   AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                  AND le.latitude IS NOT NULL
                  AND le.longitude IS NOT NULL
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            points = [
                {"lat": float(row["latitude"]), "lon": float(row["longitude"])}
                for row in cur.fetchall()
            ]

    if append_home_if_close and points:
        home_lat, home_lon, home_radius_meters = get_home_profile()
        if home_lat is not None and home_lon is not None:
            last_point = points[-1]
            distance_km = _haversine_km(
                float(last_point["lat"]),
                float(last_point["lon"]),
                float(home_lat),
                float(home_lon),
            )
            if distance_km <= (float(home_radius_meters or 16093) / 1000.0):
                home_point = {"lat": float(home_lat), "lon": float(home_lon)}
                if points[-1] != home_point:
                    points.append(home_point)

    if len(points) <= 900:
        return points

    step = max(1, len(points) // 900)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _serialize_travel_legs(travel_legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for leg in travel_legs:
        payload = dict(leg)
        payload["start_time"] = _serialize_datetime(leg.get("start_time"))
        payload["end_time"] = _serialize_datetime(leg.get("end_time"))
        serialized.append(payload)
    return serialized


def _deserialize_travel_legs(travel_legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for leg in travel_legs:
        payload = dict(leg)
        if isinstance(payload.get("start_time"), str):
            payload["start_time"] = _deserialize_datetime(payload.get("start_time"))
        if isinstance(payload.get("end_time"), str):
            payload["end_time"] = _deserialize_datetime(payload.get("end_time"))
        hydrated.append(payload)
    return hydrated


def get_trip_snapshot(trip_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT public_payload_json, admin_payload_json, updated_at
                FROM trip_snapshots
                WHERE trip_id = %s
                """,
                (trip_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    public_payload = row["public_payload_json"] or {}
    admin_payload = row["admin_payload_json"] or {}
    if public_payload.get("travel_legs"):
        public_payload["travel_legs"] = _deserialize_travel_legs(public_payload["travel_legs"])
    return {
        "public": public_payload,
        "admin": admin_payload,
        "updated_at": row["updated_at"],
    }


def upsert_trip_snapshot(
    trip_id: int,
    *,
    public_payload: dict[str, Any],
    admin_payload: dict[str, Any],
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trip_snapshots (trip_id, public_payload_json, admin_payload_json, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (trip_id)
                DO UPDATE SET
                    public_payload_json = EXCLUDED.public_payload_json,
                    admin_payload_json = EXCLUDED.admin_payload_json,
                    updated_at = NOW()
                """,
                (trip_id, json.dumps(public_payload), json.dumps(admin_payload)),
            )


def build_trip_snapshot(trip_id: int) -> dict[str, Any] | None:
    trip = get_trip(trip_id)
    if not trip:
        return None
    travel_legs = trip.get("travel_legs", [])
    map_points = get_trip_route_points(trip_id, append_home_if_close=True)
    public_payload = {
        "travel_legs": _serialize_travel_legs(travel_legs),
        "map_points": map_points,
    }
    admin_payload = {
        "map_points": map_points,
    }
    upsert_trip_snapshot(trip_id, public_payload=public_payload, admin_payload=admin_payload)
    return {
        "public": {
            "travel_legs": travel_legs,
            "map_points": map_points,
        },
        "admin": admin_payload,
    }


def get_trip_light(trip_id: int) -> dict[str, Any] | None:
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
                "SELECT COUNT(*)::BIGINT AS total FROM trip_segments WHERE trip_id = %s",
                (trip_id,),
            )
            count_row = cur.fetchone()
            trip["leg_count"] = int(count_row["total"]) if count_row else 0
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
            trip["timeline"] = []
            trip["event_counts"] = []
            return trip


def get_trip_review_state(trip_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT status, review_decision, is_private, publish_ready
                FROM trips
                WHERE id = %s
                """,
                (trip_id,),
            )
            return cur.fetchone()


def record_review_light(
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
        "save": (None, None),
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
                    status = COALESCE(%s, status),
                    review_decision = COALESCE(%s, review_decision),
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
            if reviewer_name or review_notes or action != "save":
                cur.execute(
                    """
                    INSERT INTO admin_reviews (trip_id, reviewer_name, review_action, review_notes)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (trip_id, reviewer_name, action, review_notes),
                )

    return get_trip_review_state(trip_id)


def enrich_trip_leg_places(trip_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    te.event_time,
                    le.source_event_id,
                    le.raw_payload_json,
                    le.latitude,
                    le.longitude
                FROM trip_events te
                JOIN location_events le
                    ON te.event_type = 'location_event'
                   AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            legs = _build_travel_legs(cur.fetchall())

    seen: set[tuple[float, float]] = set()
    enriched = 0
    for leg in legs:
        for latitude, longitude in (
            (leg.get("start_latitude"), leg.get("start_longitude")),
            (leg.get("end_latitude"), leg.get("end_longitude")),
        ):
            if latitude is None or longitude is None:
                continue
            key = (round(float(latitude), 5), round(float(longitude), 5))
            if key in seen:
                continue
            seen.add(key)
            current_name = _leg_point_place_name(float(latitude), float(longitude))
            force_refresh = _is_regional_place(current_name)
            profile = _resolve_destination_profile(
                float(latitude),
                float(longitude),
                force_refresh=force_refresh,
            )
            exact_title = _destination_title(profile)
            if force_refresh and _is_regional_place(exact_title) and exact_title == current_name:
                enriched += _enrich_nearby_endpoint_places(float(latitude), float(longitude))
            enriched += 1
    return enriched


def _sync_trip_segments(
    cur: Any,
    trip_id: int,
    legs: list[dict[str, Any]],
    *,
    trip_name: str | None = None,
    trip_summary_text: str | None = None,
    origin_name: str | None = None,
    destination_name: str | None = None,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            segment_type,
            start_time,
            end_time,
            segment_name,
            notes,
            rating,
            source_event_id
        FROM trip_segments
        WHERE trip_id = %s
        ORDER BY start_time ASC, id ASC
        """,
        (trip_id,),
    )
    existing_rows = cur.fetchall()
    existing_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in existing_rows:
        key = (
            row["segment_type"],
            row["start_time"],
            row["end_time"],
            row.get("source_event_id"),
        )
        existing_by_key[key] = row

    synced = []
    synced_ids: set[int] = set()
    for index, leg in enumerate(legs):
        key = (
            leg["leg_type"],
            leg["start_time"],
            leg["end_time"],
            leg.get("source_event_id"),
        )
        persisted = existing_by_key.get(key)
        previous_leg_type = legs[index - 1]["leg_type"] if index > 0 else None
        next_leg_type = legs[index + 1]["leg_type"] if index + 1 < len(legs) else None
        default_summary = _leg_default_summary(
            leg,
            trip_name=trip_name,
            trip_summary_text=trip_summary_text,
            origin_name=origin_name,
            destination_name=destination_name,
            previous_leg_type=previous_leg_type,
            next_leg_type=next_leg_type,
        )
        if not persisted:
            cur.execute(
                """
                INSERT INTO trip_segments (
                    trip_id,
                    segment_type,
                    start_time,
                    end_time,
                    start_place_name,
                    end_place_name,
                    notes,
                    segment_name,
                    rating,
                    source_event_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, segment_name, notes, rating
                """,
                (
                    trip_id,
                    leg["leg_type"],
                    leg["start_time"],
                    leg["end_time"],
                    leg.get("start_place_name"),
                    leg.get("end_place_name"),
                    default_summary,
                    leg["label"],
                    None,
                    leg.get("source_event_id"),
                ),
            )
            persisted = cur.fetchone()
            auto_summary = True
        elif _should_refresh_segment_summary(
            persisted.get("notes"),
            leg=leg,
            trip_name=trip_name,
            destination_name=destination_name,
        ):
            cur.execute(
                """
                UPDATE trip_segments
                SET notes = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, segment_name, notes, rating
                """,
                (default_summary, persisted["id"]),
            )
            persisted = cur.fetchone()
            auto_summary = True
        else:
            auto_summary = not persisted.get("notes")
        leg["segment_id"] = int(persisted["id"])
        synced_ids.add(int(persisted["id"]))
        leg["segment_name"] = persisted.get("segment_name") or leg["label"]
        leg["segment_summary"] = persisted.get("notes") or default_summary
        leg["segment_rating"] = persisted.get("rating")
        leg["segment_summary_auto"] = auto_summary
        synced.append(leg)
    _apply_duplicate_leg_summary_disambiguation(cur, synced)
    stale_ids = [int(row["id"]) for row in existing_rows if int(row["id"]) not in synced_ids]
    if stale_ids:
        cur.execute(
            "DELETE FROM trip_segments WHERE id = ANY(%s)",
            (stale_ids,),
        )
    return synced


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


def list_published_trips(*, limit: int = 12, offset: int = 0) -> list[dict[str, Any]]:
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
                WHERE is_private = FALSE
                  AND (
                    status = 'published'
                    OR publish_ready = TRUE
                    OR published_at IS NOT NULL
                  )
                ORDER BY COALESCE(published_at, end_time, start_time) DESC, id DESC
                LIMIT %s
                OFFSET %s
                """,
                (limit, offset),
            )
            return [_normalize_trip(row) for row in cur.fetchall()]


def count_published_trips() -> int:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT COUNT(*)::BIGINT AS total
                FROM trips
                WHERE is_private = FALSE
                  AND (
                    status = 'published'
                    OR publish_ready = TRUE
                    OR published_at IS NOT NULL
                  )
                """
            )
            row = cur.fetchone()
            return int(row["total"] or 0)


def build_public_home_intro(*, limit: int | None = None) -> dict[str, str]:
    trips = list_published_trips(limit=limit or 200)
    if not trips:
        return {
            "hero_note": "My published trips will show up here after I review them.",
            "highlight_line": "",
        }
    trip_ids = [trip["id"] for trip in trips]
    snapshots: dict[int, dict[str, Any]] = {}
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT trip_id, public_payload_json
                FROM trip_snapshots
                WHERE trip_id = ANY(%s)
                """,
                (trip_ids,),
            )
            for row in cur.fetchall():
                snapshots[int(row["trip_id"])] = row["public_payload_json"] or {}

    activities: list[str] = []
    places: list[str] = []

    def _clean_place_name(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = value.strip()
        lowered = cleaned.lower()
        if "rental car facility" in lowered:
            cleaned = re.sub(r"\s+rental car facility\b", "", cleaned, flags=re.IGNORECASE).strip(" -,")
        return cleaned or None

    def _is_regional_place(name: str | None) -> bool:
        if not name:
            return False
        lowered = name.lower()
        return any(token in lowered for token in ("county", "state", "region"))

    def _add_place(name: str | None) -> None:
        cleaned = _clean_place_name(name)
        if not cleaned or _is_regional_place(cleaned):
            return
        if cleaned not in places:
            places.append(cleaned)

    for trip in trips:
        snapshot = snapshots.get(trip["id"], {})
        travel_legs = snapshot.get("travel_legs") or []
        if travel_legs:
            for leg in travel_legs:
                label = (leg.get("label") or "").strip()
                if label and label not in activities:
                    activities.append(label)
                _add_place(leg.get("start_place_name"))
                _add_place(leg.get("end_place_name"))
        _add_place(trip.get("primary_destination_name"))

    activity_line = ", ".join(activities[:3]) if activities else "travel, walks, and drives"
    if len(activities) > 3:
        activity_line = f"{activity_line}, and more"
    place_line = " · ".join(places[:4]) if places else "my favorite destinations"
    if len(places) > 4:
        place_line = f"{place_line} · and more"

    hero_note = f"My experiences captured through hiking, driving, and trips."
    highlight_line = f"Places I’ve been: {place_line}. Activities include {activity_line}."
    return {
        "hero_note": hero_note,
        "highlight_line": highlight_line,
    }


def get_public_trip_by_slug(trip_slug: str) -> dict[str, Any] | None:
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
                WHERE trip_slug = %s
                  AND is_private = FALSE
                  AND (
                    status = 'published'
                    OR publish_ready = TRUE
                    OR published_at IS NOT NULL
                  )
                LIMIT 1
                """,
                (trip_slug,),
            )
            row = cur.fetchone()
    if not row:
        return None
    trip = _normalize_trip(row)
    snapshot = get_trip_snapshot(trip["id"])
    if not snapshot:
        snapshot = build_trip_snapshot(trip["id"])
    if snapshot and snapshot.get("public"):
        public_payload = snapshot["public"]
        trip["travel_legs"] = public_payload.get("travel_legs", [])
        trip["map_points"] = public_payload.get("map_points", [])
    else:
        trip["travel_legs"] = []
        trip["map_points"] = []
    return trip


def get_public_trip_by_id(trip_id: int) -> dict[str, Any] | None:
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
                  AND is_private = FALSE
                  AND (
                    status = 'published'
                    OR publish_ready = TRUE
                    OR published_at IS NOT NULL
                  )
                LIMIT 1
                """,
                (trip_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    trip = _normalize_trip(row)
    snapshot = get_trip_snapshot(trip["id"])
    if not snapshot:
        snapshot = build_trip_snapshot(trip["id"])
    if snapshot and snapshot.get("public"):
        public_payload = snapshot["public"]
        trip["travel_legs"] = public_payload.get("travel_legs", [])
        trip["map_points"] = public_payload.get("map_points", [])
    else:
        trip["travel_legs"] = []
        trip["map_points"] = []
    return trip


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
                    le.longitude,
                    le.source_event_id,
                    le.raw_payload_json
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
                    te.event_time,
                    le.source_event_id,
                    le.raw_payload_json,
                    le.latitude,
                    le.longitude
                FROM trip_events te
                JOIN location_events le
                    ON te.event_type = 'location_event'
                   AND le.id = te.event_ref_id
                WHERE te.trip_id = %s
                ORDER BY te.event_time ASC, te.id ASC
                """,
                (trip_id,),
            )
            trip["travel_legs"] = _sync_trip_segments(
                cur,
                trip_id,
                _build_travel_legs(cur.fetchall()),
                trip_name=trip.get("trip_name"),
                trip_summary_text=trip.get("summary_text"),
                origin_name=trip.get("origin_place_name"),
                destination_name=trip.get("primary_destination_name"),
            )

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


def rebuild_recent_snapshots(hours: int = 24) -> int:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id
                FROM trips
                WHERE updated_at >= NOW() - (%s || ' hours')::interval
                ORDER BY updated_at DESC
                """,
                (hours,),
            )
            trip_ids = [int(row["id"]) for row in cur.fetchall()]
    rebuilt = 0
    for trip_id in trip_ids:
        snapshot = build_trip_snapshot(trip_id)
        if snapshot:
            rebuilt += 1
    return rebuilt


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
        "save": (None, None),
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
                    status = COALESCE(%s, status),
                    review_decision = COALESCE(%s, review_decision),
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
            if reviewer_name or review_notes or action != "save":
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


def update_trip_segment(
    trip_id: int,
    segment_id: int,
    *,
    segment_name: str | None,
    summary_text: str | None,
    rating: int | None,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE trip_segments
                SET segment_name = COALESCE(%s, segment_name),
                    notes = COALESCE(%s, notes),
                    rating = %s,
                    updated_at = NOW()
                WHERE id = %s AND trip_id = %s
                RETURNING id
                """,
                (segment_name, summary_text, rating, segment_id, trip_id),
            )
            updated = cur.fetchone()
            if not updated:
                return None
    return get_trip(trip_id)
