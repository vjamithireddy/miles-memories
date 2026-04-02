import base64
import binascii
from datetime import datetime, timedelta
from html import escape
import json
import math
import re
import secrets
import time
from typing import Any, List, Optional, Union
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.bootstrap import get_user_timezone
from app import destination_overrides, parks, trip_admin
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


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 404:
        accept = request.headers.get("accept", "")
        wants_html = "text/html" in accept or "*/*" in accept
        if wants_html and not request.url.path.startswith("/api/"):
            return HTMLResponse(_render_not_found_page(), status_code=404)
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(str(exc.detail), status_code=exc.status_code)


def _html_response(content: str) -> HTMLResponse:
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/map-tiles/osm/{z}/{x}/{y}.png")
def proxy_osm_tile(z: int, x: int, y: int) -> Response:
    request = UrlRequest(
        f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        headers={
            "User-Agent": "MilesMemories/1.0 (+https://travel.navi-services.com)",
            "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
        },
    )
    with urlopen(request, timeout=12) as upstream:
        return Response(
            content=upstream.read(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
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

def _render_public_trip_cards(trips: List[dict]) -> str:
    cards = []
    for trip in trips:
        title = escape(trip.get("trip_name") or "Untitled trip")
        destination = escape(trip.get("primary_destination_name") or "Destination pending")
        summary = escape(trip.get("summary_text") or "Published from the MilesMemories archive.")
        trip_type = escape((trip.get("trip_type") or "trip").replace("_", " "))
        timing = f"{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}"
        trip_href = f"/trips/{escape(str(trip.get('id') or ''))}"
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
    return "".join(cards)


def _render_public_homepage(
    trips: List[dict],
    *,
    intro: dict[str, Any] | None = None,
    total: int | None = None,
    page: int = 1,
    per_page: int = 12,
    show_archive_link: bool = True,
    show_load_more: bool = False,
    parks_list: list[dict[str, Any]] | None = None,
    parks_counts: dict[str, int] | None = None,
) -> str:
    published_total = total if total is not None else len(trips)
    latest_label = "No published trips yet"
    if trips:
        latest_trip = trips[0]
        latest_dt = latest_trip.get("published_at") or latest_trip.get("end_time") or latest_trip.get("start_time")
        latest_label = _format_local_datetime(latest_dt) if latest_dt else "Recently updated"

    trips_markup = _render_public_trip_cards(trips) if trips else """
      <article class="trip-card empty-state">
        <h3>No public trips yet</h3>
        <p>Publish a reviewed trip from the admin workflow and it will appear here automatically.</p>
      </article>
    """

    intro = intro or {}
    hero_note = "Travel stories from my own data."
    highlight_line = "Experiences captured through hiking, driving, and trips."

    parks_list = parks_list or []
    parks_counts = parks_counts or {"total": 0, "visited": 0, "planned": 0}
    parks_items = []
    for park in parks_list:
        status = "visited" if park.get("visited") else "planned" if park.get("planned") else "unvisited"
        location_bits = [bit for bit in [park.get("state"), park.get("city")] if bit]
        location = " · ".join(location_bits)
        parks_items.append(
            f"""
            <li class="park-item" data-park-name="{escape(park['name'].lower())}" data-park-status="{status}">
              <div>
                <p class="park-name">{escape(park['name'])}</p>
                <p class="park-meta">{escape(location)}</p>
              </div>
            </li>
            """
        )
    parks_list_markup = (
        "".join(parks_items)
        if parks_items
        else "<li class=\"park-item empty\">No parks loaded yet.</li>"
    )

    has_more = published_total > page * per_page if published_total else False
    load_more_markup = ""
    if show_load_more and published_total:
        next_page = page + 1
        noscript_markup = (
            f'<noscript><a class="button" href="/trips?page={next_page}">Next page</a></noscript>'
            if has_more
            else ""
        )
        load_more_markup = f"""
        <div class="load-more" data-load-more data-page="{page}" data-per-page="{per_page}" data-total="{published_total}">
          <button class="button load-more-button" type="button" data-load-more-button{" disabled" if not has_more else ""}>
            {"No more trips" if not has_more else "Load more trips"}
          </button>
          {noscript_markup}
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MilesMemories</title>
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
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

    .parks {{
      display: grid;
      gap: 18px;
    }}

    .parks-grid {{
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 18px;
    }}

    .parks-map {{
      display: grid;
      gap: 12px;
    }}

    .parks-map .maplibre-shell {{
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid var(--line);
      background: #f1e7d6;
      min-height: 360px;
    }}

    .parks-map .maplibre-map {{
      width: 100%;
      height: 360px;
    }}

    .parks-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      font-size: 0.9rem;
      color: var(--muted);
    }}

    .legend-dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 6px;
    }}

    .legend-visited {{ background: #2f6c5b; }}
    .legend-planned {{ background: #d28b3c; }}
    .legend-unvisited {{ background: #9aa2ad; }}

    .parks-list {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}

    .parks-search {{
      order: 1;
    }}
    .parks-search input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 12px 14px;
      font: inherit;
      background: #fffdf8;
      color: var(--ink);
    }}

    .parks-tabs {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 10px;
      order: 0;
    }}

    .parks-tab {{
      border: 1px solid var(--line);
      border-radius: 999px;
      height: 42px;
      width: 100%;
      padding: 0 16px;
      background: rgba(255, 255, 255, 0.65);
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}

    .parks-tab.is-active {{
      background: rgba(200, 100, 59, 0.16);
      color: var(--accent-dark);
      border-color: rgba(200, 100, 59, 0.4);
    }}

    .parks-scroll {{
      order: 2;
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 12px;
      background: rgba(255, 255, 255, 0.65);
      max-height: 420px;
      overflow: auto;
    }}

    .parks-scroll ul {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 10px;
    }}

    .park-item {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 16px;
      border: 1px solid rgba(216, 201, 179, 0.7);
      background: rgba(255, 255, 255, 0.6);
    }}

    .park-item.empty {{
      justify-content: center;
      color: var(--muted);
    }}

    .park-name {{
      margin: 0;
      font-weight: 700;
    }}

    .park-meta {{
      margin: 4px 0 0;
      font-size: 0.88rem;
      color: var(--muted);
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
    .archive-link {{
      margin-bottom: 16px;
    }}
    .pagination {{
      margin-top: 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-weight: 600;
    }}
    .pagination-actions {{
      display: inline-flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .load-more {{
      margin-top: 18px;
      display: flex;
      justify-content: center;
    }}
    .load-more .load-more-button {{
      min-width: 180px;
      text-align: center;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      border-radius: 999px;
      padding: 12px 18px;
      font: inherit;
      font-weight: 700;
      appearance: none;
    }}
    .load-more .load-more-button:hover {{
      background: rgba(200,100,59,0.12);
    }}
    .load-more .load-more-button:disabled {{
      opacity: 0.55;
      cursor: not-allowed;
      box-shadow: none;
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
      .parks-grid {{
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
        <h1>Miles awaiting &amp; memories created</h1>
        <p class="hero-note">{hero_note}</p>
        {f'<p class="hero-note">{highlight_line}</p>' if highlight_line else ''}
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

    <section class="panel parks">
      <div class="section-head">
        <div>
          <span class="eyebrow">National Parks</span>
          <h2>My national park checklist</h2>
        </div>
        <p>{parks_counts.get("visited", 0)} visited · {parks_counts.get("planned", 0)} planned · {parks_counts.get("total", 0)} total</p>
      </div>
      <div class="parks-grid">
        <div class="parks-map">
          <div class="maplibre-shell">
            <div class="maplibre-map" data-parks-map data-parks-url="/api/parks"></div>
          </div>
          <div class="parks-legend">
            <span><span class="legend-dot legend-visited"></span>Visited</span>
            <span><span class="legend-dot legend-planned"></span>Planned</span>
            <span><span class="legend-dot legend-unvisited"></span>Not visited</span>
          </div>
        </div>
        <div class="parks-list">
          <div class="parks-tabs" data-parks-tabs>
            <button class="parks-tab is-active" type="button" data-parks-tab="visited">Visited</button>
            <button class="parks-tab" type="button" data-parks-tab="planned">Planned</button>
            <button class="parks-tab" type="button" data-parks-tab="unvisited">Not visited</button>
            <button class="parks-tab" type="button" data-parks-tab="all">All</button>
          </div>
          <div class="parks-search">
            <input type="search" placeholder="Search parks..." data-parks-filter>
          </div>
          <div class="parks-scroll" data-parks-list>
            <ul>
              {parks_list_markup}
            </ul>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <span class="eyebrow">Published Archive</span>
          <h2>Recent published trips</h2>
        </div>
        <p>{published_total} visible trip{"s" if published_total != 1 else ""}</p>
      </div>
      {f'<div class=\"archive-link\"><a class=\"button\" href=\"/trips?page=1\">View all trips</a></div>' if show_archive_link else ''}
      <div class="published-grid">
        {trips_markup}
      </div>
      {load_more_markup}
    </section>
  </main>
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  {_render_public_maplibre_script()}
  <script>
    (() => {{
      const filterInput = document.querySelector("[data-parks-filter]");
      const list = document.querySelector("[data-parks-list]");
      const tabs = document.querySelector("[data-parks-tabs]");
      let activeStatus = "visited";
      const activeTab = tabs?.querySelector(".parks-tab.is-active");
      if (activeTab?.dataset.parksTab) {{
        activeStatus = activeTab.dataset.parksTab;
      }}
      if (!list) {{
        return;
      }}
      const applyFilter = () => {{
        const term = (filterInput?.value || "").trim().toLowerCase();
        list.querySelectorAll(".park-item").forEach((item) => {{
          const name = item.dataset.parkName || "";
          const status = item.dataset.parkStatus || "unvisited";
          const matchesTerm = !term || name.includes(term);
          const matchesStatus = activeStatus === "all" || status === activeStatus;
          item.style.display = matchesTerm && matchesStatus ? "" : "none";
        }});
      }};
      applyFilter();
      filterInput?.addEventListener("input", applyFilter);
      tabs?.addEventListener("click", (event) => {{
        const button = event.target.closest("[data-parks-tab]");
        if (!button) return;
        activeStatus = button.dataset.parksTab || "all";
        tabs.querySelectorAll(".parks-tab").forEach((tab) => {{
          tab.classList.toggle("is-active", tab === button);
        }});
        applyFilter();
      }});
    }})();
  </script>
  {f'''
  <script>
    (() => {{
      const container = document.querySelector("[data-load-more]");
      if (!container) return;
      const button = container.querySelector("[data-load-more-button]");
      const grid = document.querySelector(".published-grid");
      if (!button || !grid) return;
      let page = Number(container.dataset.page || "1");
      const perPage = Number(container.dataset.perPage || "{per_page}");
      const total = Number(container.dataset.total || "0");

      const setButton = (enabled) => {{
        button.disabled = !enabled;
        button.textContent = enabled ? "Load more trips" : "No more trips";
      }};

      if (page * perPage >= total) {{
        setButton(false);
      }}

      button.addEventListener("click", async () => {{
        if (button.disabled) return;
        button.disabled = true;
        button.textContent = "Loading...";
        try {{
          const nextPage = page + 1;
          const response = await fetch(`/trips?partial=1&page=${{nextPage}}&per_page=${{perPage}}`);
          if (!response.ok) throw new Error("Unable to load more trips");
          const payload = await response.json();
          if (payload.html) {{
            grid.insertAdjacentHTML("beforeend", payload.html);
          }}
          page = payload.next_page || nextPage;
          const hasMore = Boolean(payload.has_more);
          setButton(hasMore);
        }} catch (error) {{
          console.error(error);
          setButton(true);
          button.textContent = "Load more trips";
        }}
      }});
    }})();
  </script>
  ''' if show_load_more else ''}
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def homepage() -> RedirectResponse:
    return RedirectResponse(url="/trips", status_code=308)


@app.get("/trips", response_class=HTMLResponse)
def public_trips_page(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=48),
    partial: bool = Query(default=False),
) -> HTMLResponse:
    start_time = time.perf_counter()
    total = trip_admin.count_published_trips()
    offset = (page - 1) * per_page
    trips = trip_admin.list_published_trips(limit=per_page, offset=offset)
    intro = trip_admin.build_public_home_intro()
    if partial:
        html = _render_public_trip_cards(trips)
        has_more = total > page * per_page
        next_page = page + 1 if has_more else None
        _log_timing("public_trips_partial", start_time)
        return JSONResponse(
            {
                "html": html,
                "next_page": next_page,
                "has_more": has_more,
            }
        )
    parks_list = parks.list_parks()
    parks_counts = parks.park_counts(parks_list)
    response = _html_response(
        _render_public_homepage(
            trips,
            intro=intro,
            total=total,
            page=page,
            per_page=per_page,
            show_archive_link=False,
            show_load_more=True,
            parks_list=parks_list,
            parks_counts=parks_counts,
        )
    )
    _log_timing("public_trips_page", start_time)
    return response


@app.get("/api/parks", response_class=JSONResponse)
def public_parks_api() -> JSONResponse:
    parks_list = parks.list_parks()
    counts = parks.park_counts(parks_list)
    return JSONResponse({"parks": parks_list, "counts": counts})


@app.get("/admin/parks", response_class=HTMLResponse)
def admin_parks_page() -> HTMLResponse:
    parks_list = parks.list_parks()
    return _html_response(_render_admin_parks_page(parks_list))


@app.post("/admin/parks/bulk", response_class=JSONResponse)
async def admin_parks_bulk(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    else:
        payload = await request.form()
    action = payload.get("action") if isinstance(payload, dict) else payload.get("action")
    codes = payload.get("park_codes") if isinstance(payload, dict) else payload.getlist("park_codes")
    if isinstance(codes, str):
        codes = [codes]
    codes = [code for code in (codes or []) if isinstance(code, str) and code]
    if not action or not codes:
        return JSONResponse({"updated": 0})
    action_map = {
        "mark_visited": ("visited", True),
        "clear_visited": ("visited", False),
        "mark_planned": ("planned", True),
        "clear_planned": ("planned", False),
    }
    if action not in action_map:
        raise HTTPException(status_code=400, detail="Invalid action")
    field, value = action_map[action]
    updated = parks.bulk_update_parks(codes, field=field, value=value)
    return JSONResponse({"updated": updated})


@app.post("/admin/parks/{park_code}", response_class=JSONResponse)
async def admin_update_park(park_code: str, request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    else:
        payload = await request.form()
    visited = _parse_optional_bool(payload.get("visited") if isinstance(payload, dict) else payload.get("visited"))
    planned = _parse_optional_bool(payload.get("planned") if isinstance(payload, dict) else payload.get("planned"))
    updated = parks.update_park_status(park_code, visited=visited, planned=planned)
    if not updated:
        raise HTTPException(status_code=404, detail="Park not found")
    return JSONResponse({"park": updated})


def _render_public_trip_detail_page(trip: dict) -> str:
    title = escape(trip["trip_name"] or "Untitled trip")
    summary = escape(trip["summary_text"] or "A published trip from the MilesMemories archive.")
    destination = escape(trip["primary_destination_name"] or "Destination pending")
    trip_type = escape((trip["trip_type"] or "trip").replace("_", " "))
    timing = f"{escape(_format_local_datetime(trip['start_time']))} → {escape(_format_local_datetime(trip['end_time']))}"
    short_timing = f"{escape(str(trip['start_date']))} to {escape(str(trip['end_date']))}"
    travel_legs = _coalesce_public_story_legs(trip.get("travel_legs", []))
    trip_duration = escape(_format_duration(trip["start_time"], trip["end_time"]))
    map_points = trip.get("map_points") or trip_admin.get_trip_route_points(
        trip["id"], append_home_if_close=True
    )
    if not map_points:
        map_points = [
            {
                "lat": item["latitude"],
                "lon": item["longitude"],
            }
            for item in trip["timeline"]
            if item.get("latitude") is not None and item.get("longitude") is not None
        ]
    trip_map_markup = ""
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
    notable_places = []
    route_places: list[str] = []
    for item in travel_legs:
        for name, place_type in (
            (item.get("start_place_name"), item.get("start_place_type")),
            (item.get("end_place_name"), item.get("end_place_type")),
        ):
            cleaned = _map_clean_place_name(name)
            if not cleaned or _map_is_regional_place(cleaned):
                continue
            if cleaned not in notable_places:
                notable_places.append(cleaned)
            if not _is_route_flow_stop(cleaned, place_type):
                continue
            if route_places and route_places[-1] == cleaned:
                continue
            route_places.append(cleaned)
    fallback_origin = _map_clean_place_name(public_origin)
    fallback_destination = _map_clean_place_name(public_destination)
    if not route_places:
        route_places = [name for name in (fallback_origin, fallback_destination) if name]
    else:
        if fallback_origin and route_places[0] != fallback_origin:
            route_places.insert(0, fallback_origin)
        if fallback_destination and route_places[-1] != fallback_destination:
            route_places.append(fallback_destination)
    route_summary = " → ".join(route_places) if route_places else f"{public_origin} → {public_destination}"
    route_note = (
        f"Key stops selected from {len(notable_places)} distinct recorded place{'s' if len(notable_places) != 1 else ''}."
        if notable_places
        else "Start and finish from the published travel record."
    )
    route_stop_markers = _build_route_stop_markers(travel_legs, route_places)
    trip_map_markup = _render_public_trip_map(
        _build_public_trip_map_payload(
            trip, travel_legs, map_points, stop_markers=route_stop_markers
        )
    )
    travel_leg_items = "".join(
        f"""
        <details class="public-leg-card">
          <summary class="public-leg-header">
            <div class="public-leg-headline">
              <h3>{escape(_public_leg_base_comment(item))}</h3>
              <span class="public-leg-tag">{escape(item['label'])}</span>
            </div>
            <div class="public-leg-summary-row">
              <p class="public-leg-meta">{escape(_format_local_datetime(item['start_time']))} → {escape(_format_local_datetime(item['end_time']))} ({escape(_format_duration(item['start_time'], item['end_time']))})</p>
              <span class="public-leg-toggle"><span class="toggle-icon"></span><span>Expand</span></span>
            </div>
          </summary>
          <div class="public-leg-body">
            <div class="public-leg-map">{_render_public_leg_map(item)}</div>
            <div class="public-leg-copy">
              <p>{escape(_public_leg_base_comment(item))}</p>
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
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
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
    .button.primary {{
      background: var(--accent);
      color: #fff7ef;
      box-shadow: 0 12px 24px rgba(184, 95, 53, 0.18);
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
    .story-card.wide {{
      grid-column: 1 / -1;
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
    .maplibre-shell {{
      border-radius: 22px;
      overflow: hidden;
      border: 1px solid var(--line);
      background: #efe5d7;
    }}
    .maplibre-map {{
      width: 100%;
      height: 560px;
    }}
    .public-leg-maplibre {{
      height: 360px;
    }}
    .maplibregl-popup-content {{
      border-radius: 18px;
      border: 1px solid rgba(219, 202, 177, 0.94);
      box-shadow: 0 10px 20px rgba(37, 28, 14, 0.16);
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
    }}
    .map-popup-title {{
      font-size: 1rem;
      font-weight: 700;
      line-height: 1.35;
      margin-bottom: 4px;
    }}
    .map-popup-meta {{
      font-size: 0.84rem;
      color: var(--muted);
      line-height: 1.35;
    }}
    .maplibregl-ctrl.public-home-ctrl {{
      margin: 10px;
    }}
    .public-home-btn {{
      border: 0;
      background: #ffffff;
      color: var(--ink);
      width: 32px;
      height: 32px;
      border-radius: 8px;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 1px 2px rgba(29, 36, 48, 0.14);
    }}
    .public-home-btn:hover {{
      background: #f7efe3;
      color: var(--accent-dark);
    }}
    .route-stop-marker,
    .route-stop-cluster {{
      border: 0;
      padding: 0;
      background: transparent;
      cursor: pointer;
      position: relative;
      z-index: 12;
    }}
    .maplibre-map .maplibregl-marker {{
      z-index: 12;
      pointer-events: auto;
    }}
    .route-stop-marker {{
      width: 34px;
      height: 34px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      color: #fff8ef;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 0.72rem;
      font-weight: 800;
      box-shadow: 0 8px 18px rgba(27, 36, 51, 0.22);
      border: 3px solid #fff8ef;
    }}
    .route-stop-marker--airport {{ background: #365f98; }}
    .route-stop-marker--fuel {{ background: #c26a39; }}
    .route-stop-marker--park {{ background: #2f6c5b; }}
    .route-stop-marker--camp {{ background: #5e8c37; }}
    .route-stop-marker--lodging {{ background: #7f5ea7; }}
    .route-stop-marker--food {{ background: #b84f51; }}
    .route-stop-marker--parking {{ background: #6e7b8f; }}
    .route-stop-marker--school {{ background: #8a6a34; }}
    .route-stop-marker--default {{ background: #8f5f48; }}
    .route-stop-marker:hover,
    .route-stop-marker:focus-visible {{
      transform: scale(1.08);
      box-shadow: 0 10px 22px rgba(27, 36, 51, 0.28);
      outline: none;
    }}
    .route-stop-cluster {{
      min-width: 34px;
      height: 34px;
      padding: 0 10px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      background: #d06b39;
      color: #fff8ef;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 0.78rem;
      font-weight: 800;
      box-shadow: 0 8px 18px rgba(27, 36, 51, 0.22);
      border: 3px solid #fff8ef;
    }}
    .route-stop-cluster:hover,
    .route-stop-cluster:focus-visible {{
      transform: scale(1.06);
      box-shadow: 0 10px 22px rgba(27, 36, 51, 0.28);
      outline: none;
    }}
    .leg-map-frame {{
      position: relative;
      width: 100%;
      height: 100%;
      min-height: 320px;
      background: #efe5d7;
      overflow: hidden;
      cursor: grab;
    }}
    .leg-map-viewport {{
      position: absolute;
      inset: 0;
      transform-origin: center center;
      transition: transform 180ms ease;
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
    .leg-map-controls {{
      position: absolute;
      top: 14px;
      right: 14px;
      z-index: 5;
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 8px;
      border-radius: 999px;
      background: rgba(255, 248, 239, 0.92);
      border: 1px solid rgba(219, 202, 177, 0.9);
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .map-zoom-btn {{
      border: 0;
      border-radius: 999px;
      min-width: 40px;
      min-height: 34px;
      padding: 0 12px;
      background: rgba(29,36,48,0.06);
      color: var(--ink);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .map-zoom-btn:hover {{
      background: rgba(184,95,53,0.12);
      color: var(--accent-dark);
    }}
    .leg-map-tooltip {{
      position: absolute;
      top: 64px;
      right: 14px;
      z-index: 5;
      max-width: min(280px, calc(100% - 28px));
      padding: 10px 12px;
      border-radius: 16px;
      background: rgba(255, 248, 239, 0.96);
      border: 1px solid rgba(219, 202, 177, 0.94);
      color: var(--ink);
      font-size: 0.84rem;
      line-height: 1.4;
      white-space: pre-line;
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .map-stop-marker {{
      cursor: pointer;
      transition: filter 140ms ease, transform 140ms ease;
    }}
    .map-stop-marker .map-stop-pin,
    .map-stop-marker .map-stop-core {{
      transition: transform 140ms ease, stroke-width 140ms ease, opacity 140ms ease, filter 140ms ease;
      transform-origin: center;
      transform-box: fill-box;
    }}
    .map-stop-marker:hover .map-stop-pin,
    .map-stop-marker.is-active .map-stop-pin {{
      filter: brightness(1.05);
    }}
    .map-stop-marker:hover .map-stop-core,
    .map-stop-marker.is-active .map-stop-core {{
      transform: scale(1.1);
    }}
    .map-stop-marker:hover,
    .map-stop-marker.is-active {{
      filter: drop-shadow(0 7px 12px rgba(37, 28, 14, 0.22));
    }}
    .map-stop-cluster {{
      cursor: pointer;
      transition: transform 140ms ease, filter 140ms ease;
    }}
    .map-stop-cluster:hover,
    .map-stop-cluster:focus,
    .map-stop-cluster.is-active {{
      filter: drop-shadow(0 8px 16px rgba(37, 28, 14, 0.24));
      transform: scale(1.05);
      transform-origin: center;
      transform-box: fill-box;
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
          <span class="story-card-label">Travel modes</span>
          <div class="story-card-value">{escape(travel_modes)}</div>
          <p class="story-card-note">Based on inferred travel legs.</p>
        </article>
        <article class="story-card wide">
          <span class="story-card-label">Route</span>
          <div class="story-card-value">{escape(route_summary)}</div>
          <p class="story-card-note">{escape(route_note)}</p>
        </article>
      </div>
    </section>

    <section class="feature-grid">
      <article class="panel">
        <h2>Trip map</h2>
        <p>Published route preview built from the full inferred travel-leg path for the trip.</p>
        {trip_map_markup}
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
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  {_render_public_maplibre_script()}
</body>
</html>"""


def _render_not_found_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Not found · MilesMemories</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: rgba(255, 250, 242, 0.92);
      --ink: #1d2430;
      --muted: #5f6b7a;
      --line: #d8c9b3;
      --accent: #c8643b;
      --shadow: rgba(50, 33, 15, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(200,100,59,0.18), transparent 28%),
        radial-gradient(circle at right 20%, rgba(39,93,79,0.12), transparent 24%),
        linear-gradient(180deg, #eed6bd 0%, var(--bg) 34%, #f8f4ed 100%);
      display: grid;
      place-items: center;
      padding: 32px 18px;
    }
    .panel {
      max-width: 640px;
      width: 100%;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 30px;
      text-align: center;
    }
    h1 {
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3rem);
    }
    p {
      margin: 0 0 20px;
      color: var(--muted);
      line-height: 1.6;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: center;
    }
    .button {
      display: inline-block;
      border: 1px solid var(--accent);
      background: transparent;
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
      border-radius: 999px;
      padding: 12px 18px;
    }
  </style>
</head>
<body>
  <div class="panel">
    <h1>Page not found</h1>
    <p>That link doesn't exist. Head back to the public trips or the admin dashboard.</p>
    <div class="actions">
      <a class="button" href="/trips">Public trips</a>
      <a class="button" href="/admin">Admin</a>
    </div>
  </div>
</body>
</html>"""


@app.get("/trips/{trip_ref}", response_class=HTMLResponse)
def public_trip_detail_page(trip_ref: str) -> HTMLResponse:
    if trip_ref.isdigit():
        trip = trip_admin.get_public_trip_by_id(int(trip_ref))
        if not trip:
            raise HTTPException(status_code=404, detail="Trip not found")
        return _html_response(_render_public_trip_detail_page(trip))

    trip = trip_admin.get_public_trip_by_slug(trip_ref)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return RedirectResponse(url=f"/trips/{trip['id']}", status_code=307)


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
    return timedelta(0) <= gap <= timedelta(minutes=90)


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
    "airport": {"fill": "#2f6cb3", "symbol": "AP"},
    "fuel": {"fill": "#cc6b2c", "symbol": "GS"},
    "park": {"fill": "#2f6c5b", "symbol": "TR"},
    "camp": {"fill": "#587d32", "symbol": "CG"},
    "lodging": {"fill": "#7b4da3", "symbol": "IN"},
    "food": {"fill": "#b34747", "symbol": "FD"},
    "parking": {"fill": "#6e7886", "symbol": "PK"},
    "school": {"fill": "#7a5a30", "symbol": "SC"},
    "default": {"fill": "#c8643b", "symbol": "ST"},
}

OVERALL_TRIP_MAP_MARKER_KINDS = {
    "airport",
    "park",
    "camp",
    "lodging",
    "food",
    "parking",
    "school",
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
    symbol = marker["symbol"]
    fill = marker["fill"]
    font_size = 7 if len(symbol) > 1 else 8
    return f"""
    <g data-marker-kind="{escape(marker_kind)}" transform="translate({x} {y})">
      <path d="M0 -18 C10 -18 17 -11 17 -2 C17 11 3 24 0 27 C-3 24 -17 11 -17 -2 C-17 -11 -10 -18 0 -18 Z"
        fill="{fill}" stroke="#fff8ef" stroke-width="4" />
      <circle cx="0" cy="-7" r="8.8" fill="#fff8ef" opacity="0.96" />
      <text x="0" y="-4.5" text-anchor="middle" font-family="Arial, sans-serif" font-size="{font_size}" font-weight="700" fill="{fill}">{escape(symbol)}</text>
    </g>
    """


def _map_clean_place_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    lowered = cleaned.lower()
    if "rental car facility" in lowered:
        cleaned = re.sub(r"\s+rental car facility\b", "", cleaned, flags=re.IGNORECASE).strip(" -,")
    return cleaned or None


def _map_is_regional_place(name: Optional[str]) -> bool:
    if not name:
        return False
    lowered = name.lower()
    return any(token in lowered for token in ("county", "state", "region"))


def _build_route_stop_markers(
    travel_legs: List[dict[str, Any]],
    route_places: List[str],
) -> list[dict[str, Any]]:
    if not travel_legs or not route_places:
        return []
    normalized_route = [(_map_clean_place_name(name) or "").lower() for name in route_places]
    match_index = {name: index for index, name in enumerate(normalized_route) if name}
    matched: dict[int, dict[str, Any]] = {}
    for item in travel_legs:
        for name_key, place_key, lat_key, lon_key in (
            ("start_place_name", "start_place_type", "start_latitude", "start_longitude"),
            ("end_place_name", "end_place_type", "end_latitude", "end_longitude"),
        ):
            raw_name = item.get(name_key)
            cleaned = _map_clean_place_name(raw_name)
            if not cleaned:
                continue
            normalized = cleaned.lower()
            if normalized not in match_index:
                continue
            index = match_index[normalized]
            if index in matched:
                continue
            lat = item.get(lat_key)
            lon = item.get(lon_key)
            if lat is None or lon is None:
                continue
            place_type = item.get(place_key) or ""
            marker_kind = _start_marker_kind_for_leg(
                {"start_place_name": cleaned, "start_place_type": place_type}
            )
            matched[index] = {
                "lat": float(lat),
                "lon": float(lon),
                "kind": marker_kind if marker_kind in OVERALL_TRIP_MAP_MARKER_KINDS else "default",
                "label": cleaned,
            }
    markers: list[dict[str, Any]] = []
    for index in range(len(route_places)):
        if index in matched:
            markers.append(matched[index])
    seen = {(round(item["lat"], 5), round(item["lon"], 5)) for item in markers}
    for marker in _build_trip_stop_markers(
        travel_legs, include_kinds={"airport", "park"}
    ):
        key = (round(marker["lat"], 5), round(marker["lon"], 5))
        if key in seen:
            continue
        seen.add(key)
        markers.append(marker)
    return markers


def _is_route_flow_stop(
    name: Optional[str],
    place_type: Optional[str] = None,
) -> bool:
    cleaned = _map_clean_place_name(name)
    if not cleaned or _map_is_regional_place(cleaned):
        return False
    lowered = cleaned.lower()
    place_type = (place_type or "").strip().lower()
    if place_type in {"fuel", "supermarket", "convenience", "grocery"}:
        return False
    excluded_tokens = (
        "gas",
        "fuel",
        "truck stop",
        "quiktrip",
        "costco",
        "walmart",
        "sam's club",
        "pilot",
        "love's",
        "grocery",
        "supermarket",
        "market",
        "parking",
        "garage",
        "i-",
        " i ",
        "interstate",
        "highway",
        "hwy",
        "us ",
        "us-",
        "route",
        "rte ",
        "county road",
        "co rd",
        "state road",
        "campground",
        "camp site",
        "camp",
        "trail",
        "trailhead",
        "visitor center",
        "overlook",
        "turnout",
        "point",
        "lodge",
        "hotel",
        "inn",
        "resort",
        "road",
        "street",
        "avenue",
        "boulevard",
        "drive",
        "dr ",
        "airport",
        "terminal",
        "restaurant",
        "cafe",
        "diner",
        "pizza",
        "subway",
        "taco",
        "domino",
        "school",
        "university",
    )
    if any(char.isdigit() for char in lowered):
        return False
    return not any(token in lowered for token in excluded_tokens)


def _route_stop_type_label(marker_kind: str) -> str:
    return {
        "airport": "Airport",
        "fuel": "Fuel stop",
        "park": "Park or trail stop",
        "camp": "Campground",
        "lodging": "Lodging",
        "food": "Food stop",
        "parking": "Parking",
        "school": "School or university",
        "default": "Road stop",
    }.get(marker_kind, "Trip stop")


def _route_stop_label(item: dict, marker_kind: str) -> str:
    place = _map_clean_place_name(
        item.get("start_place_name")
        or item.get("end_place_name")
        or item.get("primary_destination_name")
        or "Trip stop"
    ) or "Trip stop"
    return f"{place}\n{_route_stop_type_label(marker_kind)}"


def _route_stop_code(marker_kind: str) -> str:
    return {
        "airport": "AP",
        "park": "TR",
        "camp": "CG",
        "lodging": "IN",
        "food": "FD",
        "parking": "PK",
        "school": "SC",
        "default": "ST",
    }.get(marker_kind, "ST")


def _route_stop_color(marker_kind: str) -> str:
    return START_MARKER_STYLES.get(marker_kind, START_MARKER_STYLES["default"])["fill"]


def _downsample_coords(coords: list[list[float]], max_points: int = 600) -> list[list[float]]:
    if len(coords) <= max_points:
        return coords
    step = max(1, math.ceil(len(coords) / max_points))
    sampled = coords[::step]
    if coords[-1] != sampled[-1]:
        sampled.append(coords[-1])
    return sampled


def _build_public_trip_map_payload(
    trip: dict,
    travel_legs: List[dict],
    map_points: List[dict[str, float]],
    *,
    stop_markers: Optional[List[dict[str, Any]]] = None,
) -> dict[str, Any]:
    line_features: list[dict[str, Any]] = []
    line_coords: list[list[float]] = []
    for item in travel_legs:
        coords: list[list[float]] = []
        for point in item.get("path_points") or []:
            lat = point.get("lat")
            lon = point.get("lon")
            if lat is None or lon is None:
                continue
            candidate = [float(lon), float(lat)]
            if not coords or coords[-1] != candidate:
                coords.append(candidate)
        if len(coords) < 2:
            start_lat = item.get("start_latitude")
            start_lon = item.get("start_longitude")
            end_lat = item.get("end_latitude")
            end_lon = item.get("end_longitude")
            if start_lat is not None and start_lon is not None:
                coords.append([float(start_lon), float(start_lat)])
            if end_lat is not None and end_lon is not None:
                candidate = [float(end_lon), float(end_lat)]
                if not coords or coords[-1] != candidate:
                    coords.append(candidate)
        if len(coords) < 2:
            continue
        coords = _downsample_coords(coords, max_points=520)
        line_coords.extend(coords)
        line_features.append(
            {
                "type": "Feature",
                "properties": {
                    "label": _public_leg_base_comment(item),
                    "leg_type": item.get("label") or "Travel leg",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords,
                },
            }
        )

    if not line_features:
        fallback_coords: list[list[float]] = []
        for point in map_points:
            lat = point.get("lat")
            lon = point.get("lon")
            if lat is None or lon is None:
                continue
            candidate = [float(lon), float(lat)]
            if not fallback_coords or fallback_coords[-1] != candidate:
                fallback_coords.append(candidate)
        if len(fallback_coords) >= 2:
            fallback_coords = _downsample_coords(fallback_coords, max_points=520)
            line_coords = fallback_coords[:]
            line_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "label": trip.get("trip_name") or "Trip route",
                        "leg_type": "Trip route",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": fallback_coords,
                    },
                }
            )

    stop_features: list[dict[str, Any]] = []
    markers = stop_markers or _build_trip_stop_markers(
        travel_legs, include_kinds=OVERALL_TRIP_MAP_MARKER_KINDS
    )
    for marker in markers:
        lon = marker.get("lon")
        lat = marker.get("lat")
        if lon is None or lat is None:
            continue
        kind = str(marker.get("kind") or "default")
        label = str(marker.get("label") or "Trip stop")
        place, _, type_label = label.partition("\n")
        stop_features.append(
            {
                "type": "Feature",
                "properties": {
                    "label": place or "Trip stop",
                    "type_label": type_label or _route_stop_type_label(kind),
                    "kind": kind,
                    "kind_code": _route_stop_code(kind),
                    "color": _route_stop_color(kind),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                },
            }
        )

    coords_for_bounds = line_coords or [feature["geometry"]["coordinates"] for feature in stop_features]
    lons = [coord[0] for coord in coords_for_bounds]
    lats = [coord[1] for coord in coords_for_bounds]
    if not lons or not lats:
        bounds = None
    else:
        bounds = [[min(lons), min(lats)], [max(lons), max(lats)]]
    return {
        "bounds": bounds,
        "route": {"type": "FeatureCollection", "features": line_features},
        "stops": {"type": "FeatureCollection", "features": stop_features},
    }


def _render_admin_trip_map(payload: dict[str, Any]) -> str:
    has_route = bool(payload.get("route", {}).get("features"))
    has_stops = bool(payload.get("stops", {}).get("features"))
    has_bounds = bool(payload.get("bounds"))
    if not (has_route or has_stops or has_bounds):
        return """
    <div class="map-placeholder">No map data available yet for this trip.</div>
    """
    return f"""
    <div class="maplibre-shell">
      <div class="maplibre-map" style="min-height: 360px;" data-admin-trip-map='{escape(json.dumps(payload, separators=(",", ":")))}'></div>
    </div>
    """


def _render_public_trip_map(payload: dict[str, Any]) -> str:
    return f"""
    <div class="maplibre-shell">
      <div class="maplibre-map" data-public-trip-map='{escape(json.dumps(payload, separators=(",", ":")))}'></div>
    </div>
    """


def _build_public_leg_map_payload(item: dict[str, Any]) -> dict[str, Any]:
    coords: list[list[float]] = []
    for point in item.get("path_points") or []:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        candidate = [float(lon), float(lat)]
        if not coords or coords[-1] != candidate:
            coords.append(candidate)
    if coords:
        coords = _downsample_coords(coords, max_points=320)
    if len(coords) < 2:
        start_lat = item.get("start_latitude")
        start_lon = item.get("start_longitude")
        end_lat = item.get("end_latitude")
        end_lon = item.get("end_longitude")
        if start_lat is not None and start_lon is not None:
            coords.append([float(start_lon), float(start_lat)])
        if end_lat is not None and end_lon is not None:
            candidate = [float(end_lon), float(end_lat)]
            if not coords or coords[-1] != candidate:
                coords.append(candidate)
    route_features: list[dict[str, Any]] = []
    if len(coords) >= 2:
        route_features.append(
            {
                "type": "Feature",
                "properties": {
                    "label": _public_leg_base_comment(item),
                    "leg_type": item.get("label") or "Travel leg",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords,
                },
            }
        )
    stop_features: list[dict[str, Any]] = []
    for code, lat, lon, label in (
        (
            "S",
            item.get("start_latitude"),
            item.get("start_longitude"),
            _map_clean_place_name(item.get("start_place_name")) or "Journey start",
        ),
        (
            "E",
            item.get("end_latitude"),
            item.get("end_longitude"),
            _map_clean_place_name(item.get("end_place_name")) or "Journey end",
        ),
    ):
        if lat is None or lon is None:
            continue
        stop_features.append(
            {
                "type": "Feature",
                "properties": {
                    "label": label,
                    "type_label": "Start" if code == "S" else "End",
                    "code": code,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                },
            }
        )
    coords_for_bounds = coords or [feature["geometry"]["coordinates"] for feature in stop_features]
    lons = [coord[0] for coord in coords_for_bounds]
    lats = [coord[1] for coord in coords_for_bounds]
    bounds = [[min(lons), min(lats)], [max(lons), max(lats)]] if lons and lats else None
    return {
        "bounds": bounds,
        "route": {"type": "FeatureCollection", "features": route_features},
        "stops": {"type": "FeatureCollection", "features": stop_features},
    }


def _render_public_leg_map(item: dict[str, Any]) -> str:
    return f"""
    <div class="maplibre-shell">
      <div class="maplibre-map public-leg-maplibre" data-public-leg-map='{escape(json.dumps(_build_public_leg_map_payload(item), separators=(",", ":")))}'></div>
    </div>
    """


def _render_admin_leg_items(trip_id: int, travel_legs: List[dict[str, Any]]) -> str:
    return "".join(
        f"""
        <li class="leg-item">
          <form class="segment-form leg-form" method="post" action="/admin/trip/{trip_id}/segments/{item['segment_id']}" data-autosave="segment">
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
                <div class="maplibre-shell">
                  <div class="maplibre-map public-leg-maplibre" style="min-height: 320px;" data-admin-leg-map='{escape(json.dumps(_build_public_leg_map_payload(item), separators=(",", ":")))}'></div>
                </div>
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

def _build_trip_stop_markers(travel_legs: List[dict], *, include_kinds: Optional[set[str]] = None) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for item in travel_legs:
        latitude = item.get("start_latitude")
        longitude = item.get("start_longitude")
        if latitude is None or longitude is None:
            continue
        marker_kind = _start_marker_kind_for_leg(item)
        if include_kinds is not None and marker_kind not in include_kinds:
            continue
        key = (round(float(latitude), 4), round(float(longitude), 4), marker_kind)
        if key in seen:
            continue
        seen.add(key)
        markers.append(
            {
                "lat": float(latitude),
                "lon": float(longitude),
                "kind": marker_kind,
                "label": _route_stop_label(item, marker_kind),
            }
        )
    return markers


def _cluster_scaled_stop_markers(markers: List[dict[str, Any]], radius: float = 34.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(markers) < 2:
        return markers, []
    clusters: list[dict[str, Any]] = []
    singles: list[dict[str, Any]] = []
    used: set[int] = set()
    for index, marker in enumerate(markers):
        if index in used:
            continue
        members = [marker]
        for inner_index in range(index + 1, len(markers)):
            if inner_index in used:
                continue
            candidate = markers[inner_index]
            distance = math.hypot(candidate["x"] - marker["x"], candidate["y"] - marker["y"])
            if distance <= radius:
                members.append(candidate)
                used.add(inner_index)
        if len(members) == 1:
            singles.append(marker)
            continue
        used.add(index)
        clusters.append(
            {
                "id": f"cluster-{len(clusters)}",
                "x": round(sum(item["x"] for item in members) / len(members), 2),
                "y": round(sum(item["y"] for item in members) / len(members), 2),
                "count": len(members),
                "labels": [str(item["label"]) for item in members],
                "member_ids": [str(item["id"]) for item in members],
            }
        )
    return singles, clusters


def _render_route_stop_marker(
    x: float,
    y: float,
    marker_kind: str,
    *,
    label: str,
    is_start: bool = False,
    is_end: bool = False,
) -> str:
    marker = START_MARKER_STYLES.get(marker_kind, START_MARKER_STYLES["default"])
    fill = marker["fill"]
    symbol = marker["symbol"]
    state_class = " is-route-start" if is_start else " is-route-end" if is_end else ""
    font_size = 7 if len(symbol) > 1 else 8.1
    return f"""
    <g class="map-stop-marker{state_class}" data-marker-id="{escape(str(label))}" data-marker-kind="{escape(marker_kind)}" data-label="{escape(label)}" tabindex="0">
      <path class="map-stop-pin" d="M {x} {y - 16} C {x + 10} {y - 16} {x + 16} {y - 9} {x + 16} {y + 1} C {x + 16} {y + 12} {x + 4} {y + 22} {x} {y + 25} C {x - 4} {y + 22} {x - 16} {y + 12} {x - 16} {y + 1} C {x - 16} {y - 9} {x - 10} {y - 16} {x} {y - 16} Z" fill="{fill}" />
      <circle class="map-stop-core" cx="{x}" cy="{y}" r="9.5" fill="#fff8ef" stroke="{fill}" stroke-width="4" />
      <text x="{x}" y="{y + 3.4}" text-anchor="middle" font-family="Arial, sans-serif" font-size="{font_size}" font-weight="700" fill="{fill}">{escape(symbol)}</text>
      <title>{escape(label)}</title>
    </g>
    """


def _render_route_cluster_marker(cluster: dict[str, Any]) -> str:
    label = f"{cluster['count']} stops"
    labels = " | ".join(cluster["labels"][:5])
    if cluster["count"] > 5:
        labels = f"{labels} | +{cluster['count'] - 5} more"
    return f"""
    <g class="map-stop-cluster" data-cluster-id="{escape(cluster['id'])}" data-member-ids="{escape(','.join(cluster['member_ids']))}" data-label="{escape(labels or label)}" data-x="{cluster['x']}" data-y="{cluster['y']}" tabindex="0">
      <circle cx="{cluster['x']}" cy="{cluster['y']}" r="17" fill="#fff8ef" stroke="#d06b39" stroke-width="6" />
      <circle cx="{cluster['x']}" cy="{cluster['y']}" r="9" fill="#d06b39" />
      <text x="{cluster['x']}" y="{cluster['y'] + 3.4}" text-anchor="middle" font-family="Arial, sans-serif" font-size="8" font-weight="700" fill="#fff8ef">{cluster['count']}</text>
      <title>{escape(label)}</title>
    </g>
    """


def _render_public_maplibre_script() -> str:
    return """
  <script>
    (() => {
      if (!window.maplibregl) return;

      const lower48Bounds = [[-137.0, 23.0], [-62.0, 52.5]];
      let mapSequence = 0;
      const initializedNodes = new WeakSet();
      const popup = new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        maxWidth: "280px",
        offset: 14,
      });

      const popupHtml = (title, meta) => `
        <div class="map-popup">
          <div class="map-popup-title">${title}</div>
          <div class="map-popup-meta">${meta}</div>
        </div>
      `;

      const clampBoundsToLower48 = (inputBounds) => {
        if (!inputBounds || inputBounds.length !== 2) return lower48Bounds;
        const west = Math.max(inputBounds[0][0], lower48Bounds[0][0]);
        const south = Math.max(inputBounds[0][1], lower48Bounds[0][1]);
        const east = Math.min(inputBounds[1][0], lower48Bounds[1][0]);
        const north = Math.min(inputBounds[1][1], lower48Bounds[1][1]);
        if (west >= east || south >= north) return lower48Bounds;
        return [[west, south], [east, north]];
      };

      class HomeControl {
        constructor(onClick, label = "H") {
          this.onClick = onClick;
          this.label = label;
        }
        onAdd() {
          this._container = document.createElement("div");
          this._container.className = "maplibregl-ctrl maplibregl-ctrl-group public-home-ctrl";
          const button = document.createElement("button");
          button.type = "button";
          button.className = "public-home-btn";
          button.setAttribute("aria-label", "Reset trip map view");
          button.textContent = this.label;
          button.addEventListener("click", () => this.onClick());
          this._container.appendChild(button);
          return this._container;
        }
        onRemove() {
          this._container?.remove();
        }
      }

      const addPopupHandlers = (map, layerId) => {
        map.on("mouseenter", layerId, (event) => {
          const feature = event.features && event.features[0];
          if (!feature) return;
          map.getCanvas().style.cursor = "pointer";
          popup
            .setLngLat(event.lngLat)
            .setHTML(
              popupHtml(
                feature.properties?.label || "Trip stop",
                feature.properties?.type_label || "Stop"
              )
            )
            .addTo(map);
        });
        map.on("mouseleave", layerId, () => {
          map.getCanvas().style.cursor = "";
          popup.remove();
        });
        map.on("click", layerId, (event) => {
          const feature = event.features && event.features[0];
          if (!feature) return;
          popup
            .setLngLat(event.lngLat)
            .setHTML(
              popupHtml(
                feature.properties?.label || "Trip stop",
                feature.properties?.type_label || "Stop"
              )
            )
            .addTo(map);
        });
      };

      const mapStyle = {
        version: 8,
        glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        sources: {
          osm: {
            type: "raster",
            tiles: ["/map-tiles/osm/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": "#efe5d7" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
          },
        ],
      };

      const initWhenVisible = (node, initFn) => {
        if (!node || initializedNodes.has(node)) return;
        const doInit = () => {
          if (initializedNodes.has(node)) return;
          initializedNodes.add(node);
          initFn();
        };
        if (!("IntersectionObserver" in window)) {
          doInit();
          return;
        }
        const observer = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            doInit();
          });
        }, { rootMargin: "220px" });
        observer.observe(node);
      };

      const initMap = (node, payload, { clusterStops = false, fitMaxZoom = 6.5, maxZoom = 8 } = {}) => {
        const routeData = payload.route || { type: "FeatureCollection", features: [] };
        const stopData = payload.stops || { type: "FeatureCollection", features: [] };
        const bounds = payload.bounds || null;
        const suffix = `map-${++mapSequence}`;
        const routeSourceId = `trip-route-${suffix}`;
        const routeHaloId = `trip-route-halo-${suffix}`;
        const routeLineId = `trip-route-line-${suffix}`;
        const stopSourceId = `trip-stops-${suffix}`;
        const stopClusterId = `trip-stops-clusters-${suffix}`;
        const stopClusterCountId = `trip-stops-cluster-count-${suffix}`;
        const stopCircleId = `trip-stops-circles-${suffix}`;
        const stopLabelId = `trip-stops-labels-${suffix}`;
        const map = new maplibregl.Map({
          container: node,
          style: mapStyle,
          attributionControl: true,
          cooperativeGestures: false,
          dragRotate: false,
          pitchWithRotate: false,
          touchPitch: false,
          maxBounds: lower48Bounds,
          maxZoom,
        });
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
        if (window.ResizeObserver) {
          const resizeObserver = new ResizeObserver(() => map.resize());
          resizeObserver.observe(node);
        }

        const fitBounds = () => {
          map.fitBounds(clampBoundsToLower48(bounds), { padding: 36, duration: 350, maxZoom: fitMaxZoom });
        };
        map.addControl(new HomeControl(fitBounds, "H"), "top-right");

        let initialized = false;
        const setupMapContent = () => {
          if (initialized) return;
          initialized = true;
          if (typeof map.setProjection === "function") {
            map.setProjection({ name: "mercator" });
          }
          map.setPitch(0);
          map.setBearing(0);

          map.addSource(routeSourceId, { type: "geojson", data: routeData });
          map.addLayer({
            id: routeHaloId,
            type: "line",
            source: routeSourceId,
            paint: { "line-color": "#ffffff", "line-width": 10, "line-opacity": 0.68 },
            layout: { "line-cap": "round", "line-join": "round" },
          });
          map.addLayer({
            id: routeLineId,
            type: "line",
            source: routeSourceId,
            paint: { "line-color": "#2f6c5b", "line-width": 6 },
            layout: { "line-cap": "round", "line-join": "round" },
          });

          map.on("mouseenter", routeLineId, (event) => {
            const feature = event.features && event.features[0];
            if (!feature) return;
            map.getCanvas().style.cursor = "pointer";
            popup
              .setLngLat(event.lngLat)
              .setHTML(
                popupHtml(
                  feature.properties?.label || "Travel leg",
                  feature.properties?.leg_type || "Trip route"
                )
              )
              .addTo(map);
          });
          map.on("mouseleave", routeLineId, () => {
            map.getCanvas().style.cursor = "";
            popup.remove();
          });

          const stopFeatures = Array.isArray(stopData.features) ? stopData.features : [];
          if (stopFeatures.length) {
            map.addSource(stopSourceId, {
              type: "geojson",
              data: stopData,
              cluster: clusterStops,
              clusterMaxZoom: Math.max(maxZoom - 1, 1),
              clusterRadius: 40,
            });
            map.addLayer({
              id: stopClusterId,
              type: "circle",
              source: stopSourceId,
              filter: ["has", "point_count"],
              paint: {
                "circle-color": "#d06b39",
                "circle-radius": [
                  "step",
                  ["get", "point_count"],
                  16,
                  8,
                  18,
                  20,
                  22,
                ],
                "circle-stroke-color": "#fff8ef",
                "circle-stroke-width": 3,
              },
            });
            map.addLayer({
              id: stopClusterCountId,
              type: "symbol",
              source: stopSourceId,
              filter: ["has", "point_count"],
              layout: {
                "text-field": ["to-string", ["get", "point_count"]],
                "text-font": ["Noto Sans Regular"],
                "text-size": 13,
                "text-allow-overlap": true,
                "text-ignore-placement": true,
              },
              paint: {
                "text-color": "#fff8ef",
                "text-halo-color": "#8a4522",
                "text-halo-width": 1.25,
              },
            });
            map.addLayer({
              id: stopCircleId,
              type: "circle",
              source: stopSourceId,
              filter: ["!", ["has", "point_count"]],
              paint: {
                "circle-radius": 16,
                "circle-color": [
                  "match",
                  ["coalesce", ["get", "kind"], "default"],
                  "visited", "#2f6c5b",
                  "planned", "#d28b3c",
                  "unvisited", "#9aa2ad",
                  "airport", "#365f98",
                  "fuel", "#c26a39",
                  "park", "#2f6c5b",
                  "camp", "#5e8c37",
                  "lodging", "#7f5ea7",
                  "food", "#b84f51",
                  "parking", "#6e7b8f",
                  "school", "#8a6a34",
                  "#8f5f48",
                ],
                "circle-stroke-color": "#fff8ef",
                "circle-stroke-width": 3,
              },
            });
            map.addLayer({
              id: stopLabelId,
              type: "symbol",
              source: stopSourceId,
              filter: ["!", ["has", "point_count"]],
              layout: {
                "text-field": ["coalesce", ["get", "kind_code"], "ST"],
                "text-font": ["Noto Sans Regular"],
                "text-size": 12,
                "text-allow-overlap": true,
                "text-ignore-placement": true,
              },
              paint: { "text-color": "#fff8ef" },
            });

            addPopupHandlers(map, stopCircleId);
            map.on("click", stopClusterId, (event) => {
              const feature = event.features && event.features[0];
              if (!feature) return;
              const clusterId = feature.properties?.cluster_id;
              const source = map.getSource(stopSourceId);
              if (!source || typeof source.getClusterExpansionZoom !== "function") return;
              if (typeof source.getClusterLeaves === "function") {
                source.getClusterLeaves(clusterId, 200, 0, (leafError, leaves) => {
                  if (!leafError && Array.isArray(leaves) && leaves.length) {
                    const bounds = new maplibregl.LngLatBounds();
                    leaves.forEach((leaf) => {
                      const coords = leaf.geometry && leaf.geometry.coordinates;
                      if (Array.isArray(coords) && coords.length === 2) {
                        bounds.extend(coords);
                      }
                    });
                    if (!bounds.isEmpty()) {
                      map.fitBounds(bounds, { padding: 48, duration: 350, maxZoom });
                      return;
                    }
                  }
                  source.getClusterExpansionZoom(clusterId, (error, zoom) => {
                    if (error) return;
                    map.easeTo({
                      center: feature.geometry.coordinates,
                      zoom: Math.min(zoom, maxZoom),
                      duration: 300,
                    });
                  });
                });
                return;
              }
              source.getClusterExpansionZoom(clusterId, (error, zoom) => {
                if (error) return;
                map.easeTo({
                  center: feature.geometry.coordinates,
                  zoom: Math.min(zoom, maxZoom),
                  duration: 300,
                });
              });
            });
            map.on("mouseenter", stopClusterId, () => {
              map.getCanvas().style.cursor = "pointer";
            });
            map.on("mouseleave", stopClusterId, () => {
              map.getCanvas().style.cursor = "";
            });
          }

          fitBounds();
          window.setTimeout(() => {
            map.resize();
          }, 50);
        };

        if (typeof map.isStyleLoaded === "function" && map.isStyleLoaded()) {
          setupMapContent();
        } else {
          map.on("load", setupMapContent);
        }

        return map;
      };

      const buildParkGeoJson = (parks = []) => ({
        type: "FeatureCollection",
        features: parks.map((park) => {
          const status = park.visited ? "visited" : park.planned ? "planned" : "unvisited";
          const statusLabel = status === "visited" ? "Visited park" : status === "planned" ? "Planned visit" : "Not visited";
          const statusCode = status === "visited" ? "V" : status === "planned" ? "P" : "N";
          return {
            type: "Feature",
            geometry: {
              type: "Point",
              coordinates: [park.lon, park.lat],
            },
            properties: {
              label: park.name,
              type_label: statusLabel,
              kind: status,
              kind_code: statusCode,
            },
          };
        }),
      });

      const initParksMap = async (node) => {
        const url = node.dataset.parksUrl || "/api/parks";
        const response = await fetch(url);
        if (!response.ok) throw new Error("Failed to load parks");
        const payload = await response.json();
        const parks = Array.isArray(payload.parks) ? payload.parks : [];
        const stopData = buildParkGeoJson(parks);
        initMap(
          node,
          { route: { type: "FeatureCollection", features: [] }, stops: stopData, bounds: lower48Bounds },
          { clusterStops: true, fitMaxZoom: 3.1, maxZoom: 7 }
        );
      };

      document.querySelectorAll("[data-public-trip-map]").forEach((node) => {
        initWhenVisible(node, () => {
          try {
            initMap(node, JSON.parse(node.dataset.publicTripMap || "{}"), { clusterStops: true, fitMaxZoom: 6.5, maxZoom: 12 });
          } catch (error) {
            console.error("Failed to initialize public trip map", error);
          }
        });
      });

      document.querySelectorAll("[data-admin-trip-map]").forEach((node) => {
        initWhenVisible(node, () => {
          try {
            initMap(node, JSON.parse(node.dataset.adminTripMap || "{}"), { clusterStops: true, fitMaxZoom: 7.5, maxZoom: 9 });
          } catch (error) {
            console.error("Failed to initialize admin trip map", error);
          }
        });
      });

      document.querySelectorAll("[data-parks-map]").forEach((node) => {
        initWhenVisible(node, () => {
          initParksMap(node).catch((error) => {
            console.error("Failed to initialize parks map", error);
          });
        });
      });

      const activeLegMaps = new Map();
      const legMapQueue = [];
      const maxLegMaps = 4;

      const destroyLegMap = (node) => {
        const map = activeLegMaps.get(node);
        if (map && typeof map.remove === "function") {
          map.remove();
        }
        activeLegMaps.delete(node);
        node.innerHTML = "";
      };

      const registerLegMap = (node, map) => {
        activeLegMaps.set(node, map);
        legMapQueue.push(node);
        while (legMapQueue.length > maxLegMaps) {
          const oldest = legMapQueue.shift();
          if (!oldest || oldest === node) continue;
          if (activeLegMaps.has(oldest)) {
            destroyLegMap(oldest);
          }
        }
      };

      const bindLegMap = (card, node, payloadKey) => {
        const mountLegMap = () => {
          if (activeLegMaps.has(node)) {
            return;
          }
          const width = node.offsetWidth || 0;
          const height = node.offsetHeight || 0;
          if (!width || !height) {
            return false;
          }
          initWhenVisible(node, () => {
            try {
              const payload = JSON.parse(node.dataset[payloadKey] || "{}");
              const map = initMap(node, payload, { clusterStops: false, fitMaxZoom: 10, maxZoom: 12 });
              registerLegMap(node, map);
              window.setTimeout(() => {
                if (typeof map.resize === "function") {
                  map.resize();
                }
              }, 120);
              window.setTimeout(() => {
                if (typeof map.resize === "function") {
                  map.resize();
                }
              }, 420);
            } catch (error) {
              console.error("Failed to initialize leg map", error);
            }
          });
          return true;
        };

        const scheduleMount = (attempt = 0) => {
          if (attempt > 6) {
            mountLegMap();
            return;
          }
          const mounted = mountLegMap();
          if (!mounted) {
            window.setTimeout(() => scheduleMount(attempt + 1), 120);
          }
        };

        const unmountLegMap = () => {
          if (activeLegMaps.has(node)) {
            destroyLegMap(node);
          }
        };

        if (card.open) {
          scheduleMount();
        }

        card.addEventListener("toggle", () => {
          if (card.open) {
            scheduleMount();
          } else {
            unmountLegMap();
          }
        });
      };

      const bindLegMapsInRoot = (root) => {
        const scope = root || document;
        scope.querySelectorAll(".public-leg-card").forEach((card) => {
          const node = card.querySelector("[data-public-leg-map]");
          if (!node) return;
          bindLegMap(card, node, "publicLegMap");
        });

        scope.querySelectorAll(".leg-collapse").forEach((card) => {
          const node = card.querySelector("[data-admin-leg-map]");
          if (!node) return;
          bindLegMap(card, node, "adminLegMap");
        });
      };

      window.milesMemoriesInitLegMaps = bindLegMapsInRoot;
      bindLegMapsInRoot(document);
    })();
  </script>
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


def _button_class(*names: str) -> str:
    classes = ["button", *names]
    return " ".join(part for part in classes if part)


def _log_timing(label: str, start_time: float) -> None:
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    print(f"[perf] {label} {elapsed_ms}ms")


def _render_admin_page(
    trips: List[dict],
    *,
    status: Optional[str],
    review_decision: Optional[str],
    include_private: bool,
    only_private: bool,
    limit: int,
    counts: dict[str, int],
    current_view: Optional[str] = None,
) -> str:
    def selected(current: Optional[str], expected: str) -> str:
        return ' selected="selected"' if current == expected else ""

    def view_key() -> str:
        if only_private:
            return "private"
        if not include_private:
            return "public"
        if status == "published":
            return "published"
        if status == "needs_review":
            return "needs_review"
        if review_decision == "confirmed":
            return "reviewed"
        if review_decision in {"rejected", "ignored"} or status == "ignored":
            return "rejected"
        return "all"

    current_view = current_view or view_key()
    filter_query = urlencode(
        {
            "status": status or "",
            "review_decision": review_decision or "",
            "include_private": str(include_private).lower(),
            "private_only": str(only_private).lower(),
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
    .stat-link {{
      text-decoration: none;
      color: inherit;
      transition: transform 160ms ease, box-shadow 160ms ease;
    }}
    .stat-link:hover {{
      transform: translateY(-2px);
      box-shadow: 0 20px 36px var(--shadow);
    }}
    .filters {{
      display: grid;
      grid-template-columns: minmax(200px, 1.2fr) minmax(140px, 0.6fr) auto;
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
    .filter-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.55);
      color: var(--muted);
      font-weight: 600;
      cursor: pointer;
      user-select: none;
    }}
    .filter-toggle.is-active {{
      background: rgba(184, 95, 53, 0.16);
      border-color: rgba(184, 95, 53, 0.45);
      color: var(--accent);
    }}
    .filter-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      justify-content: flex-end;
    }}
    .button.ghost {{
      border-color: var(--line);
      color: var(--muted);
      background: transparent;
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
        <a class="button" href="/admin/parks">National parks</a>
        <a class="button" href="/trips">Homepage</a>
      </div>
    </section>

    <section class="stats">
      <a class="panel stat stat-link" href="/admin?view=reviewed&limit={limit}"><strong>{counts.get("reviewed", 0)}</strong><span>Reviewed</span></a>
      <a class="panel stat stat-link" href="/admin?view=needs_review&limit={limit}"><strong>{counts.get("needs_review", 0)}</strong><span>Needs review</span></a>
      <a class="panel stat stat-link" href="/admin?view=rejected&limit={limit}"><strong>{counts.get("rejected", 0)}</strong><span>Rejected</span></a>
      <a class="panel stat stat-link" href="/admin?view=private&limit={limit}"><strong>{counts.get("private", 0)}</strong><span>Private</span></a>
      <a class="panel stat stat-link" href="/admin?view=public&limit={limit}"><strong>{counts.get("public", 0)}</strong><span>Public</span></a>
    </section>

    <section class="panel">
      <form method="get" action="/admin" class="filters" data-admin-filters>
        <label>View
          <select name="view" data-view-select>
            <option value="all"{selected(current_view, "all")}>All trips</option>
            <option value="needs_review"{selected(current_view, "needs_review")}>Needs review</option>
            <option value="reviewed"{selected(current_view, "reviewed")}>Reviewed</option>
            <option value="rejected"{selected(current_view, "rejected")}>Rejected</option>
            <option value="published"{selected(current_view, "published")}>Published</option>
            <option value="public"{selected(current_view, "public")}>Public only</option>
            <option value="private"{selected(current_view, "private")}>Private only</option>
          </select>
        </label>
        <label>Limit
          <select name="limit">
            <option value="10"{selected(str(limit), "10")}>10</option>
            <option value="25"{selected(str(limit), "25")}>25</option>
            <option value="50"{selected(str(limit), "50")}>50</option>
            <option value="200"{selected(str(limit), "200")}>All</option>
          </select>
        </label>
        <div class="filter-actions">
          <button class="button ghost" type="button" data-reset-filters>Reset</button>
        </div>
      </form>

      <div class="trips">
        {cards_html}
      </div>
    </section>
  </main>
  <script>
    (() => {{
      const form = document.querySelector("[data-admin-filters]");
      if (!form) return;
      const viewSelect = form.querySelector("[data-view-select]");
      const reset = form.querySelector("[data-reset-filters]");
      const limitSelect = form.querySelector("select[name='limit']");
      let submitTimer = null;

      const scheduleSubmit = () => {{
        if (submitTimer) window.clearTimeout(submitTimer);
        submitTimer = window.setTimeout(() => form.submit(), 180);
      }};

      viewSelect?.addEventListener("change", scheduleSubmit);
      limitSelect?.addEventListener("change", scheduleSubmit);

      reset?.addEventListener("click", () => {{
        if (viewSelect) viewSelect.value = "all";
        if (limitSelect) limitSelect.value = "25";
        form.submit();
      }});
    }})();
  </script>
</body>
</html>"""


def _render_admin_parks_page(parks_list: list[dict[str, Any]]) -> str:
    items = []
    for park in parks_list:
        park_code = str(park.get("park_code") or "")
        name = str(park.get("name") or "Unknown park")
        state = str(park.get("state") or "")
        city = str(park.get("city") or "")
        location_bits = [bit for bit in [state, city] if bit]
        location = " · ".join(location_bits)
        status = "visited" if park.get("visited") else "planned" if park.get("planned") else "unvisited"
        status_label = "Visited" if status == "visited" else "Planned" if status == "planned" else "Not visited"
        search_blob = f"{name} {location}".lower().strip()
        items.append(
            f"""
            <li class="park-row" data-park-row data-park-code="{escape(park_code)}" data-park-status="{status}"
                data-park-visited="{str(bool(park.get('visited'))).lower()}" data-park-planned="{str(bool(park.get('planned'))).lower()}"
                data-park-filter="{escape(search_blob)}">
              <div class="park-main">
                <h3>{escape(name)}</h3>
                <p>{escape(location)}</p>
              </div>
              <div class="park-toggle" role="group" aria-label="Park status">
                <button class="park-toggle-btn{" is-active" if status == "visited" else ""}" type="button" data-park-set="visited">Visited</button>
                <button class="park-toggle-btn{" is-active" if status == "planned" else ""}" type="button" data-park-set="planned">Planned</button>
                <button class="park-toggle-btn{" is-active" if status == "unvisited" else ""}" type="button" data-park-set="unvisited">Not visited</button>
              </div>
            </li>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>National Parks · MilesMemories Admin</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: #fff9f0;
      --line: #dcccb4;
      --ink: #182233;
      --muted: #657286;
      --accent: #b85f35;
      --good: #2e6a4b;
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
    .panel {{
      background: rgba(255, 249, 240, 0.92);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 18px 40px var(--shadow);
      padding: 22px;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 20px;
    }}
    .eyebrow {{
      font-size: 0.82rem;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    h1 {{
      margin: 6px 0 0;
      font-size: clamp(2rem, 5vw, 3.6rem);
    }}
    .sub {{
      color: var(--muted);
      margin-top: 6px;
    }}
    .links {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
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
    .button.primary {{
      background: var(--accent);
      color: #fff;
    }}
    .parks-controls {{
      display: grid;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .parks-controls input {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 10px 12px;
      font: inherit;
      background: #fffdf8;
    }}
    .parks-controls .search {{
      width: 100%;
    }}
    .parks-tabs {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .parks-tab {{
      border: 1px solid var(--line);
      border-radius: 999px;
      height: 42px;
      width: 100%;
      padding: 0 16px;
      background: rgba(255, 255, 255, 0.65);
      color: var(--muted);
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}
    .parks-tab.is-active {{
      background: rgba(200, 100, 59, 0.16);
      color: var(--accent);
      border-color: rgba(200, 100, 59, 0.4);
    }}
    .parks-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 12px;
    }}
    .park-row {{
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 12px 14px;
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 14px;
      align-items: center;
      background: rgba(255, 255, 255, 0.6);
    }}
    .park-row:hover {{
      border-color: rgba(184, 95, 53, 0.45);
      box-shadow: 0 10px 22px rgba(37, 28, 14, 0.08);
    }}
    .park-main h3 {{
      margin: 0;
      font-size: 1.1rem;
    }}
    .park-main p {{
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .park-toggle {{
      display: inline-flex;
      border: 1px solid var(--line);
      border-radius: 999px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.7);
    }}
    .park-toggle-btn {{
      border: none;
      background: transparent;
      color: var(--muted);
      padding: 6px 12px;
      font: inherit;
      font-size: 0.82rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .park-toggle-btn:disabled {{
      opacity: 0.6;
      cursor: wait;
    }}
    .park-toggle-btn.is-active {{
      background: rgba(200, 100, 59, 0.18);
      color: var(--accent);
    }}
    .park-toggle-btn + .park-toggle-btn {{
      border-left: 1px solid var(--line);
    }}
    @media (max-width: 860px) {{
      .topbar {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .park-row {{
        grid-template-columns: 1fr;
      }}
      .park-toggle {{
        justify-content: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="topbar">
      <div>
        <div class="eyebrow">MilesMemories Admin</div>
        <h1>National parks checklist</h1>
        <p class="sub">Update each park's status and keep the checklist current.</p>
      </div>
      <div class="links">
        <a class="button" href="/admin">Back to admin</a>
        <a class="button" href="/trips">Public trips</a>
      </div>
    </section>

    <section class="panel">
      <div class="parks-controls">
        <div class="parks-tabs" data-admin-park-tabs>
          <button class="parks-tab" type="button" data-admin-park-tab="visited">Visited</button>
          <button class="parks-tab" type="button" data-admin-park-tab="planned">Planned</button>
          <button class="parks-tab" type="button" data-admin-park-tab="unvisited">Not visited</button>
          <button class="parks-tab is-active" type="button" data-admin-park-tab="all">All</button>
        </div>
        <input class="search" type="search" placeholder="Search parks..." data-admin-park-search>
      </div>
      <ul class="parks-list" data-admin-park-list>
        {''.join(items)}
      </ul>
    </section>
  </main>
  <script>
    (() => {{
      const list = document.querySelector("[data-admin-park-list]");
      if (!list) return;
      const search = document.querySelector("[data-admin-park-search]");
      const tabs = document.querySelector("[data-admin-park-tabs]");
      let activeStatus = "all";
      const activeTab = tabs?.querySelector(".parks-tab.is-active");
      if (activeTab?.dataset.adminParkTab) {{
        activeStatus = activeTab.dataset.adminParkTab;
      }}

      const setStatusPill = (row) => {{
        const visited = row.dataset.parkVisited === "true";
        const planned = row.dataset.parkPlanned === "true";
        let status = "unvisited";
        if (visited) {{
          status = "visited";
        }} else if (planned) {{
          status = "planned";
        }}
        row.dataset.parkStatus = status;
        row.querySelectorAll("[data-park-set]").forEach((button) => {{
          const isActive = button.dataset.parkSet === status;
          button.classList.toggle("is-active", isActive);
        }});
      }};

      const applyFilter = () => {{
        const term = (search?.value || "").trim().toLowerCase();
        list.querySelectorAll("[data-park-row]").forEach((row) => {{
          const haystack = row.dataset.parkFilter || "";
          const status = row.dataset.parkStatus || "unvisited";
          const matchesTerm = !term || haystack.includes(term);
          const matchesStatus = activeStatus === "all" || status === activeStatus;
          row.style.display = matchesTerm && matchesStatus ? "" : "none";
        }});
      }};

      search?.addEventListener("input", applyFilter);

      tabs?.addEventListener("click", (event) => {{
        const button = event.target.closest("[data-admin-park-tab]");
        if (!button) return;
        activeStatus = button.dataset.adminParkTab || "all";
        tabs.querySelectorAll(".parks-tab").forEach((tab) => {{
          tab.classList.toggle("is-active", tab === button);
        }});
        applyFilter();
      }});

      list.addEventListener("click", async (event) => {{
        const button = event.target.closest("[data-park-set]");
        if (!button) return;
        const row = button.closest("[data-park-row]");
        if (!row || row.dataset.parkSaving === "true") return;
        const status = button.dataset.parkSet || "unvisited";
        const code = row.dataset.parkCode;
        if (!code) return;
        const payload = {{
          visited: status === "visited",
          planned: status === "planned",
        }};
        row.dataset.parkSaving = "true";
        row.querySelectorAll("[data-park-set]").forEach((btn) => (btn.disabled = true));
        try {{
          const response = await fetch(`/admin/parks/${{encodeURIComponent(code)}}`, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(payload),
          }});
          if (!response.ok) throw new Error("Update failed");
          const data = await response.json();
          const park = data.park || {{}};
          row.dataset.parkVisited = park.visited ? "true" : "false";
          row.dataset.parkPlanned = park.planned ? "true" : "false";
          setStatusPill(row);
          applyFilter();
        }} catch (error) {{
          console.error(error);
        }} finally {{
          row.dataset.parkSaving = "false";
          row.querySelectorAll("[data-park-set]").forEach((btn) => (btn.disabled = false));
        }}
      }});

      applyFilter();
    }})();
  </script>
</body>
</html>"""


def _parse_flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return None


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
    .hero-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .hero-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
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
        <a class="button" href="/trips">Homepage</a>
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
    .maplibre-shell {{
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
      background: #efe5d7;
    }}
    .maplibre-map {{
      width: 100%;
      aspect-ratio: 16 / 9;
      min-height: 320px;
    }}
    .maplibregl-popup-content {{
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #fffdf7;
      box-shadow: 0 10px 26px rgba(33, 24, 14, 0.18);
      font-family: Georgia, "Times New Roman", serif;
    }}
    .maplibregl-ctrl.public-home-ctrl button {{
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 700;
      color: #2b3748;
    }}
    .maplibre-map .maplibregl-marker {{
      cursor: pointer;
    }}
    .maplibre-shell {{
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
      background: #efe5d7;
    }}
    .maplibre-map {{
      width: 100%;
      aspect-ratio: 16 / 9;
      min-height: 260px;
    }}
    .map-placeholder {{
      border: 1px dashed var(--line);
      border-radius: 18px;
      padding: 28px;
      color: var(--muted);
      text-align: center;
      background: rgba(255,255,255,0.55);
    }}
    .maplibregl-popup-content {{
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #fffdf7;
      box-shadow: 0 10px 26px rgba(33, 24, 14, 0.18);
      font-family: Georgia, "Times New Roman", serif;
    }}
    .maplibregl-ctrl.public-home-ctrl button {{
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 700;
      color: #2b3748;
    }}
    .maplibre-map .maplibregl-marker {{
      cursor: pointer;
    }}
    .maplibre-shell {{
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
      background: #efe5d7;
    }}
    .maplibre-map {{
      width: 100%;
      aspect-ratio: 16 / 9;
      min-height: 260px;
    }}
    .maplibregl-popup-content {{
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #fffdf7;
      box-shadow: 0 10px 26px rgba(33, 24, 14, 0.18);
      font-family: Georgia, "Times New Roman", serif;
    }}
    .maplibregl-ctrl.public-home-ctrl button {{
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 700;
      color: #2b3748;
    }}
    .maplibre-map .maplibregl-marker {{
      cursor: pointer;
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
    travel_legs = trip.get("travel_legs", [])
    leg_count = len(travel_legs) if travel_legs else int(trip.get("leg_count") or 0)
    map_points = trip.get("map_points") or [
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
    trip_map_payload = _build_public_trip_map_payload(
        trip,
        travel_legs,
        [{"lat": point["lat"], "lon": point["lon"]} for point in map_points],
    )
    trip_map_markup = _render_admin_trip_map(trip_map_payload)

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
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
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
    details.admin-legs > summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--accent);
      list-style: none;
      display: inline-flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    details.admin-legs > summary::-webkit-details-marker {{
      display: none;
    }}
    details.admin-legs > summary::after {{
      content: "Show";
      font-size: 0.88rem;
      color: var(--muted);
    }}
    details.admin-legs[open] > summary::after {{
      content: "Hide";
    }}
    .admin-legs-body {{
      display: grid;
      gap: 14px;
    }}
    .leg-loading {{
      color: var(--muted);
      font-weight: 600;
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
      overflow: hidden;
      cursor: grab;
    }}
    .leg-map-viewport {{
      position: absolute;
      inset: 0;
      transform-origin: center center;
      transition: transform 180ms ease;
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
      width: 100%;
    }}
    .leg-map-panel .maplibre-shell {{
      width: 100%;
      height: 100%;
    }}
    .leg-map-panel .maplibre-map {{
      width: 100%;
      height: 100%;
      min-height: 320px;
    }}
    .leg-map-svg {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      display: block;
    }}
    .leg-map-controls {{
      position: absolute;
      top: 14px;
      right: 14px;
      z-index: 5;
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 8px;
      border-radius: 999px;
      background: rgba(255, 248, 239, 0.92);
      border: 1px solid rgba(219, 202, 177, 0.9);
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .map-zoom-btn {{
      border: 0;
      border-radius: 999px;
      min-width: 40px;
      min-height: 34px;
      padding: 0 12px;
      background: rgba(29,36,48,0.06);
      color: var(--ink);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .map-zoom-btn:hover {{
      background: rgba(184,95,53,0.12);
      color: var(--accent-dark);
    }}
    .leg-map-tooltip {{
      position: absolute;
      top: 64px;
      right: 14px;
      z-index: 5;
      max-width: min(280px, calc(100% - 28px));
      padding: 10px 12px;
      border-radius: 16px;
      background: rgba(255, 248, 239, 0.96);
      border: 1px solid rgba(219, 202, 177, 0.94);
      color: var(--ink);
      font-size: 0.84rem;
      line-height: 1.4;
      box-shadow: 0 8px 18px rgba(37, 28, 14, 0.12);
    }}
    .map-stop-marker {{
      cursor: pointer;
      transition: filter 140ms ease, transform 140ms ease;
    }}
    .map-stop-marker .map-stop-pin,
    .map-stop-marker .map-stop-core {{
      transition: transform 140ms ease, stroke-width 140ms ease, opacity 140ms ease, filter 140ms ease;
      transform-origin: center;
      transform-box: fill-box;
    }}
    .map-stop-marker:hover .map-stop-pin,
    .map-stop-marker.is-active .map-stop-pin {{
      filter: brightness(1.05);
    }}
    .map-stop-marker:hover .map-stop-core,
    .map-stop-marker.is-active .map-stop-core {{
      transform: scale(1.1);
    }}
    .map-stop-marker:hover,
    .map-stop-marker.is-active {{
      filter: drop-shadow(0 7px 12px rgba(37, 28, 14, 0.22));
    }}
    .map-stop-cluster {{
      cursor: pointer;
      transition: transform 140ms ease, filter 140ms ease;
    }}
    .map-stop-cluster:hover,
    .map-stop-cluster:focus,
    .map-stop-cluster.is-active {{
      filter: drop-shadow(0 8px 16px rgba(37, 28, 14, 0.24));
      transform: scale(1.05);
      transform-origin: center;
      transform-box: fill-box;
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
        <div class="hero-head">
          <div class="eyebrow">Trip Overview</div>
          <div class="hero-actions">
            <a class="button" href="/admin">Back to queue</a>
            <a class="button" href="{destination_href}">Destination context</a>
            <a class="button utility" href="/admin/trips/{trip['id']}">Open JSON</a>
          </div>
        </div>
        <form class="trip-overview-form" method="post" action="/admin/trip/{trip['id']}/review" data-review-submit="ajax">
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
      </article>
    </section>

    <section class="panel">
      <h2>Travel legs</h2>
      <details class="admin-legs" data-trip-id="{trip['id']}">
        <summary>Expand travel legs ({leg_count})</summary>
        <div class="admin-legs-body">
          <div class="leg-loading">Travel legs load on demand.</div>
          <ul class="list" data-leg-list></ul>
        </div>
      </details>
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
      const bindLegSummaryInputs = (root) => {{
        const scope = root || document;
        scope.querySelectorAll(".leg-summary-input").forEach((node) => {{
          ["click", "focus", "keydown", "mousedown", "mouseup"].forEach((eventName) => {{
            node.addEventListener(eventName, (event) => event.stopPropagation());
          }});
        }});
      }};

      const autosaveForm = async (form) => {{
        if (!form || form.dataset.saveState === "saving") {{
          return;
        }}
        if (form.dataset.actionInFlight === "true") {{
          return;
        }}
        const body = new URLSearchParams(new FormData(form));
        const actionUrl = form.getAttribute("action") || form.action;
        form.dataset.saveState = "saving";
        try {{
              const response = await fetch(actionUrl, {{
                method: "POST",
                credentials: "same-origin",
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

      const bindSegmentForm = (form) => {{
        if (!form) return;
        if (form.dataset.autosaveBound === "true") return;
        form.dataset.autosaveBound = "true";
        form.dataset.savedKey = "segment";
        const summaryField = form.querySelector(".leg-summary-input");
        if (summaryField) {{
          summaryField.addEventListener("blur", () => autosaveForm(form));
        }}
        form.querySelectorAll('input[name="rating"]').forEach((field) => {{
          field.addEventListener("change", () => autosaveForm(form));
        }});
      }};

      const bindSegmentForms = (root) => {{
        const scope = root || document;
        scope.querySelectorAll('form[data-autosave="segment"]').forEach((form) => {{
          bindSegmentForm(form);
        }});
      }};

      bindLegSummaryInputs(document);
      bindSegmentForms(document);

      const overviewForm = document.querySelector(".trip-overview-form");
      if (overviewForm) {{
        overviewForm.dataset.savedKey = "details";
        const actionField = document.createElement("input");
        actionField.type = "hidden";
        actionField.name = "action";
        overviewForm.appendChild(actionField);
        overviewForm
          .querySelectorAll('input[name="trip_name"], input[name="reviewer_name"], textarea[name="summary_text"], textarea[name="review_notes"]')
          .forEach((field) => {{
            field.addEventListener("blur", () => autosaveForm(overviewForm));
          }});

        overviewForm.querySelectorAll('button[name="action"]').forEach((btn) => {{
          btn.addEventListener("pointerdown", () => {{
            overviewForm.dataset.actionInFlight = "true";
            actionField.value = btn.value;
          }});
        }});

        overviewForm.addEventListener("submit", async (event) => {{
          if (overviewForm.dataset.reviewSubmit === "full") {{
            return;
          }}
          const submitter = event.submitter;
          if (!submitter || submitter.name !== "action") {{
            return;
          }}
          actionField.value = submitter.value;
          if (overviewForm.dataset.saveState === "saving") {{
            delete overviewForm.dataset.saveState;
          }}
          if (overviewForm.dataset.forceSubmit === "true") {{
            delete overviewForm.dataset.forceSubmit;
            return;
          }}
          event.preventDefault();
          if (overviewForm.dataset.actionTimer) {{
            window.clearTimeout(Number(overviewForm.dataset.actionTimer));
          }}
          overviewForm.dataset.actionTimer = String(window.setTimeout(async () => {{
            delete overviewForm.dataset.actionTimer;
            const body = new URLSearchParams(new FormData(overviewForm));
            body.set("action", submitter.value);
            overviewForm.dataset.saveState = "saving";
            try {{
              const actionUrl = overviewForm.getAttribute("action") || overviewForm.action;
              const response = await fetch(actionUrl, {{
                method: "POST",
                credentials: "same-origin",
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
              const reviewMap = {{
                confirm: "yes",
                reject: "no",
              }};
              reviewButtons.forEach((node) => {{
                const expected = reviewMap[node.value] || "";
                const active = payload.review_state === expected;
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
              overviewForm.dataset.forceSubmit = "true";
              overviewForm.submit();
            }} finally {{
              delete overviewForm.dataset.actionInFlight;
            }}
          }}, 160));
        }});
      }}

      document.querySelectorAll("details.admin-legs").forEach((details) => {{
        const list = details.querySelector("[data-leg-list]");
        const loading = details.querySelector(".leg-loading");
        const tripId = details.dataset.tripId;
        if (!list || !tripId) return;
        let loaded = false;

        const loadLegs = async () => {{
          if (loaded) return;
          loaded = true;
          if (loading) {{
            loading.textContent = "Loading travel legs…";
          }}
          try {{
            const response = await fetch(`/admin/trip/${{tripId}}/legs`, {{
              credentials: "same-origin"
            }});
            if (!response.ok) {{
              throw new Error(`Failed to load travel legs (${{response.status}})`);
            }}
            const html = await response.text();
            list.innerHTML = html;
            if (loading) {{
              loading.remove();
            }}
            bindLegSummaryInputs(list);
            bindSegmentForms(list);
            if (window.milesMemoriesInitLegMaps) {{
              window.milesMemoriesInitLegMaps(list);
            }}
          }} catch (error) {{
            console.error(error);
            if (loading) {{
              loading.textContent = "Unable to load travel legs.";
            }}
          }}
        }};

        details.addEventListener("toggle", () => {{
          if (details.open) {{
            loadLegs();
          }}
        }});

        if (details.open) {{
          loadLegs();
        }}
      }});

      const toast = document.querySelector("[data-toast]");
      if (toast) {{
        window.setTimeout(() => {{
          toast.classList.add("is-hiding");
          window.setTimeout(() => toast.remove(), 240);
        }}, 3800);
      }}
    }})();
  </script>
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  {_render_public_maplibre_script()}
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_homepage(
    view: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    review_decision: Optional[str] = Query(default=None),
    include_private: Optional[bool] = Query(default=None),
    private_only: Optional[bool] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
) -> HTMLResponse:
    only_private = bool(private_only)
    if view:
        view_key = view.lower()
        status = None
        review_decision = None
        include_private = True
        only_private = False
        if view_key == "needs_review":
            status = "needs_review"
        elif view_key == "reviewed":
            review_decision = "confirmed"
        elif view_key == "published":
            status = "published"
        elif view_key == "rejected":
            review_decision = "rejected"
        elif view_key == "ignored":
            review_decision = "rejected"
        elif view_key == "private":
            only_private = True
        elif view_key == "public":
            include_private = False
    if include_private is None:
        include_private = True
    counts = trip_admin.get_trip_status_counts()
    trips = trip_admin.list_trips(
        status=status,
        review_decision=review_decision,
        include_private=include_private,
        only_private=only_private,
        limit=limit,
    )
    return _html_response(
        _render_admin_page(
            trips,
            status=status,
            review_decision=review_decision,
            include_private=include_private,
            only_private=only_private,
            limit=limit,
            counts=counts,
            current_view=view,
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
    start_time = time.perf_counter()
    trip = trip_admin.get_trip_light(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    snapshot = trip_admin.get_trip_snapshot(trip_id)
    if snapshot and snapshot.get("public"):
        public_payload = snapshot["public"]
        trip["travel_legs"] = public_payload.get("travel_legs", [])
        trip["map_points"] = public_payload.get("map_points", [])
    elif trip.get("leg_count"):
        trip["travel_legs"] = []
        trip["map_points"] = trip_admin.get_trip_route_points(trip_id, append_home_if_close=True)
    else:
        trip["travel_legs"] = []
        trip["map_points"] = []
    trip["matching_overrides"] = _matching_overrides_for_trip(trip)
    trip["neighbors"] = trip_admin.get_trip_neighbors(trip_id)
    response = _html_response(_render_trip_detail_page(trip, saved=saved))
    _log_timing("admin_trip_detail", start_time)
    return response


@app.get("/admin/trip/{trip_id}/legs", response_class=HTMLResponse)
def admin_trip_leg_items(trip_id: int) -> HTMLResponse:
    snapshot = trip_admin.get_trip_snapshot(trip_id)
    if not snapshot:
        snapshot = trip_admin.build_trip_snapshot(trip_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Trip not found")
    travel_legs = snapshot.get("public", {}).get("travel_legs", [])
    return HTMLResponse(_render_admin_leg_items(trip_id, travel_legs))


@app.post("/admin/trip/{trip_id}/review")
async def review_trip_from_form(
    trip_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    start_time = time.perf_counter()
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

    updated = trip_admin.record_review_light(
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
    if action in {"publish", "mark_private"}:
        background_tasks.add_task(trip_admin.build_trip_snapshot, trip_id)
    if request_headers.get("x-requested-with") == "fetch":
        if action == "save":
            _log_timing("admin_review_action", start_time)
            return Response(status_code=204)
        saved_key = "details"
        message = "Trip details saved."
        if action == "publish":
            saved_key = "published"
            message = "Trip published and marked ready."
        elif action == "mark_private":
            saved_key = "privacy"
            message = "Trip visibility updated."
        elif action in {"confirm", "reject", "ignore"}:
            saved_key = "review"
            message = "Review saved."
        _log_timing("admin_review_action", start_time)
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
    _log_timing("admin_review_action", start_time)
    return RedirectResponse(url=f"/admin/trip/{trip_id}?saved={saved_key}", status_code=303)


@app.post("/admin/trip/{trip_id}/segments/{segment_id}")
async def update_trip_segment_from_form(
    trip_id: int,
    segment_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(trip_admin.build_trip_snapshot, trip_id)
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
    private_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> List[TripSummary]:
    return trip_admin.list_trips(
        status=status,
        review_decision=review_decision,
        include_private=include_private,
        only_private=private_only,
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
