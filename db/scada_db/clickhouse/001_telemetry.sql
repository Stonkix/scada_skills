-- Raw normalized events, one row per Event (common/scada_common/events.py).
-- `metrics` holds the numeric/bool payload fields (bool as 0/1) for aggregation;
-- `payload` keeps the full original JSON.
--
-- ReplacingMergeTree collapses rows with the same ORDER BY key, and event_id is in
-- the key, so a re-sent event is deduplicated eventually (on merge, or with FINAL).
-- Materialized views below see every INSERT before that merge, so the worker
-- must drop duplicates itself (Redis SET NX on event_id) — see scada_common.keys.seen_event.
CREATE TABLE IF NOT EXISTS telemetry
(
    event_id    UUID,
    schema_v    UInt8,
    sensor_id   LowCardinality(String),
    type        LowCardinality(String),
    ts          DateTime64(3, 'UTC') CODEC(Delta, ZSTD),
    received_at DateTime64(3, 'UTC') CODEC(Delta, ZSTD),
    x           Nullable(Float64),
    y           Nullable(Float64),
    floor       Nullable(Int16),
    zone_id     LowCardinality(Nullable(String)),
    metrics     Map(LowCardinality(String), Float64),
    payload     String CODEC(ZSTD)
)
ENGINE = ReplacingMergeTree(received_at)
PARTITION BY toYYYYMMDD(ts)
ORDER BY (sensor_id, ts, event_id)
TTL toDateTime(ts) + INTERVAL 30 DAY DELETE
SETTINGS ttl_only_drop_parts = 1;
