import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.db import get_conn

from ingestion.common import parse_ts


@dataclass
class LocationEvent:
    timestamp: datetime
    latitude: float
    longitude: float
    accuracy_meters: float | None
    source_event_id: str | None
    raw_payload: dict[str, Any]


def _from_e7(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 1e7
    except (TypeError, ValueError):
        return None


def _parse_locations_array(data: dict[str, Any]) -> Iterable[LocationEvent]:
    for item in data.get("locations", []):
        lat = _from_e7(item.get("latitudeE7"))
        lon = _from_e7(item.get("longitudeE7"))
        ts = None
        ts_ms = item.get("timestampMs")
        if ts_ms:
            try:
                ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
            except (TypeError, ValueError):
                ts = None
        if ts is None:
            ts = parse_ts(item.get("timestamp"))
        if lat is None or lon is None or ts is None:
            continue
        yield LocationEvent(
            timestamp=ts,
            latitude=lat,
            longitude=lon,
            accuracy_meters=item.get("accuracy") or item.get("accuracyMeters"),
            source_event_id=item.get("source") or str(ts_ms) if ts_ms else None,
            raw_payload=item,
        )


def _parse_timeline_objects(data: dict[str, Any]) -> Iterable[LocationEvent]:
    for obj in data.get("timelineObjects", []):
        pv = obj.get("placeVisit")
        if pv:
            loc = pv.get("location", {})
            lat = _from_e7(loc.get("latitudeE7"))
            lon = _from_e7(loc.get("longitudeE7"))
            dur = pv.get("duration", {})
            ts = parse_ts(dur.get("startTimestamp")) or parse_ts(dur.get("endTimestamp"))
            if lat is None or lon is None or ts is None:
                continue
            yield LocationEvent(
                timestamp=ts,
                latitude=lat,
                longitude=lon,
                accuracy_meters=None,
                source_event_id=loc.get("placeId"),
                raw_payload=obj,
            )
        seg = obj.get("activitySegment")
        if seg:
            start = seg.get("startLocation", {})
            lat = _from_e7(start.get("latitudeE7"))
            lon = _from_e7(start.get("longitudeE7"))
            dur = seg.get("duration", {})
            ts = parse_ts(dur.get("startTimestamp"))
            if lat is None or lon is None or ts is None:
                continue
            yield LocationEvent(
                timestamp=ts,
                latitude=lat,
                longitude=lon,
                accuracy_meters=None,
                source_event_id=seg.get("activityType"),
                raw_payload=obj,
            )


def parse_location_history(path: str) -> list[LocationEvent]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = list(_parse_locations_array(data))
    events.extend(_parse_timeline_objects(data))
    events.sort(key=lambda e: e.timestamp)
    return events


def save_location_events(import_id: int, events: list[LocationEvent]) -> int:
    if not events:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for event in events:
                cur.execute(
                    """
                    INSERT INTO location_events (
                        import_id, source_event_id, event_timestamp, latitude, longitude,
                        accuracy_meters, source, raw_payload_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'google_timeline', %s)
                    """,
                    (
                        import_id,
                        event.source_event_id,
                        event.timestamp,
                        event.latitude,
                        event.longitude,
                        event.accuracy_meters,
                        json.dumps(event.raw_payload),
                    ),
                )
    return len(events)
