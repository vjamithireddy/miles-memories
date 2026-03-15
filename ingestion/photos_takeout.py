from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.db import get_conn

from ingestion.common import parse_ts

MEDIA_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
}


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
    root = Path(path)
    parent = root.parent
    stem = root.name
    base_stem = root.stem
    candidates = [str(root) + ".json", str(root.with_suffix(".json"))]

    # Google Takeout often truncates long sidecar names, so match any json file
    # that starts with the media filename or the filename stem.
    try:
        for child in parent.iterdir():
            if not child.is_file() or child.suffix.lower() != ".json":
                continue
            name = child.name
            if name == "metadata.json":
                continue
            if name.startswith(stem) or name.startswith(base_stem):
                candidates.append(str(child))
    except OSError:
        pass

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


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


def _extract_sidecar_only(path: str, sidecar: dict[str, Any]) -> PhotoRecord | None:
    title = sidecar.get("title")
    if not isinstance(title, str) or not title or title == os.path.basename(os.path.dirname(path)):
        return None

    synthetic_path = os.path.join(os.path.dirname(path), title)
    return _extract_photo(synthetic_path, sidecar)


def _parse_takeout_tree(root_dir: str) -> list[PhotoRecord]:
    records: list[PhotoRecord] = []
    seen_paths: set[str] = set()
    for root, _, files in os.walk(root_dir):
        for name in files:
            full = os.path.join(root, name)
            if name.lower().endswith(".json"):
                if name == "metadata.json":
                    continue
                sidecar = _read_json(full)
                if sidecar is None:
                    continue
                record = _extract_sidecar_only(full, sidecar)
                if record is None or record.original_filepath in seen_paths:
                    continue
                records.append(record)
                seen_paths.add(record.original_filepath)
                continue
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in MEDIA_EXTS:
                continue
            sidecar = None
            for candidate in _sidecar_candidates(full):
                if os.path.exists(candidate):
                    sidecar = _read_json(candidate)
                    if sidecar is not None:
                        break
            record = _extract_photo(full, sidecar)
            if record.original_filepath in seen_paths:
                continue
            records.append(record)
            seen_paths.add(record.original_filepath)

    return records


def parse_takeout_zip(path: str) -> list[PhotoRecord]:
    with TemporaryDirectory(prefix="milesphotos-") as tmp:
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(tmp)

        return _parse_takeout_tree(tmp)


def parse_takeout_dir(path: str) -> list[PhotoRecord]:
    return _parse_takeout_tree(path)


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
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'review', %s
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM photos
                        WHERE import_id = %s
                          AND source_photo_id = %s
                    )
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
                        import_id,
                        rec.original_filepath,
                    ),
                )
    return len(records)
