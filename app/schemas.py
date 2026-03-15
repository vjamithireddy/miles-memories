from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TripEventCount(BaseModel):
    event_type: str
    total: int


class TripTimelineEvent(BaseModel):
    event_type: str
    event_ref_id: int
    event_time: datetime
    sort_order: Optional[int] = None
    day_index: Optional[int] = None
    timeline_label: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class TripReviewHistoryItem(BaseModel):
    reviewer_name: Optional[str] = None
    review_action: str
    review_notes: Optional[str] = None
    reviewed_at: datetime


class TripTravelLeg(BaseModel):
    leg_type: str
    label: str
    start_time: datetime
    end_time: datetime
    start_latitude: Optional[float] = None
    start_longitude: Optional[float] = None
    end_latitude: Optional[float] = None
    end_longitude: Optional[float] = None
    source_event_id: Optional[str] = None


class TripSummary(BaseModel):
    id: int
    trip_name: Optional[str] = None
    trip_slug: Optional[str] = None
    trip_type: Optional[str] = None
    status: str
    review_decision: str
    start_time: datetime
    end_time: datetime
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    primary_destination_name: Optional[str] = None
    origin_place_name: Optional[str] = None
    confidence_score: Optional[int] = None
    summary_text: Optional[str] = None
    is_private: bool
    publish_ready: bool
    published_at: Optional[datetime] = None
    updated_at: datetime


class TripDetail(TripSummary):
    event_counts: List[TripEventCount] = Field(default_factory=list)
    timeline: List[TripTimelineEvent] = Field(default_factory=list)
    review_history: List[TripReviewHistoryItem] = Field(default_factory=list)
    travel_legs: List[TripTravelLeg] = Field(default_factory=list)


class TripReviewRequest(BaseModel):
    action: Literal["confirm", "reject", "ignore", "publish", "mark_private"]
    reviewer_name: Optional[str] = None
    review_notes: Optional[str] = None
    trip_name: Optional[str] = None
    summary_text: Optional[str] = None
    primary_destination_name: Optional[str] = None
    is_private: Optional[bool] = None
    publish_ready: Optional[bool] = None


class PublishReadyRequest(BaseModel):
    publish_ready: bool
