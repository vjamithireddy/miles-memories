import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db import get_conn


@dataclass
class ActivityRecord:
    activity_type: str
    activity_name: str
    start_time: datetime
    end_time: datetime | None
    distance_meters: float | None
    start_latitude: float | None
    start_longitude: float | None
    end_latitude: float | None
    end_longitude: float | None


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
            activity_type="other",
            activity_name=os.path.basename(path),
            start_time=now,
            end_time=None,
            distance_meters=None,
            start_latitude=None,
            start_longitude=None,
            end_latitude=None,
            end_longitude=None,
        )

    points.sort(key=lambda x: x[0])
    start = points[0]
    end = points[-1]
    return ActivityRecord(
        activity_type="hike",
        activity_name=os.path.basename(path),
        start_time=start[0],
        end_time=end[0],
        distance_meters=None,
        start_latitude=start[1],
        start_longitude=start[2],
        end_latitude=end[1],
        end_longitude=end[2],
    )


def parse_activity(path: str) -> ActivityRecord:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        return _parse_gpx(path)
    now = datetime.now(timezone.utc)
    return ActivityRecord(
        activity_type="other",
        activity_name=os.path.basename(path),
        start_time=now,
        end_time=None,
        distance_meters=None,
        start_latitude=None,
        start_longitude=None,
        end_latitude=None,
        end_longitude=None,
    )


def save_activity(import_id: int, record: ActivityRecord) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO activities (
                    import_id, source_activity_id, activity_type, activity_name,
                    start_time, end_time, distance_meters,
                    start_latitude, start_longitude, end_latitude, end_longitude
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    import_id,
                    record.activity_name,
                    record.activity_type,
                    record.activity_name,
                    record.start_time,
                    record.end_time,
                    record.distance_meters,
                    record.start_latitude,
                    record.start_longitude,
                    record.end_latitude,
                    record.end_longitude,
                ),
            )
            return int(cur.fetchone()[0])
