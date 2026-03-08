PYTHON ?= python3
DB_URL ?= postgresql://miles:milespass@localhost:5432/milesmemories

.PHONY: db-init run-api ingest-location ingest-photos ingest-garmin detect-trips

db-init:
	psql "$(DB_URL)" -f database/schema.sql

run-api:
	$(PYTHON) -m app.main

ingest-location:
	$(PYTHON) scripts/ingest_location.py --file "$(FILE)"

ingest-photos:
	$(PYTHON) scripts/ingest_photos.py --file "$(FILE)"

ingest-garmin:
	$(PYTHON) scripts/ingest_garmin.py --file "$(FILE)"

detect-trips:
	$(PYTHON) scripts/detect_trips.py
