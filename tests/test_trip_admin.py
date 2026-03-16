from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app.trip_admin import (
    _apply_duplicate_leg_summary_disambiguation,
    _best_nearby_place_name,
    _build_travel_legs,
    _is_generic_regional_segment_summary,
    _is_placeholder_segment_summary,
    _leg_default_summary,
    _place_candidate_score,
    _prefer_locality_over_region,
    _should_refresh_segment_summary,
)


class TripAdminTests(unittest.TestCase):
    def test_placeholder_segment_summary_flags_low_quality_airport_text(self) -> None:
        self.assertTrue(
            _is_placeholder_segment_summary("Drive near Harry Reid Airport Rental Car Facility.")
        )

    def test_generic_regional_segment_summary_is_flagged(self) -> None:
        self.assertTrue(_is_generic_regional_segment_summary("Drive in Flathead County."))
        self.assertTrue(_is_generic_regional_segment_summary("Monday Morning drive in Flathead County (2)"))
        self.assertTrue(_is_generic_regional_segment_summary("Sunday Morning walk in Flathead County (3)"))

    def test_prefer_locality_over_region_uses_city_when_name_is_county(self) -> None:
        self.assertEqual(
            _prefer_locality_over_region("Flathead County", "Whitefish"),
            "Whitefish",
        )

    def test_prefer_locality_over_region_keeps_specific_place(self) -> None:
        self.assertEqual(
            _prefer_locality_over_region("Washburn Lodge", "Yellowstone National Park"),
            "Washburn Lodge",
        )

    def test_place_candidate_score_prefers_specific_name_over_county(self) -> None:
        self.assertGreater(
            _place_candidate_score("Whitefish", None),
            _place_candidate_score("Flathead County", None),
        )

    def test_place_candidate_score_prefers_locality_over_regional_fallback(self) -> None:
        self.assertGreater(
            _place_candidate_score("Flathead County", "Whitefish"),
            _place_candidate_score("Flathead County", "Flathead County"),
        )

    def test_best_nearby_place_name_prefers_specific_locality_over_county(self) -> None:
        rows = [
            {
                "id": 1,
                "place_name": "Flathead County",
                "city": "Flathead County",
                "latitude": 48.1,
                "longitude": -113.9,
            },
            {
                "id": 2,
                "place_name": "Whitefish",
                "city": "Whitefish",
                "latitude": 48.12,
                "longitude": -114.03,
            },
        ]

        self.assertEqual(
            _best_nearby_place_name(rows, latitude=48.11, longitude=-114.02),
            "Whitefish",
        )

    def test_refresh_segment_summary_flags_legacy_trip_context_flight_text(self) -> None:
        self.assertTrue(
            _should_refresh_segment_summary(
                "Flight from Grand Canyon - NPS to Harry Reid Airport.",
                leg={"leg_type": "air"},
                trip_name="Grand Canyon - NPS",
                destination_name="Harry Reid Airport Rental Car Facility",
            )
        )

    def test_refresh_segment_summary_flags_generic_regional_drive_text(self) -> None:
        self.assertTrue(
            _should_refresh_segment_summary(
                "Monday Morning drive in Flathead County (2)",
                leg={"leg_type": "car"},
                trip_name="Road Trip - Summer 2025",
                destination_name="Flathead County",
            )
        )

    def test_leg_default_summary_cleans_rental_car_facility_for_flight(self) -> None:
        summary = _leg_default_summary(
            {"label": "Air travel", "leg_type": "air"},
            trip_name="Grand Canyon - NPS",
            destination_name="Harry Reid Airport Rental Car Facility",
            origin_name="St. Louis",
        )

        self.assertEqual(summary, "Flight from St. Louis to Harry Reid Airport.")

    def test_leg_default_summary_does_not_use_trip_context_as_flight_origin(self) -> None:
        summary = _leg_default_summary(
            {"label": "Air travel", "leg_type": "air"},
            trip_name="Grand Canyon - NPS",
            destination_name="Harry Reid Airport Rental Car Facility",
        )

        self.assertEqual(summary, "Flight to Harry Reid Airport.")

    def test_leg_default_summary_prefers_trip_context_for_walks(self) -> None:
        summary = _leg_default_summary(
            {"label": "Walking", "leg_type": "walk"},
            trip_name="Grand Canyon - NPS",
            destination_name="Harry Reid Airport Rental Car Facility",
        )

        self.assertEqual(summary, "Walk in Grand Canyon - NPS.")

    def test_leg_default_summary_uses_cleaner_context_for_car_segments(self) -> None:
        summary = _leg_default_summary(
            {"label": "Car travel", "leg_type": "car"},
            trip_name="Grand Canyon - NPS",
            destination_name="Harry Reid Airport Rental Car Facility",
        )

        self.assertEqual(summary, "Drive in Grand Canyon - NPS.")

    def test_leg_default_summary_uses_trailhead_when_car_leads_into_hike(self) -> None:
        summary = _leg_default_summary(
            {
                "label": "Car travel",
                "leg_type": "car",
                "end_place_name": "Bright Angel Trailhead",
            },
            trip_name="Grand Canyon - NPS",
            next_leg_type="hike",
        )

        self.assertEqual(summary, "Drive to Bright Angel Trailhead.")

    def test_leg_default_summary_prefers_specific_lodging_stop(self) -> None:
        summary = _leg_default_summary(
            {
                "label": "Car travel",
                "leg_type": "car",
                "end_place_name": "Yavapai Lodge",
            },
            trip_name="Grand Canyon - NPS",
        )

        self.assertEqual(summary, "Drive to Yavapai Lodge.")

    def test_leg_default_summary_prefers_specific_viewpoint_stop(self) -> None:
        summary = _leg_default_summary(
            {
                "label": "Car travel",
                "leg_type": "car",
                "end_place_name": "Mather Point Overlook",
            },
            trip_name="Grand Canyon - NPS",
        )

        self.assertEqual(summary, "Drive to Mather Point Overlook.")

    def test_leg_default_summary_prefers_endpoint_places_for_long_drive(self) -> None:
        summary = _leg_default_summary(
            {
                "label": "Car travel",
                "leg_type": "car",
                "start_place_name": "West Glacier",
                "end_place_name": "Whitefish Village",
                "start_time": datetime(2025, 7, 28, 13, 0, tzinfo=timezone.utc),
                "end_time": datetime(2025, 7, 28, 16, 0, tzinfo=timezone.utc),
            },
            trip_name="Montana Road Trip",
            destination_name="Flathead County",
        )

        self.assertEqual(summary, "Drive from West Glacier to Whitefish Village.")

    def test_leg_default_summary_uses_neutral_text_for_long_regional_drive(self) -> None:
        summary = _leg_default_summary(
            {
                "label": "Car travel",
                "leg_type": "car",
                "start_place_name": "Flathead County",
                "end_place_name": "Flathead County",
                "start_time": datetime(2025, 7, 28, 13, 0, tzinfo=timezone.utc),
                "end_time": datetime(2025, 7, 28, 16, 0, tzinfo=timezone.utc),
            },
            trip_name="Montana Road Trip",
            destination_name="Flathead County",
        )

        self.assertEqual(summary, "Road trip drive.")

    def test_duplicate_leg_summaries_are_disambiguated(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.calls = []

            def execute(self, sql: str, params: tuple[object, ...]) -> None:
                self.calls.append((sql, params))

        legs = [
            {
                "segment_id": 10,
                "leg_type": "car",
                "start_time": datetime(2025, 7, 28, 13, 0, tzinfo=timezone.utc),
                "segment_summary": "Drive in Flathead County.",
                "segment_summary_auto": True,
            },
            {
                "segment_id": 11,
                "leg_type": "car",
                "start_time": datetime(2025, 7, 28, 20, 0, tzinfo=timezone.utc),
                "segment_summary": "Drive in Flathead County.",
                "segment_summary_auto": True,
            },
        ]
        cur = FakeCursor()

        _apply_duplicate_leg_summary_disambiguation(cur, legs)

        self.assertNotEqual(legs[0]["segment_summary"], legs[1]["segment_summary"])
        self.assertIn("drive in flathead county", legs[0]["segment_summary"].lower())
        self.assertIn("drive in flathead county", legs[1]["segment_summary"].lower())
        self.assertEqual(len(cur.calls), 2)

    def test_build_travel_legs_populates_place_names_from_leg_points(self) -> None:
        row = {
            "event_time": datetime(2025, 7, 28, 13, 0, tzinfo=timezone.utc),
            "latitude": 48.0,
            "longitude": -114.0,
            "source_event_id": "IN_PASSENGER_VEHICLE",
            "raw_payload_json": {
                "semanticSegmentIndex": 3,
                "activity": {
                    "topCandidate": {"type": "IN_PASSENGER_VEHICLE"},
                    "start": {"latLng": "48.1000°, -114.1000°"},
                    "end": {"latLng": "48.2000°, -114.2000°"},
                    "startTime": "2025-07-28T13:00:00Z",
                    "endTime": "2025-07-28T15:00:00Z",
                },
            },
        }

        with patch(
            "app.trip_admin._leg_point_place_name",
            side_effect=["Whitefish Village", "Avalanche Trailhead"],
        ):
            legs = _build_travel_legs([row])

        self.assertEqual(legs[0]["start_place_name"], "Whitefish Village")
        self.assertEqual(legs[0]["end_place_name"], "Avalanche Trailhead")


if __name__ == "__main__":
    unittest.main()
