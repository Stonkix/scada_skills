"""Integration tests against the running stack (`python dev.py up`): real Postgres, Redis, ClickHouse.

The simulator and worker must be running for the live/history assertions. Tests clean up what they create.
"""

import json
import time
import uuid

import pytest
from scada_db import models as m
from scada_db.postgres import engine
from sqlalchemy import delete
from sqlalchemy.orm import Session

from scada_db import redis_init

try:
    redis_init.client().ping()
    with engine().connect():
        pass
except Exception as e:  # noqa: BLE001
    pytest.skip(f"stack is not running: {e}", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.live.schemas import WS_SERVER_ADAPTER  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str) -> dict:
    r = client.post("/auth/login", json={"username": username, "password": "demo"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def tokens(client: TestClient) -> dict[str, dict]:
    return {u: login(client, u) for u in ("dispatcher", "security", "admin")}


def auth(tokens: dict, user: str) -> dict:
    return {"Authorization": f"Bearer {tokens[user]['access_token']}"}


@pytest.fixture
def cleanup():
    sensors: list[str] = []
    alerts: list[int] = []
    yield sensors, alerts
    with Session(engine()) as s:
        if alerts:
            s.execute(delete(m.Alert).where(m.Alert.id.in_(alerts)))
        if sensors:
            s.execute(delete(m.Sensor).where(m.Sensor.id.in_(sensors)))
        s.commit()


# --- auth ------------------------------------------------------------------------------------------


def test_login_refresh_me(client: TestClient, tokens: dict) -> None:
    assert client.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    me = client.get("/auth/me", headers=auth(tokens, "security")).json()
    assert me["role"] == "security"
    refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["admin"]["refresh_token"]})
    assert refreshed.status_code == 200 and refreshed.json()["user"]["username"] == "admin"
    # an access token is not a refresh token
    assert client.post("/auth/refresh", json={"refresh_token": tokens["admin"]["access_token"]}).status_code == 401


def test_endpoints_require_auth_and_permissions(client: TestClient, tokens: dict) -> None:
    assert client.get("/objects").status_code == 401
    assert client.get("/objects", headers={"Authorization": "Bearer garbage"}).status_code == 401
    r = client.post("/sensors", headers=auth(tokens, "dispatcher"), json={"id": "x", "type": "climate", "name": "x"})
    assert r.status_code == 403


# --- objects & live --------------------------------------------------------------------------------


def test_objects_come_from_the_registry(client: TestClient, tokens: dict) -> None:
    body = client.get("/objects", headers=auth(tokens, "dispatcher")).json()
    assert len(body["buildings"]) == 7 and len(body["vehicles"]) == 15
    assert len(body["sensors"]) >= 67 and body["layout_version"] >= 1
    clim = next(s for s in body["sensors"] if s["id"] == "clim-wh2-storage")
    assert clim["geo"] and {t["metric"] for t in clim["thresholds"]} == {"temperature_c", "humidity_pct"}


def test_live_state_from_worker(client: TestClient, tokens: dict) -> None:
    body = client.get("/state/live", headers=auth(tokens, "dispatcher")).json()
    assert body["vehicles"] and body["sensors"] and len(body["zones"]) == 7
    wh1 = client.get("/state/live", params={"building_id": "b-wh1"}, headers=auth(tokens, "dispatcher")).json()
    assert wh1["vehicles"] == [] and {s["building_id"] for s in wh1["sensors"]} == {"b-wh1"}


def test_websocket_auth_and_layer_filter(client: TestClient, tokens: dict) -> None:
    with pytest.raises(Exception):  # closed with 4401 before accept
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_text()
    with client.websocket_connect(f"/ws/live?token={tokens['dispatcher']['access_token']}") as ws:
        ws.send_text(json.dumps({"op": "subscribe", "layers": ["vehicles"]}))
        time.sleep(0.5)  # let the filter apply before counting
        types = {WS_SERVER_ADAPTER.validate_json(ws.receive_text()).type for _ in range(10)}
    assert types == {"vehicle"}


