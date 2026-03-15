from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import (
    admin_homepage,
    admin_overrides_page,
    admin_trip_destination_page,
    admin_trip_detail_page,
    create_destination_override,
    delete_destination_override,
    get_admin_trip,
    homepage,
    list_admin_trips,
    review_trip_from_form,
    review_trip,
    update_trip_segment_from_form,
    update_publish_ready,
)
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
            "latitude": 38.6270,
            "longitude": -90.1994,
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
    trip["travel_legs"] = [
        {
            "leg_type": "hike",
            "label": "Hiking",
            "start_time": datetime(2026, 3, 1, 13, 0, tzinfo=timezone.utc),
            "end_time": datetime(2026, 3, 1, 16, 15, tzinfo=timezone.utc),
            "start_latitude": 36.1069,
            "start_longitude": -112.1129,
            "end_latitude": 36.0570,
            "end_longitude": -112.1438,
            "source_event_id": "HIKING",
            "segment_id": 40,
            "segment_name": "South Kaibab hike",
            "segment_summary": "Inner canyon hiking: South Kaibab -> Phantom Ranch -> Tonto -> Bright Angel.",
            "segment_rating": 5,
            "path_points": [
                {"lat": 36.1069, "lon": -112.1129},
                {"lat": 36.0930, "lon": -112.1180},
                {"lat": 36.0815, "lon": -112.1275},
                {"lat": 36.0690, "lon": -112.1362},
                {"lat": 36.0570, "lon": -112.1438},
            ],
        },
        {
            "leg_type": "air",
            "label": "Air travel",
            "start_time": datetime(2026, 3, 1, 8, 30, tzinfo=timezone.utc),
            "end_time": datetime(2026, 3, 1, 11, 45, tzinfo=timezone.utc),
            "start_latitude": 38.7416,
            "start_longitude": -90.3619,
            "end_latitude": 36.0866,
            "end_longitude": -115.1385,
            "source_event_id": "FLYING",
            "segment_id": 41,
            "segment_name": "Flight to Las Vegas",
            "segment_summary": "Flight from St. Louis to Las Vegas.",
            "segment_rating": 4,
            "path_points": [
                {"lat": 38.7416, "lon": -90.3619},
                {"lat": 37.1000, "lon": -96.2000},
                {"lat": 36.0866, "lon": -115.1385},
            ],
        }
    ]
    return trip


