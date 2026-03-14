from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import get_admin_trip, homepage, list_admin_trips, review_trip, update_publish_ready
from app.schemas import PublishReadyRequest, TripReviewRequest


def _trip_summary() -> dict:
    return {
        "id": 7,
        "trip_name": "Colorado Weekend",
        "trip_slug": "colorado-weekend",
        "trip_type": "overnight_trip",
        "status": "needs_review",
        "review_decision": "pending",
        "start_time": datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        "end_time": datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc),
        "start_date": date(2026, 3, 1),
        "end_date": date(2026, 3, 2),
        "primary_destination_name": "Denver",
        "origin_place_name": "St. Louis",
        "confidence_score": 86,
        "summary_text": "Late winter trip.",
        "is_private": True,
        "publish_ready": False,
        "published_at": None,
        "updated_at": datetime(2026, 3, 3, 10, 0, tzinfo=timezone.utc),
    }


def _trip_detail() -> dict:
    trip = _trip_summary()
    trip["event_counts"] = [{"event_type": "location_event", "total": 12}]
    trip["timeline"] = [
        {
            "event_type": "location_event",
            "event_ref_id": 101,
            "event_time": datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc),
            "sort_order": 1,
            "day_index": 0,
            "timeline_label": "Left home",
        }
    ]
    trip["review_history"] = [
        {
            "reviewer_name": "Venkat",
            "review_action": "confirm",
            "review_notes": "Looks right.",
            "reviewed_at": datetime(2026, 3, 3, 11, 0, tzinfo=timezone.utc),
        }
    ]
    return trip


class AppApiTests(unittest.TestCase):
    def test_homepage_returns_html(self) -> None:
        response = homepage()

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.media_type)
        self.assertIn(b"MilesMemories", response.body)
        self.assertIn(b"/admin/trips", response.body)

    def test_list_trips_passes_filters(self) -> None:
        with patch("app.main.trip_admin.list_trips", return_value=[_trip_summary()]) as mock_list:
            response = list_admin_trips(
                status="needs_review",
                review_decision="pending",
                include_private=False,
                limit=25,
            )

        self.assertEqual(response[0]["trip_name"], "Colorado Weekend")
        mock_list.assert_called_once_with(
            status="needs_review",
            review_decision="pending",
            include_private=False,
            limit=25,
        )

    def test_get_trip_returns_404_when_missing(self) -> None:
        with patch("app.main.trip_admin.get_trip", return_value=None):
            with self.assertRaises(HTTPException) as exc:
                get_admin_trip(999)

        self.assertEqual(exc.exception.status_code, 404)
        self.assertEqual(exc.exception.detail, "Trip not found")

    def test_review_trip_returns_updated_detail(self) -> None:
        with patch("app.main.trip_admin.record_review", return_value=_trip_detail()) as mock_review:
            response = review_trip(
                7,
                TripReviewRequest(
                    action="confirm",
                    reviewer_name="Venkat",
                    review_notes="Looks right.",
                    trip_name="Colorado Weekend",
                    summary_text="Late winter trip.",
                    primary_destination_name="Denver",
                    is_private=False,
                    publish_ready=True,
                ),
            )

        self.assertEqual(response["review_history"][0]["review_action"], "confirm")
        mock_review.assert_called_once_with(
            7,
            action="confirm",
            reviewer_name="Venkat",
            review_notes="Looks right.",
            trip_name="Colorado Weekend",
            summary_text="Late winter trip.",
            primary_destination_name="Denver",
            is_private=False,
            publish_ready=True,
        )

    def test_publish_ready_patch_uses_repository(self) -> None:
        updated_trip = _trip_detail()
        updated_trip["publish_ready"] = True
        with patch("app.main.trip_admin.set_publish_ready", return_value=updated_trip) as mock_publish:
            response = update_publish_ready(7, PublishReadyRequest(publish_ready=True))

        self.assertIs(response["publish_ready"], True)
        mock_publish.assert_called_once_with(7, publish_ready=True)


if __name__ == "__main__":
    unittest.main()
