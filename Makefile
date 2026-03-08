PYTHON ?= python3
DB_URL ?= postgresql://miles:milespass@localhost:5432/milesmemories
RADIUS ?= 16093

.PHONY: db-init run-api ingest-location ingest-photos ingest-garmin set-home detect-trips

db-init:
	psql "$(DB_URL)" -f database/schema.sql

.PHONY: db-init-docker
db-init-docker:
	cat database/schema.sql | docker compose exec -T db psql -U miles -d milesmemories

run-api:
	$(PYTHON) -m app.main

ingest-location:
	$(PYTHON) scripts/ingest_location.py --file "$(FILE)"

ingest-photos:
	$(PYTHON) scripts/ingest_photos.py --file "$(FILE)"

ingest-garmin:
	$(PYTHON) scripts/ingest_garmin.py --file "$(FILE)"

set-home:
	$(PYTHON) scripts/set_home.py --lat "$(LAT)" --lon "$(LON)" --local-radius-meters "$(RADIUS)"

detect-trips:
	$(PYTHON) scripts/detect_trips.py
