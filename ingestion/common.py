import hashlib
import os
from datetime import datetime, timezone


def file_sha256(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def basename(path: str) -> str:
    return os.path.basename(path)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
