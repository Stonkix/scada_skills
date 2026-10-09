"""Integration: HTTP ingest against the running Redis/Postgres. Skipped when the stack is down."""

import itertools
import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from scada_common import keys
from scada_db import models as m
from scada_db import redis_init
from scada_db.postgres import engine
from sqlalchemy.orm import Session
from scada_db.seed_data import api_keys

os.environ["CONNECTORS_MQTT"] = "0"
from fastapi.testclient import TestClient  # noqa: E402

from scada_connectors.main import app  # noqa: E402

try:
    redis_init.client().ping()
except Exception as e:  # noqa: BLE001
    pytest.skip(f"stack is not running: {e}", allow_module_level=True)

SIM_KEY = api_keys()[0][1]
CLIMATE_ONLY_KEY = api_keys()[1][1]
r = redis_init.client()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


_older = itertools.count()


def _climate(device: str = "clim-wh1-storage", **over) -> dict:
    # each reading a bit older than the previous: never closer than the climate min interval to another one
    ts_ms = int(time.time() * 1000) - next(_older) * 11_000
    return {"device": device, "temperature": 18.5, "humidity": 50, "ts_ms": ts_ms} | over


def _last(stream: str) -> dict:
    return r.xrevrange(stream, count=1)[0][1]


def test_accepts_and_enriches_fixed_sensor(client: TestClient) -> None:
    resp = client.post("/ingest/climate", json=_climate(), headers={"X-API-Key": SIM_KEY})
    assert resp.status_code == 202, resp.text
    event = json.loads(_last(keys.STREAM_EVENTS)["data"])
    assert event["event_id"] == resp.json()["event_ids"][0]
    assert event["zone_id"] == "r-wh1-storage" and event["geo"]["x"] is not None, "position comes from the registry"
    assert event["received_at"] is not None


def test_rejects_missing_or_wrong_key(client: TestClient) -> None:
    assert client.post("/ingest/climate", json=_climate()).status_code == 401
    assert client.post("/ingest/climate", json=_climate(), headers={"X-API-Key": SIM_KEY[:-1] + "x"}).status_code == 401


def test_unknown_adapter_is_404(client: TestClient) -> None:
    assert client.post("/ingest/teleport", json={}, headers={"X-API-Key": SIM_KEY}).status_code == 404


def test_batch_with_bad_items_goes_partially_to_dlq(client: TestClient) -> None:
    future_ms = int((datetime.now(UTC) + timedelta(hours=1)).timestamp() * 1000)
    batch = [_climate(), _climate("no-such-sensor"), _climate("acs-wh1"), {"device": "clim-wh1-dock"},
             _climate(ts_ms=future_ms)]
    resp = client.post("/ingest/climate", json=batch, headers={"X-API-Key": SIM_KEY})
    assert resp.status_code == 202
    body = resp.json()
    assert body["accepted"] == 1
    assert [(x["index"], x["reason"]) for x in body["rejected"]] == [
        (1, "unknown_sensor"), (2, "type_mismatch"), (3, "invalid_payload"), (4, "clock_skew")]
    assert _last(keys.STREAM_DLQ)["reason"] == "clock_skew"


def test_all_rejected_is_422(client: TestClient) -> None:
    resp = client.post("/ingest/climate", json=[_climate("no-such-sensor")], headers={"X-API-Key": SIM_KEY})
    assert resp.status_code == 422


def test_key_limited_to_sensor_types(client: TestClient) -> None:
    gnss = {"device_id": "gnss-truck-1", "lat": 55.7, "lon": 37.4, "speed": 0, "course": 0, "ignition": 0,
            "fix_time": time.time()}
    resp = client.post("/ingest/gnss", json=gnss, headers={"X-API-Key": CLIMATE_ONLY_KEY})
    assert resp.json()["rejected"][0]["reason"] == "forbidden_type"
    assert client.post("/ingest/climate", json=_climate(), headers={"X-API-Key": CLIMATE_ONLY_KEY}).status_code == 202


def test_native_contract_passes_through(client: TestClient) -> None:
    event = {"sensor_id": "mot-wh1", "type": "motion", "ts": datetime.now(UTC).isoformat(),
             "payload": {"detected": True}}
    resp = client.post("/ingest/native", json=event, headers={"X-API-Key": SIM_KEY})
    assert resp.status_code == 202, resp.text


def test_rate_limit(client: TestClient) -> None:
    key_id = client.app.state.pipeline.registry.keys[CLIMATE_ONLY_KEY[:12]].id
    window = keys.rate_limit(key_id, int(time.time() // 60))
    r.set(window, 10**9, ex=120)  # pretend the minute is used up
    try:
        resp = client.post("/ingest/climate", json=_climate(), headers={"X-API-Key": CLIMATE_ONLY_KEY})
        assert resp.status_code == 429
    finally:
        r.delete(window)


def test_reports_faster_than_the_type_allows_are_rejected(client: TestClient) -> None:
    base = int(time.time() * 1000) + 30_000  # newer than anything the simulator has sent for this sensor
    first = client.post("/ingest/climate", json=_climate("clim-wh3-dock", ts_ms=base), headers={"X-API-Key": SIM_KEY})
    assert first.status_code == 202, first.text
    again = client.post("/ingest/climate", json=_climate("clim-wh3-dock", ts_ms=base + 2_000), headers={"X-API-Key": SIM_KEY})
    assert again.json()["rejected"][0]["reason"] == "too_frequent"


def test_waybill_creates_and_updates_a_trip(client: TestClient) -> None:
    doc = {"waybill_no": "ПЛ-TEST-000001", "plate": "А123ВС77", "origin_site_id": "s-podolsk",
           "destination_site_id": "s-chekhov", "cargo": "Тестовый груз", "weight_t": 3.5, "status": "loading"}
    assert client.post("/documents/waybill", json=doc).status_code == 401
    assert client.post("/documents/waybill", json=doc, headers={"X-API-Key": CLIMATE_ONLY_KEY}).status_code == 403
    assert client.post("/documents/waybill", json=doc | {"plate": "Х000ХХ00"}, headers={"X-API-Key": SIM_KEY}).status_code == 422
    resp = client.post("/documents/waybill", json=doc | {"status": "en_route"}, headers={"X-API-Key": SIM_KEY})
    assert resp.status_code == 202 and resp.json() == {"trip_id": "ПЛ-TEST-000001"}
    with Session(engine()) as s:
        trip = s.get(m.Trip, "ПЛ-TEST-000001")
        assert (trip.vehicle_id, trip.status) == ("v-truck-1", "en_route")
        s.delete(trip)
        s.commit()


def test_adapters_catalog(client: TestClient) -> None:
    catalog = {a["name"]: a for a in client.get("/adapters").json()}
    assert {"native", "anpr", "skud", "gnss", "motion", "climate"} <= set(catalog)
    assert "properties" in catalog["gnss"]["schema"]
