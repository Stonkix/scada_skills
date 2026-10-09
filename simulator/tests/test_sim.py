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
from scada_connectors.documents import Waybill
from scada_simulator import scenarios as sc
from scada_simulator.roads import Road, RoadGraph
from scada_simulator.sim import Simulation
from scada_simulator.world import Area, SensorSpec, VehicleSpec, World, sites_from_layout

SEED = Path(__file__).resolve().parents[2] / "deploy" / "seed"
LAYOUT = json.loads((SEED / "layout.geojson").read_text(encoding="utf-8"))
FLEET = json.loads((SEED / "fleet.json").read_text(encoding="utf-8"))
UNPLACED = json.loads((SEED / "sensors.json").read_text(encoding="utf-8"))
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
    for s in UNPLACED:  # registered in a building without a place on the plan
        nominal = {t["metric"]: t["nominal"] for t in catalog.default_thresholds(s["type"], s["id"]) if t["nominal"] is not None}
        sensors[s["id"]] = SensorSpec(s["id"], s["type"], s["building_id"], s["zone_id"], None, None, nominal=nominal)
    vehicles = {v["id"]: VehicleSpec(v["id"], v["plate"], v["kind"], catalog.gnss_sensor_id(v["id"]), v["home_site_id"])
                for v in FLEET}
    return World(Georef.from_layout(LAYOUT), sites_from_layout(LAYOUT), roads, areas, sensors, vehicles,
                 valid_cards=[(f"P-{n:06d}", None) for n in range(280)], expired_cards=["P-000285"],
                 unknown_cards=["P-000295"])


@pytest.fixture
def sim() -> Simulation:
    s = Simulation(make_world(), seed=3)
    start = s.clock()
    s.clock = lambda: start + s.tick_no  # one simulated second per tick
    return s


def run(sim: Simulation, seconds: int) -> list:
    out = []
    for _ in range(seconds):
        out += sim.tick()
    return out


def test_road_graph_reaches_every_stop_of_every_site(sim: Simulation) -> None:
    assert set(sim.sites) == {"s-podolsk", "s-domodedovo", "s-chekhov"}
    for site in sim.sites.values():
        for target in [*site.docks, *site.parking]:
            path = sim.graph.path(sim.main.approach, target)
            assert path[-1][0] == target, (site.id, target)
        assert site.docks and site.parking and site.cam_in and site.cam_out, site.id
    assert len(sim.main.docks) == 3


def test_sites_are_tens_of_kilometres_apart_by_road(sim: Simulation) -> None:
    km = {(a, b): sim.route_m(sim.sites[a], sim.sites[b]) / 1000 for a in sim.sites for b in sim.sites if a < b}
    assert all(15 < d < 80 for d in km.values()), km


def test_stops_are_inside_their_zones(sim: Simulation) -> None:
    # stopping on a zone boundary would let GNSS noise trigger false breakdown alerts
    for i, dock in enumerate(sim.main.docks, start=1):
        assert ZONES[f"z-docks-wh{i}"].buffer(-2).contains(Point(dock))
    for site in sim.sites.values():
        for stop in site.docks + site.parking:
            assert any(z.buffer(-2).contains(Point(stop)) for z in ZONES.values()), (site.id, stop)


def test_spur_speed_limit_is_respected() -> None:
    graph = RoadGraph([Road("main", [(0, 0), (100, 0)], 20), Road("spur", [(50, 0), (50, 40)], 10)])
    path = graph.path((0, 0), (50, 40))
    assert [lim for _, lim in path[1:]] == [20, 10]


def test_every_message_passes_its_adapter_and_the_contract(sim: Simulation) -> None:
    out = run(sim, 240)
    assert {o.adapter for o in out} >= {"gnss", "climate", "motion", "skud", "anpr", "waybill"}
    for o in out:
        if o.adapter == "waybill":
            Waybill.model_validate(o.payload)
            continue
        a = ADAPTERS[o.adapter]
        fields = a.convert(a.raw_model.model_validate(o.payload), sim.world.georef)
        parse_event({**fields, "type": a.sensor_type})


def test_vehicles_stay_on_roads(sim: Simulation) -> None:
    roads = [shape({"type": "LineString", "coordinates": r.coords}) for r in sim.world.roads]
    for _ in range(300):
        sim.tick()
        for v in sim.vehicles:
            assert min(r.distance(Point(v.x, v.y)) for r in roads) < 0.01, v.id


