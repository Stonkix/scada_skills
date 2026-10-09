"""Generate the Grafana dashboard (grafana/dashboards/scada.json). Run: python deploy/observability/build_dashboard.py"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("grafana") / "dashboards" / "scada.json"
DS = {"type": "prometheus", "uid": "prometheus"}

panels = []


def panel(title: str, exprs: list[tuple[str, str]], x: int, y: int, w: int = 12, h: int = 8, unit: str = "short",
          kind: str = "timeseries", desc: str = "") -> None:
    panels.append({
        "id": len(panels) + 1, "type": kind, "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 8}}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"},
                    "reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value"},
        "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": legend}
                    for i, (e, legend) in enumerate(exprs)],
    })


# row 1: headline numbers
panel("Принято событий/с", [('sum(rate(scada_ingest_events_total{result="accepted"}[1m]))', "")], 0, 0, 6, 5,
      "ops", "stat", "Коннекторы: сообщения датчиков, прошедшие проверки")
panel("Записано worker/с", [('sum(rate(scada_worker_events_total{outcome="stored"}[1m]))', "")], 6, 0, 6, 5,
      "ops", "stat", "Событий в ClickHouse в секунду")
panel("Отставание потока", [("scada_worker_stream_lag", "")], 12, 0, 6, 5, "short", "stat",
      "Сообщений в stream:events, ещё не прочитанных worker. Растёт — worker не успевает")
panel("Открытые тревоги", [("scada_alerts_open", "")], 18, 0, 6, 5, "short", "stat")

# row 2: flow
panel("Поток событий", [
    ('sum(rate(scada_ingest_events_total{result="accepted"}[1m]))', "принято коннекторами"),
    ('sum(rate(scada_worker_events_total{outcome="stored"}[1m]))', "записано worker"),
    ('sum(rate(scada_worker_events_total{outcome="duplicate"}[1m]))', "повторы отброшены"),
], 0, 5, 12, 8, "ops")
panel("Отклонено по причинам", [
    ('sum by (reason) (rate(scada_ingest_events_total{result="rejected"}[5m]))', "{{reason}}"),
], 12, 5, 12, 8, "ops", desc="stream:dlq: что приходит неправильно")

# row 3: latency
panel("Вставка в ClickHouse", [
    ("histogram_quantile(0.5, sum by (le) (rate(scada_worker_clickhouse_insert_seconds_bucket[5m])))", "p50"),
    ("histogram_quantile(0.95, sum by (le) (rate(scada_worker_clickhouse_insert_seconds_bucket[5m])))", "p95"),
], 0, 13, 8, 8, "s")
panel("Обработка пачки worker", [
    ("histogram_quantile(0.5, sum by (le) (rate(scada_worker_batch_seconds_bucket[5m])))", "p50"),
    ("histogram_quantile(0.95, sum by (le) (rate(scada_worker_batch_seconds_bucket[5m])))", "p95"),
], 8, 13, 8, 8, "s")
panel("Задержка API (p95)", [
    ('histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket{job="api"}[5m])))', "{{handler}}"),
], 16, 13, 8, 8, "s")

# row 4: alerts and HTTP
panel("Переходы тревог", [("sum by (transition) (increase(scada_alert_transitions_total[5m]))", "{{transition}}")],
      0, 21, 12, 8, "short")
panel("HTTP-запросы по кодам", [
    ('sum by (job, status) (rate(http_requests_total[1m]))', "{{job}} {{status}}"),
], 12, 21, 12, 8, "reqps")

dashboard = {
    "uid": "scada-overview", "title": "SCADA: поток данных и нагрузка", "timezone": "browser", "schemaVersion": 39,
    "refresh": "5s", "time": {"from": "now-30m", "to": "now"}, "tags": ["scada"], "panels": panels,
}

if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dashboard, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(panels)} panels)")
