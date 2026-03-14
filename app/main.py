from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import trip_admin
from app.schemas import PublishReadyRequest, TripDetail, TripReviewRequest, TripSummary
from app.settings import get_app_host, get_app_port, get_app_reload

app = FastAPI(title="MilesMemories API", version="0.1.0")

HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MilesMemories</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: #fffaf2;
      --ink: #1d2430;
      --muted: #5f6b7a;
      --line: #d8c9b3;
      --accent: #c8643b;
      --accent-dark: #8e3f22;
      --shadow: rgba(50, 33, 15, 0.12);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(200,100,59,0.18), transparent 28%),
        radial-gradient(circle at right 20%, rgba(39,93,79,0.12), transparent 24%),
        linear-gradient(180deg, #eed6bd 0%, var(--bg) 34%, #f8f4ed 100%);
    }

    main {
      max-width: 1080px;
      margin: 0 auto;
      padding: 48px 20px 72px;
    }

    .hero {
      display: grid;
      gap: 24px;
      grid-template-columns: 1.4fr 1fr;
      align-items: stretch;
    }

    .card {
      background: rgba(255, 250, 242, 0.9);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 28px;
    }

    .eyebrow {
      display: inline-block;
      margin-bottom: 14px;
      font-size: 0.8rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent-dark);
    }

    h1, h2, h3 {
      margin: 0 0 12px;
      line-height: 1.05;
      font-weight: 700;
    }

    h1 {
      font-size: clamp(2.3rem, 5vw, 4.8rem);
      max-width: 10ch;
    }

    p {
      margin: 0;
      line-height: 1.65;
      color: var(--muted);
      font-size: 1rem;
    }

    .hero-copy {
      display: grid;
      gap: 18px;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 8px;
    }

    .button, .link-card a {
      text-decoration: none;
    }

    .button {
      padding: 12px 16px;
      border-radius: 999px;
      border: 1px solid var(--accent);
      color: white;
      background: var(--accent);
      font-weight: 700;
      box-shadow: 0 8px 20px rgba(200, 100, 59, 0.25);
    }

    .button.secondary {
      background: transparent;
      color: var(--accent-dark);
    }

    .stats {
      display: grid;
      gap: 14px;
    }

    .stat {
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
    }

    .stat:last-child { border-bottom: 0; }

    .stat strong {
      display: block;
      font-size: 1.9rem;
      margin-bottom: 4px;
      color: var(--ink);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
      margin-top: 22px;
    }

    .link-card {
      min-height: 180px;
      display: grid;
      gap: 12px;
      align-content: start;
    }

    .link-card span {
      display: inline-block;
      font-size: 0.76rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent-dark);
    }

    .link-card a {
      color: var(--ink);
      font-size: 1.3rem;
      font-weight: 700;
    }

    .foot {
      margin-top: 22px;
      font-size: 0.95rem;
    }

    code {
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 0.95em;
      background: rgba(29, 36, 48, 0.06);
      padding: 0.15rem 0.35rem;
      border-radius: 6px;
    }

    @media (max-width: 820px) {
      .hero, .grid {
        grid-template-columns: 1fr;
      }

      main {
        padding-top: 28px;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <article class="card hero-copy">
        <div>
          <div class="eyebrow">MilesMemories</div>
          <h1>Travel stories assembled from your own data.</h1>
        </div>
        <p>
          The service is live. Location history and Garmin activity data can flow into trip review,
          publishing, and timeline tooling from this deployment.
        </p>
        <div class="actions">
          <a class="button" href="/admin/trips">Open Trip API</a>
          <a class="button secondary" href="/health">Service Health</a>
        </div>
      </article>

      <aside class="card stats">
        <div class="stat">
          <strong>Live</strong>
          <p>FastAPI app running behind Nginx on the VPS.</p>
        </div>
        <div class="stat">
          <strong>/admin/trips</strong>
          <p>Trip review endpoints are available for the next UI layer.</p>
        </div>
        <div class="stat">
          <strong>Scope</strong>
          <p>Current build excludes photo processing and focuses on trips, locations, and Garmin data.</p>
        </div>
      </aside>
    </section>

    <section class="grid">
      <article class="card link-card">
        <span>Endpoint</span>
        <a href="/admin/trips">Trip Review Feed</a>
        <p>List detected trips, filter by review state, and use this as the source for an admin review screen.</p>
      </article>
      <article class="card link-card">
        <span>Endpoint</span>
        <a href="/health">Health Check</a>
        <p>Simple uptime check for browser, curl, systemd validation, and reverse proxy smoke tests.</p>
      </article>
      <article class="card link-card">
        <span>Next Build</span>
        <a href="/admin/trips">Admin Homepage Placeholder</a>
        <p>Next sensible step is a lightweight trip operations UI on top of the review endpoints.</p>
      </article>
    </section>

    <p class="foot">
      Base API is available now. Start with <code>/admin/trips</code> for trip data and <code>/health</code> for deployment checks.
    </p>
  </main>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def homepage() -> HTMLResponse:
    return HTMLResponse(HOME_PAGE)


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
