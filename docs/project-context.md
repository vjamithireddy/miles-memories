# MilesMemories - Project Context

## Project Identity
- Name: `MilesMemories`
- Domain: `travel.navi-services.com`
- Primary domain remains separate: `navi-services.com` (WordPress)

## Problem to Solve
Automatically build a personal travel blog from personal data sources while preserving privacy and requiring explicit review before public publishing.

## Confirmed Decisions
- Android location ingestion (MVP): Option 1, manual Google Takeout upload.
- Garmin ingestion (MVP): export files, no hard dependency on direct API/MCP.
- Google Photos ingestion (MVP): Takeout metadata + EXIF.
- Participant detection: face-grouping hints can suggest only.
- Publishing gate: must agree/disagree and confirm trip details before publish.
- Host architecture: separate VPS stack for travel subdomain.

## Required Review Controls
Before publish, user must be able to:
- Agree/Disagree with trip detection
- Edit/merge/split trip
- Confirm/decline participants
- Set participant visibility
- Mark trip private or publish

## Privacy Requirements
- Do not expose exact home area publicly.
- Do not auto-publish participant identities from hints.
- Support private trips and hidden photos.

## Primary Docs
- `docs/requirements.md`
- `docs/trip-detection-engine.md`
- `docs/data-schema.md`
- `docs/system-architecture.md`

## Suggested Next Implementation Steps
1. Create SQL schema + migrations from `data-schema.md`.
2. Build import tracker + upload endpoints.
3. Implement location parser (Takeout) and photo parser (Takeout + EXIF).
4. Implement Garmin parser and normalized activity ingestion.
5. Implement rules-based trip detection and review queue.
6. Implement publish pipeline to static pages.
