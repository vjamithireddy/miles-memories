# MilesMemories - Trip Detection Engine

## 1. Purpose
Convert normalized location, photo, and Garmin activity events into meaningful trips and timelines.

## 2. Inputs
- `location_events` from Google Takeout (Android location history)
- `photos` from Google Takeout + EXIF
- `activities` from Garmin exports

## 3. Outputs
- `trips` candidates with confidence score
- `trip_segments`
- `trip_events` ordered timeline mapping
- Participant suggestions (from photo hints + manual edits)

## 4. Trip Types
- `local_activity`
- `day_trip`
- `overnight_trip`
- `multi_day_trip`

## 5. Core Concepts
- Home area with two radii:
  - Privacy radius: hide exact home-region details
  - Local radius: classify local vs travel
- Stay point: location cluster with dwell time
- Movement gap: sparse period that may indicate transit or data gap

## 6. MVP Rules-Based Detection
1. Merge all source events into chronological stream.
2. Cluster events by time + proximity.
3. Detect departure from home region.
4. Detect destination clusters/stay points.
5. Detect return-home stabilization.
6. Classify trip type.

## 7. Heuristic Rules
- Distance threshold from home for travel candidacy
- Minimum duration threshold
- Overnight window detection
- Garmin boost: far-from-home activities increase confidence
- Photo boost: multiple geotagged photos increase confidence

## 8. Confidence Scoring
Trip candidate gets score from combined signals:
- Distance from home
- Duration away
- Overnight signal
- Activity signal
- Photo density signal
- Stable destination cluster

Bands:
- 80-100: strong
- 50-79: likely
- <50: weak (no auto-publish)

## 9. Naming
Generate provisional trip names from:
1. Destination cluster place
2. Garmin activity locale/title
3. Photo metadata
4. Reverse geocode fallback

Names are always editable before publish.

## 10. Merge and Split
Merge when short time gaps and no confirmed return-home state.
Split when clear return-home signal or large continuity break.

## 11. Edge Cases
- Sparse location data
- Missing GPS on photos
- Flight days with long data gaps
- Repeated visits to same destination

## 12. Participants
- Suggestions from face-grouping hints are allowed
- Never auto-confirm participants
- Manual confirm/decline required in admin review

## 13. Review Gate (Required)
A trip cannot be published until reviewed:
- Agree/Disagree
- Edit/Merge/Split
- Participant confirmation
- Privacy decision

## 14. Lifecycle
`detected -> needs_review -> confirmed -> published`
Alternative exits: `rejected`, `private`, `archived`
