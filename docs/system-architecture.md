# MilesMemories - System Architecture

## 1. Deployment Context
- `navi-services.com` remains WordPress on Hostinger hosting.
- `travel.navi-services.com` is separate on Hostinger Linux VPS.
- Travel platform is isolated from WordPress stack.

## 2. High-Level Flow
1. Upload/import data (Takeout Photos, Takeout Location History, Garmin exports)
2. Normalize into DB
3. Run trip detection
4. Run admin review workflow
5. Generate site content
6. Publish to `travel.navi-services.com`

## 3. Components

### Ingestion Layer
- Web upload, SFTP, or CLI
- Import validation, dedupe, and job tracking

### Normalization Layer
- Parse JSON sidecars, EXIF, GPX/FIT
- Store normalized records with raw payload traceability

### Travel Database
- PostgreSQL preferred (SQLite ok for local dev)
- System-of-record for events, trips, participants, reviews, publish logs

### Trip Detection Engine
- Rules-based MVP
- Home/departure/return logic
- Destination clustering and confidence scoring

### Admin Review Layer
- Required manual approval before publish
- Agree/disagree/edit/merge/split/private/ignore/publish
- Participant confirmation and visibility controls

### Content Generator
- Builds timeline-based trip pages and map views
- AI-assisted summaries (editable)
- Static output preferred for MVP

### Web Delivery Layer
- Nginx serves generated site
- `/admin` protected interface for ingestion/review/publish

## 4. Source-Specific Strategy

### Google Photos
- MVP source: Google Takeout uploads
- Process images/videos + sidecar metadata
- Face-grouping hints are suggestions only

### Android Location History
- MVP source: Google Takeout uploads
- Real-time logging deferred

### Garmin
- MVP source: exported activity files
- API/MCP revisit later

## 5. Privacy Architecture
- Mask home-area coordinates/route segments
- Keep raw imports private
- Manual participant confirmation required
- Respect private trips and hidden photos

## 6. Operations
- Cron-based scheduled processing (MVP)
- Daily backup of DB + generated site + config
- SSL via Let's Encrypt

## 7. Recommended MVP Stack
- Python ingestion and trip engine
- PostgreSQL
- Lightweight admin UI (FastAPI/Flask/Django or Node alternative)
- Static site generation
- Nginx on VPS
