from __future__ import annotations

import unittest

from ingestion.garmin_parser import _canonical_activity_type


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

    def test_canonical_activity_type_falls_back_to_activity_name(self) -> None:
        self.assertEqual(
            _canonical_activity_type(None, activity_name="Columbia Walking"),
            "walking",
        )


if __name__ == "__main__":
    unittest.main()
