PYTHON ?= python3
DB_URL ?= postgresql://miles:milespass@localhost:5432/milesmemories
RADIUS ?= 16093

.PHONY: db-init run-api ingest-location ingest-photos ingest-garmin set-home detect-trips build-latest-trips-from-timeline build-garmin-trips refresh-recent-auto-trips run-garmin-mcp

db-init:
	psql "$(DB_URL)" -f database/schema.sql

.PHONY: db-init-docker
db-init-docker:
	cat database/schema.sql | docker compose exec -T db psql -U miles -d milesmemories

run-api:
	PYTHONPATH=. $(PYTHON) -m app.main

ingest-location:
	PYTHONPATH=. $(PYTHON) scripts/ingest_location.py --file "$(FILE)"

ingest-photos:
	PYTHONPATH=. $(PYTHON) scripts/ingest_photos.py --file "$(FILE)"

ingest-garmin:
	PYTHONPATH=. $(PYTHON) scripts/ingest_garmin.py --file "$(FILE)"

set-home:
	PYTHONPATH=. $(PYTHON) scripts/set_home.py --lat "$(LAT)" --lon "$(LON)" --local-radius-meters "$(RADIUS)"

detect-trips:
	PYTHONPATH=. $(PYTHON) scripts/detect_trips.py

build-latest-trips-from-timeline:
	PYTHONPATH=. $(PYTHON) scripts/build_latest_trips_from_timeline.py --file "$(FILE)"

build-garmin-trips:
	PYTHONPATH=. $(PYTHON) scripts/build_garmin_trips.py

refresh-recent-auto-trips:
	PYTHONPATH=. $(PYTHON) scripts/refresh_recent_auto_trips.py --min-trip-id "$(MIN_TRIP_ID)"

run-garmin-mcp:
	@PYTHONPATH=. $(PYTHON) scripts/run_garmin_mcp.py
