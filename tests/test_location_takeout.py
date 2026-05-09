from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ingestion.location_takeout import LocationEvent, save_location_events
from scripts.build_latest_trips_from_timeline import _filter_recent_events


class _FakeCursor:
    def __init__(self, rowcounts: list[int]) -> None:
        self._rowcounts = rowcounts
        self.rowcount = 0
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, query: str, params=None) -> None:
        self.executed.append((query, params))
        self.rowcount = self._rowcounts.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class LocationTakeoutTests(unittest.TestCase):
    def test_filter_recent_events_keeps_only_incremental_window(self) -> None:
        older = LocationEvent(
            timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            latitude=38.0,
            longitude=-90.0,
            accuracy_meters=None,
            source_event_id="older",
            raw_payload={},
        )
        newer = LocationEvent(
            timestamp=datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
            latitude=38.1,
            longitude=-90.1,
            accuracy_meters=None,
            source_event_id="newer",
            raw_payload={},
        )

        filtered = _filter_recent_events([older, newer], datetime(2026, 5, 2, 0, 0, tzinfo=timezone.utc))

        self.assertEqual([event.source_event_id for event in filtered], ["newer"])

    def test_save_location_events_counts_only_new_rows(self) -> None:
        cursor = _FakeCursor([1, 0])
        events = [
            LocationEvent(
                timestamp=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
                latitude=38.0,
                longitude=-90.0,
                accuracy_meters=None,
                source_event_id="a",
                raw_payload={"id": "a"},
            ),
            LocationEvent(
                timestamp=datetime(2026, 5, 8, 12, 5, tzinfo=timezone.utc),
                latitude=38.0,
                longitude=-90.0,
                accuracy_meters=None,
                source_event_id="b",
                raw_payload={"id": "b"},
            ),
        ]

        with patch("ingestion.location_takeout.get_conn", return_value=_FakeConn(cursor)):
            inserted = save_location_events(7, events)

        self.assertEqual(inserted, 1)
        self.assertEqual(len(cursor.executed), 2)
        self.assertIn("WHERE NOT EXISTS", cursor.executed[0][0])


if __name__ == "__main__":
    unittest.main()