# --- registry --------------------------------------------------------------------------------------


def test_sensor_lifecycle_and_threshold_versions(client: TestClient, tokens: dict, cleanup) -> None:
    sid = f"clim-test-{uuid.uuid4().hex[:6]}"
    cleanup[0].append(sid)
    admin = auth(tokens, "admin")
    r = client.post("/sensors", headers=admin, json={"id": sid, "type": "climate", "name": "Тест",
                                                       "building_id": "b-wh1", "zone_id": "r-wh1-storage",
                                                       "geo": {"x": 250, "y": 350}})
    assert r.status_code == 201, r.text
    assert {t["version"] for t in r.json()["thresholds"]} == {1}, "typical thresholds from the catalogue"
    assert client.post("/sensors", headers=admin, json={"id": sid, "type": "climate", "name": "x"}).status_code == 409
    r = client.put(f"/sensors/{sid}/thresholds", headers=admin,
                   json=[{"metric": "temperature_c", "min": 10, "max": 20, "critical_max": 25}])
    assert r.status_code == 200 and r.json()["thresholds"] == [
        {"metric": "temperature_c", "nominal": None, "min": 10.0, "max": 20.0, "critical_min": None,
         "critical_max": 25.0, "version": 2}]
    bad = client.put(f"/sensors/{sid}/thresholds", headers=admin, json=[{"metric": "plate"}])
    assert bad.status_code == 422
    r = client.patch(f"/sensors/{sid}", headers=admin, json={"name": "Переименован", "enabled": False})
    assert r.json()["name"] == "Переименован" and r.json()["enabled"] is False


def test_bulk_dry_run_then_apply(client: TestClient, tokens: dict, cleanup) -> None:
    good = f"clim-bulk-{uuid.uuid4().hex[:6]}"
    cleanup[0].append(good)
    csv = ("id,type,name,building_id,zone_id,x,y\n"
           f"{good},climate,Новый,b-wh1,r-wh1-storage,250,350\n"
           "bad-1,thermometer,Плохой тип,,,1,1\n"
           "cam-gate-in,anpr_camera,Дубль,,,1,1\n"
           "nowhere-1,climate,Нет здания,b-nope,,1,1\n")
    admin = auth(tokens, "admin")
    r = client.post("/sensors/bulk", params={"dry_run": True}, headers=admin, files={"file": ("s.csv", csv, "text/csv")}).json()
    assert (r["total"], r["valid"], r["created"]) == (4, 1, 0)
    assert {e["row"] for e in r["errors"]} == {2, 3, 4}
    r = client.post("/sensors/bulk", params={"dry_run": False}, headers=admin,
                    files={"file": ("s.csv", csv, "text/csv")}).json()
    assert r["created"] == 0, "all or nothing"
    only_good = "id,type,name,building_id,zone_id,x,y\n" + csv.splitlines()[1] + "\n"
    r = client.post("/sensors/bulk", params={"dry_run": False}, headers=admin,
                    files={"file": ("s.csv", only_good, "text/csv")}).json()
    assert r["created"] == 1
    assert client.get(f"/sensors/{good}", headers=admin).status_code == 200


def test_history_and_route(client: TestClient, tokens: dict) -> None:
    h = auth(tokens, "dispatcher")
    raw = client.get("/sensors/clim-wh1-storage/history", params={"metric": "temperature_c"}, headers=h).json()
    assert raw["step"] == "raw" and raw["points"], "simulator + worker must have been running"
    agg = client.get("/sensors/clim-wh1-storage/history", headers=h,
                     params={"metric": "temperature_c", "from": "2026-01-01T00:00:00Z", "to": "2026-01-01T12:00:00Z"})
    assert agg.json()["step"] == "1m"
    assert client.get("/sensors/clim-wh1-storage/history", params={"metric": "plate"}, headers=h).status_code == 422
    route = client.get("/vehicles/v-truck-1/route", headers=h).json()
    assert route["points"] and all("x" in p for p in route["points"])


