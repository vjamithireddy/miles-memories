from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ingestion.garmin_parser import ActivityRecord, _canonical_activity_type, save_activity


class GarminParserTests(unittest.TestCase):
    def test_canonical_activity_type_uses_string_activity_type(self) -> None:
        self.assertEqual(
            _canonical_activity_type("running", activity_name="O'Fallon Running"),
            "running",
        )

    def test_canonical_activity_type_maps_cycling_variants(self) -> None:
        self.assertEqual(
            _canonical_activity_type("road_biking", activity_name="Saturday Ride"),
            "cycling",
        )

    def test_canonical_activity_type_maps_floor_climbing(self) -> None:
        self.assertEqual(
            _canonical_activity_type("floor_climbing", activity_name="Floor Climb"),
            "climbing",
        )

    def test_canonical_activity_type_falls_back_to_activity_name(self) -> None:
        self.assertEqual(
            _canonical_activity_type(None, activity_name="Columbia Walking"),
            "walking",
        )

    def test_save_activity_links_trip_using_same_cursor(self) -> None:
        executed: list[str] = []

        class FakeCursor:
            def execute(self, query, params=None):
                executed.append(" ".join(query.split()))

            def fetchone(self):
                if "RETURNING id, (xmax = 0) AS inserted" in executed[-1]:
                    return (42, True)
                if "SELECT id, LEAST(end_time," in executed[-1]:
                    return (9, "01:00:00")
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeConn:
            def cursor(self):
                return FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        record = ActivityRecord(
            source="garmin",
            source_activity_id="abc",
            activity_type="running",
            activity_name="Test Run",
            start_time=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 5, 1, 13, 0, tzinfo=timezone.utc),
            duration_seconds=3600,
            distance_meters=10000.0,
            elevation_gain_meters=None,
            elevation_loss_meters=None,
            moving_time_seconds=None,
            elapsed_time_seconds=None,
            average_speed_mps=None,
            max_speed_mps=None,
            average_heart_rate=None,
            max_heart_rate=None,
            calories=None,
            start_latitude=38.0,
            start_longitude=-90.0,
            end_latitude=38.0,
            end_longitude=-90.0,
            route_polyline=None,
            raw_metadata_json=None,
        )

        with patch("ingestion.garmin_parser.get_conn", return_value=FakeConn()):
            activity_id, inserted, trip_id = save_activity(1, record)

        self.assertEqual((activity_id, inserted, trip_id), (42, True, 9))
        self.assertTrue(any("UPDATE activities SET trip_id = %s WHERE id = %s" in query for query in executed))


if __name__ == "__main__":
    unittest.main()
