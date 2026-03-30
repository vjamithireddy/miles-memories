from __future__ import annotations

import argparse
import json
from pathlib import Path

from psycopg import sql

from app.db import get_conn


def load_parks(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("Expected JSON list of parks")
    return data


def seed_parks(parks: list[dict]) -> int:
    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for park in parks:
                cur.execute(
                    """
                    INSERT INTO national_parks (
                        park_code, name, state, city, lat, lon, visited, planned
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (park_code) DO UPDATE SET
                        name = EXCLUDED.name,
                        state = EXCLUDED.state,
                        city = EXCLUDED.city,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        updated_at = NOW()
                    """,
                    (
                        park["park_code"],
                        park["name"],
                        park.get("state"),
                        park.get("city"),
                        park["lat"],
                        park["lon"],
                        bool(park.get("visited", False)),
                        bool(park.get("planned", False)),
                    ),
                )
                count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed National Parks data.")
    parser.add_argument(
        "--file",
        default="data/nps_parks.json",
        help="Path to the parks JSON data file.",
    )
    args = parser.parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Missing data file: {path}")
    parks = load_parks(path)
    count = seed_parks(parks)
    print(f"Seeded {count} parks.")


if __name__ == "__main__":
    main()
