from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from trip_engine.detector import (
    _apply_destination_override,
    _best_activity_destination,
    _cluster_nonlocal_garmin_activities,
    _generate_trip_name,
    _generate_trip_summary,
    _haversine_km,
    _is_stale_cached_place,
    _resolve_destination_profile,
    _select_locality,
    detect_garmin_trips,
    detect_trips,
)


class FakeCursor:
    def __init__(self) -> None:
        self.inserted_trip_params = []
        self.override_rows = []
        self._last_fetchall = []
        self._last_fetchone = None
        self.place_row = None
        self.insert_place_params = []

    def execute(self, query: str, params=None) -> None:
        compact = " ".join(query.split())
        if "INSERT INTO trips" in compact:
            self.inserted_trip_params.append(params)
            self._last_fetchone = (1,)
        elif "SELECT MAX(end_time) FROM trips" in compact:
            self._last_fetchone = None
        elif "SELECT place_name, place_type, source, city FROM places" in compact:
            self._last_fetchone = self.place_row
        elif "INSERT INTO places" in compact:
            self.insert_place_params.append(params)
            self._last_fetchone = None
        elif "FROM destination_overrides" in compact:
            self._last_fetchall = self.override_rows
            self._last_fetchone = None
        elif "SELECT id, event_timestamp FROM location_events" in compact:
            self._last_fetchall = [(10, datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc))]
            self._last_fetchone = None
        else:
            self._last_fetchall = []
            self._last_fetchone = None

    def fetchone(self):
        row = self._last_fetchone
        self._last_fetchone = None
        return row

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

    def test_best_activity_destination_prefers_national_park_over_county(self) -> None:
        destination, title_destination, cleaned_names = _best_activity_destination(
            [
                "Storm king trail - Olympic National Parks",
                "Sol duc falls trail",
                "Rialto beach - Hole in the wall trail",
            ],
            "Clallam County",
        )

        self.assertEqual(destination, "Olympic - NPS")
        self.assertEqual(title_destination, "Olympic - NPS")
        self.assertIn("Sol duc falls trail", cleaned_names)

    def test_best_activity_destination_prefers_city_over_generic_county(self) -> None:
        destination, title_destination, _ = _best_activity_destination(
            [
                "Tampa Walking",
                "Hillsborough County Walking",
            ],
            "Hillsborough County",
        )

        self.assertEqual(destination, "Tampa")
        self.assertEqual(title_destination, "Tampa")

    def test_generate_garmin_trip_summary_uses_destination_and_highlights(self) -> None:
        summary = _generate_trip_summary(
            source="garmin",
            destination="Olympic - NPS",
            trip_type="day_trip",
            activity_names=[
                "Storm king trail - Olympic National Parks",
                "Sol duc falls trail",
                "Rialto beach - Hole in the wall trail",
            ],
        )

        self.assertEqual(
            summary,
            "Detected from non-local Garmin activities around Olympic - NPS. Highlights: Storm king trail - Olympic National Parks, Sol duc falls trail, Rialto beach - Hole in the wall trail.",
        )

    def test_generate_timeline_trip_summary_uses_destination(self) -> None:
        summary = _generate_trip_summary(
            source="timeline",
            destination="Minneapolis",
            trip_type="overnight_trip",
        )

        self.assertEqual(summary, "Detected from Google Timeline travel around Minneapolis.")

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

    def test_cluster_nonlocal_garmin_activities_skips_local_and_groups_remote(self) -> None:
        activities = [
            {
                "id": 1,
                "activity_name": "Local Run",
                "start_time": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc),
                "start_latitude": 38.7505,
                "start_longitude": -90.6877,
                "end_latitude": 38.7505,
                "end_longitude": -90.6877,
            },
            {
                "id": 2,
                "activity_name": "Remote Hike 1",
                "start_time": datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 10, 18, 0, tzinfo=timezone.utc),
                "start_latitude": 36.107,
                "start_longitude": -112.113,
                "end_latitude": 36.107,
                "end_longitude": -112.113,
            },
            {
                "id": 3,
                "activity_name": "Remote Hike 2",
                "start_time": datetime(2026, 1, 11, 15, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 11, 17, 0, tzinfo=timezone.utc),
                "start_latitude": 36.12,
                "start_longitude": -112.10,
                "end_latitude": 36.12,
                "end_longitude": -112.10,
            },
        ]

        clusters = _cluster_nonlocal_garmin_activities(
            activities,
            home_lat=38.7504884,
            home_lon=-90.6877536,
            local_cutoff_km=80.0,
            cluster_gap_hours=36,
        )

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].activity_ids, [2, 3])

    def test_detect_garmin_trips_creates_trip_for_remote_unattached_activities(self) -> None:
        cursor = FakeCursor()
        cursor.fetchone_results = [None, (1,)]

        with patch("trip_engine.detector.ensure_default_user", return_value=1), \
             patch("trip_engine.detector.get_home_profile", return_value=(38.7504884, -90.6877536, 16093)), \
             patch("trip_engine.detector.get_user_timezone", return_value="America/Chicago"), \
             patch(
                 "trip_engine.detector._fetch_unattached_garmin_activities",
                 return_value=[
                     {
                         "id": 21,
                         "activity_name": "Grand Canyon Hike",
                         "activity_type": "hiking",
                         "start_time": datetime(2026, 3, 1, 13, 0, tzinfo=timezone.utc),
                         "end_time": datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc),
                         "start_latitude": 36.057,
                         "start_longitude": -112.143,
                         "end_latitude": 36.057,
                         "end_longitude": -112.143,
                     }
                 ],
             ), \
             patch(
                 "trip_engine.detector._resolve_destination_profile",
                 return_value={"name": "Grand Canyon", "locality": "Tusayan", "classification": None},
             ), \
             patch("trip_engine.detector.get_conn", return_value=FakeConn(cursor)):
            created, linked = detect_garmin_trips()

        self.assertEqual((created, linked), (1, 1))
        self.assertEqual(cursor.inserted_trip_params[0][2], "garmin-detected-2026-03-01-1")


if __name__ == "__main__":
    unittest.main()
