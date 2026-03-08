import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from tempfile import TemporaryDirectory
from typing import Any

from app.db import get_conn

from ingestion.common import parse_ts


@dataclass
class PhotoRecord:
    filename: str
    original_filepath: str
    media_type: str
    captured_at: datetime | None
    latitude: float | None
    longitude: float | None
    camera_make: str | None
    camera_model: str | None
    raw_metadata: dict[str, Any]


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _sidecar_candidates(path: str) -> list[str]:
    base = path + ".json"
    root, _ = os.path.splitext(path)
    return [base, root + ".json"]


def _extract_photo(path: str, sidecar: dict[str, Any] | None) -> PhotoRecord:
    captured = parse_ts((sidecar or {}).get("photoTakenTime", {}).get("timestamp"))
    geo = (sidecar or {}).get("geoDataExif") or (sidecar or {}).get("geoData") or {}
    lat = geo.get("latitude")
    lon = geo.get("longitude")
    width = None
    height = None
    if sidecar:
        width = sidecar.get("width")
        height = sidecar.get("height")
    _ = width, height

    ext = os.path.splitext(path)[1].lower()
    media_type = "video" if ext in {".mp4", ".mov", ".mkv", ".avi"} else "photo"

    return PhotoRecord(
        filename=os.path.basename(path),
        original_filepath=path,
        media_type=media_type,
        captured_at=captured,
        latitude=float(lat) if lat not in (None, "") else None,
        longitude=float(lon) if lon not in (None, "") else None,
        camera_make=(sidecar or {}).get("googlePhotosOrigin", {})
        .get("mobileUpload", {})
        .get("deviceType"),
        camera_model=(sidecar or {}).get("googlePhotosOrigin", {})
        .get("mobileUpload", {})
        .get("deviceFolder", {})
        .get("localFolderName"),
        raw_metadata=sidecar or {},
    )


def parse_takeout_zip(path: str) -> list[PhotoRecord]:
    records: list[PhotoRecord] = []
    with TemporaryDirectory(prefix="milesphotos-") as tmp:
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(tmp)

        for root, _, files in os.walk(tmp):
            for name in files:
                full = os.path.join(root, name)
                if name.lower().endswith(".json"):
                    continue
                if name.startswith("."):
                    continue
                sidecar = None
                for candidate in _sidecar_candidates(full):
                    if os.path.exists(candidate):
                        sidecar = _read_json(candidate)
                        break
                records.append(_extract_photo(full, sidecar))

    return records


def save_photo_records(import_id: int, records: list[PhotoRecord]) -> int:
    if not records:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for rec in records:
                cur.execute(
                    """
                    INSERT INTO photos (
                        import_id, source_photo_id, filename, original_filepath, storage_path,
                        media_type, captured_at, latitude, longitude, camera_make, camera_model,
                        visibility_status, raw_metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'review', %s)
                    """,
                    (
                        import_id,
                        rec.original_filepath,
                        rec.filename,
                        rec.original_filepath,
                        rec.original_filepath,
                        rec.media_type,
                        rec.captured_at,
                        rec.latitude,
                        rec.longitude,
                        rec.camera_make,
                        rec.camera_model,
                        json.dumps(rec.raw_metadata),
                    ),
                )
    return len(records)
