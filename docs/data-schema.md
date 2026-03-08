# MilesMemories - Data Schema

## 1. Core Tables (MVP)
- `users`
- `imports`
- `location_events`
- `stay_points`
- `photos`
- `photo_person_hints`
- `activities`
- `trips`
- `trip_segments`
- `trip_events`
- `trip_participants`
- `admin_reviews`
- `publish_records`
- `places`

## 2. Key Table Notes

### users
Single-user MVP, future multi-user compatible.
Important fields: home center/radii, timezone.

### imports
Tracks each uploaded file/job for dedupe and audit.
Fields: type, filename, hash, status, started/completed timestamps.

### location_events
Normalized Android/Timeline location points.
Fields: timestamp, lat/lon, accuracy, source, raw payload JSON.

### stay_points
Derived dwell clusters used for destination and overnight detection.

### photos
Normalized photo metadata from Takeout + EXIF.
Fields include capture time, lat/lon, media type, storage path, inferred place, trip link.

### photo_person_hints
Face-grouping and person hints.
Fields: hint key, label, confidence, review status.
Used for suggestions only.

### activities
Garmin activity summaries and route references.
Fields: type, start/end, distance, elevation, speeds, trip link.

### trips
Primary trip object.
Fields: name, slug, type, status, review_decision, time range, destination, confidence, privacy, publish flags.

### trip_segments
Optional phases: outbound, exploration, activity, return.

### trip_events
Timeline join table mapping events to trip in order.

### trip_participants
Participants per trip with status and display controls.
Statuses: `suggested`, `confirmed`, `declined`.
Display: full name, first-name-only, relationship, hidden.

### admin_reviews
Audit of actions: agree/disagree/edit/merge/split/private/publish.

### publish_records
Tracks publish jobs and outputs (URL/status/errors).

## 3. Critical Relationships
- `imports` 1..n `location_events`, `photos`, `activities`
- `trips` 1..n `trip_segments`, `trip_events`, `trip_participants`
- `photos` 1..n `photo_person_hints`
- `places` referenced by trips/photos/activities/stay_points

## 4. Privacy Controls in Schema
- `trips.is_private`
- `photos.visibility_status`
- `trip_participants.display_mode`
- Masking logic for home-area coordinates at publish time

## 5. MVP First Build Order
1. `imports`
2. `location_events`
3. `photos`
4. `activities`
5. `trips`
6. `trip_events`
7. `trip_participants`
8. `photo_person_hints`
9. `admin_reviews`
