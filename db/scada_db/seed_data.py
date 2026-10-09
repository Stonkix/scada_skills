"""Demo reference data that is not part of the plan (layout.geojson) or the fleet (fleet.json).

The demo scenarios rely on a few deliberate gaps:
  * cards P-000280..P-000289 are expired and P-000290..P-000299 are unknown -> "пропуск вне базы";
  * plate of v-truck-8 is not whitelisted -> "номер вне базы" at КПП-1;
  * motion in a warehouse nobody badged into -> "движение в пустом здании" (simulator: evacuate + motion).
"""

import os
import random
from datetime import UTC, datetime, timedelta

from faker import Faker
from scada_common.enums import AlertKind, Role, Severity

DEMO_PASSWORD = "demo"

ROLES = [
    (Role.DISPATCHER, "Диспетчер", ["map:view", "alerts:ack", "kpi:view", "sensors:view"]),
    (Role.SECURITY, "Охрана", ["map:view", "sensors:view", "alerts:ack", "people:view_pii", "whitelist:edit"]),
    (Role.ADMIN, "Администратор", ["*"]),
]

USERS = [
    ("dispatcher", "Иванов Иван Иванович", Role.DISPATCHER),
    ("dispatcher2", "Кузнецова Мария Олеговна", Role.DISPATCHER),
    ("security", "Петров Пётр Петрович", Role.SECURITY),
    ("admin", "Смирнова Анна Андреевна", Role.ADMIN),
]

WORKDAYS, ALL_DAYS = [1, 2, 3, 4, 5], [1, 2, 3, 4, 5, 6, 7]
SCHEDULES = [
    ("work-hours", "Рабочее время склада и цехов", [{"days": WORKDAYS, "start": "07:00", "end": "21:00"},
                                                     {"days": [6], "start": "09:00", "end": "15:00"}]),
    ("office-hours", "Офис", [{"days": WORKDAYS, "start": "08:00", "end": "19:00"}]),
    ("shift-day", "Дневная смена", [{"days": ALL_DAYS, "start": "08:00", "end": "20:00"}]),
    ("shift-night", "Ночная смена", [{"days": ALL_DAYS, "start": "20:00", "end": "08:00"}]),
    ("round-the-clock", "Круглосуточно", [{"days": ALL_DAYS, "start": "00:00", "end": "24:00"}]),
]

# (id, name, kind, severity, params, schedule_id, escalate_after_s)
ALERT_RULES = [
    # escalate_after_s: unacknowledged alerts gain a level every N seconds, up to MAX_ESCALATION
    ("rule-threshold", "Выход метрики за пороги", AlertKind.THRESHOLD, Severity.WARNING,
     # warnings hold 30 s against noise; a critical value alarms at once (critical_min_duration_s: 0)
     {"hysteresis_pct": 5, "min_duration_s": 30, "critical_min_duration_s": 0, "critical_severity": "critical",
      "exclude_metrics": ["speed_kmh", "heading_deg"]}, None, 300),  # speed has its own rule
    # every site has a speed geozone; public roads between sites have none, so highway speed is not an alert
    ("rule-speed", "Превышение скорости на территории", AlertKind.SPEED, Severity.WARNING,
     {"zone_type": "speed", "limit_kmh": 20, "critical_kmh": 30, "min_duration_s": 5}, None, None),
    ("rule-whitelist-plate", "Номер вне базы пропусков", AlertKind.WHITELIST, Severity.WARNING,
     {"sensor_type": "anpr_camera", "list": "plate", "zones": ["z-gate", "z-dmd-gate", "z-chk-gate"]}, None, 120),
    ("rule-whitelist-card", "Пропуск вне базы или просрочен", AlertKind.WHITELIST, Severity.WARNING,
     {"sensor_type": "access_control", "list": "card"}, None, 300),
    ("rule-after-hours", "Движение без прохода по СКУД", AlertKind.SCHEDULE, Severity.CRITICAL,
     {"sensor_type": "motion", "building_types": ["warehouse", "production"], "require_empty": True,
      # motion reports only on change: a second report may be minutes away, so no hold time
      "min_duration_s": 0, "quiet_s": 300}, "work-hours", 60),
    ("rule-breakdown", "Остановка техники вне стоянки", AlertKind.BREAKDOWN, Severity.CRITICAL,
     # vehicles switch the engine off only at docks and parkings: off anywhere else is an alarm at once
     {"stopped_min": 0, "allowed_zone_types": ["parking", "docks", "restricted"]}, None, 300),
    ("rule-offline", "Датчик не на связи", AlertKind.OFFLINE, Severity.WARNING,
     # cameras and turnstiles report only when someone passes: silence is normal for them
     # 3 missed heartbeats (scada_common.catalog.REPORTING): climate/motion 120 s, parked tracker 60 s → 5 min
     {"timeout_s": 360, "mobile_timeout_s": 300, "sensor_types": ["climate", "motion", "gnss"]}, None, 900),
    ("rule-geozone-garage", "Грузовик во дворе ремзоны", AlertKind.GEOZONE, Severity.INFO,
     {"zone_id": "z-garage-yard", "event": "enter", "vehicle_kinds": ["truck"]}, None, None),
]

