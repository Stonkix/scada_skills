from datetime import UTC, datetime, timedelta

import pytest
from scada_common import SensorType, parse_event
from shapely.geometry import box

from scada_worker.registry import Pass, Rule, Schedule, SensorRef, Snapshot, Threshold, VehicleRef, ZoneRef
from scada_worker.rules import Context, RuleState, evaluate, safely_inside
from scada_worker.schedule import in_schedule

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)  # Wednesday 15:00 Moscow
WORK = [{"days": [1, 2, 3, 4, 5], "start": "07:00", "end": "21:00"}]

COLD = Threshold("temperature_c", 1, 4, 2, 6, 0, 8)
HUM = Threshold("humidity_pct", 1, 60, 40, 75, 30, 85)
FUEL = Threshold("fuel_pct", 1, None, 15, None, 5, None)
SPEED_TH = Threshold("speed_kmh", 1, None, None, 20, None, 30)

RULES = {
    "rule-threshold": Rule("rule-threshold", 1, "Пороги", "threshold", "warning",
                           {"hysteresis_pct": 5, "min_duration_s": 30, "exclude_metrics": ["speed_kmh"]}, None, 300),
    "rule-speed": Rule("rule-speed", 1, "Скорость", "speed", "warning",
                       {"zone_type": "speed", "limit_kmh": 20, "critical_kmh": 30, "min_duration_s": 5}, None, None),
    "rule-plate": Rule("rule-plate", 1, "Номер", "whitelist", "warning",
                       {"sensor_type": "anpr_camera", "list": "plate", "zones": ["z-gate"]}, None, None),
    "rule-card": Rule("rule-card", 1, "Пропуск", "whitelist", "warning",
                      {"sensor_type": "access_control", "list": "card"}, None, None),
    "rule-after-hours": Rule("rule-after-hours", 1, "Ночь", "schedule", "critical",
                             {"sensor_type": "motion", "building_types": ["warehouse"], "min_duration_s": 20}, "work-hours", None),
    "rule-breakdown": Rule("rule-breakdown", 1, "Поломка", "breakdown", "critical",
                           {"stopped_min": 3, "allowed_zone_types": ["parking", "docks"]}, None, None),
    "rule-geo": Rule("rule-geo", 1, "Ремзона", "geozone", "info",
                     {"zone_id": "z-yard", "event": "enter", "vehicle_kinds": ["truck"]}, None, None),
}
SENSORS = {
    "clim": SensorRef("clim", SensorType.CLIMATE, "Климат склад 2", "b-wh2", "r-wh2", False, None,
                      {"temperature_c": COLD, "humidity_pct": HUM}),
    "gnss": SensorRef("gnss", SensorType.GNSS, "Трекер", None, None, True, "v1", {"fuel_pct": FUEL, "speed_kmh": SPEED_TH}),
    "cam": SensorRef("cam", SensorType.ANPR_CAMERA, "Камера КПП", None, "z-gate", False, None),
    "acs": SensorRef("acs", SensorType.ACCESS_CONTROL, "СКУД", "b-wh1", "b-wh1", False, None),
    "mot": SensorRef("mot", SensorType.MOTION, "Движение", "b-wh1", "r-wh1", False, None),
}
SITE, PARKING, YARD = (ZoneRef("z-site", "Территория", "speed", box(0, 0, 800, 500)),
                       ZoneRef("z-parking", "Стоянка", "parking", box(30, 40, 110, 180)),
                       ZoneRef("z-yard", "Двор ремзоны", "restricted", box(645, 40, 795, 175)))


def snap() -> Snapshot:
    return Snapshot(SENSORS, {"v1": VehicleRef("v1", "А123ВС77", "truck")}, {"b-wh1": "warehouse", "b-wh2": "warehouse"},
                    {"b-wh1": "Склад №1", "b-wh2": "Склад №2"}, [SITE, PARKING, YARD], RULES,
                    {"work-hours": Schedule("work-hours", "Europe/Moscow", WORK)},
                    {"P-1": Pass(None, None), "P-2": Pass(("b-admin",), None)}, {"P-9"}, {"А123ВС77"}, {})


def ev(sensor: str, payload: dict, at: datetime = T0, geo: dict | None = None):
    return parse_event({"sensor_id": sensor, "type": SENSORS[sensor].type, "ts": at, "payload": payload, "geo": geo})


