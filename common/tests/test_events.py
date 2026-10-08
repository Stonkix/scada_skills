from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from scada_common import ClimateEvent, GnssEvent, SensorType, parse_event


def _event(**over) -> dict:
    base = {
        "sensor_id": "clim-wh2-storage",
        "type": "climate",
        "ts": "2026-10-08T12:00:00+03:00",
        "payload": {"temperature_c": 4.2, "humidity_pct": 61},
    }
    return base | over


def test_parses_into_type_specific_model() -> None:
    ev = parse_event(_event())
    assert isinstance(ev, ClimateEvent)
    assert ev.schema_v == 1
    assert ev.event_id is not None


def test_json_roundtrip_keeps_event_id() -> None:
    ev = parse_event(_event(geo={"x": 470, "y": 365, "floor": 1}))
    again = parse_event(ev.model_dump_json())
    assert again == ev


def test_gnss_event() -> None:
    ev = parse_event({
        "sensor_id": "gnss-truck-1", "type": SensorType.GNSS, "ts": datetime.now(UTC),
        "geo": {"x": 100, "y": 250},
        "payload": {"speed_kmh": 18, "heading_deg": 90, "fuel_pct": 64, "engine_on": True},
    })
    assert isinstance(ev, GnssEvent)


@pytest.mark.parametrize("bad", [
    _event(payload={"temperature_c": 4.2}),  # missing humidity
    _event(type="unknown"),
    _event(ts="2026-10-08T12:00:00"),  # naive time is ambiguous
    _event(payload={"plate": "А123ВС77", "direction": "in", "confidence": 0.9}),  # payload of another type
    _event(extra_field=1),
    _event(schema_v=2),
])
def test_rejects_invalid(bad: dict) -> None:
    with pytest.raises(ValidationError):
        parse_event(bad)
