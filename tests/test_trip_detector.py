from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from trip_engine.detector import _apply_destination_override, _haversine_km, detect_trips


class FakeCursor:
    def __init__(self) -> None:
        self.fetchone_results = [(1,), None]
        self.inserted_trip_params = []
        self.override_rows = []
        self._last_fetchall = []

    def execute(self, query: str, params=None) -> None:
        compact = " ".join(query.split())
        if "INSERT INTO trips" in compact:
            self.inserted_trip_params.append(params)
        elif "FROM destination_overrides" in compact:
            self._last_fetchall = self.override_rows
        elif "SELECT id, event_timestamp FROM location_events" in compact:
            self._last_fetchall = [(10, datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc))]
        else:
            self._last_fetchall = []

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None

    def fetchall(self):
        return self._last_fetchall

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DetectorTests(unittest.TestCase):
    def test_haversine_is_reasonable_for_home_to_work(self) -> None:
        distance = _haversine_km(38.7504884, -90.6877536, 38.757, -90.465)
        self.assertGreater(distance, 15)

    def test_destination_override_matches_pattern(self) -> None:
        cursor = FakeCursor()
        cursor.override_rows = [
            ("rec plex", None, None, 1000, "amateur_sports_venue", False, True),
        ]

        with patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            profile = _apply_destination_override(
                38.8,
                -90.4,
                {
                    "name": "St Peters Rec Plex",
                    "category": "sports_centre",
                    "display_name": "St Peters Rec Plex, Missouri",
                    "classification": None,
                },
            )

        self.assertEqual(profile["classification"], "amateur_sports_venue")
        self.assertIs(profile["keep_trip"], False)
        self.assertIs(profile["ignore_trip"], True)

    def test_destination_override_can_keep_trip(self) -> None:
        cursor = FakeCursor()
        cursor.override_rows = [
            ("enterprise center", None, None, 1000, "pro_sports_venue", True, False),
        ]

        with patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            profile = _apply_destination_override(
                38.6268,
                -90.2026,
                {
                    "name": "Enterprise Center",
                    "category": "arena",
                    "display_name": "Enterprise Center, St. Louis, Missouri",
                    "classification": "amateur_sports_venue",
                },
            )

        self.assertEqual(profile["classification"], "pro_sports_venue")
        self.assertIs(profile["keep_trip"], True)
        self.assertIs(profile["ignore_trip"], False)

    def test_detect_trips_skips_commute_like_work_trip(self) -> None:
        cursor = FakeCursor()

        with patch("trip_engine.detector.ensure_default_user", return_value=1), \
             patch(
                 "trip_engine.detector.get_home_profile",
                 return_value=(38.7504884, -90.6877536, 16093),
             ), \
             patch(
                 "trip_engine.detector.get_work_profile",
                 return_value=(38.768479, -90.46854, 1609),
             ), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc), 38.80, -90.55),
                     (2, datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc), 38.768479, -90.46854),
                     (3, datetime(2026, 1, 1, 17, 30, tzinfo=timezone.utc), 38.75, -90.68),
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Office", "classification": None},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_trips()

        self.assertEqual((created, linked), (0, 0))
        self.assertEqual(cursor.inserted_trip_params, [])

    def test_detect_trips_marks_cross_midnight_trip_as_overnight(self) -> None:
        cursor = FakeCursor()

        with patch("trip_engine.detector.ensure_default_user", return_value=1), \
             patch(
                 "trip_engine.detector.get_home_profile",
                 return_value=(38.7504884, -90.6877536, 16093),
             ), \
             patch(
                 "trip_engine.detector.get_work_profile",
                 return_value=(None, None, 1609),
             ), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc), 39.5, -91.2),
                     (2, datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc), 39.5, -91.2),
                     (3, datetime(2026, 1, 2, 8, 30, tzinfo=timezone.utc), 38.75, -90.68),
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Lake of the Ozarks", "classification": None},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_trips()

        self.assertEqual(created, 1)
        self.assertEqual(linked, 1)
        self.assertEqual(cursor.inserted_trip_params[0][3], "overnight_trip")

    def test_detect_trips_ignores_amateur_sports_venue(self) -> None:
        cursor = FakeCursor()

        with patch("trip_engine.detector.ensure_default_user", return_value=1), \
             patch(
                 "trip_engine.detector.get_home_profile",
                 return_value=(38.7504884, -90.6877536, 16093),
             ), \
             patch(
                 "trip_engine.detector.get_work_profile",
                 return_value=(None, None, 1609),
             ), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc), 38.84, -90.42),
                     (2, datetime(2026, 1, 1, 17, 45, tzinfo=timezone.utc), 38.84, -90.42),
                     (3, datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc), 38.75, -90.68),
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Rec Plex South", "classification": "amateur_sports_venue"},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_trips()

        self.assertEqual((created, linked), (0, 0))
        self.assertEqual(cursor.inserted_trip_params, [])

    def test_detect_trips_keeps_professional_venue(self) -> None:
        cursor = FakeCursor()

        with patch("trip_engine.detector.ensure_default_user", return_value=1), \
             patch(
                 "trip_engine.detector.get_home_profile",
                 return_value=(38.7504884, -90.6877536, 16093),
             ), \
             patch(
                 "trip_engine.detector.get_work_profile",
                 return_value=(None, None, 1609),
             ), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc), 38.6226, -90.1928),
                     (2, datetime(2026, 1, 1, 17, 45, tzinfo=timezone.utc), 38.6226, -90.1928),
                     (3, datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc), 38.75, -90.68),
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Busch Stadium", "classification": "pro_sports_venue"},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_trips()

        self.assertEqual(created, 1)
        self.assertEqual(linked, 1)
        self.assertEqual(cursor.inserted_trip_params[0][8], "Busch Stadium")


if __name__ == "__main__":
    unittest.main()
