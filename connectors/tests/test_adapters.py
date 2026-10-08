import pytest
from scada_common import SensorType, parse_event
from scada_common.geo import Georef

from scada_connectors.adapters import ADAPTERS

GEOREF = Georef(origin_lat=55.7, origin_lon=37.4)


def convert(name: str, raw: dict) -> dict:
    a = ADAPTERS[name]
    return a.convert(a.raw_model.model_validate(raw), GEOREF)


@pytest.mark.parametrize("name", [n for n in ADAPTERS if n != "native"])
def test_every_example_becomes_a_valid_event(name: str) -> None:
    a = ADAPTERS[name]
    fields = convert(name, a.example)
    event = parse_event({**fields, "type": a.sensor_type})
    assert event.type == a.sensor_type


def test_anpr_normalizes_latin_lookalikes_and_spaces() -> None:
    f = convert("anpr", ADAPTERS["anpr"].example)
    assert f["payload"] == {"plate": "А123ВС77", "direction": "in", "confidence": 0.965}


def test_skud_formats_wiegand_code() -> None:
    f = convert("skud", {**ADAPTERS["skud"].example, "card": 7, "event": "exit", "result": "denied"})
    assert f["payload"] == {"card_id": "P-000007", "direction": "out", "granted": False}


def test_climate_converts_fahrenheit() -> None:
    f = convert("climate", {**ADAPTERS["climate"].example, "temperature": 50, "unit": "F"})
    assert f["payload"]["temperature_c"] == 10.0


def test_gnss_projects_to_plan_metres() -> None:
    lat, lon = GEOREF.to_wgs84(300.0, 250.0)
    f = convert("gnss", {**ADAPTERS["gnss"].example, "lat": lat, "lon": lon, "odometer_m": 1500})
    assert f["geo"] == pytest.approx({"x": 300.0, "y": 250.0}, abs=0.01)
    assert f["payload"]["odometer_km"] == 1.5


def test_retried_delivery_gets_the_same_event_id() -> None:
    a = convert("climate", ADAPTERS["climate"].example)
    b = convert("climate", ADAPTERS["climate"].example)
    assert a["event_id"] == b["event_id"]


def test_same_card_passing_again_is_a_new_event() -> None:
    first = convert("skud", {**ADAPTERS["skud"].example, "time": "2026-10-08T08:00:00+03:00"})
    later = convert("skud", {**ADAPTERS["skud"].example, "time": "2026-10-08T17:30:00+03:00"})
    leaving = convert("skud", {**ADAPTERS["skud"].example, "time": "2026-10-08T08:00:00+03:00", "event": "exit"})
    assert len({first["event_id"], later["event_id"], leaving["event_id"]}) == 3


def test_georef_roundtrip_with_rotation() -> None:
    g = Georef(55.7, 37.4, rotation_deg=17)
    assert g.to_local(*g.to_wgs84(123.4, -56.7)) == pytest.approx((123.4, -56.7), abs=1e-6)


def test_adapter_registry_covers_every_sensor_type() -> None:
    assert {a.sensor_type for a in ADAPTERS.values() if a.sensor_type} == set(SensorType)
