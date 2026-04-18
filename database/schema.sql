CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL,
    email TEXT,
    home_latitude DOUBLE PRECISION,
    home_longitude DOUBLE PRECISION,
    home_privacy_radius_meters INTEGER DEFAULT 500,
    home_local_radius_meters INTEGER DEFAULT 16093,
    work_latitude DOUBLE PRECISION,
    work_longitude DOUBLE PRECISION,
    work_local_radius_meters INTEGER DEFAULT 1609,
    timezone TEXT DEFAULT 'America/Chicago',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS work_latitude DOUBLE PRECISION;
ALTER TABLE users ADD COLUMN IF NOT EXISTS work_longitude DOUBLE PRECISION;
ALTER TABLE users ADD COLUMN IF NOT EXISTS work_local_radius_meters INTEGER DEFAULT 1609;

CREATE TABLE IF NOT EXISTS imports (
    id BIGSERIAL PRIMARY KEY,
    import_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT,
    file_hash TEXT,
    import_status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (file_hash)
);

CREATE TABLE IF NOT EXISTS places (
    id BIGSERIAL PRIMARY KEY,
    place_name TEXT NOT NULL,
    city TEXT,
    region TEXT,
    country TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    place_type TEXT,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS location_events (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT REFERENCES imports(id) ON DELETE SET NULL,
    source_event_id TEXT,
    event_timestamp TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    accuracy_meters DOUBLE PRECISION,
    altitude_meters DOUBLE PRECISION,
    velocity_mps DOUBLE PRECISION,
    heading_degrees DOUBLE PRECISION,
    source TEXT NOT NULL,
    raw_payload_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stay_points (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    radius_meters DOUBLE PRECISION,
    duration_minutes INTEGER,
    inferred_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    inferred_place_name TEXT,
    stay_type TEXT DEFAULT 'unknown',
    confidence_score INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trips (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    trip_name TEXT,
    trip_slug TEXT UNIQUE,
    trip_type TEXT,
    status TEXT NOT NULL DEFAULT 'detected',
    review_decision TEXT NOT NULL DEFAULT 'pending',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    start_date DATE,
    end_date DATE,
    primary_destination_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    primary_destination_name TEXT,
    origin_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    origin_place_name TEXT,
    confidence_score INTEGER,
    summary_text TEXT,
    cover_photo_id BIGINT,
    is_private BOOLEAN NOT NULL DEFAULT TRUE,
    publish_ready BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    created_by TEXT DEFAULT 'system',
    detection_version TEXT DEFAULT 'v0',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS photos (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT REFERENCES imports(id) ON DELETE SET NULL,
    source_photo_id TEXT,
    filename TEXT NOT NULL,
    original_filepath TEXT,
    storage_path TEXT,
    media_type TEXT NOT NULL DEFAULT 'photo',
    captured_at TIMESTAMPTZ,
    uploaded_at TIMESTAMPTZ,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    altitude_meters DOUBLE PRECISION,
    width INTEGER,
    height INTEGER,
    camera_make TEXT,
    camera_model TEXT,
    album_name TEXT,
    inferred_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    inferred_place_name TEXT,
    trip_id BIGINT REFERENCES trips(id) ON DELETE SET NULL,
    is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
    visibility_status TEXT NOT NULL DEFAULT 'review',
    raw_metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_trips_cover_photo'
    ) THEN
        ALTER TABLE trips
            ADD CONSTRAINT fk_trips_cover_photo
            FOREIGN KEY (cover_photo_id) REFERENCES photos(id) ON DELETE SET NULL;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS photo_person_hints (
    id BIGSERIAL PRIMARY KEY,
    photo_id BIGINT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_hint_key TEXT,
    person_label TEXT,
    source TEXT NOT NULL,
    confidence_score INTEGER,
    review_status TEXT NOT NULL DEFAULT 'suggested',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS activities (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT REFERENCES imports(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'garmin',
    source_activity_id TEXT,
    activity_type TEXT NOT NULL,
    activity_name TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    duration_seconds INTEGER,
    distance_meters DOUBLE PRECISION,
    elevation_gain_meters DOUBLE PRECISION,
    elevation_loss_meters DOUBLE PRECISION,
    moving_time_seconds INTEGER,
    elapsed_time_seconds INTEGER,
    average_speed_mps DOUBLE PRECISION,
    max_speed_mps DOUBLE PRECISION,
    average_heart_rate INTEGER,
    max_heart_rate INTEGER,
    calories INTEGER,
    start_latitude DOUBLE PRECISION,
    start_longitude DOUBLE PRECISION,
    end_latitude DOUBLE PRECISION,
    end_longitude DOUBLE PRECISION,
    route_polyline TEXT,
    trip_id BIGINT REFERENCES trips(id) ON DELETE SET NULL,
    inferred_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    inferred_place_name TEXT,
    raw_metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE activities ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'garmin';

CREATE UNIQUE INDEX IF NOT EXISTS uq_activities_source_activity
    ON activities(source, source_activity_id);

CREATE TABLE IF NOT EXISTS trip_segments (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    segment_type TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    start_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    start_place_name TEXT,
    end_place_id BIGINT REFERENCES places(id) ON DELETE SET NULL,
    end_place_name TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trip_snapshots (
    trip_id BIGINT PRIMARY KEY REFERENCES trips(id) ON DELETE CASCADE,
    public_payload_json JSONB NOT NULL,
    admin_payload_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE trip_segments ADD COLUMN IF NOT EXISTS segment_name TEXT;
ALTER TABLE trip_segments ADD COLUMN IF NOT EXISTS rating INTEGER;
ALTER TABLE trip_segments ADD COLUMN IF NOT EXISTS source_event_id TEXT;

CREATE TABLE IF NOT EXISTS trip_events (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_ref_id BIGINT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    sort_order INTEGER,
    day_index INTEGER,
    timeline_label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trip_participants (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    participant_key TEXT,
    participant_name TEXT,
    participant_type TEXT DEFAULT 'unknown',
    status TEXT NOT NULL DEFAULT 'suggested',
    display_mode TEXT NOT NULL DEFAULT 'first_name_only',
    source TEXT NOT NULL DEFAULT 'manual',
    confidence_score INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_reviews (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    reviewer_name TEXT,
    review_action TEXT NOT NULL,
    review_notes TEXT,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS publish_records (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    publish_status TEXT NOT NULL DEFAULT 'pending',
    publish_target TEXT NOT NULL DEFAULT 'website',
    published_url TEXT,
    published_at TIMESTAMPTZ,
    build_id TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS destination_overrides (
    id BIGSERIAL PRIMARY KEY,
    rule_name TEXT NOT NULL,
    match_pattern TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    radius_meters INTEGER DEFAULT 1000,
    classification TEXT NOT NULL,
    keep_trip BOOLEAN NOT NULL DEFAULT FALSE,
    ignore_trip BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS national_parks (
    park_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT,
    city TEXT,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    visited BOOLEAN NOT NULL DEFAULT FALSE,
    planned BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_location_events_timestamp ON location_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_location_events_lat_lon ON location_events(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_photos_captured_at ON photos(captured_at);
CREATE INDEX IF NOT EXISTS idx_activities_start_time ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_trips_time_range ON trips(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_trip_events_trip_sort ON trip_events(trip_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_trip_participants_trip ON trip_participants(trip_id);
