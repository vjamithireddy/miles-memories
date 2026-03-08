# Garmin MCP Setup

## Status
The Garmin MCP server code is in place, but it requires Python 3.10+ because the MCP SDK does not support Python 3.9.

Implemented server:
- `mcp_server/garmin_server.py`

Runner:
- `scripts/run_garmin_mcp.py`

Make target:
- `make run-garmin-mcp`

## Tools Exposed
- `ingest_garmin_export(file_path)`
- `list_activities(limit, offset, activity_type)`
- `get_activity(activity_id)`
- `activity_stats(days)`

## Enable On Your Machine
1. Install Python 3.11 (or 3.10+).
2. Recreate venv with 3.11:
   - `python3.11 -m venv .venv`
3. Install dependencies:
   - `.venv/bin/pip install --upgrade pip`
   - `.venv/bin/pip install -e .`
   - `.venv/bin/pip install "milesmemories[mcp]"`
4. Run server:
   - `make PYTHON=.venv/bin/python run-garmin-mcp`

## Quick Local Validation
In a second shell, run one Garmin ingestion through existing CLI first:
- `make PYTHON=.venv/bin/python ingest-garmin FILE=/absolute/path/activity.gpx`

Then query via MCP client with:
- `list_activities`
- `activity_stats`
