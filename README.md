# MilesMemories

MilesMemories builds a personal travel site from Google Takeout (Photos + Android location history) and Garmin activity exports.

## Current Status
- Product docs are in `docs/`
- Initial project scaffold is in place
- PostgreSQL schema is in `database/schema.sql`

## Project Layout
- `docs/` specs and architecture decisions
- `app/` API/admin service entrypoints
- `ingestion/` source parsers and normalizers
- `trip_engine/` trip detection logic
- `publisher/` static site generation logic
- `database/` SQL schema and migrations
- `scripts/` runnable CLI wrappers

## Local Development
1. Create environment file:
   - `cp env.example .env`
2. Start PostgreSQL:
   - `docker compose up -d db`
3. Apply schema:
   - `make db-init`
4. Run API service:
   - `make run-api`

## Commands
- `make db-init` initialize DB schema
- `make db-init-docker` initialize DB schema using containerized `psql` (no local `psql` needed)
- `make run-api` start local API service
- `make ingest-location FILE=/path/to/location.json`
- `make ingest-photos FILE=/path/to/takeout.zip`
- `make ingest-garmin FILE=/path/to/activity.gpx`
- `make set-home LAT=<home_lat> LON=<home_lon> RADIUS=16093`
- `make detect-trips` run rules-based trip detection (v0)
- `make run-garmin-mcp` run MCP server for Garmin activity tools over stdio

## Local MVP Flow
1. `docker compose up -d db`
2. `make db-init`
   - If `psql` is missing locally, use `make db-init-docker`
3. Set home coordinates:
   - `make set-home LAT=38.9517 LON=-92.3341`
4. Ingest files:
   - `make ingest-location FILE=/absolute/path/Location\ History.json`
   - `make ingest-photos FILE=/absolute/path/google-photos-takeout.zip`
   - `make ingest-garmin FILE=/absolute/path/activity.gpx`
5. Detect trips:
   - `make detect-trips`

## When You Need To Intervene
- VPS provisioning and DNS changes
- SSL setup on Hostinger
- GitHub Actions secrets
- Any API credentials (if/when direct API integrations are added)

## Garmin MCP Server
MilesMemories includes a local MCP server for Garmin data in:
- `mcp_server/garmin_server.py`

Tools exposed:
- `ingest_garmin_export(file_path)`
- `list_activities(limit, offset, activity_type)`
- `get_activity(activity_id)`
- `activity_stats(days)`

Run it:
- `make PYTHON=.venv/bin/python run-garmin-mcp`

Requirements for MCP server:
- Python 3.10+ (MCP SDK requirement)
- Install MCP SDK: `.venv/bin/pip install \"milesmemories[mcp]\"` or `.venv/bin/pip install mcp`

Note:
- Your current local runtime is Python 3.9, so MCP server startup will fail until you create a Python 3.10+ virtual environment.