def run(sensor: str, payload: dict, state: RuleState, at: datetime = T0, **ctx) -> list:
    s = snap()
    c = Context(ev(sensor, payload, at), SENSORS[sensor], s.vehicles.get(SENSORS[sensor].vehicle_id or ""), **ctx)
    return [(x.action, x.rule.id, x.object_id, x.severity) for x in evaluate(c, s, state)]


def opens(out: list) -> list:
    return [x for x in out if x[0] == "open"]


def clears(out: list) -> list:
    return [x[:3] for x in out if x[0] == "clear"]


def climate(t: float) -> dict:
    return {"temperature_c": t, "humidity_pct": 60}


def test_threshold_needs_min_duration_then_escalates_and_clears_with_hysteresis() -> None:
    st = RuleState()
    assert ("open", "rule-threshold", "clim:temperature_c", "warning") not in run("clim", climate(7), st)
    assert ("open", "rule-threshold", "clim:temperature_c", "warning") in run("clim", climate(7), st, T0 + timedelta(seconds=31))
    assert ("open", "rule-threshold", "clim:temperature_c", "critical") in run("clim", climate(9), st, T0 + timedelta(seconds=40))
    # 5.9 is back in the norm but within the 5 % margin of max=6: hold, neither open nor clear
    held = run("clim", climate(5.9), st, T0 + timedelta(seconds=50))
    assert not [x for x in held if x[2] == "clim:temperature_c"]
    assert ("clear", "rule-threshold", "clim:temperature_c") in clears(run("clim", climate(4), st, T0 + timedelta(seconds=60)))


def test_hysteresis_margin_for_one_sided_threshold() -> None:
    assert not safely_inside(15.5, FUEL, 5) and safely_inside(16, FUEL, 5)


def gnss(speed: float, engine: bool = True, fuel: float = 50) -> dict:
    return {"speed_kmh": speed, "heading_deg": 0, "fuel_pct": fuel, "engine_on": engine}


def test_speed_rule_and_excluded_speed_threshold() -> None:
    st = RuleState()
    run("gnss", gnss(35), st, zones=[SITE])
    out = run("gnss", gnss(35), st, T0 + timedelta(seconds=6), zones=[SITE])
    assert ("open", "rule-speed", "v1", "critical") in out
    assert not [x for x in out if x[2] == "gnss:speed_kmh"], "speed is excluded from the threshold rule"
    assert ("clear", "rule-speed", "v1") in clears(run("gnss", gnss(17), st, T0 + timedelta(seconds=8), zones=[SITE]))


def test_speed_rule_covers_every_site_but_not_the_highway() -> None:
    other_site = ZoneRef("z-dmd-speed", "РЦ", "speed", box(15000, 2800, 15420, 3100))
    st = RuleState()
    run("gnss", gnss(35), st, zones=[other_site])
    assert ("open", "rule-speed", "v1", "critical") in run("gnss", gnss(35), st, T0 + timedelta(seconds=6), zones=[other_site])
    highway = RuleState()
    run("gnss", gnss(80), highway, zones=[])
    assert not opens(run("gnss", gnss(80), highway, T0 + timedelta(seconds=60), zones=[]))


def test_low_fuel_goes_through_threshold_rule() -> None:
    st = RuleState()
    run("gnss", gnss(10, fuel=12), st, zones=[SITE])
    assert ("open", "rule-threshold", "gnss:fuel_pct", "warning") in run(
        "gnss", gnss(10, fuel=12), st, T0 + timedelta(seconds=31), zones=[SITE])


def test_breakdown_only_on_site_outside_allowed_zones_after_stopped_min() -> None:
    st = RuleState()
    run("gnss", gnss(0, engine=False), st, zones=[SITE])
    assert not [x for x in run("gnss", gnss(0, engine=False), st, T0 + timedelta(minutes=2), zones=[SITE]) if x[0] == "open"]
    assert ("open", "rule-breakdown", "v1", "critical") in run("gnss", gnss(0, engine=False), st, T0 + timedelta(minutes=3), zones=[SITE])
    assert not [x for x in run("gnss", gnss(0, engine=False), RuleState(), T0, zones=[SITE, PARKING]) if x[0] == "open"]
    assert not [x for x in run("gnss", gnss(0, engine=False), RuleState(), T0, zones=[]) if x[0] == "open"], "off site"


