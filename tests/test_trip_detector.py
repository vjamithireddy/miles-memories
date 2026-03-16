from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from trip_engine.detector import (
    _apply_destination_override,
    _generate_trip_name,
    _haversine_km,
    _is_stale_cached_place,
    _resolve_destination_profile,
    _select_locality,
    detect_trips,
)


class FakeCursor:
    def __init__(self) -> None:
        self.fetchone_results = [(1,), None]
        self.inserted_trip_params = []
        self.override_rows = []
        self._last_fetchall = []
        self.place_row = None
        self.insert_place_params = []

    def execute(self, query: str, params=None) -> None:
        compact = " ".join(query.split())
        if "INSERT INTO trips" in compact:
            self.inserted_trip_params.append(params)
        elif "SELECT place_name, place_type, source, city FROM places" in compact:
            self.fetchone_results.insert(0, self.place_row)
        elif "INSERT INTO places" in compact:
            self.insert_place_params.append(params)
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
    def test_stale_cached_place_flags_county_only_cache(self) -> None:
        self.assertTrue(_is_stale_cached_place("Flathead County", "boundary", "Flathead County"))

    def test_haversine_is_reasonable_for_home_to_work(self) -> None:
        distance = _haversine_km(38.7504884, -90.6877536, 38.757, -90.465)
        self.assertGreater(distance, 15)

    def test_generate_trip_name_uses_locality_when_name_is_address_like(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "13736 Riverport Drive",
                "locality": "Maryland Heights",
                "category": "house",
                "classification": None,
            },
            "day_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Maryland Heights Day Trip")

    def test_generate_trip_name_prefers_pro_venue(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "Busch Stadium",
                "locality": "St. Louis",
                "category": "stadium",
                "classification": "pro_sports_venue",
            },
            "day_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Busch Stadium Day Trip")

    def test_generate_trip_name_downranks_county_in_favor_of_locality(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "Saint Louis County",
                "locality": "Saint Louis",
                "category": "administrative",
                "classification": None,
            },
            "day_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Saint Louis Day Trip")

    def test_generate_trip_name_downranks_road_in_favor_of_locality(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "Olive Boulevard",
                "locality": "Chesterfield",
                "category": "road",
                "classification": None,
            },
            "overnight_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Chesterfield Weekend")

    def test_generate_trip_name_downranks_lot_in_favor_of_locality(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "Ted Drewes West Lot",
                "locality": "Saint Louis",
                "category": "parking",
                "classification": None,
            },
            "day_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Saint Louis Day Trip")

    def test_select_locality_prefers_city_like_fields_over_county(self) -> None:
        locality = _select_locality(
            {
                "suburb": "Chesterfield",
                "county": "Saint Louis County",
                "state": "Missouri",
            }
        )

        self.assertEqual(locality, "Chesterfield")

    def test_generate_trip_name_ignores_unknown_destination_placeholder(self) -> None:
        trip_name = _generate_trip_name(
            {
                "name": "Unknown destination",
                "locality": None,
                "category": None,
                "classification": None,
            },
            "day_trip",
            datetime(2026, 3, 7, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 7, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(trip_name, "Trip on 2026-03-07")

    def test_resolve_destination_profile_refetches_stale_unknown_cache(self) -> None:
        cursor = FakeCursor()
        cursor.place_row = ("Unknown destination", "house", "nominatim", None)

        with patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)), \
             patch(
                 "trip_engine.detector._fetch_destination_profile",
                 return_value={
                     "name": "13736 Riverport Drive",
                     "category": "house",
                     "display_name": "13736 Riverport Drive, Maryland Heights, Missouri",
                     "locality": "Maryland Heights",
                 },
             ):
            profile = _resolve_destination_profile(38.7548, -90.4668)

        self.assertEqual(profile["locality"], "Maryland Heights")
        self.assertIsNone(profile["name"])
        self.assertEqual(cursor.insert_place_params[0][0], "Maryland Heights")

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
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
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
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc), 39.5, -91.2),
                     (2, datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc), 39.5, -91.2),
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

    def test_detect_trips_uses_local_timezone_for_same_day_return(self) -> None:
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
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
             patch(
                 "trip_engine.detector._fetch_location_events",
                 return_value=[
                     (1, datetime(2026, 3, 7, 15, 10, tzinfo=timezone.utc), 38.8104, -90.8693),
                     (2, datetime(2026, 3, 8, 1, 48, tzinfo=timezone.utc), 38.8099, -90.8691),
                     (3, datetime(2026, 3, 8, 3, 0, tzinfo=timezone.utc), 38.7504, -90.6878),
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Columbia", "classification": None},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_trips()

        self.assertEqual(created, 1)
        self.assertEqual(linked, 1)
        self.assertEqual(cursor.inserted_trip_params[0][3], "day_trip")
        self.assertEqual(cursor.inserted_trip_params[0][6].isoformat(), "2026-03-07")
        self.assertEqual(cursor.inserted_trip_params[0][7].isoformat(), "2026-03-07")

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
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
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
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
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
