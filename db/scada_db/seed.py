"""Load the demo enterprise into Postgres from deploy/seed/{layout.geojson, fleet.json, sensors.json} and seed_data.

Without --force the seed only runs on an empty registry, so `up` never wipes
sensors added through the API. With --force every store is reset to the demo
state: Postgres tables, ClickHouse telemetry, Redis streams and live state.
"""

import hashlib
import json

from argon2 import PasswordHasher
from geoalchemy2.shape import from_shape
from scada_common import SensorType, catalog
from shapely.geometry import shape
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from scada_db import clickhouse, models as m, redis_init, seed_data
from scada_db.config import settings
from scada_db.postgres import engine

TABLES = ["trips", "alerts", "alert_rules", "thresholds", "sensors", "vehicles", "sensor_types", "whitelist", "schedules",
          "layouts", "users", "roles", "api_keys", "checkpoints", "roads", "zones", "buildings"]
CH_TABLES = ["telemetry", "telemetry_1m", "telemetry_1h", "alerts_log", "zone_events"]


def _geom(geometry: dict):
    return from_shape(shape(geometry), srid=m.SRID)


def _load_json(name: str):
    return json.loads((settings.seed_dir / name).read_text(encoding="utf-8"))


def is_seeded() -> bool:
    with Session(engine()) as s:
        return s.scalar(select(func.count()).select_from(m.Sensor)) > 0


def wipe() -> None:
    with engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    ch = clickhouse.client()
    for t in CH_TABLES:
        ch.command(f"TRUNCATE TABLE IF EXISTS {t}")
    redis_init.reset_live_state()


def seed(force: bool = False) -> dict[str, int] | None:
    if not force and is_seeded():
        return None
    if force:
        wipe()
    layout = _load_json("layout.geojson")
    fleet = _load_json("fleet.json")
    by_kind: dict[str, list[dict]] = {}
    for f in layout["features"]:
        by_kind.setdefault(f["properties"]["kind"], []).append(f)
    now = seed_data.now()
    hasher = PasswordHasher()

    rows: dict[type[m.Base], list[dict]] = {
        m.RoleRow: [{"id": r, "name": n, "permissions": p} for r, n, p in seed_data.ROLES],
        m.User: [{"username": u, "full_name": n, "role_id": r, "password_hash": hasher.hash(seed_data.DEMO_PASSWORD)}
                 for u, n, r in seed_data.USERS],
        m.ApiKey: [{"name": n, "key_prefix": k[:12], "key_hash": hashlib.sha256(k.encode()).hexdigest(),
                    "sensor_types": t} for n, k, t in seed_data.api_keys()],
        m.SensorTypeRow: [{**t, "payload_schema": catalog.PAYLOAD_MODELS[t["id"]].model_json_schema()}
                          for t in catalog.SENSOR_TYPES],
        m.Building: [{"id": f["id"], "name": f["properties"]["name"], "building_type": f["properties"]["building_type"],
                      "floors": f["properties"]["floors"], "geom": _geom(f["geometry"])}
                     for f in by_kind["building"]],
        m.Zone: [{"id": f["id"], "name": p["name"], "kind": p["kind"],
                  "zone_type": p.get("room_type") or p.get("zone_type"), "building_id": p.get("building_id"),
                  "floor": p.get("floor"), "speed_limit_kmh": p.get("speed_limit_kmh"), "geom": _geom(f["geometry"])}
                 for f in by_kind["room"] + by_kind["geozone"] for p in [f["properties"]]],
        m.Road: [{"id": f["id"], "name": p["name"], "width_m": p["width_m"], "speed_limit_kmh": p["speed_limit_kmh"],
                  "road_class": p.get("road_class", "site"), "connects": p.get("connects"),
                  "geom": _geom(f["geometry"])} for f in by_kind["road"] for p in [f["properties"]]],
        m.Checkpoint: [{"id": f["id"], "name": p["name"], "checkpoint_type": p["checkpoint_type"],
                        "zone_id": p["zone_id"], "geom": _geom(f["geometry"])}
                       for f in by_kind["checkpoint"] for p in [f["properties"]]],
        m.Layout: [{"version": 1, "geojson": layout}],
        m.Vehicle: fleet,
        m.Schedule: [{"id": i, "name": n, "intervals": iv} for i, n, iv in seed_data.SCHEDULES],
        m.WhitelistEntry: seed_data.cards(now) + seed_data.plates(fleet, now),
        m.AlertRule: [{"id": i, "version": 1, "name": n, "kind": k, "severity": sev, "params": p,
                       "schedule_id": sch, "escalate_after_s": esc}
                      for i, n, k, sev, p, sch, esc in seed_data.ALERT_RULES],
    }

    sensors, thresholds = [], []
    for f in by_kind["sensor"]:
        p = f["properties"]
        sensors.append({"id": f["id"], "type": p["sensor_type"], "name": p["name"], "is_mobile": False,
                        "building_id": p.get("building_id"), "zone_id": p.get("zone_id"), "floor": p.get("floor"),
                        "geom": _geom(f["geometry"])})
    # registered in a building without a place on the plan (the building has no floor plan yet)
    for s in _load_json("sensors.json"):
        sensors.append({**s, "is_mobile": False, "floor": None, "geom": None})
    for v in fleet:
        sensors.append({"id": catalog.gnss_sensor_id(v["id"]), "type": SensorType.GNSS, "name": f"Трекер {v['plate']}",
                        "is_mobile": True, "vehicle_id": v["id"]})
    for s in sensors:
        thresholds += [{"sensor_id": s["id"], **t} for t in catalog.default_thresholds(SensorType(s["type"]), s["id"])]
    rows[m.Sensor] = sensors
    rows[m.Threshold] = thresholds

    with Session(engine()) as session, session.begin():
        for model, data in rows.items():  # dict order = FK order
            if data:
                session.execute(insert(model), data)
    redis_init.init()
    return {model.__tablename__: len(data) for model, data in rows.items()}
