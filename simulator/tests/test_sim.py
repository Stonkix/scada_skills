"""Simulation tests on a world built from the seed files (no database needed)."""

import asyncio
import json
import math
from pathlib import Path

import pytest
from scada_common import catalog, parse_event
from scada_common.geo import Georef
from shapely.geometry import Point, shape

from scada_connectors.adapters import ADAPTERS
from scada_simulator import scenarios as sc
from scada_simulator.roads import Road, RoadGraph
from scada_simulator.sim import GATE, OFF_SITE, PARKING, Simulation
from scada_simulator.world import Area, SensorSpec, VehicleSpec, World

SEED = Path(__file__).resolve().parents[2] / "deploy" / "seed"
LAYOUT = json.loads((SEED / "layout.geojson").read_text(encoding="utf-8"))
FLEET = json.loads((SEED / "fleet.json").read_text(encoding="utf-8"))
ZONES = {f["id"]: shape(f["geometry"]) for f in LAYOUT["features"] if f["properties"]["kind"] == "geozone"}


def make_world() -> World:
    feats = LAYOUT["features"]
    roads = [Road(f["id"], [tuple(c) for c in f["geometry"]["coordinates"]], f["properties"]["speed_limit_kmh"])
             for f in feats if f["properties"]["kind"] == "road"]
    areas = {f["id"]: Area(f["id"], f["properties"]["kind"],
                           f["properties"].get("building_type") or f["properties"].get("zone_type")
                           or f["properties"].get("room_type"), shape(f["geometry"]).bounds)
             for f in feats if f["properties"]["kind"] in ("building", "room", "geozone")}
    sensors = {}
    for f in feats:
        p = f["properties"]
        if p["kind"] == "sensor":
            nominal = {t["metric"]: t["nominal"] for t in catalog.default_thresholds(p["sensor_type"], f["id"])
                       if t["nominal"] is not None}
            sensors[f["id"]] = SensorSpec(f["id"], p["sensor_type"], p.get("building_id"), p.get("zone_id"),
                                          *f["geometry"]["coordinates"], nominal=nominal)
    vehicles = {v["id"]: VehicleSpec(v["id"], v["plate"], v["kind"], catalog.gnss_sensor_id(v["id"])) for v in FLEET}
    return World(Georef.from_layout(LAYOUT), roads, areas, sensors, vehicles,
                 valid_cards=[(f"P-{n:06d}", None) for n in range(280)], expired_cards=["P-000285"],
                 unknown_cards=["P-000295"])


@pytest.fixture
def sim() -> Simulation:
    return Simulation(make_world(), seed=3)


def run(sim: Simulation, seconds: int) -> list:
    out = []
    for _ in range(seconds):
        out += sim.tick()
    return out


def test_road_graph_reaches_every_stop(sim: Simulation) -> None:
    for target in [*sim._docks, PARKING, (725.0, 160.0)]:
        path = sim.graph.path(OFF_SITE, target)
        assert path[-1][0] == sim.graph.nearest(*target)
    assert len(sim._docks) == 3


def test_stops_are_inside_their_zones(sim: Simulation) -> None:
    # stopping on a zone boundary would let GNSS noise trigger false breakdown alerts
    for i, dock in enumerate(sorted(sim._docks), start=1):
        assert ZONES[f"z-docks-wh{i}"].buffer(-2).contains(Point(dock))
    assert ZONES["z-parking"].buffer(-2).contains(Point(PARKING))


def test_spur_speed_limit_is_respected() -> None:
    graph = RoadGraph([Road("main", [(0, 0), (100, 0)], 20), Road("spur", [(50, 0), (50, 40)], 10)])
    path = graph.path((0, 0), (50, 40))
    assert [lim for _, lim in path[1:]] == [20, 10]


