-- Per-minute and per-hour rollups for history charts, KPIs, heatmaps and predictive.
-- avg = sum_v / cnt. Query with GROUP BY (sensor_id, metric, bucket) and min/max/sum
-- over the columns: rows are merged in the background, not at insert time.
CREATE TABLE IF NOT EXISTS telemetry_1m
(
    sensor_id LowCardinality(String),
    metric    LowCardinality(String),
    bucket    DateTime('UTC'),
    min_v     SimpleAggregateFunction(min, Float64),
    max_v     SimpleAggregateFunction(max, Float64),
    sum_v     SimpleAggregateFunction(sum, Float64),
    cnt       SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(bucket)
ORDER BY (sensor_id, metric, bucket)
TTL bucket + INTERVAL 1 YEAR DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_1m_mv TO telemetry_1m AS
SELECT
    sensor_id,
    m.1 AS metric,
    toStartOfMinute(ts) AS bucket,
    min(m.2) AS min_v,
    max(m.2) AS max_v,
    sum(m.2) AS sum_v,
    count() AS cnt
FROM telemetry
ARRAY JOIN CAST(metrics, 'Array(Tuple(String, Float64))') AS m
GROUP BY sensor_id, metric, bucket;

CREATE TABLE IF NOT EXISTS telemetry_1h
(
    sensor_id LowCardinality(String),
    metric    LowCardinality(String),
    bucket    DateTime('UTC'),
    min_v     SimpleAggregateFunction(min, Float64),
    max_v     SimpleAggregateFunction(max, Float64),
    sum_v     SimpleAggregateFunction(sum, Float64),
    cnt       SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYear(bucket)
ORDER BY (sensor_id, metric, bucket)
TTL bucket + INTERVAL 3 YEAR DELETE;

-- Cascades from the minute table so each event is unpacked only once.
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_1h_mv TO telemetry_1h AS
SELECT
    sensor_id,
    metric,
    toStartOfHour(bucket) AS bucket,
    min(min_v) AS min_v,
    max(max_v) AS max_v,
    sum(sum_v) AS sum_v,
    sum(cnt) AS cnt
FROM telemetry_1m
GROUP BY sensor_id, metric, bucket;
