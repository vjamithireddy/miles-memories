from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import base64
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.main import (
    admin_basic_auth,
    admin_homepage,
    admin_overrides_page,
    admin_trip_destination_page,
    admin_trip_detail_page,
    admin_uploads_page,
    create_destination_override,
    delete_destination_override,
    get_admin_trip,
    health,
    homepage,
    list_admin_trips,
    public_trip_detail_page,
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
    trip["activities_summary"] = {
        "count": 2,
        "items": [
            {
                "activity_name": "South Kaibab Hike",
                "activity_type": "hiking",
                "start_time": datetime(2026, 3, 1, 13, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 3, 1, 16, 15, tzinfo=timezone.utc),
                "duration_seconds": 11_700,
                "distance_meters": 17_500,
                "elevation_gain_meters": 820,
                "elevation_loss_meters": 1_340,
            },
            {
                "activity_name": "Desert View Walk",
                "activity_type": "walking",
                "start_time": datetime(2026, 3, 2, 18, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 3, 2, 19, 5, tzinfo=timezone.utc),
                "duration_seconds": 3_900,
                "distance_meters": 4_100,
                "elevation_gain_meters": 60,
                "elevation_loss_meters": 55,
            },
        ],
    }
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
            "start_place_type": "path",
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
            "start_place_name": "St. Louis Lambert Airport",
            "start_place_type": "aerodrome",
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


def _trip_status_counts() -> dict:
    return {
        "total": 1,
        "needs_review": 1,
        "reviewed": 0,
        "published": 0,
        "rejected": 0,
        "ignored": 0,
        "private": 1,
        "public": 0,
    }


def _trip_light_with_snapshot() -> tuple[dict, dict]:
    trip = _trip_detail()
    trip["leg_count"] = len(trip.get("travel_legs") or [])
    snapshot = {
        "public": {
            "travel_legs": trip["travel_legs"],
            "map_points": [
                {"lat": 38.6, "lon": -90.2},
                {"lat": 39.1, "lon": -94.5},
            ],
            "activities_summary": {"items": [], "total": 0},
        }
    }
    return trip, snapshot


class AppApiTests(unittest.TestCase):
    def test_admin_routes_require_basic_auth(self) -> None:
        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def allow(_request: Request):
            return PlainTextResponse("ok")

        base_scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/admin",
            "raw_path": b"/admin",
            "query_string": b"",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "headers": [],
        }

        with patch("app.main.get_admin_username", return_value="venkat"), patch(
            "app.main.get_admin_password", return_value="secret-pass"
        ):
            unauthorized = asyncio.run(admin_basic_auth(Request(base_scope, receive), allow))
            self.assertEqual(unauthorized.status_code, 401)
            self.assertEqual(
                unauthorized.headers.get("www-authenticate"),
                'Basic realm="MilesMemories Admin"',
            )

            token = base64.b64encode(b"venkat:secret-pass").decode("ascii")
            authorized_scope = dict(base_scope)
            authorized_scope["headers"] = [
                (b"authorization", f"Basic {token}".encode("ascii"))
            ]
            authorized = asyncio.run(admin_basic_auth(Request(authorized_scope, receive), allow))
            self.assertEqual(authorized.status_code, 200)
            self.assertEqual(authorized.body, b"ok")

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

    def test_admin_uploads_page_documents_import_flow(self) -> None:
        response = admin_uploads_page()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Data uploads", response.body)
        self.assertIn(b"make ingest-location", response.body)
        self.assertIn(b"make ingest-photos", response.body)
        self.assertIn(b"make ingest-garmin", response.body)
        self.assertIn(b"make detect-trips", response.body)
        self.assertIn(b"make run-garmin-mcp", response.body)
        self.assertIn(b"Back to queue", response.body)

    def test_homepage_returns_html(self) -> None:
        published_trip = _trip_summary()
        published_trip["trip_name"] = "Glacier National Park"
        published_trip["primary_destination_name"] = "Montana"
        published_trip["summary_text"] = "Road trip through Glacier and Yellowstone."
        published_trip["is_private"] = False
        published_trip["publish_ready"] = True
        published_trip["status"] = "published"

        with patch("app.main.trip_admin.list_published_trips", return_value=[published_trip]) as mock_list, \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = homepage()

        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers["location"], "/trips")
        mock_list.assert_not_called()

    def test_public_trip_detail_renders_read_only_story(self) -> None:
        trip = _trip_detail()
        trip["is_private"] = False
        trip["publish_ready"] = True
        trip["status"] = "published"

        with patch("app.main.trip_admin.get_public_trip_by_slug", return_value=trip) as mock_get, \
             patch(
                 "app.main.trip_admin.get_trip_route_points",
                 return_value=[
                     {"lat": 38.6, "lon": -90.2},
                     {"lat": 39.1, "lon": -94.5},
                 ],
             ) as mock_route, \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = public_trip_detail_page("colorado-weekend")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Published Trip", response.body)
        self.assertIn(b"Travel legs", response.body)
        self.assertIn(b'class="public-legs"', response.body)
        self.assertIn(b"Expand / Collapse", response.body)
        self.assertIn(b'class="public-leg-header"', response.body)
        self.assertIn(b"Trip map", response.body)
        self.assertIn(b"Route", response.body)
        self.assertIn(b"St. Louis", response.body)
        self.assertIn(b"Las Vegas", response.body)
        self.assertIn(b"Travel modes", response.body)
        self.assertNotIn(b"Journey size", response.body)
        self.assertIn(b'class="story-card"', response.body)
        self.assertIn(b"Back to published trips", response.body)
        self.assertIn(b'South Kaibab Hike', response.body)
        self.assertIn(b'Desert View Walk', response.body)
        self.assertNotIn(b"Activities load on demand.", response.body)
        self.assertIn(b"Published route preview built from the full inferred travel-leg path for the trip.", response.body)
        self.assertIn(b"maplibre-gl.css", response.body)
        self.assertIn(b"maplibre-gl.js", response.body)
        self.assertIn(b'data-public-trip-map=', response.body)
        self.assertIn(b"trip-stops-clusters", response.body)
        self.assertIn(b"getClusterExpansionZoom", response.body)
        self.assertIn(b"Reset trip map view", response.body)
        self.assertIn(b"grid-template-columns: 1fr;", response.body)
        self.assertIn(b"Each leg can also be opened individually.", response.body)
        self.assertIn(b"Back to published trips", response.body)
        self.assertIn(b"Flight from St. Louis to Las Vegas", response.body)
        self.assertIn(b"Part of the published journey route.", response.body)
        self.assertNotIn(b"Reviewer name", response.body)
        self.assertNotIn(b"Yes", response.body)
        self.assertNotIn(b"Public", response.body)
        self.assertNotIn(b'data-autosave="segment"', response.body)
        self.assertNotIn(b"class=\"leg-summary-input\"", response.body)
        self.assertNotIn(b"Trip moments", response.body)
        self.assertNotIn(b"Source activity:", response.body)
        mock_get.assert_called_once_with("colorado-weekend")
        mock_route.assert_called_once_with(trip["id"], append_home_if_close=True)

    def test_public_trip_detail_coalesces_adjacent_duplicate_legs(self) -> None:
        trip = _trip_detail()
        trip["trip_slug"] = "road-trip"
        trip["is_private"] = False
        trip["publish_ready"] = True
        trip["status"] = "published"
        trip["travel_legs"] = [
            {
                "leg_type": "car",
                "label": "Car travel",
                "start_time": datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
                "end_time": datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
                "start_latitude": 38.0,
                "start_longitude": -90.0,
                "end_latitude": 39.0,
                "end_longitude": -89.0,
                "start_place_name": "Normal",
                "end_place_name": "Towanda",
                "segment_summary": "Monday Morning drive from Normal to Towanda (1).",
                "path_points": [{"lat": 38.0, "lon": -90.0}, {"lat": 39.0, "lon": -89.0}],
            },
            {
                "leg_type": "car",
                "label": "Car travel",
                "start_time": datetime(2026, 3, 1, 9, 5, tzinfo=timezone.utc),
                "end_time": datetime(2026, 3, 1, 9, 20, tzinfo=timezone.utc),
                "start_latitude": 38.0,
                "start_longitude": -90.0,
                "end_latitude": 39.0,
                "end_longitude": -89.0,
                "start_place_name": "Normal",
                "end_place_name": "Towanda",
                "segment_summary": "Monday Morning drive from Normal to Towanda (2).",
                "path_points": [{"lat": 39.0, "lon": -89.0}],
            },
        ]

        with patch("app.main.trip_admin.get_public_trip_by_slug", return_value=trip), \
             patch("app.main.trip_admin.get_trip_route_points", return_value=[]), \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = public_trip_detail_page("road-trip")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Monday Morning drive from Normal to Towanda", response.body)
        self.assertNotIn(
            b"Monday Morning drive from Normal to Towanda (1)", response.body
        )
        self.assertNotIn(
            b"Monday Morning drive from Normal to Towanda (2)", response.body
        )

    def test_public_trip_detail_renders_for_numeric_trip_id(self) -> None:
        trip = _trip_detail()
        trip["is_private"] = False
        trip["publish_ready"] = True
        trip["status"] = "published"
        for item in trip["activities_summary"]["items"]:
            item["start_time"] = item["start_time"].isoformat()
            item["end_time"] = item["end_time"].isoformat()

        with patch("app.main.trip_admin.get_public_trip_by_id", return_value=trip) as mock_get, \
             patch("app.main.trip_admin.get_trip_route_points", return_value=[]), \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = public_trip_detail_page(str(trip["id"]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Published Trip", response.body)
        self.assertIn(b"Back to published trips", response.body)
        self.assertIn(b"South Kaibab Hike", response.body)
        mock_get.assert_called_once_with(trip["id"])

    def test_health_returns_plain_text(self) -> None:
        response = health()

        self.assertEqual(response, "ok")

    def test_admin_homepage_renders_trips(self) -> None:
        with patch("app.main.trip_admin.list_trips", return_value=[_trip_summary()]) as mock_list, \
             patch("app.main.trip_admin.get_trip_status_counts", return_value=_trip_status_counts()):
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
        self.assertIn(b"Data uploads", response.body)
        self.assertIn(b'class="button" href="/admin/trips?', response.body)
        self.assertIn(b"Open detail page", response.body)
        self.assertIn(b'class="utility-link"', response.body)
        mock_list.assert_called_once_with(
            status="needs_review",
            review_decision="pending",
            include_private=True,
            only_private=False,
            limit=24,
        )

    def test_admin_trip_detail_renders_map(self) -> None:
        trip, snapshot = _trip_light_with_snapshot()
        with patch("app.main.trip_admin.get_trip_light", return_value=trip) as mock_get, \
             patch("app.main.trip_admin.get_trip_snapshot", return_value=snapshot), \
             patch("app.main.trip_admin.get_trip_activity_summary", return_value={"items": [], "total": 0}), \
             patch("app.main.destination_overrides.list_overrides", return_value=[]), \
             patch("app.main.trip_admin.get_trip_neighbors", return_value={"previous": None, "next": None}), \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = admin_trip_detail_page(7, saved="review")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Trip map", response.body)
        self.assertIn(b"Travel legs", response.body)
        self.assertIn(b"Trip timing", response.body)
        self.assertIn(b"2026-03-01", response.body)
        self.assertIn(b"class=\"trip-map-static\"", response.body)
        self.assertIn(b"Linked trip coordinates are plotted in order so you can review the route shape and destination cluster.", response.body)
        self.assertIn(b"data-admin-trip-map=", response.body)
        self.assertIn(b"Trip Overview", response.body)
        self.assertIn(b"Destination context", response.body)
        self.assertIn(b"Expand travel legs", response.body)
        self.assertIn(b"Travel legs load on demand.", response.body)
        self.assertIn(b"data-leg-list", response.body)
        self.assertIn(b"Review saved.", response.body)
        self.assertIn(b"Yes", response.body)
        self.assertIn(b"No", response.body)
        self.assertIn(b"Public", response.body)
        self.assertIn(b"Private", response.body)
        self.assertNotIn(b"Save details", response.body)
        self.assertIn(b"Review complete. Choose whether this trip should stay private or be visible on the public site.", response.body)
        self.assertIn(b"Text edits autosave when you leave a field.", response.body)
        self.assertIn(b'is-current', response.body)
        self.assertIn(b'segmented-control', response.body)
        self.assertIn(b"Visibility", response.body)
        self.assertNotIn(b"Previous trip", response.body)
        self.assertNotIn(b"Next trip", response.body)
        self.assertNotIn(b"Review the trip first with a simple yes/no decision.", response.body)
        self.assertIn(b'detail-cell wide', response.body)
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

    def test_admin_trip_detail_disables_visibility_until_reviewed(self) -> None:
        trip = _trip_detail()
        trip["status"] = "needs_review"
        trip["review_decision"] = "pending"
        trip["is_private"] = True
        trip["publish_ready"] = False

        snapshot = {
            "public": {
                "travel_legs": trip["travel_legs"],
                "map_points": [],
                "activities_summary": {"items": [], "total": 0},
            }
        }
        trip["leg_count"] = len(trip["travel_legs"])
        with patch("app.main.trip_admin.get_trip_light", return_value=trip), \
             patch("app.main.trip_admin.get_trip_snapshot", return_value=snapshot), \
             patch("app.main.trip_admin.get_trip_activity_summary", return_value={"items": [], "total": 0}), \
             patch("app.main.destination_overrides.list_overrides", return_value=[]), \
             patch("app.main.trip_admin.get_trip_neighbors", return_value={"previous": None, "next": None}), \
             patch("app.main.get_user_timezone", return_value="America/Chicago"):
            response = admin_trip_detail_page(7)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Start by answering Yes or No. Visibility becomes available only after the trip is reviewed.",
            response.body,
        )
        self.assertIn(b'aria-label="Visibility"', response.body)
        self.assertIn(b'value="publish" aria-pressed="false" disabled', response.body)
        self.assertIn(b'value="mark_private" aria-pressed="true" disabled', response.body)

    def test_review_trip_from_form_uses_repository(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return (
                    b"action=confirm&reviewer_name=Venkat&trip_name=Colorado+Weekend"
                    b"&summary_text=Late+winter+trip.&primary_destination_name=Denver"
                    b"&review_notes=Looks+good"
                )

        with patch("app.main.trip_admin.record_review_light", return_value=_trip_detail()) as mock_review:
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

        with patch("app.main.trip_admin.record_review_light", return_value=_trip_detail()) as mock_review:
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

    def test_review_trip_from_form_defaults_to_save(self) -> None:
        class FakeRequest:
            async def body(self) -> bytes:
                return b"reviewer_name=Venkat&trip_name=Colorado+Weekend&summary_text=Late+winter+trip."

        with patch("app.main.trip_admin.record_review_light", return_value=_trip_detail()) as mock_review:
            response = asyncio.run(review_trip_from_form(7, FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/trip/7?saved=details")
        mock_review.assert_called_once_with(
            7,
            action="save",
            reviewer_name="Venkat",
            review_notes=None,
            trip_name="Colorado Weekend",
            summary_text="Late winter trip.",
            primary_destination_name=None,
            is_private=None,
            publish_ready=None,
        )

    def test_review_trip_from_form_returns_no_content_for_fetch_save(self) -> None:
        class FakeRequest:
            headers = {"x-requested-with": "fetch"}

            async def body(self) -> bytes:
                return b"reviewer_name=Venkat&trip_name=Colorado+Weekend&summary_text=Late+winter+trip."

        with patch("app.main.trip_admin.record_review_light", return_value=_trip_detail()) as mock_review:
            response = asyncio.run(review_trip_from_form(7, FakeRequest()))

        self.assertEqual(response.status_code, 204)
        mock_review.assert_called_once_with(
            7,
            action="save",
            reviewer_name="Venkat",
            review_notes=None,
            trip_name="Colorado Weekend",
            summary_text="Late winter trip.",
            primary_destination_name=None,
            is_private=None,
            publish_ready=None,
        )

    def test_review_trip_from_form_returns_json_for_fetch_action(self) -> None:
        updated_trip = _trip_detail()
        updated_trip["review_decision"] = "confirmed"
        updated_trip["status"] = "confirmed"
        updated_trip["is_private"] = False
        updated_trip["publish_ready"] = True

        class FakeRequest:
            headers = {"x-requested-with": "fetch"}

            async def body(self) -> bytes:
                return b"action=publish&reviewer_name=Venkat"

        with patch("app.main.trip_admin.record_review_light", return_value=updated_trip) as mock_review:
            response = asyncio.run(review_trip_from_form(7, FakeRequest()))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"saved":"published"', response.body)
        self.assertIn(b'"visibility_state":"public"', response.body)
        self.assertIn(b'"review_state":"yes"', response.body)
        mock_review.assert_called_once_with(
            7,
            action="publish",
            reviewer_name="Venkat",
            review_notes=None,
            trip_name=None,
            summary_text=None,
            primary_destination_name=None,
            is_private=None,
            publish_ready=None,
        )

    def test_update_trip_segment_from_form_uses_repository(self) -> None:
        class FakeRequest:
            headers = {}

            async def body(self) -> bytes:
                return b"summary_text=Trail+walk&rating=5"

        with patch("app.main.trip_admin.update_trip_segment", return_value=_trip_detail()) as mock_update:
            response = asyncio.run(update_trip_segment_from_form(7, 41, FakeRequest()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/trip/7?saved=segment")
        mock_update.assert_called_once_with(
            7,
            41,
            segment_name=None,
            summary_text="Trail walk",
            rating=5,
        )

    def test_update_trip_segment_from_form_returns_no_content_for_fetch(self) -> None:
        class FakeRequest:
            headers = {"x-requested-with": "fetch"}

            async def body(self) -> bytes:
                return b"summary_text=Trail+walk&rating=4"

        with patch("app.main.trip_admin.update_trip_segment", return_value=_trip_detail()) as mock_update:
            response = asyncio.run(update_trip_segment_from_form(7, 41, FakeRequest()))

        self.assertEqual(response.status_code, 204)
        mock_update.assert_called_once_with(
            7,
            41,
            segment_name=None,
            summary_text="Trail walk",
            rating=4,
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
            only_private=False,
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
