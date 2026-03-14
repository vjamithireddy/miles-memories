from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query

from app import trip_admin
from app.schemas import PublishReadyRequest, TripDetail, TripReviewRequest, TripSummary
from app.settings import get_app_host, get_app_port, get_app_reload

app = FastAPI(title="MilesMemories API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin/trips", response_model=List[TripSummary])
def list_admin_trips(
    status: Optional[str] = Query(default=None),
    review_decision: Optional[str] = Query(default=None),
    include_private: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
) -> List[TripSummary]:
    return trip_admin.list_trips(
        status=status,
        review_decision=review_decision,
        include_private=include_private,
        limit=limit,
    )


@app.get("/admin/trips/{trip_id}", response_model=TripDetail)
def get_admin_trip(trip_id: int) -> TripDetail:
    trip = trip_admin.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@app.post("/admin/trips/{trip_id}/review", response_model=TripDetail)
def review_trip(trip_id: int, payload: TripReviewRequest) -> TripDetail:
    trip = trip_admin.record_review(
        trip_id,
        action=payload.action,
        reviewer_name=payload.reviewer_name,
        review_notes=payload.review_notes,
        trip_name=payload.trip_name,
        summary_text=payload.summary_text,
        primary_destination_name=payload.primary_destination_name,
        is_private=payload.is_private,
        publish_ready=payload.publish_ready,
    )
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@app.patch("/admin/trips/{trip_id}/publish-ready", response_model=TripDetail)
def update_publish_ready(trip_id: int, payload: PublishReadyRequest) -> TripDetail:
    trip = trip_admin.set_publish_ready(trip_id, publish_ready=payload.publish_ready)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=get_app_host(),
        port=get_app_port(),
        reload=get_app_reload(),
    )
