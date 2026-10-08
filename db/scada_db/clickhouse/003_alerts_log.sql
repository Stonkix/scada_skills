-- Append-only log of alert lifecycle transitions, for KPIs ("top alerts", reaction time).
-- The source of truth for the current alert state is Postgres `alerts`.
CREATE TABLE IF NOT EXISTS alerts_log
(
    ts               DateTime64(3, 'UTC'),
    alert_id         UInt64,
    transition       LowCardinality(String),  -- opened | escalated | ack | resolved
    rule_id          LowCardinality(String),
    rule_version     UInt32,
    kind             LowCardinality(String),
    severity         LowCardinality(String),
    escalation_level UInt8,
    sensor_id        LowCardinality(Nullable(String)),
    vehicle_id       LowCardinality(Nullable(String)),
    building_id      LowCardinality(Nullable(String)),
    zone_id          LowCardinality(Nullable(String)),
    value            Nullable(Float64),
    actor            Nullable(String)          -- username for ack/resolve
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (rule_id, ts, alert_id)
TTL toDateTime(ts) + INTERVAL 1 YEAR DELETE;
