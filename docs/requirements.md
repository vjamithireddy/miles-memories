# MilesMemories - Requirements

## 1. Overview
MilesMemories is a personal travel blog platform that builds trip stories from:
- Google Photos metadata
- Garmin activity data
- Android location history (Google Takeout)

Site host:
- Public travel site: `travel.navi-services.com` (Hostinger VPS, Linux)
- Primary site remains separate: `navi-services.com` (WordPress on Hostinger)

## 2. Goals
- Automatically create trip entries from uploaded data.
- Build unified trip timelines from photos, activities, and location history.
- Show maps for visited places, routes, and photo points.
- Generate AI-assisted trip summaries.
- Support public/private publishing per trip.
- Enable search by location, year, and activity type.
- Maintain a long-term searchable travel archive.

## 3. Non-Goals (MVP)
- Social features/comments
- Multi-user blogging
- Full CMS
- Monetization

## 4. Data Sources
### 4.1 Google Photos
- Ingestion method (MVP): Google Takeout upload
- Use sidecar JSON + EXIF metadata
- Use face-grouping hints only for participant suggestions

### 4.2 Garmin
- Ingestion method (MVP): export files (GPX/FIT/CSV where available)
- API/MCP integration can be revisited later

### 4.3 Android Location History
- Ingestion method (MVP): Google Takeout upload (manual)
- Real-time mobile logging deferred to later phase

## 5. Core Features
- Trip detection (local/day/overnight/multi-day)
- Timeline builder across sources
- Activity visualization (distance/elevation/duration/routes)
- Photo galleries and map overlays
- Admin review queue before publish
- Participant capture and confirmation workflow

## 6. Participant Capture and Confirmation
- Support participants: self/family/friends/other
- Status per participant: `suggested`, `confirmed`, `declined`
- Face-grouping hints can suggest participants but cannot auto-confirm
- Public display mode per participant: full name, first name only, relationship, hidden

## 7. Review and Publishing Workflow
Before publishing a trip, user must review and decide:
- Agree / Disagree with identified trip
- Edit details
- Merge / Split candidates
- Mark private / Ignore / Publish

Publish safeguards:
- Must be `confirmed`
- Must not be private
- Required metadata present (title, date range, destination)
- Participant visibility resolved

## 8. Hosting and Deployment
- WordPress remains on `navi-services.com`
- MilesMemories runs independently on VPS at `travel.navi-services.com`
- Nginx + SSL (Let's Encrypt)
- Background jobs via cron for ingestion/detection/build

## 9. Privacy and Security
- Mask home coordinates and routes near home
- Support private trips
- Do not auto-publish participant identities from hints
- Keep raw imports private

## 10. Milestones
1. Data ingestion (Takeout + Garmin exports)
2. Trip detection engine
3. Admin review + participant confirmation
4. Site generation + publish workflow
5. Automation and operational hardening
