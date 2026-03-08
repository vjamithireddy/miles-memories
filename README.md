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
