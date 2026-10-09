"""Prometheus metric names shared by services, so dashboards and alerts reference one vocabulary.

Import-light on purpose: prometheus_client is only needed by the processes that expose metrics.
"""

INGEST_EVENTS = "scada_ingest_events_total"  # connectors: labels adapter, source, result, reason
WORKER_EVENTS = "scada_worker_events_total"  # worker: labels outcome (stored|duplicate|invalid)
WORKER_BATCH_SECONDS = "scada_worker_batch_seconds"
WORKER_INSERT_SECONDS = "scada_worker_clickhouse_insert_seconds"
WORKER_LAG = "scada_worker_stream_lag"  # messages in stream:events not yet delivered to the group
WORKER_ALERT_TRANSITIONS = "scada_alert_transitions_total"  # labels transition
WORKER_ALERTS_OPEN = "scada_alerts_open"
