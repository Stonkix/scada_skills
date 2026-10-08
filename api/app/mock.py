"""In-memory demo world used until the real storages are wired (stages 1–4).

Static objects come from deploy/seed/layout.geojson; live values are pure
functions of time, so every endpoint and the WebSocket agree with each other.
Routers depend only on the functions here — swapping them for Postgres /
Redis / ClickHouse queries must not change the API contract.
"""

import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scada_common import Geo, SensorType, catalog
from shapely.geometry import LineString, Point, shape

from app.alerts.schemas import Alert, AlertKind, AlertStatus, Severity
from app.layout.schemas import (
    Building,
    Checkpoint,
    Georef,
    Geozone,
    LineGeometry,
    ObjectsResponse,
    PolygonGeometry,
    Road,
    Room,
)
from app.live.schemas import SensorLive, SensorStatus, VehicleLive, VehicleStatus, ZoneOccupancy
from app.registry.schemas import (
    HistoryPoint,
    RoutePoint,
    Sensor,
    SensorCreate,
    SensorTypeInfo,
    Threshold,
    Vehicle,
)

LAYOUT_PATH = Path(
    os.environ.get("SCADA_LAYOUT_PATH", Path(__file__).resolve().parents[2] / "deploy" / "seed" / "layout.geojson")
)
FLEET_PATH = LAYOUT_PATH.with_name("fleet.json")
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

SENSOR_TYPES = [SensorTypeInfo.model_validate(t) for t in catalog.SENSOR_TYPES]

# Demo loops along the roads of layout.geojson; vehicles without a loop stand in the truck parking.
_ROUTES: dict[str, tuple[list[tuple[float, float]], float]] = {
    "v-truck-1": ([(20, 250), (290, 250), (290, 290), (290, 250)], 18),
    "v-truck-2": ([(20, 250), (470, 250), (470, 290), (470, 250)], 20),
    "v-truck-3": ([(20, 250), (650, 250), (650, 290), (650, 250)], 22),
    "v-loader-1": ([(190, 30), (790, 30), (790, 470), (190, 470)], 12),
    "v-car-1": ([(190, 250), (310, 250), (310, 205), (310, 250), (550, 250), (550, 205), (550, 250)], 25),
}


