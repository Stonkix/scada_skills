import json

from fastapi.testclient import TestClient
from shapely.geometry import Point, shape

from app.live.schemas import WS_SERVER_ADAPTER
from app.main import app
from app.mock import LAYOUT_PATH

client = TestClient(app)


def test_layout_is_consistent() -> None:
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    features = layout["features"]
    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids)), "feature ids must be unique"
    by_id = {f["id"]: f for f in features}
    for f in features:
        p = f["properties"]
        if p["kind"] in ("sensor", "room") and p.get("building_id"):
            assert p["building_id"] in by_id, f"{f['id']}: unknown building"
            # sensors may sit on the wall (entrance readers), so allow touching the boundary
            assert shape(by_id[p["building_id"]]["geometry"]).buffer(0.01).contains(
                shape(f["geometry"]) if p["kind"] == "room" else Point(f["geometry"]["coordinates"])), f["id"]
        if p.get("zone_id"):
            assert p["zone_id"] in by_id, f"{f['id']}: unknown zone {p['zone_id']}"


def test_objects() -> None:
    body = client.get("/objects").json()
    assert body["layout_version"] == 1
    assert len(body["buildings"]) == 7
    assert {s["id"] for s in body["sensors"]} >= {"cam-gate-in", "gnss-truck-1"}


def test_live_state_and_filter() -> None:
    body = client.get("/state/live").json()
    assert body["vehicles"] and body["sensors"] and body["zones"]
    wh1 = client.get("/state/live", params={"building_id": "b-wh1"}).json()
    assert wh1["vehicles"] == []
    assert {s["building_id"] for s in wh1["sensors"]} == {"b-wh1"}


def test_history_and_route() -> None:
    h = client.get("/sensors/clim-wh2-storage/history", params={"metric": "temperature_c"})
    assert h.status_code == 200 and h.json()["step"] == "raw"
    assert client.get("/sensors/clim-wh2-storage/history", params={"metric": "plate"}).status_code == 422
    r = client.get("/vehicles/v-truck-1/route").json()
    assert len(r["points"]) > 10


def test_alert_lifecycle() -> None:
    open_ = client.get("/alerts", params={"status": "open"}).json()["items"]
    alert_id = open_[0]["id"]
    acked = client.post(f"/alerts/{alert_id}/ack", json={"comment": "выехали"}).json()
    assert acked["status"] == "ack" and acked["comment"] == "выехали"
    assert client.post(f"/alerts/{alert_id}/ack", json={}).status_code == 409
    assert client.post(f"/alerts/{alert_id}/resolve", json={}).json()["status"] == "resolved"


def test_layout_version_conflict() -> None:
    doc = client.get("/layout").json()
    ok = client.post("/layout", json={"base_version": doc["version"], "geojson": doc["geojson"]})
    assert ok.status_code == 200 and ok.json()["version"] == doc["version"] + 1
    stale = client.post("/layout", json={"base_version": doc["version"], "geojson": doc["geojson"]})
    assert stale.status_code == 409


def test_bulk_dry_run_reports_rows_and_creates_nothing() -> None:
    csv = ("id,type,name,building_id,zone_id,x,y\n"
           "clim-new-1,climate,Новый датчик,b-wh1,r-wh1-storage,250,350\n"
           "bad-1,thermometer,Плохой тип,,,1,1\n"
           "cam-gate-in,anpr_camera,Дубль,,,1,1\n")
    r = client.post("/sensors/bulk", params={"dry_run": True}, files={"file": ("s.csv", csv, "text/csv")}).json()
    assert (r["total"], r["valid"], r["created"]) == (3, 1, 0)
    assert {e["row"] for e in r["errors"]} == {2, 3}
    good = "id,type,name,x,y\nclim-new-2,climate,Ещё датчик,250,350\n"
    r = client.post("/sensors/bulk", params={"dry_run": False}, files={"file": ("s.csv", good, "text/csv")}).json()
    assert r["created"] == 1
    assert client.get("/sensors/clim-new-2").status_code == 200


def test_auth() -> None:
    assert client.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    tokens = client.post("/auth/login", json={"username": "admin", "password": "demo"}).json()
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert me["role"] == "admin"


def test_ws_subscription_filters_layers() -> None:
    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps({"op": "subscribe", "layers": ["vehicles"]}))
        types = {WS_SERVER_ADAPTER.validate_json(ws.receive_text()).type for _ in range(6)}
    assert types == {"vehicle"}
