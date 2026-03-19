import base64
import binascii
from datetime import datetime, timedelta
from html import escape
import json
import math
import re
import secrets
from typing import List, Optional, Union
from urllib.parse import parse_qs
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.bootstrap import get_user_timezone
from app import destination_overrides, trip_admin
from app.schemas import PublishReadyRequest, TripDetail, TripReviewRequest, TripSummary
from app.settings import (
    get_admin_password,
    get_admin_username,
    get_app_host,
    get_app_port,
    get_app_reload,
)

app = FastAPI(title="MilesMemories API", version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _html_response(content: str) -> HTMLResponse:
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _admin_auth_failure(message: str, status_code: int = 401) -> PlainTextResponse:
    response = PlainTextResponse(message, status_code=status_code)
    response.headers["WWW-Authenticate"] = 'Basic realm="MilesMemories Admin"'
    return response


def _decode_basic_auth_header(header_value: str) -> Optional[tuple[str, str]]:
    if not header_value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header_value[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


@app.middleware("http")
async def admin_basic_auth(request: Request, call_next):
    if not request.url.path.startswith("/admin"):
        return await call_next(request)

    expected_username = get_admin_username()
    expected_password = get_admin_password()
    if not expected_username or not expected_password:
        return _admin_auth_failure("Admin authentication is not configured.", status_code=503)

    credentials = _decode_basic_auth_header(request.headers.get("Authorization", ""))
    if not credentials:
        return _admin_auth_failure("Admin authentication required.")

    username, password = credentials
    if not (
        secrets.compare_digest(username, expected_username)
        and secrets.compare_digest(password, expected_password)
    ):
        return _admin_auth_failure("Invalid admin credentials.")

    return await call_next(request)

def _render_public_homepage(trips: List[dict]) -> str:
    published_total = len(trips)
    latest_label = "No published trips yet"
    if trips:
        latest_trip = trips[0]
        latest_dt = latest_trip.get("published_at") or latest_trip.get("end_time") or latest_trip.get("start_time")
        latest_label = _format_local_datetime(latest_dt) if latest_dt else "Recently updated"

    if trips:
        feature_trip = trips[0]
        feature_title = escape(feature_trip.get("trip_name") or "Untitled trip")
        feature_destination = escape(feature_trip.get("primary_destination_name") or "Destination pending")
        feature_href = f"/trips/{escape(feature_trip.get('trip_slug') or str(feature_trip.get('id') or ''))}"
        feature_summary = escape(
            feature_trip.get("summary_text")
            or "A published trip from the MilesMemories archive."
        )
        feature_timing = (
            f"{escape(_format_local_datetime(feature_trip['start_time']))} → "
            f"{escape(_format_local_datetime(feature_trip['end_time']))}"
        )
        feature_trip_type = escape((feature_trip.get("trip_type") or "trip").replace("_", " "))
        feature_link_markup = f'<div><a class="trip-card-link" href="{feature_href}"><span class="trip-chip">Open trip details</span></a></div>'
    else:
        feature_title = "Published trips will appear here"
        feature_destination = "MilesMemories archive"
        feature_href = ""
        feature_summary = "Trips you publish from the admin workflow will appear on this landing page."
        feature_timing = "Publish a reviewed trip to open the public archive."
        feature_trip_type = "Published archive"
        feature_link_markup = ""

    cards = []
    for trip in trips:
        title = escape(trip.get("trip_name") or "Untitled trip")
        destination = escape(trip.get("primary_destination_name") or "Destination pending")
        summary = escape(trip.get("summary_text") or "Published from the MilesMemories archive.")
        trip_type = escape((trip.get("trip_type") or "trip").replace("_", " "))
        timing = f"{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}"
        trip_href = f"/trips/{escape(trip.get('trip_slug') or str(trip.get('id') or ''))}"
        cards.append(
            f"""
            <article class="trip-card">
              <a class="trip-card-link" href="{trip_href}" aria-label="Open {title}">
              <div class="trip-card-top">
                <span class="trip-chip">{trip_type}</span>
                <span class="trip-chip muted">{timing}</span>
              </div>
              <h3>{title}</h3>
              <p class="trip-destination">{destination}</p>
              <p>{summary}</p>
              </a>
            </article>
            """
        )

    trips_markup = "".join(cards) if cards else """
      <article class="trip-card empty-state">
        <h3>No public trips yet</h3>
        <p>Publish a reviewed trip from the admin workflow and it will appear here automatically.</p>
      </article>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MilesMemories</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --panel: rgba(255, 250, 242, 0.92);
      --ink: #1d2430;
      --muted: #5f6b7a;
      --line: #d8c9b3;
      --accent: #c8643b;
      --accent-dark: #8e3f22;
      --good: #275d4f;
      --shadow: rgba(50, 33, 15, 0.12);
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(200,100,59,0.18), transparent 28%),
        radial-gradient(circle at right 20%, rgba(39,93,79,0.12), transparent 24%),
        linear-gradient(180deg, #eed6bd 0%, var(--bg) 34%, #f8f4ed 100%);
    }}

    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 48px 20px 80px;
      display: grid;
      gap: 22px;
    }}

    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 30px;
    }}

    .hero {{
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 20px;
    }}

    .hero-copy {{
      display: grid;
      gap: 18px;
    }}

    .eyebrow {{
      display: inline-block;
      font-size: 0.8rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent-dark);
    }}

    h1, h2, h3 {{
      margin: 0;
      line-height: 1.04;
      font-weight: 700;
    }}

    h1 {{
      font-size: clamp(2.4rem, 5vw, 5rem);
      max-width: 11ch;
    }}

    h2 {{
      font-size: clamp(1.6rem, 2vw, 2.3rem);
      margin-bottom: 14px;
    }}

    h3 {{
      font-size: 1.6rem;
      margin-bottom: 10px;
    }}

    p {{
      margin: 0;
      line-height: 1.65;
      color: var(--muted);
      font-size: 1rem;
    }}

    .hero-note {{
      font-size: 1.08rem;
      max-width: 60ch;
    }}

    .stats {{
      display: grid;
      gap: 14px;
      align-content: start;
    }}

    .stat {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px 18px;
      background: rgba(255,255,255,0.52);
    }}

    .stat strong {{
      display: block;
      font-size: 2rem;
      color: var(--ink);
      margin-bottom: 4px;
    }}

    .feature-grid {{
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 20px;
    }}

    .feature-card {{
      display: grid;
      gap: 14px;
    }}

    .feature-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}

    .trip-chip {{
      display: inline-flex;
      align-items: center;
      padding: 7px 11px;
      border-radius: 999px;
      font-size: 0.84rem;
      text-transform: capitalize;
      background: rgba(200,100,59,0.14);
      color: var(--accent-dark);
    }}

    .trip-chip.muted {{
      background: rgba(29,36,48,0.06);
      color: var(--muted);
    }}

    .feature-destination {{
      color: var(--good);
      font-size: 1rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .published-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
    }}

    .trip-card {{
      display: grid;
      gap: 12px;
      min-height: 220px;
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 22px;
      background: rgba(255,255,255,0.58);
    }}
    .trip-card-link {{
      display: grid;
      gap: 12px;
      color: inherit;
      text-decoration: none;
      height: 100%;
    }}
    .trip-card-link:hover h3 {{
      color: var(--accent-dark);
    }}

    .trip-card-top {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
      align-items: center;
    }}

    .trip-destination {{
      color: var(--good);
      font-weight: 700;
      margin-top: -4px;
    }}

    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: baseline;
      margin-bottom: 16px;
    }}

    .foot {{
      color: var(--muted);
      font-size: 0.94rem;
    }}

    .empty-state {{
      grid-column: 1 / -1;
      min-height: 0;
    }}

    @media (max-width: 960px) {{
      .hero,
      .feature-grid,
      .published-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel hero">
      <div class="hero-copy">
        <span class="eyebrow">MilesMemories</span>
        <h1>Published travel stories from your own data.</h1>
        <p class="hero-note">This public landing page only shows trips you have reviewed, published, and marked visible from the admin workflow.</p>
      </div>
      <aside class="stats">
        <div class="stat">
          <strong>{published_total}</strong>
          <p>Published trips currently visible on the site.</p>
        </div>
        <div class="stat">
          <strong>{escape(latest_label)}</strong>
          <p>Most recent published timeline currently surfaced here.</p>
        </div>
      </aside>
    </section>

    <section class="feature-grid">
      <article class="panel feature-card">
        <span class="eyebrow">Featured Trip</span>
        <h2>{feature_title}</h2>
        <div class="feature-destination">{feature_destination}</div>
        <p>{feature_summary}</p>
        <div class="feature-meta">
          <span class="trip-chip">{feature_trip_type}</span>
          <span class="trip-chip muted">{feature_timing}</span>
        </div>
        {feature_link_markup}
      </article>
      <article class="panel feature-card">
        <span class="eyebrow">How It Works</span>
        <h2>Publish from review</h2>
        <p>Trips stay private until you review them in the admin workflow. Once a trip is reviewed and set to public, it appears here automatically.</p>
        <p class="foot">This public page does not expose admin JSON or review tooling.</p>
      </article>
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <span class="eyebrow">Published Archive</span>
          <h2>Recent published trips</h2>
        </div>
        <p>{published_total} visible trip{"s" if published_total != 1 else ""}</p>
      </div>
      <div class="published-grid">
        {trips_markup}
      </div>
    </section>
  </main>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def homepage() -> HTMLResponse:
    trips = trip_admin.list_published_trips(limit=12)
    return _html_response(_render_public_homepage(trips))


def _render_public_trip_detail_page(trip: dict) -> str:
    title = escape(trip["trip_name"] or "Untitled trip")
    summary = escape(trip["summary_text"] or "A published trip from the MilesMemories archive.")
    destination = escape(trip["primary_destination_name"] or "Destination pending")
    trip_type = escape((trip["trip_type"] or "trip").replace("_", " "))
    timing = f"{escape(_format_local_datetime(trip['start_time']))} → {escape(_format_local_datetime(trip['end_time']))}"
    short_timing = f"{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}"
    travel_legs = _coalesce_public_story_legs(trip.get("travel_legs", []))
    trip_duration = escape(_format_duration(trip["start_time"], trip["end_time"]))
    map_points = trip_admin.get_trip_route_points(trip["id"], append_home_if_close=True)
    if not map_points:
        map_points = [
            {
                "lat": item["latitude"],
                "lon": item["longitude"],
            }
            for item in trip["timeline"]
            if item.get("latitude") is not None and item.get("longitude") is not None
        ]
    trip_map_markup = _render_route_map_preview(
        map_points,
        aria_label="Published trip route map preview",
    )
    leg_count = len(travel_legs)
    distinct_leg_labels = list(dict.fromkeys(item["label"] for item in travel_legs if item.get("label")))
    travel_modes = ", ".join(distinct_leg_labels[:4]) if distinct_leg_labels else "Story moments pending"
    if len(distinct_leg_labels) > 4:
        travel_modes = f"{travel_modes}, +{len(distinct_leg_labels) - 4} more"
    public_origin = "Journey start recorded"
    public_destination = trip["primary_destination_name"] or "Destination pending"
    if travel_legs:
        first_start = (travel_legs[0].get("start_place_name") or "").strip()
        last_end = (travel_legs[-1].get("end_place_name") or "").strip()
        if first_start:
            public_origin = first_start
        if last_end:
            public_destination = last_end
    public_origin = escape(public_origin)
    public_destination = escape(public_destination)
    travel_leg_items = "".join(
        f"""
        <details class="public-leg-card">
          <summary class="public-leg-header">
            <div class="public-leg-headline">
              <h3>{escape(_travel_leg_comment(item))}</h3>
              <span class="public-leg-tag">{escape(item['label'])}</span>
            </div>
            <div class="public-leg-summary-row">
              <p class="public-leg-meta">{escape(_format_local_datetime(item['start_time']))} → {escape(_format_local_datetime(item['end_time']))} ({escape(_format_duration(item['start_time'], item['end_time']))})</p>
              <span class="public-leg-toggle"><span class="toggle-icon"></span><span>Expand</span></span>
            </div>
          </summary>
          <div class="public-leg-body">
            <div class="public-leg-map">{_render_leg_map_preview(item)}</div>
            <div class="public-leg-copy">
              <p>{escape(_travel_leg_comment(item))}</p>
              <p class="public-leg-caption">Part of the published journey route.</p>
            </div>
          </div>
        </details>
        """
        for item in travel_legs
    ) or """
      <article class="public-leg-card empty-state">
        <div class="public-leg-header">
          <h3>Travel legs coming soon</h3>
        </div>
        <div class="public-leg-body">
          <div class="public-leg-copy">
            <p>This published trip does not have inferred leg segments yet.</p>
          </div>
        </div>
      </article>
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · MilesMemories</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #1d2430;
      --muted: #5f6b7a;
      --line: #d8c9b3;
      --accent: #c8643b;
      --accent-dark: #8e3f22;
      --shadow: rgba(50, 33, 15, 0.12);
      --good: #275d4f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(200,100,59,0.18), transparent 28%),
        radial-gradient(circle at right 20%, rgba(39,93,79,0.12), transparent 24%),
        linear-gradient(180deg, #eed6bd 0%, var(--bg) 34%, #f8f4ed 100%);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 38px 20px 80px;
      display: grid;
      gap: 22px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 28px;
    }}
    .hero {{
      display: grid;
      gap: 18px;
    }}
    .hero-copy {{
      max-width: 42rem;
      display: grid;
      gap: 12px;
    }}
    .eyebrow {{
      display: inline-block;
      font-size: 0.8rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent-dark);
    }}
    h1, h2, h3 {{
      margin: 0;
      line-height: 1.04;
      font-weight: 700;
    }}
    h1 {{
      font-size: clamp(2rem, 4vw, 3.4rem);
      max-width: 16ch;
    }}
    h2 {{
      font-size: clamp(1.55rem, 2vw, 2.2rem);
      margin-bottom: 12px;
    }}
    h3 {{
      font-size: 1.35rem;
    }}
    p {{
      margin: 0;
      line-height: 1.65;
      color: var(--muted);
      font-size: 1rem;
    }}
    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .trip-chip {{
      display: inline-flex;
      align-items: center;
      padding: 7px 11px;
      border-radius: 999px;
      font-size: 0.84rem;
      text-transform: capitalize;
      background: rgba(200,100,59,0.14);
      color: var(--accent-dark);
    }}
    .trip-chip.muted {{
      background: rgba(29,36,48,0.06);
      color: var(--muted);
    }}
    .button {{
      display: inline-block;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
      border-radius: 999px;
      padding: 12px 18px;
    }}
    .feature-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 20px;
    }}
    .story-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .story-card {{
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px 20px;
      background: rgba(255,255,255,0.52);
      display: grid;
      gap: 8px;
    }}
    .story-card-label {{
      font-size: 0.78rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .story-card-value {{
      font-size: clamp(1.15rem, 2vw, 1.55rem);
      font-weight: 700;
      line-height: 1.2;
    }}
    .story-card-note {{
      font-size: 0.94rem;
    }}
    .trip-map-static {{
      background: #efe5d7;
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid var(--line);
    }}
    .leg-map-frame {{
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 320px;
      background: #efe5d7;
      overflow: hidden;
    }}
    .leg-map-tile {{
      position: absolute;
      display: block;
      max-width: none;
      user-select: none;
      pointer-events: none;
    }}
    .leg-map-svg {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      display: block;
    }}
    .leg-map-legend {{
      position: absolute;
      left: 14px;
      bottom: 14px;
      display: inline-flex;
      gap: 12px;
      align-items: center;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255, 248, 239, 0.92);
      border: 1px solid rgba(219, 202, 177, 0.9);
      color: var(--ink);
      font-size: 0.82rem;
      font-weight: 600;
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .leg-map-legend span {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
    }}
    .legend-start {{
      background: #c8643b;
    }}
    .legend-end {{
      background: #2f6c5b;
    }}
    .trip-map-static .leg-map-frame {{
      max-width: 100%;
      min-height: 0;
      aspect-ratio: 16 / 9;
      height: auto;
    }}
    details.public-legs {{
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(255,255,255,0.56);
      overflow: clip;
    }}
    details.public-legs > summary {{
      position: sticky;
      top: 0;
      z-index: 4;
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: center;
      cursor: pointer;
      padding: 18px 22px;
      list-style: none;
      background: rgba(255, 248, 239, 0.98);
      border-bottom: 1px solid transparent;
    }}
    details.public-legs > summary::-webkit-details-marker {{
      display: none;
    }}
    details.public-legs[open] > summary {{
      border-bottom-color: var(--line);
      box-shadow: 0 8px 18px rgba(50, 33, 15, 0.08);
    }}
    .collapse-copy {{
      display: grid;
      gap: 4px;
    }}
    .collapse-copy strong {{
      font-size: 1.05rem;
    }}
    .collapse-hint {{
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .public-legs-list {{
      display: grid;
      gap: 16px;
      padding: 18px;
    }}
    .public-leg-card {{
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(255,255,255,0.72);
      overflow: hidden;
    }}
    .public-leg-header {{
      display: grid;
      gap: 8px;
      padding: 18px;
      background: rgba(255, 248, 239, 0.96);
      cursor: pointer;
      list-style: none;
    }}
    .public-leg-header::-webkit-details-marker {{
      display: none;
    }}
    .public-leg-card[open] .public-leg-header {{
      border-bottom: 1px solid var(--line);
    }}
    .public-leg-headline {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
    }}
    .public-leg-summary-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
      align-items: center;
    }}
    .public-leg-tag {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(184,95,53,0.12);
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 700;
    }}
    .public-leg-meta {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.94rem;
    }}
    .public-leg-toggle {{
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 7px 11px;
      border-radius: 999px;
      background: rgba(29,36,48,0.06);
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 700;
    }}
    .toggle-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1rem;
      font-weight: 700;
    }}
    .public-leg-card[open] .toggle-icon,
    details.public-legs[open] .toggle-icon {{
      transform: translateY(-1px);
    }}
    .public-leg-card[open] .public-leg-toggle .toggle-icon::before,
    details.public-legs[open] > summary .toggle-icon::before {{
      content: "-";
    }}
    .public-leg-card:not([open]) .public-leg-toggle .toggle-icon::before,
    details.public-legs:not([open]) > summary .toggle-icon::before {{
      content: "+";
    }}
    .public-leg-body {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 18px;
      padding: 18px;
      align-items: start;
    }}
    .public-leg-copy {{
      display: grid;
      align-content: start;
      gap: 12px;
    }}
    .public-leg-caption {{
      font-size: 0.92rem;
    }}
    .public-leg-map {{
      order: -1;
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      background: #efe5d7;
    }}
    .public-leg-map .leg-map-frame {{
      width: 100%;
      min-height: 0;
      aspect-ratio: 16 / 9;
      height: auto;
    }}
    @media (max-width: 980px) {{
      .feature-grid,
      .public-leg-body,
      .story-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel hero">
      <div class="hero-copy">
        <span class="eyebrow">Published Trip</span>
        <h1>{title}</h1>
        <p>{summary}</p>
        <div class="meta-row">
          <span class="trip-chip">{trip_type}</span>
          <span class="trip-chip">{destination}</span>
          <span class="trip-chip muted">{timing}</span>
        </div>
      </div>
      <div><a class="button" href="/">Back to published trips</a></div>
    </section>

    <section class="panel">
      <h2>Trip details</h2>
      <p>This public page focuses on the story of the trip: where it went, how long it lasted, and how the journey unfolded.</p>
      <div class="story-grid">
        <article class="story-card">
          <span class="story-card-label">When</span>
          <div class="story-card-value">{short_timing}</div>
          <p class="story-card-note">{trip_duration} total</p>
        </article>
        <article class="story-card">
          <span class="story-card-label">Route</span>
          <div class="story-card-value">{public_origin} → {public_destination}</div>
          <p class="story-card-note">Start and finish from the published travel record.</p>
        </article>
        <article class="story-card">
          <span class="story-card-label">Travel modes</span>
          <div class="story-card-value">{escape(travel_modes)}</div>
          <p class="story-card-note">Based on inferred travel legs.</p>
        </article>
        <article class="story-card">
          <span class="story-card-label">Journey size</span>
          <div class="story-card-value">{leg_count} travel leg{"s" if leg_count != 1 else ""}</div>
          <p class="story-card-note">Expand the leg list below to browse the trip step by step.</p>
        </article>
      </div>
    </section>

    <section class="feature-grid">
      <article class="panel">
        <h2>Trip map</h2>
        <p>Published route preview built from the full inferred travel-leg path for the trip.</p>
        <div class="trip-map-static">{trip_map_markup}</div>
      </article>
    </section>

    <section class="panel">
      <h2>Travel legs</h2>
      <details class="public-legs">
        <summary>
          <span class="collapse-copy">
            <strong>{leg_count} travel leg{"s" if leg_count != 1 else ""}</strong>
            <span class="collapse-hint">Expand to browse the full journey. Each leg can also be opened individually.</span>
          </span>
          <span class="public-leg-toggle"><span class="toggle-icon"></span><span>Expand / Collapse</span></span>
        </summary>
        <div class="public-legs-list">
          {travel_leg_items}
        </div>
      </details>
    </section>
  </main>
</body>
</html>"""


@app.get("/trips/{trip_slug}", response_class=HTMLResponse)
def public_trip_detail_page(trip_slug: str) -> HTMLResponse:
    trip = trip_admin.get_public_trip_by_slug(trip_slug)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return _html_response(_render_public_trip_detail_page(trip))


def _trip_badge_class(value: str) -> str:
    normalized = value.lower()
    if normalized in {"confirmed", "published"}:
        return "good"
    if normalized in {"pending", "needs_review"}:
        return "warn"
    if normalized in {"ignored", "rejected"}:
        return "muted"
    return ""


def _trip_badge_label(value: str) -> str:
    normalized = value.lower()
    if normalized in {"confirmed", "published"}:
        return "Reviewed"
    if normalized in {"pending", "needs_review"}:
        return "Needs review"
    if normalized in {"ignored", "rejected"}:
        return "Not a trip"
    return value.replace("_", " ")


def _render_trip_badges(trip: dict) -> str:
    status = trip["status"]
    review = trip["review_decision"]
    preferred = review or status
    if status == "published":
        preferred = "published"
    label = _trip_badge_label(preferred)
    badge_class = _trip_badge_class(preferred)
    return f'<span class="badge {badge_class}">{escape(label)}</span>'


def _build_trip_toast(saved: Union[bool, str]) -> str:
    if not saved:
        return ""
    saved_key = "review" if saved is True else str(saved)
    messages = {
        "review": "Review saved.",
        "published": "Trip published and marked ready.",
        "privacy": "Trip visibility updated.",
        "segment": "Travel leg saved.",
        "details": "Trip details saved.",
    }
    message = messages.get(saved_key, "Update saved.")
    return f"""
    <div class="toast toast-success" role="status" aria-live="polite" data-toast>
      <strong>{escape(message)}</strong>
    </div>
    """


def _travel_leg_comment(item: dict) -> str:
    comment = item.get("segment_summary") or f"{item['label']} inferred from timeline activity data."
    return comment.rstrip(".")


def _public_leg_base_comment(item: dict) -> str:
    comment = _travel_leg_comment(item)
    return re.sub(r"\s+\(\d+\)$", "", comment).strip()


def _merge_public_leg_path_points(target: dict, source_points: Optional[List[dict]]) -> None:
    for point in source_points or []:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        candidate = {"lat": float(lat), "lon": float(lon)}
        if not target["path_points"] or target["path_points"][-1] != candidate:
            target["path_points"].append(candidate)


def _should_merge_public_story_legs(left: dict, right: dict) -> bool:
    if left.get("leg_type") != right.get("leg_type"):
        return False
    left_start = (left.get("start_place_name") or "").strip()
    left_end = (left.get("end_place_name") or "").strip()
    right_start = (right.get("start_place_name") or "").strip()
    right_end = (right.get("end_place_name") or "").strip()
    title_match = _public_leg_base_comment(left) == _public_leg_base_comment(right)
    route_match = bool(left_start and left_end and right_start and right_end) and (
        left_start == right_start and left_end == right_end
    )
    if not (title_match or route_match):
        return False
    left_end_time = left.get("end_time")
    right_start_time = right.get("start_time")
    if not left_end_time or not right_start_time:
        return False
    gap = right_start_time - left_end_time
    return timedelta(0) <= gap <= timedelta(minutes=45)


def _coalesce_public_story_legs(travel_legs: List[dict]) -> List[dict]:
    merged: List[dict] = []
    for item in travel_legs:
        leg = {
            **item,
            "path_points": [dict(point) for point in item.get("path_points", [])],
        }
        leg["segment_summary"] = _public_leg_base_comment(leg)
        if merged and _should_merge_public_story_legs(merged[-1], leg):
            previous = merged[-1]
            if leg.get("end_time") and leg["end_time"] > previous["end_time"]:
                previous["end_time"] = leg["end_time"]
            if leg.get("end_latitude") is not None:
                previous["end_latitude"] = leg["end_latitude"]
            if leg.get("end_longitude") is not None:
                previous["end_longitude"] = leg["end_longitude"]
            if leg.get("end_place_name"):
                previous["end_place_name"] = leg["end_place_name"]
            _merge_public_leg_path_points(previous, leg.get("path_points"))
            continue
        merged.append(leg)
    return merged


def _trip_review_state(trip: dict) -> Optional[str]:
    review = (trip.get("review_decision") or "").lower()
    status = (trip.get("status") or "").lower()
    if review == "confirmed" or status == "published":
        return "yes"
    if review in {"rejected", "ignored"} or status == "ignored":
        return "no"
    return None


def _trip_visibility_state(trip: dict) -> Optional[str]:
    if trip.get("is_private"):
        return "private"
    if trip.get("publish_ready"):
        return "public"
    return None


START_MARKER_STYLES = {
    "airport": {"fill": "#2f6cb3", "label": "AIR"},
    "fuel": {"fill": "#cc6b2c", "label": "GAS"},
    "park": {"fill": "#2f6c5b", "label": "REC"},
    "camp": {"fill": "#587d32", "label": "CAMP"},
    "lodging": {"fill": "#7b4da3", "label": "HOTEL"},
    "food": {"fill": "#b34747", "label": "FOOD"},
    "parking": {"fill": "#6e7886", "label": "P"},
    "school": {"fill": "#7a5a30", "label": "SCH"},
    "default": {"fill": "#c8643b", "label": "START"},
}


def _start_marker_kind_for_leg(item: dict) -> str:
    place_type = (item.get("start_place_type") or "").strip().lower()
    place_name = (item.get("start_place_name") or "").strip().lower()
    if "airport" in place_name or place_type in {"aerodrome", "airport", "terminal"}:
        return "airport"
    if place_type in {"fuel"} or any(token in place_name for token in ("gas", "fuel", "truck stop")):
        return "fuel"
    if place_type in {"parking"} or "parking" in place_name:
        return "parking"
    if place_type in {"camp_site", "caravan_site"} or "camp" in place_name:
        return "camp"
    if place_type in {"hotel"} or any(token in place_name for token in ("hotel", "inn", "lodge", "resort")):
        return "lodging"
    if place_type in {"fast_food", "restaurant", "cafe", "picnic_site"} or any(token in place_name for token in ("restaurant", "cafe", "diner", "domino", "pizza")):
        return "food"
    if place_type in {"viewpoint", "park", "information", "path", "footway", "track", "picnic_site"} or any(
        token in place_name for token in ("trail", "park", "viewpoint", "overlook", "visitor center", "rec", "recreation")
    ):
        return "park"
    if place_type in {"school", "university"}:
        return "school"
    return "default"


def _render_route_start_marker(x: float, y: float, marker_kind: str) -> str:
    marker = START_MARKER_STYLES.get(marker_kind, START_MARKER_STYLES["default"])
    label = marker["label"]
    fill = marker["fill"]
    font_size = 7 if len(label) > 3 else 8
    label_y = -7 if len(label) > 1 else -6
    return f"""
    <g data-marker-kind="{escape(marker_kind)}" transform="translate({x} {y})">
      <path d="M0 -18 C10 -18 17 -11 17 -2 C17 11 3 24 0 27 C-3 24 -17 11 -17 -2 C-17 -11 -10 -18 0 -18 Z"
        fill="{fill}" stroke="#fff8ef" stroke-width="4" />
      <circle cx="0" cy="-7" r="8.8" fill="#fff8ef" opacity="0.96" />
      <text x="0" y="{label_y}" text-anchor="middle" font-family="Arial, sans-serif" font-size="{font_size}" font-weight="700" fill="{fill}">{escape(label)}</text>
    </g>
    """


def _review_step_hint(review_state: Optional[str]) -> str:
    if review_state == "yes":
        return "Review complete. Choose whether this trip should stay private or be visible on the public site."
    if review_state == "no":
        return "Marked as not a trip. Public visibility is disabled for rejected items."
    return "Start by answering Yes or No. Visibility becomes available only after the trip is reviewed."


def _format_duration(start: datetime, end: datetime) -> str:
    total_seconds = max(int((end - start).total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _render_route_map_preview(
    raw_points: Optional[List[dict[str, float]]] = None,
    *,
    start_latitude: Optional[float] = None,
    start_longitude: Optional[float] = None,
    end_latitude: Optional[float] = None,
    end_longitude: Optional[float] = None,
    start_marker_kind: str = "default",
    aria_label: str = "Route map preview",
) -> str:
    points: list[tuple[float, float]] = []
    for point in raw_points or []:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        points.append((float(lat), float(lon)))
    if not points:
        if start_latitude is not None and start_longitude is not None:
            points.append((float(start_latitude), float(start_longitude)))
        if end_latitude is not None and end_longitude is not None:
            end_point = (float(end_latitude), float(end_longitude))
            if not points or points[-1] != end_point:
                points.append(end_point)
    if not points:
        return '<div class="leg-map-empty">No route preview available.</div>'

    width = 640.0
    height = 360.0
    padding = 26.0
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_span = max(max_lat - min_lat, 0.0001)
    lon_span = max(max_lon - min_lon, 0.0001)

    def pick_zoom() -> int:
        span = max(lat_span, lon_span)
        if span > 30:
            return 4
        if span > 15:
            return 5
        if span > 6:
            return 6
        if span > 2.5:
            return 7
        if span > 0.9:
            return 8
        if span > 0.3:
            return 9
        if span > 0.1:
            return 10
        if span > 0.03:
            return 11
        if span > 0.01:
            return 12
        return 14

    def project(lat: float, lon: float, zoom: int) -> tuple[float, float]:
        lat = max(min(lat, 85.0511), -85.0511)
        scale = 256 * (2**zoom)
        x = (lon + 180.0) / 360.0 * scale
        lat_rad = math.radians(lat)
        y = (
            1
            - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi
        ) / 2 * scale
        return x, y

    zoom = pick_zoom()
    projected = [project(lat, lon, zoom) for lat, lon in points]
    min_px = min(point[0] for point in projected)
    max_px = max(point[0] for point in projected)
    min_py = min(point[1] for point in projected)
    max_py = max(point[1] for point in projected)
    tile_size = 256
    margin = 48
    tile_min_x = int(math.floor((min_px - margin) / tile_size))
    tile_max_x = int(math.floor((max_px + margin) / tile_size))
    tile_min_y = int(math.floor((min_py - margin) / tile_size))
    tile_max_y = int(math.floor((max_py + margin) / tile_size))
    max_tile_index = (2**zoom) - 1
    tile_min_x = max(0, min(tile_min_x, max_tile_index))
    tile_max_x = max(0, min(tile_max_x, max_tile_index))
    tile_min_y = max(0, min(tile_min_y, max_tile_index))
    tile_max_y = max(0, min(tile_max_y, max_tile_index))
    tile_count_x = max(1, tile_max_x - tile_min_x + 1)
    tile_count_y = max(1, tile_max_y - tile_min_y + 1)
    map_pixel_width = tile_count_x * tile_size
    map_pixel_height = tile_count_y * tile_size

    def scale(point: tuple[float, float]) -> tuple[float, float]:
        pixel_x, pixel_y = project(point[0], point[1], zoom)
        x = ((pixel_x - (tile_min_x * tile_size)) / map_pixel_width) * width
        y = ((pixel_y - (tile_min_y * tile_size)) / map_pixel_height) * height
        x = min(max(x, padding), width - padding)
        y = min(max(y, padding), height - padding)
        return round(x, 2), round(y, 2)

    scaled = [scale(point) for point in points]
    path_d = " ".join(
        f"{'M' if index == 0 else 'L'} {x} {y}" for index, (x, y) in enumerate(scaled)
    )
    start_x, start_y = scaled[0]
    end_x, end_y = scaled[-1]
    tiles = []
    for tile_x in range(tile_min_x, tile_max_x + 1):
        for tile_y in range(tile_min_y, tile_max_y + 1):
            left = ((tile_x - tile_min_x) * tile_size / map_pixel_width) * width
            top = ((tile_y - tile_min_y) * tile_size / map_pixel_height) * height
            tile_width = (tile_size / map_pixel_width) * width
            tile_height = (tile_size / map_pixel_height) * height
            tiles.append(
                f'<img class="leg-map-tile" src="https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png" '
                f'alt="" loading="lazy" style="left:{(left / width) * 100:.4f}%;top:{(top / height) * 100:.4f}%;'
                f'width:{(tile_width / width) * 100:.4f}%;height:{(tile_height / height) * 100:.4f}%;">'
            )
    start_marker = _render_route_start_marker(start_x, start_y, start_marker_kind)
    return f"""
    <div class="leg-map-frame" role="img" aria-label="{escape(aria_label)}">
      {''.join(tiles)}
      <svg class="leg-map-svg" viewBox="0 0 {int(width)} {int(height)}">
        <rect x="0" y="0" width="{int(width)}" height="{int(height)}" rx="22" fill="rgba(255,248,239,0.08)" />
        <path d="{path_d}" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="14" stroke-linecap="round" stroke-linejoin="round" />
        <path d="{path_d}" fill="none" stroke="#2f6c5b" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" />
        {start_marker}
        <circle cx="{end_x}" cy="{end_y}" r="11" fill="#fff8ef" stroke="#2f6c5b" stroke-width="6" />
      </svg>
      <div class="leg-map-legend">
        <span><i class="legend-dot legend-start"></i>Start</span>
        <span><i class="legend-dot legend-end"></i>End</span>
      </div>
    </div>
    """


def _render_leg_map_preview(item: dict) -> str:
    return _render_route_map_preview(
        item.get("path_points") or [],
        start_latitude=item.get("start_latitude"),
        start_longitude=item.get("start_longitude"),
        end_latitude=item.get("end_latitude"),
        end_longitude=item.get("end_longitude"),
        start_marker_kind=_start_marker_kind_for_leg(item),
        aria_label="Travel leg map preview",
    )


def _button_class(*names: str) -> str:
    classes = ["button", *names]
    return " ".join(part for part in classes if part)


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
        score_value = "n/a" if trip["confidence_score"] is None else str(trip["confidence_score"])
        detail_href = f"/admin/trip/{trip['id']}"
        json_href = f"/admin/trips/{trip['id']}"
        badges_html = _render_trip_badges(trip)

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
                {badges_html}
              </div>
              <p class="trip-range">{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}</p>
              <p class="trip-summary">{escape(trip['summary_text'] or 'No summary yet. Use review actions or future UI tools to enrich this trip.')}</p>
              <div class="card-actions">
                <a class="{_button_class('primary', 'button-sm')}" href="{detail_href}">Open detail page</a>
                <a class="utility-link" href="{json_href}">JSON</a>
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
    .button, button {{
      display: inline-block;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
      border-radius: 999px;
      padding: 12px 18px;
      font: inherit;
      cursor: pointer;
    }}
    .button.primary, button.primary {{
      background: var(--accent);
      color: white;
    }}
    .button.utility {{
      border-color: var(--line);
    }}
    .button-sm {{
      padding: 10px 14px;
      font-size: 0.92rem;
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
    .card-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
    }}
    .utility-link {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
      font-size: 0.92rem;
      opacity: 0.86;
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
        <a class="button" href="/admin/overrides">Destination overrides</a>
        <a class="button" href="/">Homepage</a>
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


def _parse_flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _query_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _get_local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_user_timezone())
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Chicago")


def _format_local_datetime(value: datetime) -> str:
    local_value = value.astimezone(_get_local_zone())
    return local_value.strftime("%Y-%m-%d %I:%M %p %Z")


def _matching_overrides_for_trip(trip: dict) -> list[dict]:
    destination_name = (trip.get("primary_destination_name") or "").lower()
    timeline_haystack = " ".join(
        (item.get("timeline_label") or item.get("event_type") or "").lower()
        for item in trip.get("timeline", [])
    )
    matches = []
    for override in destination_overrides.list_overrides():
        pattern = (override.get("match_pattern") or "").strip().lower()
        if pattern and (pattern in destination_name or pattern in timeline_haystack):
            matches.append(override)
    return matches


def _render_overrides_page(overrides: List[dict], *, return_to: str = "") -> str:
    return_target = return_to or "/admin"
    return_query = urlencode({"return_to": return_target}) if return_target else ""
    form_suffix = f"?{return_query}" if return_query else ""
    rows = "".join(
        f"""
        <tr>
          <td>{escape(item['rule_name'])}</td>
          <td>{escape(item['classification'])}</td>
          <td>{'keep' if item['keep_trip'] else 'ignore' if item['ignore_trip'] else 'classify'}</td>
          <td>{escape(item['match_pattern'] or '')}</td>
          <td>{'' if item['latitude'] is None else escape(f"{item['latitude']:.5f}, {item['longitude']:.5f}")}</td>
          <td>{escape(str(item['radius_meters']))}</td>
          <td>
            <form method="post" action="/admin/overrides/delete">
              <input type="hidden" name="override_id" value="{item['id']}">
              <input type="hidden" name="return_to" value="{escape(return_target)}">
              <button class="danger" type="submit">Delete</button>
            </form>
          </td>
        </tr>
        """
        for item in overrides
    ) or """
      <tr>
        <td colspan="7">No overrides yet. Add one only when the automatic flow gets a destination wrong.</td>
      </tr>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Destination Overrides · MilesMemories</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: #fff9f0;
      --line: #dcccb4;
      --ink: #182233;
      --muted: #657286;
      --accent: #b85f35;
      --danger: #962f24;
      --shadow: rgba(37, 28, 14, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background: linear-gradient(180deg, #e8d6c0, #f3efe7 28%, #faf7f1 100%);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 34px 18px 64px;
      display: grid;
      gap: 18px;
    }}
    .panel {{
      background: rgba(255, 249, 240, 0.94);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 24px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
    }}
    .eyebrow {{
      color: var(--accent);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-size: 0.8rem;
      margin-bottom: 10px;
    }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    .links {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .button, button {{
      border-radius: 999px;
      padding: 12px 16px;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    .button.primary, button.primary {{
      background: var(--accent);
      color: white;
    }}
    .danger {{
      background: transparent;
      color: var(--danger);
      border-color: var(--danger);
    }}
    .form-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      align-items: end;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: #fffdf8;
      font: inherit;
      color: var(--ink);
    }}
    .checks {{
      display: flex;
      gap: 18px;
      align-items: center;
      padding-bottom: 10px;
    }}
    .checks label {{
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 14px 10px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    form.inline {{ display: inline; }}
    @media (max-width: 920px) {{
      .topbar, .form-grid {{ grid-template-columns: 1fr; display: grid; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      td {{ border-top: 0; padding: 8px 0; }}
      tr {{ border-top: 1px solid var(--line); padding: 12px 0; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel topbar">
      <div>
        <div class="eyebrow">Destination Overrides</div>
        <h1>Automation first, intervention when needed.</h1>
        <p>Use overrides sparingly. Add ignore rules for recurring amateur venues the detector misses, or keep rules for destinations that should never be suppressed.</p>
      </div>
      <div class="links">
        <a class="button" href="{escape(return_target)}">Back</a>
        <a class="button" href="/">Homepage</a>
      </div>
    </section>

    <section class="panel">
      <h2>Add override</h2>
      <form method="post" action="/admin/overrides/create{form_suffix}" class="form-grid">
        <label>Rule name
          <input type="text" name="rule_name" required>
        </label>
        <label>Classification
          <select name="classification">
            <option value="amateur_sports_venue">amateur_sports_venue</option>
            <option value="pro_sports_venue">pro_sports_venue</option>
            <option value="custom_destination">custom_destination</option>
          </select>
        </label>
        <label>Match pattern
          <input type="text" name="match_pattern" placeholder="rec plex">
        </label>
        <label>Latitude
          <input type="text" name="latitude" placeholder="38.7548">
        </label>
        <label>Longitude
          <input type="text" name="longitude" placeholder="-90.4668">
        </label>
        <label>Radius meters
          <input type="number" name="radius_meters" min="1" value="1000">
        </label>
        <div class="checks">
          <label><input type="checkbox" name="keep_trip" value="true"> Keep trip</label>
          <label><input type="checkbox" name="ignore_trip" value="true"> Ignore trip</label>
        </div>
        <div>
          <button class="primary" type="submit">Save override</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <h2>Current rules</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Classification</th>
            <th>Effect</th>
            <th>Pattern</th>
            <th>Coordinates</th>
            <th>Radius</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>"""


def _render_trip_destination_page(trip: dict, *, return_to: str) -> str:
    destination = escape(trip["primary_destination_name"] or "Destination pending")
    title = escape(trip["trip_name"] or "Untitled trip")
    override_items = "".join(
        f"""
        <li class="count-item">
          <strong>{escape(item['rule_name'])}</strong>
          <span>{'keep' if item['keep_trip'] else 'ignore' if item['ignore_trip'] else escape(item['classification'])}</span>
        </li>
        """
        for item in trip.get("matching_overrides", [])
    ) or """
      <li class="count-item">
        <strong>No matching overrides</strong>
        <span>auto only</span>
      </li>
    """
    safe_return = escape(return_to)
    overrides_href = f"/admin/overrides?{urlencode({'return_to': return_to})}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Destination context · {title}</title>
  <style>
    :root {{
      --bg: #f2eee6;
      --panel: #fff8ef;
      --line: #dbcab1;
      --ink: #1b2433;
      --muted: #647084;
      --accent: #b85f35;
      --shadow: rgba(37, 28, 14, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(184,95,53,0.14), transparent 26%),
        linear-gradient(180deg, #e7d3bc, var(--bg) 30%, #faf7f1 100%);
    }}
    main {{ max-width: 980px; margin: 0 auto; padding: 34px 18px 64px; display: grid; gap: 18px; }}
    .panel {{
      background: rgba(255, 248, 239, 0.94);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 24px;
    }}
    .eyebrow {{ color: var(--accent); letter-spacing: 0.14em; text-transform: uppercase; font-size: 0.8rem; margin-bottom: 10px; }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }}
    .button {{
      display: inline-block;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
      border-radius: 999px;
      padding: 12px 16px;
    }}
    .button.primary {{ background: var(--accent); color: white; }}
    .list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }}
    .count-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
      background: rgba(255,255,255,0.5);
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <div class="eyebrow">Destination Context</div>
      <h1>{destination}</h1>
      <p>Context for <strong>{title}</strong>. Use this view to inspect destination naming and override matches, then jump back to the trip review page.</p>
      <div class="actions">
        <a class="button" href="{safe_return}">Back to trip detail</a>
        <a class="button" href="{overrides_href}">Manage overrides</a>
      </div>
    </section>

    <section class="panel">
      <h2>Destination signals</h2>
      <ul class="list">
        <li class="count-item">
          <strong>Primary destination</strong>
          <span>{destination}</span>
        </li>
        <li class="count-item">
          <strong>Matching overrides</strong>
          <span>{len(trip.get("matching_overrides", []))}</span>
        </li>
      </ul>
    </section>

    <section class="panel">
      <h2>Override matches</h2>
      <ul class="list">
        {override_items}
      </ul>
    </section>
  </main>
</body>
</html>"""


def _render_trip_detail_page(trip: dict, *, saved: Union[bool, str] = False) -> str:
    title = escape(trip["trip_name"] or "Untitled trip")
    destination = escape(trip["primary_destination_name"] or "Destination pending")
    trip_type = escape(trip["trip_type"] or "untyped")
    summary = escape(trip["summary_text"] or "No summary yet for this trip.")
    confidence = "n/a" if trip["confidence_score"] is None else str(trip["confidence_score"])
    map_points = [
        {
            "lat": item["latitude"],
            "lon": item["longitude"],
            "label": item["timeline_label"] or item["event_type"],
            "time": str(item["event_time"]),
        }
        for item in trip["timeline"]
        if item.get("latitude") is not None and item.get("longitude") is not None
    ]
    if not map_points:
        for index, item in enumerate(travel_legs, start=1):
            path_points = item.get("path_points") or []
            if path_points:
                for point in path_points:
                    lat = point.get("lat")
                    lon = point.get("lon")
                    if lat is None or lon is None:
                        continue
                    map_points.append(
                        {
                            "lat": lat,
                            "lon": lon,
                            "label": item.get("segment_summary") or item.get("label") or f"Leg {index}",
                            "time": str(item.get("start_time") or ""),
                        }
                    )
                continue
            start_lat = item.get("start_latitude")
            start_lon = item.get("start_longitude")
            end_lat = item.get("end_latitude")
            end_lon = item.get("end_longitude")
            if start_lat is not None and start_lon is not None:
                map_points.append(
                    {
                        "lat": start_lat,
                        "lon": start_lon,
                        "label": item.get("segment_summary") or item.get("label") or f"Leg {index}",
                        "time": str(item.get("start_time") or ""),
                    }
                )
            if end_lat is not None and end_lon is not None:
                end_point = {
                    "lat": end_lat,
                    "lon": end_lon,
                    "label": item.get("segment_summary") or item.get("label") or f"Leg {index}",
                    "time": str(item.get("end_time") or ""),
                }
                if not map_points or (
                    map_points[-1]["lat"] != end_point["lat"] or map_points[-1]["lon"] != end_point["lon"]
                ):
                    map_points.append(end_point)
    trip_map_markup = _render_route_map_preview(
        [{"lat": point["lat"], "lon": point["lon"]} for point in map_points],
        aria_label="Trip route map preview",
    )
    travel_legs = trip.get("travel_legs", [])

    timeline_items = "".join(
        f"""
        <li class="timeline-item">
          <div class="timeline-time">{escape(_format_local_datetime(item['event_time']))}</div>
          <div>
            <strong>{escape(item['timeline_label'] or item['event_type'])}</strong>
            <p>{escape(item['event_type'])} · ref {escape(str(item['event_ref_id']))}{f" · {item['latitude']:.5f}, {item['longitude']:.5f}" if item.get('latitude') is not None and item.get('longitude') is not None else ""}</p>
          </div>
        </li>
        """
        for item in trip["timeline"]
    ) or """
      <li class="timeline-item">
        <div>
          <strong>No timeline events yet.</strong>
          <p>Run trip detection and inspect linked events once source data is available.</p>
        </div>
      </li>
    """

    history_items = "".join(
        f"""
        <li class="history-item">
          <strong>{escape(item['review_action'])}</strong>
          <p>{escape(item['reviewer_name'] or 'Unknown reviewer')} · {escape(str(item['reviewed_at']))}</p>
          <p>{escape(item['review_notes'] or 'No notes recorded.')}</p>
        </li>
        """
        for item in trip["review_history"]
    ) or """
      <li class="history-item">
        <strong>No review history yet.</strong>
        <p>This trip has not been reviewed.</p>
      </li>
    """

    count_items = "".join(
        f"""
        <li class="count-item">
          <strong>{escape(item['event_type'])}</strong>
          <span>{escape(str(item['total']))}</span>
        </li>
        """
        for item in trip["event_counts"]
    ) or """
      <li class="count-item">
        <strong>No linked event counts</strong>
        <span>0</span>
      </li>
    """

    travel_leg_items = "".join(
        f"""
        <li class="leg-item">
          <form class="segment-form leg-form" method="post" action="/admin/trip/{trip['id']}/segments/{item['segment_id']}" data-autosave="segment">
          <details class="leg-collapse">
            <summary>
              <span class="leg-summary-copy">
                <span class="leg-heading-row">
                  <span class="leg-kind">{escape(_travel_leg_comment(item))}</span>
                  <span class="leg-tag">{escape(item['label'])}</span>
                </span>
                <span class="leg-meta">{escape(_format_local_datetime(item['start_time']))} → {escape(_format_local_datetime(item['end_time']))} ({escape(_format_duration(item['start_time'], item['end_time']))})</span>
              </span>
            </summary>
            <div class="leg-body">
              <div class="leg-copy">
                <label class="leg-field">
                  <span>Summary</span>
                  <textarea class="leg-summary-input" name="summary_text" rows="2">{escape(_travel_leg_comment(item))}</textarea>
                </label>
                <label class="star-rating-field">
                  <span>Rating</span>
                  <span class="star-rating" aria-label="Rating">
                    <input type="radio" id="segment-{item['segment_id']}-star-5" name="rating" value="5"{" checked" if item.get('segment_rating') == 5 else ""}>
                    <label for="segment-{item['segment_id']}-star-5" title="5 stars">★</label>
                    <input type="radio" id="segment-{item['segment_id']}-star-4" name="rating" value="4"{" checked" if item.get('segment_rating') == 4 else ""}>
                    <label for="segment-{item['segment_id']}-star-4" title="4 stars">★</label>
                    <input type="radio" id="segment-{item['segment_id']}-star-3" name="rating" value="3"{" checked" if item.get('segment_rating') == 3 else ""}>
                    <label for="segment-{item['segment_id']}-star-3" title="3 stars">★</label>
                    <input type="radio" id="segment-{item['segment_id']}-star-2" name="rating" value="2"{" checked" if item.get('segment_rating') == 2 else ""}>
                    <label for="segment-{item['segment_id']}-star-2" title="2 stars">★</label>
                    <input type="radio" id="segment-{item['segment_id']}-star-1" name="rating" value="1"{" checked" if item.get('segment_rating') == 1 else ""}>
                    <label for="segment-{item['segment_id']}-star-1" title="1 star">★</label>
                    <input class="star-rating-clear" type="radio" id="segment-{item['segment_id']}-star-0" name="rating" value=""{" checked" if item.get('segment_rating') is None else ""}>
                  </span>
                </label>
                <p class="leg-source">Source activity: {escape(item.get('source_event_id') or 'unknown')}</p>
              </div>
              <div class="leg-map-panel">
                <div
                  class="leg-map"
                  data-start-lat="{'' if item.get('start_latitude') is None else item['start_latitude']}"
                  data-start-lon="{'' if item.get('start_longitude') is None else item['start_longitude']}"
                  data-end-lat="{'' if item.get('end_latitude') is None else item['end_latitude']}"
                  data-end-lon="{'' if item.get('end_longitude') is None else item['end_longitude']}"
                  data-path="{escape(json.dumps(item.get('path_points') or []))}"
                >{_render_leg_map_preview(item)}</div>
              </div>
            </div>
          </details>
          </form>
        </li>
        """
        for item in travel_legs
    ) or """
      <li class="leg-item">
        <div>
          <strong>No travel legs inferred.</strong>
          <p>This trip currently only has raw location points linked.</p>
        </div>
      </li>
    """

    toast_markup = _build_trip_toast(saved)
    detail_badges = _render_trip_badges(trip)
    review_state = _trip_review_state(trip)
    visibility_state = _trip_visibility_state(trip)
    visibility_enabled = review_state == "yes"
    review_hint = _review_step_hint(review_state)
    return_to = f"/admin/trip/{trip['id']}"
    destination_href = f"/admin/trip/{trip['id']}/destination-context?{urlencode({'return_to': return_to})}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · MilesMemories</title>
  <link rel="stylesheet" href="/static/leaflet/leaflet.css">
  <style>
    :root {{
      --bg: #f2eee6;
      --panel: #fff8ef;
      --line: #dbcab1;
      --ink: #1b2433;
      --muted: #647084;
      --accent: #b85f35;
      --shadow: rgba(37, 28, 14, 0.12);
      --good: #2e6a4b;
      --warn: #9b641d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(184,95,53,0.14), transparent 26%),
        linear-gradient(180deg, #e7d3bc, var(--bg) 30%, #faf7f1 100%);
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 34px 18px 64px;
    }}
    .stack {{
      display: grid;
      gap: 18px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 18px;
    }}
    .hero.single-panel {{
      grid-template-columns: 1fr;
    }}
    .panel {{
      background: rgba(255, 248, 239, 0.94);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 24px;
    }}
    .eyebrow {{
      color: var(--accent);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      font-size: 0.8rem;
      margin-bottom: 10px;
    }}
    .sr-only {{
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }}
    h1, h2, h3 {{ margin: 0 0 12px; }}
    h1 {{ font-size: clamp(2.2rem, 5vw, 4.4rem); line-height: 0.98; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    .meta-row, .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }}
    .badge {{
      display: inline-block;
      padding: 7px 10px;
      border-radius: 999px;
      background: #eee4d6;
      font-size: 0.86rem;
      text-transform: capitalize;
    }}
    .badge.good {{ background: rgba(46,106,75,0.14); color: var(--good); }}
    .badge.warn {{ background: rgba(155,100,29,0.14); color: var(--warn); }}
    .badge.muted {{ background: rgba(100,112,132,0.14); color: var(--muted); }}
    .button, button {{
      display: inline-block;
      text-decoration: none;
      border-radius: 999px;
      padding: 12px 16px;
      border: 1px solid var(--accent);
      color: var(--accent);
      background: transparent;
      font-weight: 700;
      font: inherit;
      cursor: pointer;
    }}
    .button.primary, button.primary {{
      color: white;
      background: var(--accent);
    }}
    .button.utility {{
      border-color: var(--line);
    }}
    button:hover, .button:hover {{
      filter: brightness(0.98);
    }}
    button:focus-visible, .button:focus-visible, details > summary:focus-visible {{
      outline: 2px solid rgba(184, 95, 53, 0.35);
      outline-offset: 3px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 0.85fr 1.15fr;
      gap: 18px;
    }}
    .two-up {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
    }}
    .map-shell {{
      overflow: hidden;
      padding: 0;
    }}
    .map-copy {{
      padding: 24px 24px 0;
    }}
    .trip-map-static {{
      border-top: 1px solid var(--line);
      padding: 18px 24px 24px;
      background: #efe5d7;
    }}
    .trip-map-static .leg-map-frame {{
      max-width: 100%;
      min-height: 420px;
    }}
    .list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 12px;
    }}
    .count-item, .timeline-item, .history-item {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
      background: rgba(255,255,255,0.5);
    }}
    .leg-item {{
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.5);
      overflow: hidden;
    }}
    .count-item {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .detail-grid strong {{
      display: block;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .detail-cell {{
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.5);
    }}
    .detail-cell.wide {{
      grid-column: 1 / -1;
    }}
    .timeline-item {{
      display: grid;
      grid-template-columns: 220px 1fr;
      gap: 14px;
    }}
    .review-form {{
      display: grid;
      gap: 14px;
    }}
    .trip-overview-form {{
      display: grid;
      gap: 18px;
    }}
    .hero-title-field,
    .hero-summary-field {{
      gap: 0;
    }}
    .hero-title-input {{
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      font-size: clamp(2.1rem, 4.1vw, 4.2rem);
      line-height: 1.02;
      font-weight: 700;
      color: var(--ink);
      box-shadow: none;
    }}
    .hero-summary-field textarea {{
      min-height: 80px;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.5;
      box-shadow: none;
      resize: vertical;
    }}
    .quick-actions {{
      display: grid;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .action-group {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    .action-group-label {{
      min-width: 78px;
      font-size: 0.86rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .quick-actions button {{
      min-width: 138px;
    }}
    .segmented-control {{
      display: inline-flex;
      align-items: stretch;
      border: 1px solid var(--accent);
      border-radius: 999px;
      overflow: hidden;
      background: rgba(255,255,255,0.82);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.35);
    }}
    .segmented-control button {{
      min-width: 132px;
      border: 0;
      border-right: 1px solid rgba(184,95,53,0.22);
      border-radius: 0;
      margin: 0;
      background: transparent;
      color: var(--accent);
      box-shadow: none;
    }}
    .segmented-control button:last-child {{
      border-right: 0;
    }}
    .quick-actions button.is-current {{
      color: white;
      background: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(184,95,53,0.2);
    }}
    .segmented-control button:not(.is-current):hover {{
      background: rgba(184,95,53,0.08);
    }}
    .segmented-control button:disabled {{
      cursor: not-allowed;
      color: rgba(184,95,53,0.45);
      background: rgba(255,255,255,0.55);
      border-color: rgba(184,95,53,0.14);
      opacity: 0.8;
    }}
    .segmented-control button:disabled:hover {{
      background: rgba(255,255,255,0.55);
    }}
    .workflow-help {{
      margin-top: -4px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .review-form-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    input, textarea, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
      background: #fffdf8;
      font: inherit;
      color: var(--ink);
    }}
    textarea {{
      min-height: 120px;
      resize: vertical;
    }}
    .timeline-time {{
      color: var(--accent);
      font-weight: 700;
    }}
    details.timeline-collapse {{
      margin-top: 6px;
    }}
    details.timeline-collapse > summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--accent);
      list-style: none;
      margin-bottom: 14px;
      display: inline-block;
    }}
    details.timeline-collapse > summary::-webkit-details-marker {{
      display: none;
    }}
    details.timeline-collapse > summary::after {{
      content: "Show";
      margin-left: 10px;
      font-size: 0.88rem;
      color: var(--muted);
    }}
    details.timeline-collapse[open] > summary::after {{
      content: "Hide";
    }}
    details.leg-collapse > summary {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      cursor: pointer;
      padding: 16px 18px;
      list-style: none;
      font-weight: 700;
      background: rgba(255,255,255,0.28);
      transition: background 180ms ease, border-color 180ms ease;
    }}
    details.leg-collapse > summary::-webkit-details-marker {{
      display: none;
    }}
    .leg-form {{
      margin: 0;
    }}
    details.leg-collapse[open] {{
      background: rgba(255,255,255,0.72);
      box-shadow: inset 0 0 0 1px rgba(200,100,59,0.14), 0 14px 26px rgba(50, 33, 15, 0.08);
    }}
    details.leg-collapse[open] > summary {{
      background: linear-gradient(180deg, rgba(200,100,59,0.08), rgba(255,255,255,0.24));
      border-bottom: 1px solid rgba(216,201,179,0.9);
    }}
    .leg-summary-copy {{
      display: grid;
      gap: 6px;
      min-width: 0;
    }}
    .leg-heading-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      min-width: 0;
    }}
    .leg-kind {{
      color: var(--ink);
      font-size: 1.15rem;
      line-height: 1.25;
    }}
    .leg-tag {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(184,95,53,0.12);
      color: var(--accent);
      font-size: 0.82rem;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .leg-summary-input {{
      width: 100%;
      min-height: 74px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.92);
      color: var(--ink);
      font-size: 1rem;
      line-height: 1.4;
      resize: none;
    }}
    .leg-summary-input:focus {{
      outline: none;
      border-color: rgba(200,100,59,0.6);
      box-shadow: 0 0 0 3px rgba(200,100,59,0.12);
    }}
    .leg-meta {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.92rem;
    }}
    .leg-body {{
      padding: 18px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 16px;
      align-items: stretch;
    }}
    .leg-copy {{
      min-width: 0;
      display: grid;
      align-content: start;
      gap: 18px;
    }}
    .leg-field {{
      display: grid;
      gap: 8px;
    }}
    .leg-field > span,
    .star-rating-field > span:first-child {{
      color: var(--muted);
      font-weight: 600;
    }}
    .leg-source, .leg-comment {{
      color: var(--muted);
    }}
    .leg-map {{
      height: 100%;
      min-height: 320px;
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #efe5d7;
      overflow: hidden;
      position: relative;
    }}
    .leg-map-frame {{
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 320px;
      background: #efe5d7;
    }}
    .leg-map-tile {{
      position: absolute;
      display: block;
      max-width: none;
      user-select: none;
      pointer-events: none;
    }}
    .leg-map-panel {{
      display: flex;
      min-height: 320px;
    }}
    .leg-map-svg {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      display: block;
    }}
    .leg-map-legend {{
      position: absolute;
      left: 14px;
      bottom: 14px;
      display: inline-flex;
      gap: 12px;
      align-items: center;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255, 248, 239, 0.92);
      border: 1px solid rgba(219, 202, 177, 0.9);
      color: var(--ink);
      font-size: 0.82rem;
      font-weight: 600;
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .leg-map-legend span {{
      display: inline-flex;
      gap: 6px;
      align-items: center;
    }}
    .legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
      background: currentColor;
    }}
    .legend-start {{
      color: #c8643b;
    }}
    .legend-end {{
      color: #2f6c5b;
    }}
    .leg-map-empty {{
      display: grid;
      place-items: center;
      height: 100%;
      color: var(--muted);
      font-weight: 600;
    }}
    .leg-form[data-save-state="saving"] .leg-collapse[open] {{
      box-shadow: inset 0 0 0 1px rgba(39,93,79,0.2), 0 14px 26px rgba(50, 33, 15, 0.08);
    }}
    .leg-form[data-save-state="saved"] .leg-collapse[open] {{
      box-shadow: inset 0 0 0 1px rgba(39,93,79,0.32), 0 14px 26px rgba(50, 33, 15, 0.1);
    }}
    .detail-pair {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .detail-pair-item {{
      display: grid;
      gap: 4px;
    }}
    .detail-value-lg {{
      font-size: 1.22rem;
      line-height: 1.25;
    }}
    .segment-form {{
      display: grid;
      gap: 10px;
    }}
    .star-rating-field {{
      gap: 10px;
    }}
    .star-rating {{
      display: inline-flex;
      flex-direction: row-reverse;
      justify-content: flex-end;
      gap: 2px;
    }}
    .star-rating input {{
      position: absolute;
      opacity: 0;
      pointer-events: none;
      width: 1px;
      height: 1px;
    }}
    .star-rating label {{
      display: inline-block;
      gap: 0;
      font-size: 1.8rem;
      line-height: 1;
      color: #d8c9b3;
      cursor: pointer;
      width: auto;
    }}
    .star-rating label:hover,
    .star-rating label:hover ~ label,
    .star-rating input:checked ~ label {{
      color: var(--accent);
    }}
    .star-rating-clear {{
      display: none;
    }}
    .toast {{
      position: fixed;
      top: 18px;
      right: 18px;
      z-index: 1000;
      min-width: 260px;
      max-width: 360px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(46,106,75,0.24);
      background: rgba(244, 255, 248, 0.96);
      box-shadow: 0 18px 40px rgba(28, 43, 31, 0.18);
      transform: translateY(0);
      opacity: 1;
      transition: opacity 220ms ease, transform 220ms ease;
    }}
    .toast strong {{
      color: var(--good);
    }}
    .toast.is-hiding {{
      opacity: 0;
      transform: translateY(-8px);
      pointer-events: none;
    }}
    .trip-overview-form[data-save-state="saving"] {{
      box-shadow: inset 0 0 0 1px rgba(39,93,79,0.18);
      border-radius: 20px;
    }}
    .trip-overview-form[data-save-state="saved"] {{
      box-shadow: inset 0 0 0 1px rgba(39,93,79,0.28);
      border-radius: 20px;
    }}
    @media (max-width: 920px) {{
      .hero, .grid, .timeline-item, .two-up, .review-form-grid, .detail-grid {{
        grid-template-columns: 1fr;
      }}
      .detail-cell.wide {{
        grid-column: auto;
      }}
      .detail-pair {{
        grid-template-columns: 1fr;
      }}
      .action-group {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .action-group-label {{
        min-width: auto;
      }}
      details.leg-collapse > summary {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .leg-span {{
        text-align: left;
      }}
      .leg-body {{
        grid-template-columns: 1fr;
      }}
      .leg-summary-input {{
        min-height: 70px;
      }}
      .leg-map-panel {{
        min-height: 260px;
      }}
    }}
  </style>
</head>
<body>
  <main class="stack">
    {toast_markup}
    <section class="hero single-panel">
      <article class="panel">
        <div class="eyebrow">Trip Overview</div>
        <form class="trip-overview-form" method="post" action="/admin/trip/{trip['id']}/review">
          <label class="hero-title-field">
            <span class="sr-only">Trip name</span>
            <input class="hero-title-input" type="text" name="trip_name" value="{title}">
          </label>
          <label class="hero-summary-field">
            <span class="sr-only">Summary</span>
            <textarea name="summary_text">{escape(trip['summary_text'] or '')}</textarea>
          </label>
          <div class="meta-row">
            <span class="review-badge-slot">{detail_badges}</span>
            <span class="badge">Confidence {confidence}</span>
          </div>
          <div class="detail-grid">
            <div class="detail-cell wide">
              <strong>Trip timing</strong>
              <span class="detail-value-lg">{escape(_format_local_datetime(trip['start_time']))} → {escape(_format_local_datetime(trip['end_time']))}</span>
            </div>
            <div class="detail-cell">
              <strong>Trip type</strong>
              <span>{trip_type}</span>
            </div>
            <label class="detail-cell">
              <strong>Reviewer name</strong>
              <input type="text" name="reviewer_name" value="Venkat">
            </label>
            <label class="detail-cell wide">
              <strong>Review notes</strong>
              <textarea name="review_notes" placeholder="What changed? Why is this correct?"></textarea>
            </label>
          </div>
          <div class="workflow-help">{escape(review_hint)} Text edits autosave when you leave a field.</div>
          <div class="quick-actions">
            <div class="action-group">
              <span class="action-group-label">Review</span>
              <div class="segmented-control" role="group" aria-label="Review decision">
                <button class="button{' is-current' if review_state == 'yes' else ''}" type="submit" name="action" value="confirm" aria-pressed="{'true' if review_state == 'yes' else 'false'}">Yes</button>
                <button class="button{' is-current' if review_state == 'no' else ''}" type="submit" name="action" value="reject" aria-pressed="{'true' if review_state == 'no' else 'false'}">No</button>
              </div>
            </div>
            <div class="action-group">
              <span class="action-group-label">Visibility</span>
              <div class="segmented-control" role="group" aria-label="Visibility">
                <button class="button{' is-current' if visibility_state == 'public' else ''}" type="submit" name="action" value="publish" aria-pressed="{'true' if visibility_state == 'public' else 'false'}"{'' if visibility_enabled else ' disabled'}>Public</button>
                <button class="button{' is-current' if visibility_state == 'private' else ''}" type="submit" name="action" value="mark_private" aria-pressed="{'true' if visibility_state == 'private' else 'false'}"{'' if visibility_enabled else ' disabled'}>Private</button>
              </div>
            </div>
          </div>
        </form>
        <div class="actions">
          <a class="button" href="/admin">Back to queue</a>
          <a class="button" href="{destination_href}">Destination context</a>
          <a class="button utility" href="/admin/trips/{trip['id']}">Open JSON</a>
        </div>
      </article>
    </section>

    <section class="panel">
      <h2>Travel legs</h2>
      <ul class="list">
        {travel_leg_items}
      </ul>
    </section>

    <section class="panel map-shell">
      <div class="map-copy">
        <h2>Trip map</h2>
        <p>Linked trip coordinates are plotted in order so you can review the route shape and destination cluster.</p>
      </div>
      <div class="trip-map-static">{trip_map_markup}</div>
    </section>

    <section class="panel">
      <h2>Review history</h2>
      <ul class="list">
        {history_items}
      </ul>
    </section>

    <section class="panel">
      <h2>Timeline</h2>
      <details class="timeline-collapse">
        <summary>Expand full timeline ({len(trip["timeline"])} events)</summary>
        <ul class="list">
          {timeline_items}
        </ul>
      </details>
    </section>

    <section class="panel">
      <h2>Linked events</h2>
      <ul class="list">
        {count_items}
      </ul>
    </section>
  </main>
  <script>
    (function () {{
      document.querySelectorAll(".leg-summary-input").forEach((node) => {{
        ["click", "focus", "keydown", "mousedown", "mouseup"].forEach((eventName) => {{
          node.addEventListener(eventName, (event) => event.stopPropagation());
        }});
      }});

      const autosaveForm = async (form) => {{
        if (!form || form.dataset.saveState === "saving") {{
          return;
        }}
        const body = new URLSearchParams(new FormData(form));
        form.dataset.saveState = "saving";
        try {{
          const response = await fetch(form.action, {{
            method: "POST",
            headers: {{
              "X-Requested-With": "fetch"
            }},
            body
          }});
          if (!response.ok) {{
            throw new Error(`Save failed with ${{response.status}}`);
          }}
          form.dataset.saveState = "saved";
          const savedKey = form.dataset.savedKey;
          if (savedKey) {{
            const toastTitle = {{
              segment: "Travel leg saved.",
              details: "Trip details saved."
            }}[savedKey] || "Saved.";
            const existing = document.querySelector("[data-toast]");
            if (existing) {{
              existing.remove();
            }}
            const toast = document.createElement("div");
            toast.className = "toast";
            toast.dataset.toast = savedKey;
            toast.textContent = toastTitle;
            document.body.appendChild(toast);
            window.setTimeout(() => {{
              toast.classList.add("is-hiding");
              window.setTimeout(() => toast.remove(), 240);
            }}, 2200);
          }}
          window.setTimeout(() => {{
            if (form.dataset.saveState === "saved") {{
              delete form.dataset.saveState;
            }}
          }}, 1600);
        }} catch (error) {{
          delete form.dataset.saveState;
          console.error(error);
        }}
      }};

      document.querySelectorAll('form[data-autosave="segment"]').forEach((form) => {{
        form.dataset.savedKey = "segment";
        const summaryField = form.querySelector(".leg-summary-input");
        if (summaryField) {{
          summaryField.addEventListener("blur", () => autosaveForm(form));
        }}
        form.querySelectorAll('input[name="rating"]').forEach((field) => {{
          field.addEventListener("change", () => autosaveForm(form));
        }});
      }});

      const overviewForm = document.querySelector(".trip-overview-form");
      if (overviewForm) {{
        overviewForm.dataset.savedKey = "details";
        overviewForm
          .querySelectorAll('input[name="trip_name"], input[name="reviewer_name"], textarea[name="summary_text"], textarea[name="review_notes"]')
          .forEach((field) => {{
            field.addEventListener("blur", () => autosaveForm(overviewForm));
          }});

        overviewForm.querySelectorAll('.segmented-control button[name="action"]').forEach((button) => {{
          button.addEventListener("click", async (event) => {{
            event.preventDefault();
            if (overviewForm.dataset.saveState === "saving") {{
              return;
            }}
            const body = new URLSearchParams(new FormData(overviewForm));
            body.set("action", button.value);
            overviewForm.dataset.saveState = "saving";
            try {{
              const response = await fetch(overviewForm.action, {{
                method: "POST",
                headers: {{
                  "X-Requested-With": "fetch"
                }},
                body
              }});
              if (!response.ok) {{
                throw new Error(`Action failed with ${{response.status}}`);
              }}
              const payload = await response.json();
              overviewForm.dataset.saveState = "saved";
              const badgeSlot = overviewForm.querySelector(".review-badge-slot");
              if (badgeSlot && payload.badge_html) {{
                badgeSlot.innerHTML = payload.badge_html;
              }}
              const reviewButtons = overviewForm.querySelectorAll('[aria-label="Review decision"] button');
              reviewButtons.forEach((node) => {{
                const active = payload.review_state === (node.value === "confirm" ? "yes" : "no");
                node.classList.toggle("is-current", active);
                node.setAttribute("aria-pressed", active ? "true" : "false");
              }});
              const visibilityEnabled = payload.review_state === "yes";
              overviewForm.querySelectorAll('[aria-label="Visibility"] button').forEach((node) => {{
                const active = payload.visibility_state === (node.value === "publish" ? "public" : "private");
                node.classList.toggle("is-current", active);
                node.setAttribute("aria-pressed", active ? "true" : "false");
                node.disabled = !visibilityEnabled;
              }});
              const workflowHelp = overviewForm.querySelector(".workflow-help");
              if (workflowHelp) {{
                if (payload.review_state === "yes") {{
                  workflowHelp.textContent = "Review complete. Choose whether this trip should stay private or be visible on the public site. Text edits autosave when you leave a field.";
                }} else if (payload.review_state === "no") {{
                  workflowHelp.textContent = "Marked as not a trip. Public visibility is disabled for rejected items. Text edits autosave when you leave a field.";
                }} else {{
                  workflowHelp.textContent = "Start by answering Yes or No. Visibility becomes available only after the trip is reviewed. Text edits autosave when you leave a field.";
                }}
              }}
              const existing = document.querySelector("[data-toast]");
              if (existing) {{
                existing.remove();
              }}
              const toast = document.createElement("div");
              toast.className = "toast";
              toast.dataset.toast = payload.saved || "review";
              toast.textContent = payload.message || "Update saved.";
              document.body.appendChild(toast);
              window.setTimeout(() => {{
                toast.classList.add("is-hiding");
                window.setTimeout(() => toast.remove(), 240);
              }}, 2200);
              window.setTimeout(() => {{
                if (overviewForm.dataset.saveState === "saved") {{
                  delete overviewForm.dataset.saveState;
                }}
              }}, 1600);
            }} catch (error) {{
              delete overviewForm.dataset.saveState;
              console.error(error);
            }}
          }});
        }});
      }}

      const toast = document.querySelector("[data-toast]");
      if (toast) {{
        window.setTimeout(() => {{
          toast.classList.add("is-hiding");
          window.setTimeout(() => toast.remove(), 240);
        }}, 3800);
      }}
    }})();
  </script>
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
    return _html_response(
        _render_admin_page(
            trips,
            status=status,
            review_decision=review_decision,
            include_private=include_private,
            limit=limit,
        )
    )


@app.get("/admin/overrides", response_class=HTMLResponse)
def admin_overrides_page(return_to: str = Query(default="")) -> HTMLResponse:
    overrides = destination_overrides.list_overrides()
    return _html_response(_render_overrides_page(overrides, return_to=_query_text(return_to)))


@app.post("/admin/overrides/create")
async def create_destination_override(
    request: Request,
    return_to: str = Query(default=""),
) -> RedirectResponse:
    normalized_return_to = _query_text(return_to)
    payload = parse_qs((await request.body()).decode("utf-8"))
    rule_name = (payload.get("rule_name") or [""])[0].strip()
    classification = (payload.get("classification") or ["custom_destination"])[0].strip()
    match_pattern = (payload.get("match_pattern") or [""])[0].strip() or None
    latitude_text = (payload.get("latitude") or [""])[0].strip()
    longitude_text = (payload.get("longitude") or [""])[0].strip()
    radius_text = (payload.get("radius_meters") or ["1000"])[0].strip()
    latitude = float(latitude_text) if latitude_text else None
    longitude = float(longitude_text) if longitude_text else None
    radius_meters = int(radius_text or "1000")
    keep_trip = _parse_flag((payload.get("keep_trip") or [""])[0])
    ignore_trip = _parse_flag((payload.get("ignore_trip") or [""])[0])

    if not rule_name:
        raise HTTPException(status_code=400, detail="Rule name is required")
    if not match_pattern and (latitude is None or longitude is None):
        raise HTTPException(status_code=400, detail="Provide a pattern or coordinates")

    destination_overrides.create_override(
        rule_name=rule_name,
        classification=classification,
        keep_trip=keep_trip,
        ignore_trip=ignore_trip,
        match_pattern=match_pattern,
        latitude=latitude,
        longitude=longitude,
        radius_meters=radius_meters,
    )
    target = (
        f"/admin/overrides?{urlencode({'return_to': normalized_return_to})}"
        if normalized_return_to
        else "/admin/overrides"
    )
    return RedirectResponse(url=target, status_code=303)


@app.post("/admin/overrides/delete")
async def delete_destination_override(request: Request) -> RedirectResponse:
    payload = parse_qs((await request.body()).decode("utf-8"))
    override_id_text = (payload.get("override_id") or [""])[0].strip()
    return_to = (payload.get("return_to") or [""])[0].strip()
    if not override_id_text:
        raise HTTPException(status_code=400, detail="override_id is required")
    destination_overrides.delete_override(int(override_id_text))
    target = f"/admin/overrides?{urlencode({'return_to': return_to})}" if return_to else "/admin/overrides"
    return RedirectResponse(url=target, status_code=303)


@app.get("/admin/trip/{trip_id}/destination-context", response_class=HTMLResponse)
def admin_trip_destination_page(
    trip_id: int,
    return_to: str = Query(default=""),
) -> HTMLResponse:
    trip = trip_admin.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip["matching_overrides"] = _matching_overrides_for_trip(trip)
    normalized_return_to = _query_text(return_to)
    return _html_response(
        _render_trip_destination_page(
            trip, return_to=normalized_return_to or f"/admin/trip/{trip_id}"
        )
    )


@app.get("/admin/trip/{trip_id}", response_class=HTMLResponse)
def admin_trip_detail_page(trip_id: int, saved: str = Query(default="")) -> HTMLResponse:
    trip = trip_admin.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip["matching_overrides"] = _matching_overrides_for_trip(trip)
    trip["neighbors"] = trip_admin.get_trip_neighbors(trip_id)
    return _html_response(_render_trip_detail_page(trip, saved=saved))


@app.post("/admin/trip/{trip_id}/review")
async def review_trip_from_form(trip_id: int, request: Request) -> Response:
    payload = parse_qs((await request.body()).decode("utf-8"))
    request_headers = getattr(request, "headers", {}) or {}
    action = (payload.get("action") or ["save"])[0].strip() or "save"
    reviewer_name = (payload.get("reviewer_name") or [""])[0].strip() or None
    review_notes = (payload.get("review_notes") or [""])[0].strip() or None
    trip_name = (payload.get("trip_name") or [""])[0].strip() or None
    summary_text = (payload.get("summary_text") or [""])[0].strip() or None
    primary_destination_name = (payload.get("primary_destination_name") or [""])[0].strip() or None
    is_private_values = payload.get("is_private") or [""]
    publish_ready_values = payload.get("publish_ready") or [""]
    is_private = None if not is_private_values[0] else _parse_flag(is_private_values[0])
    publish_ready = None if not publish_ready_values[0] else _parse_flag(publish_ready_values[0])

    updated = trip_admin.record_review(
        trip_id,
        action=action,
        reviewer_name=reviewer_name,
        review_notes=review_notes,
        trip_name=trip_name,
        summary_text=summary_text,
        primary_destination_name=primary_destination_name,
        is_private=is_private,
        publish_ready=publish_ready,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Trip not found")
    if request_headers.get("x-requested-with") == "fetch":
        if action == "save":
            return Response(status_code=204)
        saved_key = "details"
        message = "Trip details saved."
        if action == "publish":
            saved_key = "published"
            message = "Trip published and marked ready."
        elif action == "mark_private":
            saved_key = "privacy"
            message = "Trip visibility updated."
        elif action in {"confirm", "reject"}:
            saved_key = "review"
            message = "Review saved."
        return JSONResponse(
            {
                "saved": saved_key,
                "message": message,
                "review_state": _trip_review_state(updated),
                "visibility_state": _trip_visibility_state(updated),
                "badge_html": _render_trip_badges(updated),
            }
        )
    saved_key = "details"
    if action == "publish":
        saved_key = "published"
    elif action == "mark_private":
        saved_key = "privacy"
    elif action in {"confirm", "reject"}:
        saved_key = "review"
    return RedirectResponse(url=f"/admin/trip/{trip_id}?saved={saved_key}", status_code=303)


@app.post("/admin/trip/{trip_id}/segments/{segment_id}")
async def update_trip_segment_from_form(
    trip_id: int,
    segment_id: int,
    request: Request,
) -> Response:
    payload = parse_qs((await request.body()).decode("utf-8"))
    summary_text = (payload.get("summary_text") or [""])[0].strip() or None
    rating_text = (payload.get("rating") or [""])[0].strip()
    rating = int(rating_text) if rating_text else None
    updated = trip_admin.update_trip_segment(
        trip_id,
        segment_id,
        segment_name=None,
        summary_text=summary_text,
        rating=rating,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Trip segment not found")
    if request.headers.get("x-requested-with") == "fetch":
        return Response(status_code=204)
    return RedirectResponse(url=f"/admin/trip/{trip_id}?saved=segment", status_code=303)


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


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
