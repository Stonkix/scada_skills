-- Derived events: an object entered or left a zone (geozone by GNSS position, building by access control).
-- Feeds the geozone rule, checkpoint throughput, dwell times at docks and the heatmap.
CREATE TABLE IF NOT EXISTS zone_events
(
    ts          DateTime64(3, 'UTC'),
    event_id    UUID,                      -- the sensor event that caused the transition
    sensor_id   LowCardinality(String),
    vehicle_id  LowCardinality(Nullable(String)),
    zone_id     LowCardinality(String),
    transition  LowCardinality(String),    -- enter | exit
    x           Nullable(Float64),
    y           Nullable(Float64)
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (zone_id, ts, sensor_id, transition, event_id)
TTL toDateTime(ts) + INTERVAL 1 YEAR DELETE;
