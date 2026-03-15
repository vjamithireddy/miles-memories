from datetime import datetime
from html import escape
import json
from typing import List, Optional
from urllib.parse import parse_qs
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.bootstrap import get_user_timezone
from app import destination_overrides, trip_admin
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
        detail_href = f"/admin/trip/{trip['id']}"
        json_href = f"/admin/trips/{trip['id']}"

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
                <a href="{detail_href}">Open detail page</a>
                <a href="{json_href}">JSON</a>
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
    .card-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
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
        <a class="button ghost" href="/admin/overrides">Destination overrides</a>
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


def _parse_flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


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


def _render_overrides_page(overrides: List[dict]) -> str:
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
      background: var(--accent);
      color: white;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
    }}
    .ghost {{
      background: transparent;
      color: var(--accent);
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
        <a class="button ghost" href="/admin">Back to trip queue</a>
        <a class="button ghost" href="/">Homepage</a>
      </div>
    </section>

    <section class="panel">
      <h2>Add override</h2>
      <form method="post" action="/admin/overrides/create" class="form-grid">
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
          <button type="submit">Save override</button>
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


def _render_trip_detail_page(trip: dict, *, saved: bool = False) -> str:
    title = escape(trip["trip_name"] or "Untitled trip")
    destination = escape(trip["primary_destination_name"] or "Destination pending")
    trip_type = escape(trip["trip_type"] or "untyped")
    summary = escape(trip["summary_text"] or "No summary yet for this trip.")
    confidence = "n/a" if trip["confidence_score"] is None else str(trip["confidence_score"])
    published = escape(str(trip["published_at"] or "not published"))
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
    map_payload = escape(json.dumps(map_points))
    matching_overrides = trip.get("matching_overrides", [])
    neighbors = trip.get("neighbors") or {}
    previous_trip = neighbors.get("previous")
    next_trip = neighbors.get("next")
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

    override_items = "".join(
        f"""
        <li class="count-item">
          <strong>{escape(item['rule_name'])}</strong>
          <span>{'keep' if item['keep_trip'] else 'ignore' if item['ignore_trip'] else escape(item['classification'])}</span>
        </li>
        """
        for item in matching_overrides
    ) or """
      <li class="count-item">
        <strong>No matching overrides</strong>
        <span>auto only</span>
      </li>
    """

    travel_leg_items = "".join(
        f"""
        <li class="timeline-item">
          <div class="timeline-time">{escape(_format_local_datetime(item['start_time']))}</div>
          <div>
            <strong>{escape(item['label'])}</strong>
            <p>{escape(item['label'])} · until {escape(_format_local_datetime(item['end_time']))}</p>
          </div>
        </li>
        """
        for item in travel_legs
    ) or """
      <li class="timeline-item">
        <div>
          <strong>No travel legs inferred.</strong>
          <p>This trip currently only has raw location points linked.</p>
        </div>
      </li>
    """

    saved_banner = """
    <section class="panel success-banner">
      <strong>Review saved.</strong>
      <p>Trip updates were written successfully.</p>
    </section>
    """ if saved else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · MilesMemories</title>
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
    crossorigin=""
  >
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
      grid-template-columns: 1.25fr 0.75fr;
      gap: 18px;
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
    .success-banner {{
      border-color: rgba(46,106,75,0.3);
      background: rgba(46,106,75,0.08);
    }}
    .success-banner strong {{
      display: block;
      margin-bottom: 6px;
      color: var(--good);
    }}
    .button {{
      display: inline-block;
      text-decoration: none;
      border-radius: 999px;
      padding: 12px 16px;
      border: 1px solid var(--accent);
      color: white;
      background: var(--accent);
      font-weight: 700;
    }}
    .button.ghost {{
      background: transparent;
      color: var(--accent);
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
    #trip-map {{
      height: 360px;
      width: 100%;
      border-top: 1px solid var(--line);
      background: #efe5d7;
    }}
    .map-fallback {{
      padding: 18px 24px 24px;
      color: var(--muted);
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
    .timeline-item {{
      display: grid;
      grid-template-columns: 220px 1fr;
      gap: 14px;
    }}
    .review-form {{
      display: grid;
      gap: 14px;
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
    @media (max-width: 920px) {{
      .hero, .grid, .timeline-item, .two-up, .review-form-grid, .detail-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="stack">
    {saved_banner}
    <section class="hero">
      <article class="panel">
        <div class="eyebrow">Trip Detail</div>
        <h1>{title}</h1>
        <p>{destination} · {trip_type}</p>
        <div class="meta-row">
          <span class="badge {_trip_badge_class(trip['status'])}">{escape(trip['status'])}</span>
          <span class="badge {_trip_badge_class(trip['review_decision'])}">{escape(trip['review_decision'])}</span>
          <span class="badge">{'Private' if trip['is_private'] else 'Visible'}</span>
          <span class="badge">{'Ready' if trip['publish_ready'] else 'Not ready'}</span>
        </div>
        <div class="actions">
          <a class="button" href="/admin">Back to queue</a>
          <a class="button ghost" href="/admin/trips/{trip['id']}">Open JSON</a>
          {f'<a class="button ghost" href="/admin/trip/{previous_trip["id"]}">Previous trip</a>' if previous_trip else ''}
          {f'<a class="button ghost" href="/admin/trip/{next_trip["id"]}">Next trip</a>' if next_trip else ''}
        </div>
      </article>
      <aside class="panel">
        <h2>Trip summary</h2>
        <p>{summary}</p>
        <div class="meta-row">
          <span class="badge">Confidence {confidence}</span>
          <span class="badge">Published {published}</span>
          <span class="badge">Date range {escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}</span>
        </div>
        <div class="detail-grid">
          <div class="detail-cell">
            <strong>Start</strong>
            <span>{escape(_format_local_datetime(trip['start_time']))}</span>
          </div>
          <div class="detail-cell">
            <strong>End</strong>
            <span>{escape(_format_local_datetime(trip['end_time']))}</span>
          </div>
          <div class="detail-cell">
            <strong>Trip type</strong>
            <span>{trip_type}</span>
          </div>
          <div class="detail-cell">
            <strong>Generated destination</strong>
            <span>{destination}</span>
          </div>
        </div>
      </aside>
    </section>

    <section class="two-up">
      <article class="panel">
        <h2>Destination context</h2>
        <ul class="list">
          <li class="count-item">
            <strong>Primary destination</strong>
            <span>{destination}</span>
          </li>
          <li class="count-item">
            <strong>Matching overrides</strong>
            <span>{len(matching_overrides)}</span>
          </li>
        </ul>
        <ul class="list" style="margin-top: 12px;">
          {override_items}
        </ul>
        <div class="actions">
          <a class="button ghost" href="/admin/overrides">Manage overrides</a>
        </div>
      </article>

      <article class="panel">
        <h2>Review trip</h2>
        <form class="review-form" method="post" action="/admin/trip/{trip['id']}/review">
          <div class="review-form-grid">
            <label>Action
              <select name="action">
                <option value="confirm">confirm</option>
                <option value="ignore">ignore</option>
                <option value="publish">publish</option>
                <option value="mark_private">mark_private</option>
                <option value="reject">reject</option>
              </select>
            </label>
            <label>Reviewer name
              <input type="text" name="reviewer_name" value="Venkat">
            </label>
            <label>Trip name
              <input type="text" name="trip_name" value="{title}">
            </label>
            <label>Destination name
              <input type="text" name="primary_destination_name" value="{destination}">
            </label>
          </div>
          <label>Summary
            <textarea name="summary_text">{escape(trip['summary_text'] or '')}</textarea>
          </label>
          <label>Review notes
            <textarea name="review_notes" placeholder="What changed? Why is this correct?"></textarea>
          </label>
          <div class="actions">
            <button type="submit">Save review</button>
          </div>
        </form>
      </article>
    </section>

    <section class="panel">
      <h2>Linked events</h2>
      <ul class="list">
        {count_items}
      </ul>
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
      <div id="trip-map" data-points="{map_payload}"></div>
      <div class="map-fallback">Map points appear here when linked location events include coordinates.</div>
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
  </main>
  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>
  <script>
    (function () {{
      const mapNode = document.getElementById("trip-map");
      if (!mapNode || !window.L) {{
        return;
      }}
      const points = JSON.parse(mapNode.dataset.points || "[]");
      if (!points.length) {{
        return;
      }}
      const fallback = document.querySelector(".map-fallback");
      if (fallback) {{
        fallback.style.display = "none";
      }}
      const map = L.map(mapNode, {{ scrollWheelZoom: false }});
      L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap contributors"
      }}).addTo(map);
      const latlngs = points.map((point) => [point.lat, point.lon]);
      const polyline = L.polyline(latlngs, {{
        color: "#b85f35",
        weight: 4,
        opacity: 0.9
      }}).addTo(map);
      const start = points[0];
      const end = points[points.length - 1];
      L.marker([start.lat, start.lon]).addTo(map).bindPopup(`Start: ${{start.label}}<br>${{start.time}}`);
      if (points.length > 1) {{
        L.marker([end.lat, end.lon]).addTo(map).bindPopup(`End: ${{end.label}}<br>${{end.time}}`);
      }}
      map.fitBounds(polyline.getBounds(), {{ padding: [24, 24] }});
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
    return HTMLResponse(
        _render_admin_page(
            trips,
            status=status,
            review_decision=review_decision,
            include_private=include_private,
            limit=limit,
        )
    )


@app.get("/admin/overrides", response_class=HTMLResponse)
def admin_overrides_page() -> HTMLResponse:
    overrides = destination_overrides.list_overrides()
    return HTMLResponse(_render_overrides_page(overrides))


@app.post("/admin/overrides/create")
async def create_destination_override(request: Request) -> RedirectResponse:
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
    return RedirectResponse(url="/admin/overrides", status_code=303)


@app.post("/admin/overrides/delete")
async def delete_destination_override(request: Request) -> RedirectResponse:
    payload = parse_qs((await request.body()).decode("utf-8"))
    override_id_text = (payload.get("override_id") or [""])[0].strip()
    if not override_id_text:
        raise HTTPException(status_code=400, detail="override_id is required")
    destination_overrides.delete_override(int(override_id_text))
    return RedirectResponse(url="/admin/overrides", status_code=303)


@app.get("/admin/trip/{trip_id}", response_class=HTMLResponse)
def admin_trip_detail_page(trip_id: int, saved: bool = Query(default=False)) -> HTMLResponse:
    trip = trip_admin.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    trip["matching_overrides"] = _matching_overrides_for_trip(trip)
    trip["neighbors"] = trip_admin.get_trip_neighbors(trip_id)
    return HTMLResponse(_render_trip_detail_page(trip, saved=saved))


@app.post("/admin/trip/{trip_id}/review")
async def review_trip_from_form(trip_id: int, request: Request) -> RedirectResponse:
    payload = parse_qs((await request.body()).decode("utf-8"))
    action = (payload.get("action") or ["confirm"])[0].strip()
    reviewer_name = (payload.get("reviewer_name") or [""])[0].strip() or None
    review_notes = (payload.get("review_notes") or [""])[0].strip() or None
    trip_name = (payload.get("trip_name") or [""])[0].strip() or None
    summary_text = (payload.get("summary_text") or [""])[0].strip() or None
    primary_destination_name = (payload.get("primary_destination_name") or [""])[0].strip() or None

    updated = trip_admin.record_review(
        trip_id,
        action=action,
        reviewer_name=reviewer_name,
        review_notes=review_notes,
        trip_name=trip_name,
        summary_text=summary_text,
        primary_destination_name=primary_destination_name,
        is_private=None,
        publish_ready=None,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Trip not found")
    return RedirectResponse(url=f"/admin/trip/{trip_id}?saved=1", status_code=303)


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