def test_every_message_passes_its_adapter_and_the_contract(sim: Simulation) -> None:
    out = run(sim, 240)
    assert {o.adapter for o in out} >= {"gnss", "climate", "motion", "skud", "anpr"}
    for o in out:
        a = ADAPTERS[o.adapter]
        fields = a.convert(a.raw_model.model_validate(o.payload), sim.world.georef)
        parse_event({**fields, "type": a.sensor_type})


def test_vehicles_stay_on_roads(sim: Simulation) -> None:
    roads = [shape({"type": "LineString", "coordinates": r.coords}) for r in sim.world.roads + [Road("a", [OFF_SITE, GATE], 0)]]
    for _ in range(300):
        sim.tick()
        for v in sim.vehicles:
            assert min(r.distance(Point(v.x, v.y)) for r in roads) < 0.01, v.id


def test_truck_entry_is_seen_by_gate_camera(sim: Simulation) -> None:
    sc.vehicle_arrival(sim, vehicle="v-truck-8")
    out = run(sim, 120)
    gate = [o.payload for o in out if o.device_id == "cam-gate-in"]
    assert any(p["plate"] == "Р135ЕК99" and p["direction"] == "approach" for p in gate)


def test_breakdown_stops_the_vehicle_with_engine_off(sim: Simulation) -> None:
    run(sim, 200)
    truck = next(v for v in sim.vehicles if v.kind == "truck" and v.speed_kmh > 0 and v.x > 0)
    sc.vehicle_breakdown(sim, duration_s=60, vehicle=truck.id)
    x, y = truck.x, truck.y
    run(sim, 20)
    assert (truck.x, truck.y, truck.speed_kmh, truck.engine_on) == (x, y, 0, False)


def test_climate_drift_raises_temperature(sim: Simulation) -> None:
    spec = sim.world.sensors["clim-wh2-storage"]
    base = sim._climate(spec, 0).payload["temperature"]
    sc.climate_drift(sim, sensor="clim-wh2-storage", delta_c=7, duration_s=600, ramp_s=120)
    sim.effects[-1].started -= 120  # ramp complete
    assert sim._climate(spec, 0).payload["temperature"] > base + 5


def test_offline_sensor_is_silenced(sim: Simulation) -> None:
    sc.sensor_offline(sim, sensor="clim-prod2-stock", duration_s=60)
    assert "clim-prod2-stock" in sim.silenced()


def test_card_swipe_picks_from_whitelist_gaps(sim: Simulation) -> None:
    [msg] = sc.card_swipe(sim, reader="acs-wh2", card="unknown", granted=False)
    assert msg.payload["card"] == "P-000295" and msg.payload["result"] == "denied"


def test_people_counts_follow_turnstile_events(sim: Simulation) -> None:
    out = run(sim, 600)
    net: dict[str, int] = {}
    reader_building = {s.id: s.zone_id for s in sim.world.sensors_of("access_control")}
    for o in out:
        if o.adapter == "skud":
            b = reader_building[o.device_id]
            net[b] = net.get(b, 0) + (1 if o.payload["event"] == "entry" else -1)
    assert net == {b: len(p) for b, p in sim.inside.items() if len(p) or b in net}
    assert sum(net.values()) > 20


def test_scenarios_file_is_valid(sim: Simulation) -> None:
    scenarios = sc.load(Path(__file__).resolve().parents[1] / "scenarios.yaml")
    assert {"breakdown", "unknown_plate", "overheat", "sensor_offline", "demo"} <= set(scenarios)

    async def execute_all() -> None:
        sent = []

        async def send(out):
            sent.extend(out)

        runner = sc.Runner(sim, send)
        for s in scenarios.values():
            s.steps = [st for st in s.steps if "wait_s" not in st]  # no sleeping in tests
            run_ = runner.start(s)
            await run_.task
            assert run_.error is None, (s.name, run_.error)

    run(sim, 200)  # get vehicles moving first
    asyncio.run(execute_all())
    assert math.isfinite(sim.vehicle("v-car-1").x)
