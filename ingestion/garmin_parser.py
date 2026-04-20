from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db import get_conn


@dataclass
class ActivityRecord:
    source: str
    source_activity_id: str
    activity_type: str
    activity_name: str
    start_time: datetime
    end_time: datetime | None
    duration_seconds: int | None
    distance_meters: float | None
    elevation_gain_meters: float | None
    elevation_loss_meters: float | None
    moving_time_seconds: int | None
    elapsed_time_seconds: int | None
    average_speed_mps: float | None
    max_speed_mps: float | None
    average_heart_rate: int | None
    max_heart_rate: int | None
    calories: int | None
    start_latitude: float | None
    start_longitude: float | None
    end_latitude: float | None
    end_longitude: float | None
    route_polyline: str | None
    raw_metadata_json: dict | None


def _nsless(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_gpx(path: str) -> ActivityRecord:
    root = ET.parse(path).getroot()
    points: list[tuple[datetime, float, float]] = []
    for elem in root.iter():
        if _nsless(elem.tag) != "trkpt":
            continue
        lat = elem.attrib.get("lat")
        lon = elem.attrib.get("lon")
        t = None
        for child in elem:
            if _nsless(child.tag) == "time" and child.text:
                txt = child.text.strip().replace("Z", "+00:00")
                try:
                    t = datetime.fromisoformat(txt)
                except ValueError:
                    t = None
        if lat is None or lon is None or t is None:
            continue
        points.append((t, float(lat), float(lon)))

    if not points:
        now = datetime.now(timezone.utc)
        return ActivityRecord(
            source="garmin",
            source_activity_id=os.path.basename(path),
            activity_type="other",
            activity_name=os.path.basename(path),
            start_time=now,
            end_time=None,
            duration_seconds=None,
            distance_meters=None,
            elevation_gain_meters=None,
            elevation_loss_meters=None,
            moving_time_seconds=None,
            elapsed_time_seconds=None,
            average_speed_mps=None,
            max_speed_mps=None,
            average_heart_rate=None,
            max_heart_rate=None,
            calories=None,
            start_latitude=None,
            start_longitude=None,
            end_latitude=None,
            end_longitude=None,
            route_polyline=None,
            raw_metadata_json=None,
        )

    points.sort(key=lambda x: x[0])
    start = points[0]
    end = points[-1]
    return ActivityRecord(
        source="garmin",
        source_activity_id=os.path.basename(path),
        activity_type="hike",
        activity_name=os.path.basename(path),
        start_time=start[0],
        end_time=end[0],
        duration_seconds=int((end[0] - start[0]).total_seconds())
        if end[0] and start[0]
        else None,
        distance_meters=None,
        elevation_gain_meters=None,
        elevation_loss_meters=None,
        moving_time_seconds=None,
        elapsed_time_seconds=None,
        average_speed_mps=None,
        max_speed_mps=None,
        average_heart_rate=None,
        max_heart_rate=None,
        calories=None,
        start_latitude=start[1],
        start_longitude=start[2],
        end_latitude=end[1],
        end_longitude=end[2],
        route_polyline=None,
        raw_metadata_json=None,
    )


def _parse_datetime(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            epoch = float(stripped)
            if epoch > 1_000_000_000_000:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        txt = stripped.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(txt)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _normalize_duration(value: object) -> int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    if numeric > 1_000_000:
        numeric /= 1000.0
    return int(round(numeric))


def _normalize_distance(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    if numeric > 1_000_000:
        numeric /= 100.0
    return numeric


def _normalize_elevation(
    value: object,
    *,
    distance_meters: float | None,
    duration_seconds: int | None,
) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    # Garmin summary exports can encode ascent/descent in centimeters.
    # Normalize obviously implausible meter values before storing them.
    if numeric >= 5_000:
        return numeric / 100.0
    if distance_meters and numeric >= 2_000 and numeric >= distance_meters * 0.25:
        return numeric / 100.0
    if duration_seconds and duration_seconds <= 4 * 3600 and numeric >= 2_000:
        return numeric / 100.0
    return numeric


def _parse_garmin_summary(path: str) -> list[ActivityRecord]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        entries = payload.get("summarizedActivitiesExport")
    elif isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and "summarizedActivitiesExport" in payload[0]:
            entries = payload[0].get("summarizedActivitiesExport")
        else:
            entries = payload
    else:
        entries = None
    if not isinstance(entries, list):
        return []

    records: list[ActivityRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_activity_id = str(entry.get("activityId") or entry.get("activity_id") or "").strip()
        if not source_activity_id:
            continue
        activity_type = "other"
        activity_type_info = entry.get("activityType")
        if isinstance(activity_type_info, dict):
            activity_type = activity_type_info.get("typeKey") or activity_type
        elif isinstance(activity_type_info, str):
            activity_type = activity_type_info
        activity_type = entry.get("activityTypeKey") or activity_type
        activity_name = entry.get("name") or entry.get("activityName") or activity_type
        start_time = _parse_datetime(entry.get("startTimeGmt") or entry.get("startTimeLocal"))
        duration_seconds = _normalize_duration(entry.get("duration"))
        end_time = _parse_datetime(entry.get("endTimeGmt") or entry.get("endTimeLocal"))
        if end_time is None and start_time and duration_seconds:
            end_time = start_time + timedelta(seconds=duration_seconds)
        distance_meters = _normalize_distance(entry.get("distance"))

        records.append(
            ActivityRecord(
                source="garmin",
                source_activity_id=source_activity_id,
                activity_type=str(activity_type).lower(),
                activity_name=str(activity_name),
                start_time=start_time or datetime.now(timezone.utc),
                end_time=end_time,
                duration_seconds=duration_seconds,
                distance_meters=distance_meters,
                elevation_gain_meters=_normalize_elevation(
                    entry.get("totalElevationGain") or entry.get("elevationGain"),
                    distance_meters=distance_meters,
                    duration_seconds=duration_seconds,
                ),
                elevation_loss_meters=_normalize_elevation(
                    entry.get("totalElevationLoss") or entry.get("elevationLoss"),
                    distance_meters=distance_meters,
                    duration_seconds=duration_seconds,
                ),
                moving_time_seconds=_normalize_duration(entry.get("movingDuration")),
                elapsed_time_seconds=_normalize_duration(entry.get("elapsedDuration")),
                average_speed_mps=entry.get("averageSpeed"),
                max_speed_mps=entry.get("maxSpeed"),
                average_heart_rate=entry.get("averageHR"),
                max_heart_rate=entry.get("maxHR"),
                calories=entry.get("calories"),
                start_latitude=entry.get("startLatitude"),
                start_longitude=entry.get("startLongitude"),
                end_latitude=entry.get("endLatitude"),
                end_longitude=entry.get("endLongitude"),
                route_polyline=entry.get("routePolyline"),
                raw_metadata_json=entry,
            )
        )
    return records


def parse_activity(path: str) -> ActivityRecord:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        return _parse_gpx(path)
    now = datetime.now(timezone.utc)
    return ActivityRecord(
        source="garmin",
        source_activity_id=os.path.basename(path),
        activity_type="other",
        activity_name=os.path.basename(path),
        start_time=now,
        end_time=None,
        duration_seconds=None,
        distance_meters=None,
        elevation_gain_meters=None,
        elevation_loss_meters=None,
        moving_time_seconds=None,
        elapsed_time_seconds=None,
        average_speed_mps=None,
        max_speed_mps=None,
        average_heart_rate=None,
        max_heart_rate=None,
        calories=None,
        start_latitude=None,
        start_longitude=None,
        end_latitude=None,
        end_longitude=None,
        route_polyline=None,
        raw_metadata_json=None,
    )


def parse_activities(path: str) -> list[ActivityRecord]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        records = _parse_garmin_summary(path)
        return records if records else []
    return [parse_activity(path)]


def _link_activity_to_trip(activity_id: int, start_time: datetime, end_time: datetime) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       LEAST(end_time, %s) - GREATEST(start_time, %s) AS overlap
                FROM trips
                WHERE start_time <= %s
                  AND end_time >= %s
                ORDER BY overlap DESC NULLS LAST
                LIMIT 1
                """,
                (end_time, start_time, end_time, start_time),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("UPDATE activities SET trip_id = NULL WHERE id = %s", (activity_id,))
                return None
            trip_id = int(row[0])
            cur.execute("UPDATE activities SET trip_id = %s WHERE id = %s", (trip_id, activity_id))
            return trip_id


def save_activity(import_id: int, record: ActivityRecord) -> tuple[int, bool, int | None]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            raw_payload = (
                json.dumps(record.raw_metadata_json)
                if record.raw_metadata_json is not None
                else None
            )
            cur.execute(
                """
                INSERT INTO activities (
                    import_id, source, source_activity_id, activity_type, activity_name,
                    start_time, end_time, duration_seconds, distance_meters,
                    elevation_gain_meters, elevation_loss_meters,
                    moving_time_seconds, elapsed_time_seconds,
                    average_speed_mps, max_speed_mps,
                    average_heart_rate, max_heart_rate, calories,
                    start_latitude, start_longitude, end_latitude, end_longitude,
                    route_polyline, raw_metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, source_activity_id)
                DO UPDATE SET
                    import_id = EXCLUDED.import_id,
                    activity_type = EXCLUDED.activity_type,
                    activity_name = EXCLUDED.activity_name,
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    duration_seconds = EXCLUDED.duration_seconds,
                    distance_meters = EXCLUDED.distance_meters,
                    elevation_gain_meters = EXCLUDED.elevation_gain_meters,
                    elevation_loss_meters = EXCLUDED.elevation_loss_meters,
                    moving_time_seconds = EXCLUDED.moving_time_seconds,
                    elapsed_time_seconds = EXCLUDED.elapsed_time_seconds,
                    average_speed_mps = EXCLUDED.average_speed_mps,
                    max_speed_mps = EXCLUDED.max_speed_mps,
                    average_heart_rate = EXCLUDED.average_heart_rate,
                    max_heart_rate = EXCLUDED.max_heart_rate,
                    calories = EXCLUDED.calories,
                    start_latitude = EXCLUDED.start_latitude,
                    start_longitude = EXCLUDED.start_longitude,
                    end_latitude = EXCLUDED.end_latitude,
                    end_longitude = EXCLUDED.end_longitude,
                    route_polyline = EXCLUDED.route_polyline,
                    raw_metadata_json = EXCLUDED.raw_metadata_json,
                    updated_at = NOW()
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    import_id,
                    record.source,
                    record.source_activity_id,
                    record.activity_type,
                    record.activity_name,
                    record.start_time,
                    record.end_time,
                    record.duration_seconds,
                    record.distance_meters,
                    record.elevation_gain_meters,
                    record.elevation_loss_meters,
                    record.moving_time_seconds,
                    record.elapsed_time_seconds,
                    record.average_speed_mps,
                    record.max_speed_mps,
                    record.average_heart_rate,
                    record.max_heart_rate,
                    record.calories,
                    record.start_latitude,
                    record.start_longitude,
                    record.end_latitude,
                    record.end_longitude,
                    record.route_polyline,
                    raw_payload,
                ),
            )
            activity_id, inserted = cur.fetchone()
            start_time = record.start_time
            end_time = record.end_time or (
                record.start_time + timedelta(seconds=record.duration_seconds)
                if record.duration_seconds
                else record.start_time
            )
            trip_id = _link_activity_to_trip(int(activity_id), start_time, end_time)
            return int(activity_id), bool(inserted), trip_id