def test_whitelist_masks_names_without_pii_permission(client: TestClient, tokens: dict) -> None:
    params = {"kind": "card", "q": "P-000001"}
    masked = client.get("/whitelist", params=params, headers=auth(tokens, "dispatcher")).json()["items"][0]
    clear = client.get("/whitelist", params=params, headers=auth(tokens, "security")).json()["items"][0]
    assert "*" in masked["holder_name"] and "*" not in clear["holder_name"]
    assert masked["holder_name"][0] == clear["holder_name"][0]


# --- layout ----------------------------------------------------------------------------------------


def test_layout_versioning_and_validation(client: TestClient, tokens: dict) -> None:
    admin = auth(tokens, "admin")
    doc = client.get("/layout", headers=admin).json()
    stale = client.post("/layout", headers=admin, json={"base_version": doc["version"] - 1, "geojson": doc["geojson"]})
    assert stale.status_code == 409
    broken = json.loads(json.dumps(doc["geojson"]))
    broken["features"].append(broken["features"][1])  # duplicate id
    r = client.post("/layout", headers=admin, json={"base_version": doc["version"], "geojson": broken})
    assert r.status_code == 422 and any("duplicate" in e for e in r.json()["detail"])
    assert client.post("/layout", headers=auth(tokens, "dispatcher"),
                       json={"base_version": doc["version"], "geojson": doc["geojson"]}).status_code == 403
    ok = client.post("/layout", headers=admin, json={"base_version": doc["version"], "geojson": doc["geojson"]})
    assert ok.status_code == 200 and ok.json()["version"] == doc["version"] + 1
    assert client.get("/objects", headers=admin).json()["layout_version"] == doc["version"] + 1


# --- alerts ----------------------------------------------------------------------------------------


def test_alert_ack_resolve_flow(client: TestClient, tokens: dict, cleanup) -> None:
    with Session(engine()) as s:
        row = m.Alert(rule_id="rule-offline", rule_version=1, kind="offline", severity="warning", title="Тест",
                      message="Тестовая тревога", sensor_id="clim-wh1-dock", dedup_key=f"rule-offline:test-{uuid.uuid4().hex}")
        s.add(row)
        s.commit()
        alert_id = row.id
    cleanup[1].append(alert_id)
    h = auth(tokens, "dispatcher")
    listed = client.get("/alerts", params={"status": "open", "sensor_id": "clim-wh1-dock"}, headers=h).json()
    assert alert_id in [a["id"] for a in listed["items"]]
    acked = client.post(f"/alerts/{alert_id}/ack", json={"comment": "выехали"}, headers=h).json()
    assert acked["status"] == "ack" and acked["ack_by"] == "dispatcher" and "выехали" in acked["comment"]
    assert client.post(f"/alerts/{alert_id}/ack", json={}, headers=h).status_code == 409
    resolved = client.post(f"/alerts/{alert_id}/resolve", json={"comment": "заменили батарею"}, headers=h).json()
    assert resolved["status"] == "resolved" and resolved["comment"].count("\n") == 1
    assert client.post(f"/alerts/{alert_id}/resolve", json={}, headers=h).status_code == 409


# --- analytics -------------------------------------------------------------------------------------


def test_kpi_heatmap_replay(client: TestClient, tokens: dict) -> None:
    h = auth(tokens, "dispatcher")
    kpi = client.get("/kpi", headers=h)
    assert kpi.status_code == 200, kpi.text
    body = kpi.json()
    assert body["vehicles"] and len(body["buildings"]) == 7
    assert any(v["mileage_km"] > 0 for v in body["vehicles"])
    heat = client.get("/heatmap", params={"cell": 20}, headers=h).json()
    assert heat["cells"] and heat["max_count"] > 0
    rep = client.get("/replay", params={"step": 10}, headers=h).json()
    assert rep["tracks"] and rep["tracks"][0]["points"]


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "postgres": "ok", "clickhouse": "ok", "redis": "ok"}


