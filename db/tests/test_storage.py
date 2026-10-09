"""Integration tests against the running stack (`python dev.py up`). Skipped if stores are unreachable.

They read the seeded demo data and clean up anything they write; they never reseed.
"""

import uuid

import pytest
from alembic import command
from scada_common import catalog, keys
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scada_db import clickhouse, models as m, postgres, redis_init

try:
    with postgres.engine().connect():
        pass
    clickhouse.client().ping()
    redis_init.client().ping()
except Exception as e:  # noqa: BLE001
    pytest.skip(f"stack is not running: {e}", allow_module_level=True)


@pytest.fixture
def session():
    with Session(postgres.engine()) as s:
        yield s
        s.rollback()


def test_models_match_migrations() -> None:
    command.check(postgres.alembic_config())  # raises if models.py has changes without a migration


def test_seed_is_consistent(session: Session) -> None:
    # tools/loadtest.py leaves its synthetic load-* sensors disabled in the registry; they are not part of the seed
    sensors = session.scalars(select(m.Sensor).where(m.Sensor.id.not_like("load-%"))).all()
    assert 50 <= len(sensors) <= 200
    areas = set(session.scalars(select(m.Building.id))) | set(session.scalars(select(m.Zone.id)))
    assert {s.zone_id for s in sensors if s.zone_id} <= areas
    vehicles = set(session.scalars(select(m.Vehicle.id)))
    trackers = {s.vehicle_id for s in sensors if s.is_mobile}
    assert trackers == vehicles, "every vehicle has exactly one GNSS tracker"
    assert all(s.geom is None for s in sensors if s.is_mobile)
    # a fixed sensor is placed on the plan, or registered in a building that has no floor plan yet
    assert all(s.geom is not None or (s.building_id and s.zone_id == s.building_id) for s in sensors if not s.is_mobile)
    assert any(s.geom is None and not s.is_mobile for s in sensors), "the demo has a building without a floor plan"


def test_thresholds_have_one_current_version(session: Session) -> None:
    th = session.scalars(select(m.Threshold).where(m.Threshold.sensor_id == "clim-wh2-storage",
                                                   m.Threshold.metric == "temperature_c")).one()
    session.add(m.Threshold(sensor_id=th.sensor_id, metric=th.metric, max=7, version=2))
    with pytest.raises(IntegrityError):
        session.flush()


def test_whitelist_has_demo_gaps(session: Session) -> None:
    def active(kind: str, value: str) -> bool:
        return session.execute(text("SELECT exists(SELECT 1 FROM whitelist WHERE kind=:k AND value=:v "
                                    "AND (valid_to IS NULL OR valid_to > now()))"), {"k": kind, "v": value}).scalar()

    assert active("card", "P-000001")
    assert not active("card", "P-000285")  # expired
    assert not active("card", "P-000295")  # unknown
    assert active("plate", "А123ВС77")
    assert not active("plate", "Р135ЕК99")  # v-truck-8 is deliberately missing


def test_one_active_alert_per_dedup_key(session: Session) -> None:
    def alert(status: str = "open") -> m.Alert:
        return m.Alert(rule_id="rule-offline", rule_version=1, kind="offline", severity="warning", status=status,
                       title="t", message="m", sensor_id="mot-wh1", dedup_key="rule-offline:test-probe")

    session.add_all([alert("resolved"), alert("resolved"), alert("open")])
    session.flush()  # resolved duplicates are history, fine
    session.add(alert("ack"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_telemetry_dedup_and_rollups() -> None:
    ch = clickhouse.client()
    sensor = f"probe-{uuid.uuid4().hex[:8]}"
    event_id = uuid.uuid4()
    row = [event_id, 1, sensor, "climate", "2026-10-08 10:00:10", "2026-10-08 10:00:10", None, None, None, None,
           {"temperature_c": 4.0}, "{}"]
    cols = ["event_id", "schema_v", "sensor_id", "type", "ts", "received_at", "x", "y", "floor", "zone_id",
            "metrics", "payload"]
    try:
        from datetime import datetime
        row[4] = row[5] = datetime(2026, 10, 8, 10, 0, 10)
        ch.insert("telemetry", [row, row], column_names=cols)  # the same event sent twice
        assert ch.query(f"SELECT count() FROM telemetry FINAL WHERE sensor_id='{sensor}'").result_rows[0][0] == 1
        # MVs see both inserts: this is why the worker dedups by event_id before inserting
        cnt = ch.query(f"SELECT sum(cnt) FROM telemetry_1m WHERE sensor_id='{sensor}'").result_rows[0][0]
        assert cnt == 2
    finally:
        for t in ("telemetry", "telemetry_1m", "telemetry_1h"):
            ch.command(f"DELETE FROM {t} WHERE sensor_id='{sensor}'")


def test_redis_groups_exist() -> None:
    r = redis_init.client()
    for stream, groups in keys.CONSUMER_GROUPS.items():
        assert {g["name"] for g in r.xinfo_groups(stream)} >= set(groups)


def test_sensor_types_carry_payload_schema(session: Session) -> None:
    climate = session.get(m.SensorTypeRow, "climate")
    assert set(climate.payload_schema["properties"]) == set(catalog.numeric_metrics("climate"))