def test_unknown_plate_only_at_the_gate() -> None:
    plate = {"plate": "Р135ЕК99", "direction": "in", "confidence": 0.9}
    assert ("open", "rule-plate", "Р135ЕК99", "warning") in run("cam", plate, RuleState())
    assert not run("cam", {**plate, "plate": "А123ВС77"}, RuleState())
    assert not run("cam", {**plate, "direction": "out"}, RuleState())


@pytest.mark.parametrize(("card", "granted", "severity"), [("P-404", False, "warning"), ("P-9", True, "critical"),
                                                           ("P-2", True, "warning")])
def test_card_problems(card: str, granted: bool, severity: str) -> None:
    out = run("acs", {"card_id": card, "direction": "in", "granted": granted}, RuleState())
    assert out == [("open", "rule-card", card, severity)]


def test_known_card_passes() -> None:
    assert run("acs", {"card_id": "P-1", "direction": "in", "granted": True}, RuleState()) == []


def test_motion_alarms_only_when_nobody_badged_in() -> None:
    night = datetime(2026, 10, 7, 23, 30, tzinfo=UTC)  # 02:30 Moscow
    st = RuleState()
    assert opens(run("mot", {"detected": True}, st, night, people=3)) == [], "night shift that badged in"
    assert opens(run("mot", {"detected": True}, st, people=0)) == [], "a badge may still be on its way"
    later = run("mot", {"detected": True}, st, T0 + timedelta(seconds=30), people=0)
    assert ("open", "rule-after-hours", "mot", "critical") in later
    st2 = RuleState()
    evaluate(Context(ev("mot", {"detected": True}, night), SENSORS["mot"], people=0), snap(), st2)
    [sig] = evaluate(Context(ev("mot", {"detected": True}, night + timedelta(seconds=20)), SENSORS["mot"], people=0),
                     snap(), st2)
    assert sig.title.startswith("Движение в нерабочее время") and "02:30" in sig.message


def test_badge_arriving_after_motion_prevents_the_alarm() -> None:
    st = RuleState()
    run("mot", {"detected": True}, st, people=0)  # motion overtook the turnstile event
    assert opens(run("mot", {"detected": True}, st, T0 + timedelta(seconds=15), people=1)) == []
    assert opens(run("mot", {"detected": True}, st, T0 + timedelta(seconds=30), people=0)) == [], "streak restarted"


def test_motion_alarm_clears_after_quiet_period() -> None:
    st = RuleState()
    run("mot", {"detected": True}, st, people=0)
    run("mot", {"detected": True}, st, T0 + timedelta(seconds=20), people=0)
    assert run("mot", {"detected": False}, st, T0 + timedelta(minutes=1), people=0) == []
    assert ("clear", "rule-after-hours", "mot") in clears(run("mot", {"detected": False}, st, T0 + timedelta(minutes=6)))


def test_geozone_enter_exit() -> None:
    assert ("open", "rule-geo", "v1", "info") in run("gnss", gnss(10), RuleState(), zones=[SITE, YARD], entered=[YARD])
    assert ("clear", "rule-geo", "v1") in clears(run("gnss", gnss(10), RuleState(), zones=[SITE], exited=[YARD]))


def test_schedule_across_midnight_belongs_to_start_day() -> None:
    night = [{"days": [5], "start": "22:00", "end": "06:00"}]  # Friday night shift
    sat_3am = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)  # Saturday 03:00 Moscow
    sun_3am = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)
    assert in_schedule(night, "Europe/Moscow", sat_3am) and not in_schedule(night, "Europe/Moscow", sun_3am)
    assert in_schedule([{"days": [3], "start": "00:00", "end": "24:00"}], "Europe/Moscow", T0)


def test_critical_value_alarms_at_once_warning_waits() -> None:
    st = RuleState()
    assert ("open", "rule-threshold", "clim:temperature_c", "critical") in run("clim", climate(12), st)
    assert not opens(run("clim", climate(7), RuleState()))  # a warning still holds for min_duration_s


def test_engine_off_outside_allowed_zones_alarms_at_once_with_zero_stopped_min() -> None:
    snapshot = snap()
    instant = Rule("rule-breakdown", 1, "Поломка", "breakdown", "critical",
                   {"stopped_min": 0, "allowed_zone_types": ["parking", "docks"]}, None, None)
    snapshot.rules = {"rule-breakdown": instant}
    c = Context(ev("gnss", gnss(0, engine=False)), SENSORS["gnss"], snapshot.vehicles["v1"], zones=[SITE])
    out = [(x.action, x.severity) for x in evaluate(c, snapshot, RuleState())]
    assert ("open", "critical") in out