def _parking_spot(i: int) -> tuple[float, float]:
    return 45 + (i % 3) * 25, 55 + (i // 3) * 22


def _phase(key: str) -> float:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * 2 * math.pi


def _now() -> datetime:
    return datetime.now(UTC)


class World:
    def __init__(self) -> None:
        self.layout: dict = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        self.layout_version = 1
        self.sensors: dict[str, Sensor] = {}
        self.alerts: dict[int, Alert] = {}
        fleet = json.loads(FLEET_PATH.read_text(encoding="utf-8"))
        self.vehicles = {v["id"]: Vehicle(**v, sensor_id=catalog.gnss_sensor_id(v["id"])) for v in fleet}
        parked = [vid for vid in self.vehicles if vid not in _ROUTES]
        self._routes = {vid: (LineString(pts + [pts[0]]), pts[0], kmh) for vid, (pts, kmh) in _ROUTES.items()}
        self._routes |= {vid: (None, _parking_spot(i), 0.0) for i, vid in enumerate(parked)}
        self._load_sensors()
        self._seed_alerts()

    # --- static ---------------------------------------------------------------------------------
    def _features(self, kind: str) -> list[dict]:
        return [f for f in self.layout["features"] if f["properties"]["kind"] == kind]

    def _zone_shapes(self) -> list[tuple[str, object]]:
        return [(f["id"], shape(f["geometry"])) for f in self._features("geozone") if f["properties"]["zone_type"] != "speed"]

    def _load_sensors(self) -> None:
        created = EPOCH
        for f in self._features("sensor"):
            p = f["properties"]
            x, y = f["geometry"]["coordinates"]
            self.add_sensor(SensorCreate(
                id=p["id"], type=p["sensor_type"], name=p["name"], building_id=p.get("building_id"),
                zone_id=p.get("zone_id"), floor=p.get("floor"), geo=Geo(x=x, y=y, floor=p.get("floor")),
                thresholds=default_thresholds(p["sensor_type"], p["id"]),
            ), created)
        for v in self.vehicles.values():
            self.add_sensor(SensorCreate(
                id=v.sensor_id, type=SensorType.GNSS, name=f"Трекер {v.plate}", vehicle_id=v.id,
                thresholds=default_thresholds(SensorType.GNSS, v.sensor_id),
            ), created)

    def add_sensor(self, data: SensorCreate, created_at: datetime | None = None) -> Sensor:
        is_mobile = next(t.is_mobile for t in SENSOR_TYPES if t.id == data.type)
        sensor = Sensor(**data.model_dump(), is_mobile=is_mobile, created_at=created_at or _now())
        self.sensors[sensor.id] = sensor
        return sensor

    def objects(self) -> ObjectsResponse:
        def poly(f: dict) -> PolygonGeometry:
            return PolygonGeometry(coordinates=f["geometry"]["coordinates"])

        def props(f: dict) -> dict:
            return {k: v for k, v in f["properties"].items() if k != "kind"}

        meta = self.layout["metadata"]
        return ObjectsResponse(
            layout_version=self.layout_version,
            extent=meta["extent"],
            georef=Georef(**meta["georef"]),
            site=poly(self._features("site")[0]),
            buildings=[Building(**props(f), geometry=poly(f)) for f in self._features("building")],
            rooms=[Room(**props(f), geometry=poly(f)) for f in self._features("room")],
            roads=[Road(**props(f), geometry=LineGeometry(coordinates=f["geometry"]["coordinates"]))
                   for f in self._features("road")],
            geozones=[Geozone(**props(f), geometry=poly(f)) for f in self._features("geozone")],
            checkpoints=[Checkpoint(**props(f), x=f["geometry"]["coordinates"][0], y=f["geometry"]["coordinates"][1])
                         for f in self._features("checkpoint")],
            sensor_types=SENSOR_TYPES,
            sensors=list(self.sensors.values()),
            vehicles=list(self.vehicles.values()),
        )

    # --- live -----------------------------------------------------------------------------------
    def vehicle_live(self, vehicle_id: str, at: datetime | None = None) -> VehicleLive:
        at = at or _now()
        v = self.vehicles[vehicle_id]
        route, start, kmh = self._routes[vehicle_id]
        t = (at - EPOCH).total_seconds()
        if route is None:
            x, y, heading, speed = *start, 0.0, 0.0
        else:
            dist = (t * kmh / 3.6 + _phase(vehicle_id) * 100) % route.length
            p, ahead = route.interpolate(dist), route.interpolate(min(dist + 1, route.length))
            x, y = p.x, p.y
            heading = math.degrees(math.atan2(ahead.x - x, ahead.y - y)) % 360
            speed = kmh * (0.9 + 0.1 * math.sin(t / 7 + _phase(v.plate)))
        zone_id = next((zid for zid, geom in self._zone_shapes() if geom.contains(Point(x, y))), None)
        return VehicleLive(
            vehicle_id=v.id, sensor_id=v.sensor_id, geo=Geo(x=round(x, 2), y=round(y, 2)),
            status=VehicleStatus.MOVING if speed > 0 else VehicleStatus.STOPPED,
            speed_kmh=round(speed, 1), heading_deg=round(heading, 1), engine_on=speed > 0,
            fuel_pct=round(80 - (t / 600 + _phase(v.id) * 10) % 70, 1), zone_id=zone_id, last_seen=at,
        )

    def sensor_values(self, sensor: Sensor, at: datetime) -> dict[str, float | bool | str]:
        t = (at - EPOCH).total_seconds()
        ph = _phase(sensor.id)
        match sensor.type:
            case SensorType.CLIMATE:
                th = next((th for th in sensor.thresholds if th.metric == "temperature_c"), None)
                nominal = th.nominal if th and th.nominal is not None else 20
                # cold storage swings close to its limits so warnings show up in the demo
                amp = 2.5 if nominal < 10 else 3
                return {"temperature_c": round(nominal + amp * math.sin(t / 300 + ph), 1),
                        "humidity_pct": round(55 + 10 * math.sin(t / 900 + ph), 1)}
            case SensorType.MOTION:
                return {"detected": math.sin(t / 20 + ph) > 0.6}
            case SensorType.ACCESS_CONTROL:
                n = int(t / 15 + ph * 10)
                return {"card_id": f"P-{n % 300:06d}", "direction": "in" if n % 2 else "out", "granted": n % 23 != 0}
            case SensorType.ANPR_CAMERA:
                v = list(self.vehicles.values())[int(t / 40) % len(self.vehicles)]
                return {"plate": v.plate, "direction": "in" if sensor.id.endswith("in") else "out", "confidence": 0.97}
            case SensorType.GNSS:
                live = self.vehicle_live(sensor.vehicle_id, at)
                return {"speed_kmh": live.speed_kmh, "heading_deg": live.heading_deg,
                        "fuel_pct": live.fuel_pct or 0.0, "engine_on": live.engine_on}
        return {}

    def sensor_live(self, sensor: Sensor, at: datetime | None = None) -> SensorLive:
        at = at or _now()
        values = self.sensor_values(sensor, at)
        return SensorLive(sensor_id=sensor.id, type=sensor.type, building_id=sensor.building_id,
                          status=evaluate(values, sensor.thresholds), last_seen=at, values=values)

    def zones(self, at: datetime | None = None) -> list[ZoneOccupancy]:
        t = ((at or _now()) - EPOCH).total_seconds()
        return [ZoneOccupancy(zone_id=f["id"], people=max(0, int(20 + 15 * math.sin(t / 600 + _phase(f["id"])))))
                for f in self._features("building")]

    # --- history --------------------------------------------------------------------------------
    def history(self, sensor: Sensor, metric: str, start: datetime, end: datetime) -> tuple[str, list[HistoryPoint]]:
        span = (end - start).total_seconds()
        step_name, step = ("raw", 10) if span <= 3600 else ("1m", 60) if span <= 86400 else ("1h", 3600)
        n = min(int(span // step), 1000)
        points = []
        for i in range(n + 1):
            ts = start + timedelta(seconds=i * step)
            v = self.sensor_values(sensor, ts).get(metric)
            v = float(v) if isinstance(v, (int, float)) else 0.0
            spread = 0 if step_name == "raw" else abs(v) * 0.03
            points.append(HistoryPoint(ts=ts, min=round(v - spread, 2), avg=round(v, 2), max=round(v + spread, 2)))
        return step_name, points

    def route(self, vehicle_id: str, start: datetime, end: datetime) -> list[RoutePoint]:
        step = max(5, int((end - start).total_seconds() // 1000))
        points, ts = [], start
        while ts <= end:
            live = self.vehicle_live(vehicle_id, ts)
            points.append(RoutePoint(ts=ts, x=live.geo.x, y=live.geo.y, speed_kmh=live.speed_kmh))
            ts += timedelta(seconds=step)
        return points

    # --- alerts ---------------------------------------------------------------------------------
    def _seed_alerts(self) -> None:
        now = _now()
        seeds = [
            ("rule-cold-storage-temp", AlertKind.THRESHOLD, Severity.CRITICAL, AlertStatus.OPEN,
             "Перегрев холодного склада", "Температура 9.4 °C выше критического порога 8.0 °C",
             dict(sensor_id="clim-wh2-storage", building_id="b-wh2", zone_id="r-wh2-storage", value=9.4), 3),
            ("rule-whitelist-plate", AlertKind.WHITELIST, Severity.WARNING, AlertStatus.OPEN,
             "Номер вне базы пропусков", "Машина Х999ХХ99 на въезде КПП-1, нет в списке допуска",
             dict(sensor_id="cam-gate-in", zone_id="z-gate"), 8),
            ("rule-after-hours", AlertKind.SCHEDULE, Severity.WARNING, AlertStatus.ACK,
             "Движение в нерабочее время", "Датчик движения сработал на складе №3 в 02:14",
             dict(sensor_id="mot-wh3", building_id="b-wh3", zone_id="r-wh3-storage"), 240),
            ("rule-breakdown", AlertKind.BREAKDOWN, Severity.CRITICAL, AlertStatus.RESOLVED,
             "Остановка техники вне стоянки", "Погрузчик ПГ-01 стоит с заглушенным двигателем 12 мин на южном проезде",
             dict(sensor_id="gnss-loader-1", vehicle_id="v-loader-1"), 600),
            ("rule-offline", AlertKind.OFFLINE, Severity.INFO, AlertStatus.OPEN,
             "Датчик не на связи", "Нет данных от clim-prod2-stock более 5 минут",
             dict(sensor_id="clim-prod2-stock", building_id="b-prod2", zone_id="r-prod2-stock"), 6),
        ]
        for i, (rule, kind, sev, status, title, msg, refs, ago_min) in enumerate(seeds, start=1):
            opened = now - timedelta(minutes=ago_min)
            self.alerts[i] = Alert(
                id=i, rule_id=rule, rule_version=1, kind=kind, severity=sev, status=status, title=title, message=msg,
                opened_at=opened, last_seen_at=opened + timedelta(minutes=ago_min / 2),
                ack_at=opened + timedelta(minutes=5) if status != AlertStatus.OPEN else None,
                ack_by="security" if status != AlertStatus.OPEN else None,
                resolved_at=opened + timedelta(minutes=20) if status == AlertStatus.RESOLVED else None,
                escalation_level=1 if status == AlertStatus.OPEN and sev == Severity.CRITICAL else 0,
                **refs,
            )


def default_thresholds(sensor_type: str, sensor_id: str) -> list[Threshold]:
    return [Threshold(**t) for t in catalog.default_thresholds(SensorType(sensor_type), sensor_id)]


def evaluate(values: dict, thresholds: list[Threshold]) -> SensorStatus:
    """Worst status across metrics: critical bounds first, then the norm band."""
    status = SensorStatus.OK
    for th in thresholds:
        v = values.get(th.metric)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if (th.critical_min is not None and v < th.critical_min) or (th.critical_max is not None and v > th.critical_max):
            return SensorStatus.CRITICAL
        if (th.min is not None and v < th.min) or (th.max is not None and v > th.max):
            status = SensorStatus.WARNING
    return status


world = World()
