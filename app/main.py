from html import escape
from typing import List, Optional
from urllib.parse import urlencode

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


def _trip_badge_class(value: str) -> str:
    normalized = value.lower()
    if normalized in {"confirmed", "published"}:
        return "good"
    if normalized in {"pending", "needs_review"}:
        return "warn"
    if normalized in {"ignored", "rejected"}:
        return "muted"
    return ""


def _render_admin_page(
    trips: List[dict],
    *,
    status: Optional[str],
    review_decision: Optional[str],
    include_private: bool,
    limit: int,
) -> str:
    total = len(trips)
    private_total = sum(1 for trip in trips if trip["is_private"])
    ready_total = sum(1 for trip in trips if trip["publish_ready"])

    def selected(current: Optional[str], expected: str) -> str:
        return ' selected="selected"' if current == expected else ""

    filter_query = urlencode(
        {
            "status": status or "",
            "review_decision": review_decision or "",
            "include_private": "true" if include_private else "false",
            "limit": str(limit),
        }
    )

    cards = []
    for trip in trips:
        title = escape(trip["trip_name"] or "Untitled trip")
        destination = escape(trip["primary_destination_name"] or "Destination pending")
        trip_type = escape(trip["trip_type"] or "untyped")
        status_value = escape(trip["status"])
        review_value = escape(trip["review_decision"])
        score_value = "n/a" if trip["confidence_score"] is None else str(trip["confidence_score"])
        privacy_value = "Private" if trip["is_private"] else "Visible"
        ready_value = "Ready" if trip["publish_ready"] else "Not ready"
        detail_href = f"/admin/trips/{trip['id']}"

        cards.append(
            f"""
            <article class="trip-card">
              <div class="trip-head">
                <div>
                  <h3>{title}</h3>
                  <p class="trip-sub">{destination} · {trip_type}</p>
                </div>
                <div class="score">{score_value}</div>
              </div>
              <div class="meta-row">
                <span class="badge {_trip_badge_class(trip['status'])}">{status_value}</span>
                <span class="badge {_trip_badge_class(trip['review_decision'])}">{review_value}</span>
                <span class="badge">{privacy_value}</span>
                <span class="badge">{ready_value}</span>
              </div>
              <p class="trip-range">{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}</p>
              <p class="trip-summary">{escape(trip['summary_text'] or 'No summary yet. Use review actions or future UI tools to enrich this trip.')}</p>
              <div class="card-actions">
                <a href="{detail_href}">Open JSON Detail</a>
              </div>
            </article>
            """
        )

    cards_html = "".join(cards) if cards else """
      <article class="trip-card empty-state">
        <h3>No trips match these filters.</h3>
        <p>Adjust the filters or run trip detection after importing location and Garmin data.</p>
      </article>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MilesMemories Admin</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: #fff9f0;
      --line: #dcccb4;
      --ink: #182233;
      --muted: #657286;
      --accent: #b85f35;
      --good: #2e6a4b;
      --warn: #9b641d;
      --shadow: rgba(37, 28, 14, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        linear-gradient(180deg, rgba(233, 206, 177, 0.8), rgba(243, 239, 231, 0.98) 28%),
        linear-gradient(90deg, rgba(184, 95, 53, 0.08), transparent 22%, transparent 78%, rgba(42, 89, 81, 0.08));
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 34px 18px 64px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 20px;
    }}
    .eyebrow {{
      font-size: 0.82rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    h1 {{
      margin: 8px 0 0;
      font-size: clamp(2.1rem, 5vw, 4rem);
      line-height: 0.95;
    }}
    .sub {{
      max-width: 56ch;
      color: var(--muted);
      line-height: 1.6;
    }}
    .panel {{
      background: rgba(255, 249, 240, 0.92);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 22px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .stat strong {{
      display: block;
      font-size: 2rem;
      margin-bottom: 6px;
    }}
    .stat span {{
      color: var(--muted);
    }}
    .filters {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
      gap: 14px;
      align-items: end;
      margin-bottom: 22px;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    select, input[type="number"] {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: #fffdf8;
      font: inherit;
      color: var(--ink);
    }}
    .checkbox {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding-bottom: 10px;
    }}
    .checkbox input {{ width: 18px; height: 18px; }}
    .button {{
      display: inline-block;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: white;
      text-decoration: none;
      font-weight: 700;
      border-radius: 999px;
      padding: 12px 18px;
    }}
    .button.ghost {{
      background: transparent;
      color: var(--accent);
    }}
    .trips {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .trip-card {{
      background: rgba(255,255,255,0.62);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 20px;
      display: grid;
      gap: 14px;
    }}
    .trip-head {{
      display: flex;
      gap: 16px;
      justify-content: space-between;
      align-items: start;
    }}
    .trip-head h3 {{
      margin: 0;
      font-size: 1.55rem;
    }}
    .trip-sub, .trip-range, .trip-summary {{
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }}
    .score {{
      min-width: 58px;
      text-align: center;
      font-weight: 700;
      font-size: 1.2rem;
      background: #f0e3d1;
      border-radius: 14px;
      padding: 10px 12px;
    }}
    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .badge {{
      display: inline-block;
      padding: 7px 10px;
      border-radius: 999px;
      background: #efe7d9;
      color: var(--ink);
      font-size: 0.86rem;
      text-transform: capitalize;
    }}
    .badge.good {{ background: rgba(46, 106, 75, 0.14); color: var(--good); }}
    .badge.warn {{ background: rgba(155, 100, 29, 0.14); color: var(--warn); }}
    .badge.muted {{ background: rgba(101, 114, 134, 0.14); color: var(--muted); }}
    .card-actions a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    .links {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    @media (max-width: 920px) {{
      .stats, .filters, .trips {{
        grid-template-columns: 1fr;
      }}
      .topbar {{
        display: grid;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="topbar">
      <div>
        <div class="eyebrow">MilesMemories Admin</div>
        <h1>Trip review queue</h1>
        <p class="sub">Review detected trips, inspect status and destination signals, and use the JSON endpoints while the richer admin workflow is still being built.</p>
      </div>
      <div class="links">
        <a class="button" href="/admin/trips?{filter_query}">Raw JSON Feed</a>
        <a class="button ghost" href="/">Homepage</a>
      </div>
    </section>

    <section class="stats">
      <article class="panel stat"><strong>{total}</strong><span>Trips in current view</span></article>
      <article class="panel stat"><strong>{ready_total}</strong><span>Marked publish-ready</span></article>
      <article class="panel stat"><strong>{private_total}</strong><span>Still private</span></article>
    </section>

    <section class="panel">
      <form method="get" action="/admin" class="filters">
        <label>Status
          <select name="status">
            <option value="">Any status</option>
            <option value="needs_review"{selected(status, "needs_review")}>needs_review</option>
            <option value="confirmed"{selected(status, "confirmed")}>confirmed</option>
            <option value="published"{selected(status, "published")}>published</option>
            <option value="ignored"{selected(status, "ignored")}>ignored</option>
          </select>
        </label>
        <label>Review
          <select name="review_decision">
            <option value="">Any decision</option>
            <option value="pending"{selected(review_decision, "pending")}>pending</option>
            <option value="confirmed"{selected(review_decision, "confirmed")}>confirmed</option>
            <option value="ignored"{selected(review_decision, "ignored")}>ignored</option>
            <option value="rejected"{selected(review_decision, "rejected")}>rejected</option>
          </select>
        </label>
        <label>Limit
          <input type="number" min="1" max="200" name="limit" value="{limit}">
        </label>
        <label class="checkbox">Include private
          <input type="checkbox" name="include_private" value="true"{" checked" if include_private else ""}>
        </label>
        <button class="button" type="submit">Apply filters</button>
      </form>

      <div class="trips">
        {cards_html}
      </div>
    </section>
  </main>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_homepage(
    status: Optional[str] = Query(default=None),
    review_decision: Optional[str] = Query(default=None),
    include_private: bool = Query(default=True),
    limit: int = Query(default=24, ge=1, le=200),
) -> HTMLResponse:
    trips = trip_admin.list_trips(
        status=status,
        review_decision=review_decision,
        include_private=include_private,
        limit=limit,
    )
    return HTMLResponse(
        _render_admin_page(
            trips,
            status=status,
            review_decision=review_decision,
            include_private=include_private,
            limit=limit,
        )
    )


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