# --- connectors ------------------------------------------------------------------------------------


def test_api_key_issue_use_restrict_revoke(client: TestClient, tokens: dict) -> None:
    import httpx

    admin = auth(tokens, "admin")
    assert client.get("/connectors/keys", headers=auth(tokens, "dispatcher")).status_code == 403
    r = client.post("/connectors/keys", headers=admin,
                    json={"name": "test: climate gateway", "sensor_types": ["climate"], "rate_limit_per_min": 100})
    assert r.status_code == 201, r.text
    issued = r.json()
    try:
        assert issued["key"].startswith("sk_") and issued["key"][:12] == issued["key_prefix"]
        listed = client.get("/connectors/keys", headers=admin).json()
        assert issued["id"] in [k["id"] for k in listed] and all("key" not in k for k in listed), "secret never listed"

        ts_ms = int(time.time() * 1000)
        body = {"device": "clim-wh1-dock", "temperature": 18.2, "humidity": 50, "ts_ms": ts_ms}
        try:
            resp = httpx.post("http://127.0.0.1:8001/ingest/climate", json=body, headers={"X-API-Key": issued["key"]},
                              timeout=10)
        except httpx.HTTPError:
            pytest.skip("connectors service is not reachable on :8001")
        assert resp.status_code == 202, resp.text  # a brand-new key works without restarting connectors
        gnss = {"device_id": "gnss-truck-1", "lat": 55.7, "lon": 37.4, "speed": 0, "course": 0, "ignition": 0,
                "fix_time": time.time()}
        resp = httpx.post("http://127.0.0.1:8001/ingest/gnss", json=gnss, headers={"X-API-Key": issued["key"]}, timeout=10)
        assert resp.json()["rejected"][0]["reason"] == "forbidden_type"
        rej = client.get("/connectors/rejections", params={"adapter": "gnss", "limit": 5}, headers=admin).json()
        assert rej and rej[0]["reason"] == "forbidden_type" and rej[0]["api_key"] == "test: climate gateway"

        revoked = client.delete(f"/connectors/keys/{issued['id']}", headers=admin).json()
        assert revoked["revoked_at"] is not None
    finally:
        with Session(engine()) as s:
            s.execute(delete(m.ApiKey).where(m.ApiKey.id == issued["id"]))
            s.commit()


def test_connector_endpoints(client: TestClient, tokens: dict) -> None:
    e = client.get("/connectors/endpoints", headers=auth(tokens, "dispatcher")).json()
    assert e["http_base"].startswith("http") and e["mqtt_port"] > 0 and e["api_key_header"] == "X-API-Key"


# --- predictive ------------------------------------------------------------------------------------


def test_predict_endpoints(client: TestClient, tokens: dict) -> None:
    h = auth(tokens, "dispatcher")
    clim = client.get("/predict/clim-wh2-storage", headers=h).json()
    assert clim["prediction"]["metric"] == "temperature_c" and clim["prediction"]["samples"] > 0
    assert clim["prediction"]["risk"] in ("ok", "watch", "warning", "critical") and clim["maintenance"] is None
    truck = client.get("/predict/gnss-truck-1", headers=h).json()
    assert truck["prediction"]["metric"] == "fuel_pct"
    assert truck["maintenance"]["remaining_km"] > 0 and truck["maintenance"]["next_service_km"] % 10000 == 0
    assert client.get("/predict/mot-wh1", headers=h).json() == {"prediction": None, "maintenance": None}
    assert client.get("/predict/clim-wh2-storage", params={"metric": "plate"}, headers=h).status_code == 422
    risks = client.get("/predict", params={"risk_at_least": "ok"}, headers=h).json()
    order = {"critical": 0, "warning": 1, "watch": 2, "ok": 3}
    assert risks and [order[r["risk"]] for r in risks] == sorted(order[r["risk"]] for r in risks)