ORGS = ["ООО «Завод»", "ООО «Завод»", "ООО «Завод»", "ООО «ТрансЛогистик»", "ООО «КлинСервис»", "ЧОП «Щит»"]
CARD_VALID, CARD_EXPIRED, CARD_TOTAL = 280, 10, 300
UNLISTED_VEHICLES = {"v-truck-8"}


def cards(now: datetime) -> list[dict]:
    fake = Faker("ru_RU")
    fake.seed_instance(42)
    rnd = random.Random(42)
    rows = []
    for n in range(CARD_VALID + CARD_EXPIRED):
        org = rnd.choice(ORGS)
        rows.append({
            "kind": "card", "value": f"P-{n:06d}", "holder_name": fake.name(), "holder_org": org,
            # contractors only get into the office; staff everywhere
            "allowed_building_ids": ["b-admin"] if org == "ООО «КлинСервис»" else None,
            "schedule_id": None,  # set a shift schedule to restrict a pass in time (checked by rule-whitelist-card)
            "valid_from": now - timedelta(days=365),
            "valid_to": now - timedelta(days=rnd.randint(1, 60)) if n >= CARD_VALID else None,
        })
    return rows


def plates(fleet: list[dict], now: datetime) -> list[dict]:
    rows = [{"kind": "plate", "value": v["plate"], "holder_name": v["model"], "holder_org": v["carrier"],
             "allowed_building_ids": None, "schedule_id": None, "valid_from": now - timedelta(days=365), "valid_to": None}
            for v in fleet if v["id"] not in UNLISTED_VEHICLES and v["kind"] != "loader"]
    rnd = random.Random(7)
    letters = "АВЕКМНОРСТУХ"
    for _ in range(20):  # regular visitors' cars
        plate = f"{rnd.choice(letters)}{rnd.randint(100, 999)}{rnd.choice(letters)}{rnd.choice(letters)}{rnd.choice(['77', '97', '50', '750'])}"
        rows.append({"kind": "plate", "value": plate, "holder_name": "Гостевой автомобиль", "holder_org": None,
                     "allowed_building_ids": None, "schedule_id": "office-hours",
                     "valid_from": now - timedelta(days=30), "valid_to": None})
    return rows


def api_keys() -> list[tuple[str, str, list[str] | None]]:
    """(name, plaintext key, allowed sensor types). Keys come from env so the simulator can use them."""
    return [
        ("simulator", os.environ.get("SIMULATOR_API_KEY", "sk_simulator_dev_3f9a1c7e5b2d4086a1e9c3b7d5f20e48"), None),
        ("demo-vendor-climate", os.environ.get("DEMO_VENDOR_API_KEY", "sk_vendordemo_8c1e2a9f4b7d4e01b5a6c3d2e1f09a7b"),
         ["climate"]),
    ]


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