class AppApiTests(unittest.TestCase):
    def test_admin_overrides_page_renders_rules(self) -> None:
        with patch(
            "app.main.destination_overrides.list_overrides",
            return_value=[
                {
                    "id": 5,
                    "rule_name": "Enterprise Center keep",
                    "match_pattern": "enterprise center",
                    "latitude": None,
                    "longitude": None,
                    "radius_meters": 1000,
                    "classification": "pro_sports_venue",
                    "keep_trip": True,
                    "ignore_trip": False,
                    "created_at": None,
                    "updated_at": None,
                }
            ],
        ):
            response = admin_overrides_page(return_to="/admin/trip/7")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Destination Overrides", response.body)
        self.assertIn(b"Enterprise Center keep", response.body)
        self.assertIn(b'href="/admin/trip/7"', response.body)

    def test_homepage_returns_html(self) -> None:
        response = homepage()

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.media_type)
        self.assertIn(b"MilesMemories", response.body)
        self.assertIn(b"/admin/trips", response.body)

    def test_admin_homepage_renders_trips(self) -> None:
        with patch("app.main.trip_admin.list_trips", return_value=[_trip_summary()]) as mock_list:
            response = admin_homepage(
                status="needs_review",
                review_decision="pending",
                include_private=True,
                limit=24,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Trip review queue", response.body)
        self.assertIn(b"Colorado Weekend", response.body)
        self.assertIn(b"Raw JSON Feed", response.body)
        self.assertIn(b'class="button" href="/admin/trips?', response.body)
        self.assertIn(b"Open detail page", response.body)
        self.assertIn(b'class="utility-link"', response.body)
        mock_list.assert_called_once_with(
            status="needs_review",
            review_decision="pending",
            include_private=True,
            limit=24,
        )

    def test_admin_trip_detail_renders_map(self) -> None:
        with patch("app.main.trip_admin.get_trip", return_value=_trip_detail()) as mock_get, \
             patch("app.main.destination_overrides.list_overrides", return_value=[]), \
             patch("app.main.trip_admin.get_trip_neighbors", return_value={"previous": None, "next": None}), \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = admin_trip_detail_page(7, saved="review")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Trip map", response.body)
        self.assertIn(b"Travel legs", response.body)
        self.assertIn(b"Hiking", response.body)
        self.assertIn(b"class=\"leg-map\"", response.body)
        self.assertIn(b"data-path=", response.body)
        self.assertIn(b"South Kaibab hike", response.body)
        self.assertIn(b"Inner canyon hiking", response.body)
        self.assertIn(b"class=\"leg-comment-preview\"", response.body)
        self.assertIn(b"Save leg", response.body)
        self.assertIn(b"Edit summary", response.body)
        self.assertIn(b"id=\"trip-map\"", response.body)
        self.assertIn(b"38.62700, -90.19940", response.body)
        self.assertIn(b"Review trip", response.body)
        self.assertIn(b"Destination context", response.body)
        self.assertIn(b"Expand full timeline", response.body)
        self.assertIn(b"Review saved.", response.body)
        self.assertIn(b"Publish", response.body)
        self.assertIn(b"Make private", response.body)
        self.assertNotIn(b"Previous trip", response.body)
        self.assertNotIn(b"Next trip", response.body)
        self.assertNotIn(b"<h2>Trip summary</h2>", response.body)
        self.assertNotIn(b"<h2>Destination context</h2>", response.body)
        self.assertNotIn(b"Segment name", response.body)
        self.assertIn(b'detail-cell wide', response.body)
        self.assertIn(b"2026-03-01 02:30 AM CST", response.body)
        mock_get.assert_called_once_with(7)

    def test_admin_trip_destination_page_renders_return_link(self) -> None:
        with patch("app.main.trip_admin.get_trip", return_value=_trip_detail()) as mock_get, \
             patch("app.main.destination_overrides.list_overrides", return_value=[]):
            response = admin_trip_destination_page(7, return_to="/admin/trip/7")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Destination Context", response.body)
        self.assertIn(b"Back to trip detail", response.body)
        self.assertIn(b'href="/admin/trip/7"', response.body)
        mock_get.assert_called_once_with(7)

    def test_review_trip_from_form_uses_repository(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return (
                    b"action=confirm&reviewer_name=Venkat&trip_name=Colorado+Weekend"
                    b"&summary_text=Late+winter+trip.&primary_destination_name=Denver"
                    b"&review_notes=Looks+good"
                )

        with patch("app.main.trip_admin.record_review", return_value=_trip_detail()) as mock_review:
            response = asyncio.run(review_trip_from_form(7, FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/trip/7?saved=review")
        mock_review.assert_called_once_with(
            7,
            action="confirm",
            reviewer_name="Venkat",
            review_notes="Looks good",
            trip_name="Colorado Weekend",
            summary_text="Late winter trip.",
            primary_destination_name="Denver",
            is_private=None,
            publish_ready=None,
        )

    def test_review_trip_from_form_handles_publish_flags(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return b"action=publish&reviewer_name=Venkat&is_private=false&publish_ready=true"

        with patch("app.main.trip_admin.record_review", return_value=_trip_detail()) as mock_review:
            response = asyncio.run(review_trip_from_form(7, FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/trip/7?saved=published")
        mock_review.assert_called_once_with(
            7,
            action="publish",
            reviewer_name="Venkat",
            review_notes=None,
            trip_name=None,
            summary_text=None,
            primary_destination_name=None,
            is_private=False,
            publish_ready=True,
        )

    def test_update_trip_segment_from_form_uses_repository(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return b"segment_name=Flight+to+Vegas&summary_text=Trail+walk&rating=5"

        with patch("app.main.trip_admin.update_trip_segment", return_value=_trip_detail()) as mock_update:
            response = asyncio.run(update_trip_segment_from_form(7, 41, FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/trip/7?saved=segment")
        mock_update.assert_called_once_with(
            7,
            41,
            segment_name="Flight to Vegas",
            summary_text="Trail walk",
            rating=5,
        )

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

    def test_create_destination_override_uses_repository(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return (
                    b"rule_name=Rec+Plex+ignore&classification=amateur_sports_venue"
                    b"&match_pattern=rec+plex&radius_meters=1200&ignore_trip=true"
                )

        with patch("app.main.destination_overrides.create_override") as mock_create:
            response = asyncio.run(create_destination_override(FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/overrides")
        mock_create.assert_called_once_with(
            rule_name="Rec Plex ignore",
            classification="amateur_sports_venue",
            keep_trip=False,
            ignore_trip=True,
            match_pattern="rec plex",
            latitude=None,
            longitude=None,
            radius_meters=1200,
        )

    def test_delete_destination_override_uses_repository(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return b"override_id=9&return_to=%2Fadmin%2Ftrip%2F7"

        with patch("app.main.destination_overrides.delete_override") as mock_delete:
            response = asyncio.run(delete_destination_override(FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/overrides?return_to=%2Fadmin%2Ftrip%2F7")
        mock_delete.assert_called_once_with(9)


if __name__ == "__main__":
    unittest.main()
