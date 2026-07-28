CREATE TABLE IF NOT EXISTS flights (
    id VARCHAR(12) PRIMARY KEY,
    callsign VARCHAR(12) NOT NULL,
    airline VARCHAR(4) DEFAULT 'QR',
    aircraft_type VARCHAR(4) NOT NULL,
    origin VARCHAR(4),
    destination VARCHAR(4),
    scheduled_arr TIMESTAMPTZ,
    actual_arr TIMESTAMPTZ,
    scheduled_dep TIMESTAMPTZ,
    actual_dep TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
    assigned_gate VARCHAR(6),
    pax_count INT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gates (
    id VARCHAR(6) PRIMARY KEY,
    concourse CHAR(1) NOT NULL,
    capacity_class VARCHAR(8) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'AVAILABLE',
    current_flight VARCHAR(12) REFERENCES flights(id),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS aircraft_positions (
    flight_id VARCHAR(12) PRIMARY KEY REFERENCES flights(id),
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    heading SMALLINT DEFAULT 0,
    ground_speed SMALLINT DEFAULT 0,
    phase VARCHAR(16) NOT NULL DEFAULT 'PARKED',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zone_metrics (
    zone_id VARCHAR(32) NOT NULL,
    metric_type VARCHAR(12) NOT NULL,
    value REAL NOT NULL,
    unit VARCHAR(8),
    recorded_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (zone_id, metric_type, recorded_at)
);
CREATE INDEX IF NOT EXISTS zone_metrics_time_idx ON zone_metrics (recorded_at DESC);