def test_truck_entry_is_seen_by_gate_camera(sim: Simulation) -> None:
    sc.vehicle_arrival(sim, vehicle="v-truck-8")
    out = run(sim, 120)
    gate = [o.payload for o in out if o.device_id == "cam-gate-in"]
    assert any(p["plate"] == "Р135ЕК99" and p["direction"] == "approach" for p in gate)


def test_trip_runs_from_dock_to_dock_through_both_gates(sim: Simulation) -> None:
    truck = next(v for v in sim.vehicles if v.kind == "truck" and v.trip is None and not v.parked_off_site)
    truck.dwell_left, truck.tasks = 0, []
    out = run(sim, 4 * 3600)  # the longest highway is ~55 km
    docs = [o.payload for o in out if o.adapter == "waybill" and o.payload["plate"] == truck.plate]
    first = docs[0]["waybill_no"]
    statuses = [d["status"] for d in docs if d["waybill_no"] == first]
    assert statuses == ["loading", "en_route", "unloading", "done"], statuses
    trip = docs[0]
    dest = sim.sites[trip["destination_site_id"]]
    seen = [o.payload for o in out if o.device_id == dest.cam_in and o.payload["plate"] == truck.plate]
    assert seen and seen[0]["direction"] == "approach"
    en_route = next(d for d in docs if d["status"] == "en_route")
    assert en_route["eta"] > en_route["departed_at"]


def test_half_of_the_trucks_start_on_the_road(sim: Simulation) -> None:
    on_road = [v for v in sim.vehicles if v.kind == "truck" and v.trip and v.trip["status"] == "en_route"]
    assert len(on_road) >= 3 and all(sim.site_at(v.x, v.y) is None for v in on_road)
    assert {o.payload["waybill_no"] for o in sim.tick() if o.adapter == "waybill"} == {v.trip["waybill_no"] for v in on_road}


def test_breakdown_stops_the_vehicle_with_engine_off(sim: Simulation) -> None:
    run(sim, 200)
    sc.vehicle_breakdown(sim, duration_s=600)
    truck = sim.vehicle(sim.effects[-1].target)
    for _ in range(300):  # armed (nobody was driving on a site): wait until it is out on a roadway
        if sim.effect("breakdown", truck.id):
            break
        run(sim, 1)
    run(sim, 1)
    x, y = truck.x, truck.y
    run(sim, 20)
    assert (truck.x, truck.y, truck.speed_kmh, truck.engine_on) == (x, y, 0, False)


def test_breakdown_picks_a_truck_on_the_roadway(sim: Simulation) -> None:
    run(sim, 200)
    picked = 0
    for _ in range(20):
        run(sim, 7)
        try:
            sc.vehicle_breakdown(sim, duration_s=60)
        except sc.ScenarioError as e:  # sometimes no truck is driving on a roadway: a valid answer
            assert "no moving" in str(e)
            continue
        if sim.effects[-1].kind == "breakdown_next":  # nobody was driving: armed, fires once on a roadway
            target = sim.effects[-1].target
            for _ in range(180):
                run(sim, 1)
                if sim.effect("breakdown", target):
                    break
            assert sim.effect("breakdown", target), "the armed breakdown happens within a few minutes"
        broken = sim.vehicle(sim.effects[-1].target)
        assert not sc._in_stop_zone(sim, broken.x, broken.y)
        sim.effects.clear()
        picked += 1
    assert picked == 20


def test_breakdown_refuses_a_vehicle_beyond_the_gate(sim: Simulation) -> None:
    with pytest.raises(sc.ScenarioError, match="not on site"):
        sc.vehicle_breakdown(sim, duration_s=60, vehicle="v-truck-8")  # waits beyond the gate


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


def test_evacuate_empties_building_and_blocks_entries(sim: Simulation) -> None:
    run(sim, 300)
    inside = len(sim.inside["b-wh3"])
    out = sc.evacuate(sim, building="b-wh3", duration_s=600)
    assert len(out) == inside and all(o.payload["event"] == "exit" for o in out)
    run(sim, 300)
    assert sim.inside["b-wh3"] == set()


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


