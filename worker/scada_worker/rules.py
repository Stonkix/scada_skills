"""Alert rules as pure functions: event context -> open/clear signals.

No I/O here: the engine (alerts.py) turns signals into stored, deduplicated alerts.
Timers that need history (how long a condition has held, last motion) live in
`RuleState`; they are per-worker memory, which is enough for a single consumer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from scada_common import Event, SensorType, catalog

from scada_worker.registry import Rule, SensorRef, Snapshot, Threshold, VehicleRef, ZoneRef
from scada_worker.schedule import in_schedule

METRICS = {m["key"]: m for t in catalog.SENSOR_TYPES for m in t["metrics"]}


@dataclass
class Signal:
    action: Literal["open", "clear"]
    rule: Rule
    object_id: str
    severity: str = "warning"
    title: str = ""
    message: str = ""
    value: float | None = None
    sensor_id: str | None = None
    vehicle_id: str | None = None
    building_id: str | None = None
    zone_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.rule.id}:{self.object_id}"


@dataclass
class Context:
    event: Event
    sensor: SensorRef
    vehicle: VehicleRef | None = None
    zones: list[ZoneRef] = field(default_factory=list)  # GNSS: geozones covering the position
    entered: list[ZoneRef] = field(default_factory=list)
    exited: list[ZoneRef] = field(default_factory=list)
    people: int | None = None  # headcount of the sensor's building by access control, if known

    @property
    def refs(self) -> dict:
        return {"sensor_id": self.sensor.id, "vehicle_id": self.sensor.vehicle_id,
                "building_id": self.sensor.building_id, "zone_id": self.event.zone_id}


def _fmt(metric: str, v: float) -> str:
    unit = METRICS.get(metric, {}).get("unit") or ""
    return f"{v:g} {unit}".strip()


# --- thresholds -------------------------------------------------------------------------------------


def level(v: float, th: Threshold) -> Literal["ok", "warning", "critical"]:
    if (th.critical_min is not None and v < th.critical_min) or (th.critical_max is not None and v > th.critical_max):
        return "critical"
    if (th.min is not None and v < th.min) or (th.max is not None and v > th.max):
        return "warning"
    return "ok"


def safely_inside(v: float, th: Threshold, hysteresis_pct: float) -> bool:
    """Inside the norm by a margin, so a value hovering at the limit does not flap open/resolved."""
    if th.min is not None and th.max is not None:
        margin = (th.max - th.min) * hysteresis_pct / 100
    else:
        margin = abs(th.max if th.max is not None else th.min or 0) * hysteresis_pct / 100
    return level(v, th) == "ok" and (th.min is None or v >= th.min + margin) and (th.max is None or v <= th.max - margin)


def describe(metric: str, v: float, th: Threshold) -> str:
    name = METRICS.get(metric, {}).get("name", metric)
    for bound, label, above in ((th.critical_max, "критического максимума", True), (th.critical_min, "критического минимума", False),
                                (th.max, "нормы", True), (th.min, "нормы", False)):
        if bound is not None and (v > bound if above else v < bound):
            return f"{name} {_fmt(metric, v)} {'выше' if above else 'ниже'} {label} {_fmt(metric, bound)}"
    return f"{name} {_fmt(metric, v)}"


def sensor_status(values: dict, thresholds: dict[str, Threshold]) -> str:
    worst = "ok"
    for metric, th in thresholds.items():
        v = values.get(metric)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lv = level(v, th)
            if lv == "critical":
                return lv
            if lv == "warning":
                worst = lv
    return worst


# --- engine -----------------------------------------------------------------------------------------


@dataclass
class RuleState:
    since: dict[str, datetime] = field(default_factory=dict)  # condition key -> first violating ts
    last_motion: dict[str, datetime] = field(default_factory=dict)

    def held(self, key: str, violated: bool, ts: datetime, need_s: float) -> bool:
        if not violated:
            self.since.pop(key, None)
            return False
        return (ts - self.since.setdefault(key, ts)).total_seconds() >= need_s


def evaluate(ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    out: list[Signal] = []
    for rule in snap.rules.values():
        fn = EVALUATORS.get(rule.kind)
        if fn is not None:
            out += fn(rule, ctx, snap, state)
    return out


def _threshold(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    p = rule.params
    excluded = set(p.get("exclude_metrics", []))
    payload = ctx.event.payload.model_dump()
    out = []
    for metric, th in ctx.sensor.thresholds.items():
        v = payload.get(metric)
        if metric in excluded or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        obj = f"{ctx.sensor.id}:{metric}"
        lv = level(v, th)
        # a warning must hold for min_duration_s (noise); a critical value is an alarm at once
        need = p.get("critical_min_duration_s", 0) if lv == "critical" else p.get("min_duration_s", 0)
        if state.held(f"{rule.id}:{obj}", lv != "ok", ctx.event.ts, need):
            sev = p.get("critical_severity", "critical") if lv == "critical" else rule.severity
            name = METRICS.get(metric, {}).get("name", metric)
            out.append(Signal("open", rule, obj, sev, f"{name}: {ctx.sensor.name}",
                              describe(metric, v, th) + f" (порог v{th.version})", v, **ctx.refs))
        elif safely_inside(v, th, p.get("hysteresis_pct", 0)):
            out.append(Signal("clear", rule, obj))
    return out


def _speed(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    if ctx.event.type != SensorType.GNSS or ctx.vehicle is None:
        return []
    p, speed = rule.params, ctx.event.payload.speed_kmh
    # one zone by id, or every zone of a type (each site has its own speed geozone)
    in_zone = any(z.id == p.get("zone_id") or z.zone_type == p.get("zone_type") for z in ctx.zones)
    limit = p.get("limit_kmh", 20)
    if state.held(f"{rule.id}:{ctx.vehicle.id}", in_zone and speed > limit, ctx.event.ts, p.get("min_duration_s", 0)):
        sev = "critical" if speed > p.get("critical_kmh", float("inf")) else rule.severity
        return [Signal("open", rule, ctx.vehicle.id, sev, f"Превышение скорости: {ctx.vehicle.plate}",
                       f"{speed:g} км/ч при ограничении {limit:g} км/ч", speed, **ctx.refs)]
    if not in_zone or speed <= limit - 2:
        return [Signal("clear", rule, ctx.vehicle.id)]
    return []


def _whitelist(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    ev, p = ctx.event, rule.params
    if ev.type != p.get("sensor_type") or ev.payload.direction != "in":
        return []
    if ev.type == SensorType.ANPR_CAMERA:
        if p.get("zones") and ctx.sensor.zone_id not in p["zones"]:
            return []
        plate = ev.payload.plate
        if plate in snap.plates:
            return []
        return [Signal("open", rule, plate, rule.severity, f"Номер вне базы: {plate}",
                       f"{ctx.sensor.name}: машина {plate} не в списке допуска", **ctx.refs)]
    card, granted = ev.payload.card_id, ev.payload.granted
    building = ctx.sensor.zone_id
    where = snap.building_names.get(building, building)
    pass_ = snap.cards.get(card)
    if pass_ is None:
        what = "просрочен" if card in snap.expired_cards else "не найден в базе"
        verdict = "контроллер ПРОПУСТИЛ" if granted else "проход запрещён контроллером"
        return [Signal("open", rule, card, "critical" if granted else rule.severity, f"Пропуск {card} {what}",
                       f"{where}: пропуск {card} {what}, {verdict}", **ctx.refs)]
    if pass_.allowed_building_ids is not None and building not in pass_.allowed_building_ids:
        return [Signal("open", rule, card, rule.severity, f"Нет доступа: {card}",
                       f"{where}: у пропуска {card} нет доступа в это здание", **ctx.refs)]
    sched = snap.schedules.get(pass_.schedule_id or "")
    if sched and not in_schedule(sched.intervals, sched.timezone, ev.ts):
        return [Signal("open", rule, card, rule.severity, f"Проход вне графика: {card}",
                       f"{where}: пропуск {card} вне графика «{sched.id}»", **ctx.refs)]
    return []


def _after_hours(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    """Motion nobody accounts for.

    With `require_empty` (default) the alarm needs the building to be empty by access control:
    a night shift that badged in moves legitimately, motion in an empty warehouse is an intrusion
    or tailgating at any hour. Without it, any motion outside the rule's schedule alarms.
    Devices report over different channels (motion via MQTT, turnstiles via HTTP), so a person
    walking in can be seen moving a moment before their badge arrives: the condition must hold
    for `min_duration_s`. It clears after `quiet_s` without unexplained motion.
    """
    ev, p = ctx.event, rule.params
    if ev.type != p.get("sensor_type", SensorType.MOTION):
        return []
    if snap.building_types.get(ctx.sensor.building_id or "") not in p.get("building_types", []):
        return []
    sched = snap.schedules.get(rule.schedule_id or "")
    off_hours = sched is not None and not in_schedule(sched.intervals, sched.timezone, ev.ts)
    empty = ctx.people == 0
    unexplained = ev.payload.detected and (empty if p.get("require_empty", True) else off_hours)
    key = f"{rule.id}:{ctx.sensor.id}"
    if unexplained:
        state.last_motion[ctx.sensor.id] = ev.ts
        if state.held(key, True, ev.ts, p.get("min_duration_s", 0)):
            where = snap.building_names.get(ctx.sensor.building_id, ctx.sensor.building_id)
            local = ev.ts.astimezone(ZoneInfo(sched.timezone if sched else "UTC"))
            title = f"Движение в нерабочее время: {where}" if off_hours else f"Движение в пустом здании: {where}"
            reason = "по СКУД внутри никого" if empty else f"вне расписания «{sched.id}»"
            return [Signal("open", rule, ctx.sensor.id, rule.severity, title,
                           f"{ctx.sensor.name}: движение в {local:%H:%M}, {reason}", **ctx.refs)]
        return []
    state.held(key, False, ev.ts, 0)  # quiet, or motion explained by a badge: the streak starts over
    last = state.last_motion.get(ctx.sensor.id)
    if last is None or (ev.ts - last).total_seconds() >= p.get("quiet_s", 300):
        return [Signal("clear", rule, ctx.sensor.id)]
    return []


def _breakdown(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    if ctx.event.type != SensorType.GNSS or ctx.vehicle is None:
        return []
    p, payload = rule.params, ctx.event.payload
    stopped = not payload.engine_on and payload.speed_kmh < 1
    allowed = any(z.zone_type in p.get("allowed_zone_types", []) for z in ctx.zones)
    violated = bool(ctx.zones) and stopped and not allowed  # no zones = off site
    if state.held(f"{rule.id}:{ctx.vehicle.id}", violated, ctx.event.ts, p.get("stopped_min", 10) * 60):
        mins = (ctx.event.ts - state.since[f"{rule.id}:{ctx.vehicle.id}"]).total_seconds() / 60
        where = next((z.name for z in ctx.zones if z.zone_type != "speed"), "на проезде")
        how_long = f"{mins:.0f} мин" if mins >= 1 else "— двигатель только что заглушен"
        return [Signal("open", rule, ctx.vehicle.id, rule.severity, f"Техника стоит вне стоянки: {ctx.vehicle.plate}",
                       f"{ctx.vehicle.plate} стоит с заглушенным двигателем {how_long} ({where})", **ctx.refs)]
    if not violated:
        return [Signal("clear", rule, ctx.vehicle.id)]
    return []


def _geozone(rule: Rule, ctx: Context, snap: Snapshot, state: RuleState) -> list[Signal]:
    p = rule.params
    if ctx.vehicle is None or ctx.vehicle.kind not in p.get("vehicle_kinds", [ctx.vehicle.kind]):
        return []
    if any(z.id == p.get("zone_id") for z in ctx.entered):
        zone = next(z for z in ctx.entered if z.id == p["zone_id"])
        return [Signal("open", rule, ctx.vehicle.id, rule.severity, f"{rule.name}: {ctx.vehicle.plate}",
                       f"{ctx.vehicle.plate} въехал в зону «{zone.name}»", **ctx.refs)]
    if any(z.id == p.get("zone_id") for z in ctx.exited):
        return [Signal("clear", rule, ctx.vehicle.id)]
    return []


EVALUATORS = {"threshold": _threshold, "speed": _speed, "whitelist": _whitelist, "schedule": _after_hours,
              "breakdown": _breakdown, "geozone": _geozone}
# "offline" is the absence of events: evaluated by a timer in alerts.py, not per event.
