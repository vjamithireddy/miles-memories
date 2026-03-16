from __future__ import annotations

from datetime import datetime, timezone
import unittest

from app.trip_admin import (
    _apply_duplicate_leg_summary_disambiguation,
    _is_placeholder_segment_summary,
    _leg_default_summary,
    _should_refresh_segment_summary,
)


class TripAdminTests(unittest.TestCase):
    def test_placeholder_segment_summary_flags_low_quality_airport_text(self) -> None:
        self.assertTrue(
            _is_placeholder_segment_summary("Drive near Harry Reid Airport Rental Car Facility.")
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


if __name__ == "__main__":
    unittest.main()