def test_devices_report_by_exception_within_their_limits(sim: Simulation) -> None:
    out = run(sim, 900)
    gnss = catalog.REPORTING["gnss"]
    fixes: dict[str, list[tuple[float, float]]] = {}
    for o in out:
        if o.adapter == "gnss":
            fixes.setdefault(o.device_id, []).append((o.payload["fix_time"], o.payload["speed"]))
    for device, fx in fixes.items():
        gaps = [(b[0] - a[0], a[1]) for a, b in zip(fx, fx[1:])]
        assert min(g for g, _ in gaps) >= gnss.min_interval_s and max(g for g, _ in gaps) <= gnss.heartbeat_s + 1, device
        # a moving vehicle is seen every moving_period_s: the map can follow the road
        assert all(g <= gnss.moving_period_s + 0.5 for g, speed in gaps if speed > 1), device
    assert sum(map(len, fixes.values())) < 900 * len(fixes)  # standing vehicles report once a minute
    climate = [o for o in out if o.adapter == "climate"]
    per_sensor = len(climate) / len(sim.world.sensors_of("climate"))
    assert 900 / catalog.REPORTING["climate"].heartbeat_s <= per_sensor < 900 / 15 / 2  # was every 15 s


def test_sensors_without_a_place_on_the_plan_still_report(sim: Simulation) -> None:
    out = run(sim, 900)
    devices = {o.device_id for o in out}
    assert {"clim-dmd-hangar", "mot-dmd-hangar"} <= devices
    assert sim.world.entrance("b-dmd-hangar").id == "acs-dmd-hangar"


def test_access_roads_dock_onto_real_roads_and_highways_avoid_sites() -> None:
    """Trucks leave a site on its access road straight onto a real road; no highway runs across a site."""
    routes = json.loads((SEED / "routes.json").read_text(encoding="utf-8"))
    georef = Georef.from_layout(LAYOUT)
    sites = sites_from_layout(LAYOUT)
    assert len(routes) == 3
    for key, entry in routes.items():
        a, b = key.split("|")
        pts = [georef.to_local(lat, lon) for lon, lat in entry["coords"]]
        assert math.dist(pts[0], sites[a].approach) <= 25, f"{a}: the access road does not reach the real road"
        assert math.dist(pts[-1], sites[b].approach) <= 25, f"{b}: the access road does not reach the real road"
        crossing = [s.id for p in pts for s in sites.values() if s.contains(*p, margin=-5)]
        assert not crossing, f"{key} crosses {set(crossing)}"


def test_fast_forward_moves_trucks_faster_and_shortens_eta(sim: Simulation) -> None:
    truck = next(v for v in sim.vehicles if v.trip and v.trip["status"] == "en_route")
    run(sim, 5)
    x, y = truck.x, truck.y
    run(sim, 10)
    normal = math.dist((x, y), (truck.x, truck.y))
    assert sim.set_time_scale(100) == 30  # clamped
    sim.set_time_scale(10)
    x, y = truck.x, truck.y
    run(sim, 10)
    fast = math.dist((x, y), (truck.x, truck.y))
    assert fast > 5 * normal, (normal, fast)
    out = run(sim, 1)
    assert all(o.adapter != "gnss" or o.payload["speed"] < 100 for o in out), "trackers still report road speed"
    sim.set_time_scale(1)
    assert sim.status()["time_scale"] == 1


def test_incidents_report_at_once(sim: Simulation) -> None:
    [msg] = sc.climate_drift(sim, sensor="clim-wh2-storage", delta_c=9, duration_s=600)
    assert msg.payload["temperature"] > 8, "past the critical bound of the cold store in the same message"
    [motion] = sc.motion_alarm(sim, sensor="mot-wh3", duration_s=60)
    assert motion.payload["state"] == "alarm"
    sc.vehicle_arrival(sim, vehicle="v-truck-8")
    out = run(sim, 2)
    assert any(o.device_id == sim.main.cam_in and o.payload["plate"] == "Р135ЕК99" for o in out)


def test_random_incidents_happen_anywhere(sim: Simulation) -> None:
    run(sim, 120)
    seen = set()
    for _ in range(40):
        what, out = sc.random_incident(sim)
        seen.add(what.split(":")[0])
        assert what != "нет подходящей аварии"
        run(sim, 3)
    assert {"Перегрев", "Номер вне базы", "Чужой пропуск", "Движение в пустом здании"} <= seen
