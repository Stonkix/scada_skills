"""Generate deploy/seed/layout.geojson — the demo enterprise: three sites across Moscow oblast.

Coordinates are local metres: the origin is the south-west corner of the main plant
(Podolsk), x grows east, y grows north. `metadata.georef` anchors the origin in WGS84;
the other sites sit tens of kilometres away and public roads between them follow real
highways (routes come from OSRM once and are cached in routes.json, so builds and the
running system never need the internet).

Every feature has `id`, `kind`, `name`; the rest of the properties depend on kind:
  site       site_type (plant|dc|cold_store), address, gate (x, y), approach (x, y)
  building   building_type (office|warehouse|production|garage), floors
  room       building_id, floor, room_type
  road       width_m, speed_limit_kmh, road_class (site|public), connects ([site, site] for public)
  checkpoint checkpoint_type (vehicle|pedestrian), zone_id
  geozone    zone_type (gate|docks|parking|restricted|speed)
  sensor     sensor_type, zone_id, building_id (null outdoors), floor

Run:  python deploy/seed/build_layout.py [--refresh-routes]
"""

import json
import sys
import urllib.request
from itertools import combinations
from pathlib import Path

from scada_common.geo import Georef
from shapely.geometry import LineString

OUT = Path(__file__).with_name("layout.geojson")
ROUTES = Path(__file__).with_name("routes.json")
OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}?overview=full&geometries=geojson"

GEOREF = Georef(origin_lat=55.4265, origin_lon=37.5690, rotation_deg=0.0)  # SW corner of the Podolsk plant
SITE_W, SITE_H = 800, 500  # the main plant


def rect(x0: float, y0: float, x1: float, y1: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def point(x: float, y: float) -> dict:
    return {"type": "Point", "coordinates": [x, y]}


def line(*pts: tuple[float, float]) -> dict:
    return {"type": "LineString", "coordinates": [list(p) for p in pts]}


def feature(geometry: dict, **props) -> dict:
    return {"type": "Feature", "id": props["id"], "geometry": geometry, "properties": props}


features: list[dict] = []
add = features.append


def sensor(sid: str, stype: str, name: str, x: float, y: float, zone_id: str,
           building_id: str | None = None, floor: int | None = None) -> None:
    add(feature(point(x, y), id=sid, kind="sensor", name=name, sensor_type=stype,
                zone_id=zone_id, building_id=building_id, floor=floor))


def site_road(rid: str, name: str, *pts: tuple[float, float], width_m: float = 8, speed_limit_kmh: float = 20) -> None:
    add(feature(line(*pts), id=rid, kind="road", name=name, width_m=width_m, speed_limit_kmh=speed_limit_kmh,
                road_class="site"))


# =============================================================================================
# Site 1: the plant in Podolsk (ids predate the region and stay short: b-wh1, z-gate, ...)
# =============================================================================================
add(feature(rect(0, 0, SITE_W, SITE_H), id="s-podolsk", kind="site", name="Завод «Подольск»", site_type="plant",
            address="Подольск, промзона", gate=[0, 250], approach=[-60, 250]))

# --- Buildings: id, name, type, floors, bbox -------------------------------------------------
BUILDINGS = [
    ("b-admin", "Административный корпус", "office", 3, (60, 380, 160, 450)),
    ("b-wh1", "Склад №1", "warehouse", 1, (220, 300, 360, 400)),
    ("b-wh2", "Склад №2 (холодный)", "warehouse", 1, (400, 300, 540, 400)),
    ("b-wh3", "Склад №3", "warehouse", 1, (580, 300, 720, 400)),
    ("b-prod1", "Цех №1", "production", 2, (220, 80, 400, 200)),
    ("b-prod2", "Цех №2", "production", 2, (460, 80, 640, 200)),
    ("b-garage", "Гараж и ремзона", "garage", 1, (680, 60, 770, 160)),
]
for bid, name, btype, floors, bbox in BUILDINGS:
    add(feature(rect(*bbox), id=bid, kind="building", name=name, building_type=btype, floors=floors))

# --- Rooms ---------------------------------------------------------------------------------
for i, (bid, (x0, y0, x1, y1)) in enumerate(
    [("b-wh1", (220, 300, 360, 400)), ("b-wh2", (400, 300, 540, 400)), ("b-wh3", (580, 300, 720, 400))], start=1
):
    ym = y0 + 30
    add(feature(rect(x0, y0, x1, ym), id=f"r-wh{i}-dock", kind="room", name=f"Склад №{i}: зона отгрузки",
                building_id=bid, floor=1, room_type="dock"))
    add(feature(rect(x0, ym, x1, y1), id=f"r-wh{i}-storage", kind="room", name=f"Склад №{i}: зона хранения",
                building_id=bid, floor=1, room_type="storage"))

for floor in (1, 2, 3):
    add(feature(rect(60, 380, 110, 450), id=f"r-admin-f{floor}-west", kind="room",
                name=f"Админ. корпус, {floor} эт.: западное крыло", building_id="b-admin", floor=floor,
                room_type="lobby" if floor == 1 else "office"))
    add(feature(rect(110, 380, 160, 450), id=f"r-admin-f{floor}-east", kind="room",
                name=f"Админ. корпус, {floor} эт.: восточное крыло", building_id="b-admin", floor=floor,
                room_type="server" if floor == 3 else "office"))

for i, (bid, (x0, y0, x1, y1)) in enumerate(
    [("b-prod1", (220, 80, 400, 200)), ("b-prod2", (460, 80, 640, 200))], start=1
):
    xm = (x0 + x1) / 2
    add(feature(rect(x0, y0, xm, y1), id=f"r-prod{i}-line", kind="room", name=f"Цех №{i}: производственная линия",
                building_id=bid, floor=1, room_type="production"))
    add(feature(rect(xm, y0, x1, y1), id=f"r-prod{i}-stock", kind="room", name=f"Цех №{i}: склад сырья",
                building_id=bid, floor=1, room_type="storage"))
    add(feature(rect(x0, y0, x1, y1), id=f"r-prod{i}-f2", kind="room", name=f"Цех №{i}, 2 эт.: офис мастеров",
                building_id=bid, floor=2, room_type="office"))

add(feature(rect(680, 60, 770, 160), id="r-garage-bay", kind="room", name="Ремзона: боксы",
            building_id="b-garage", floor=1, room_type="repair"))

# --- Roads ----------------------------------------------------------------------------------
site_road("road-main", "Главный проезд", (0, 250), (790, 250), width_m=12)
site_road("road-west", "Западный проезд", (190, 30), (190, 470))
site_road("road-east", "Восточный проезд", (790, 30), (790, 470))
site_road("road-south", "Южный проезд", (190, 30), (790, 30))
site_road("road-north", "Северный проезд", (190, 470), (790, 470))
for i, x in enumerate((290, 470, 650), start=1):
    site_road(f"road-dock-wh{i}", f"Подъезд к складу №{i}", (x, 250), (x, 290), speed_limit_kmh=10)
for i, x in enumerate((310, 550), start=1):
    site_road(f"road-prod{i}", f"Подъезд к цеху №{i}", (x, 250), (x, 200), speed_limit_kmh=10)
site_road("road-garage", "Подъезд к гаражу", (725, 250), (725, 160), speed_limit_kmh=10)
site_road("road-parking", "Въезд на стоянку", (190, 110), (100, 110), speed_limit_kmh=10)
site_road("road-podolsk-access", "Выезд с завода на дорогу общего пользования", (-60, 250), (0, 250),
          width_m=10, speed_limit_kmh=40)

# --- Geozones -------------------------------------------------------------------------------
add(feature(rect(0, 230, 40, 270), id="z-gate", kind="geozone", name="КПП-1: въездная зона", zone_type="gate"))
for i, (x0, x1) in enumerate([(220, 360), (400, 540), (580, 720)], start=1):
    add(feature(rect(x0, 270, x1, 300), id=f"z-docks-wh{i}", kind="geozone", name=f"Рампа склада №{i}", zone_type="docks"))
add(feature(rect(30, 40, 110, 180), id="z-parking", kind="geozone", name="Стоянка грузовиков", zone_type="parking"))
add(feature(rect(645, 40, 795, 175), id="z-garage-yard", kind="geozone", name="Двор ремзоны", zone_type="restricted"))
add(feature(rect(0, 0, SITE_W, SITE_H), id="z-site-speed", kind="geozone", name="Ограничение скорости: завод",
            zone_type="speed", speed_limit_kmh=20))

# --- Checkpoints ----------------------------------------------------------------------------
add(feature(point(20, 250), id="cp-1", kind="checkpoint", name="КПП-1 (транспорт)", checkpoint_type="vehicle", zone_id="z-gate"))
add(feature(point(50, 415), id="cp-2", kind="checkpoint", name="КПП-2 (проходная)", checkpoint_type="pedestrian", zone_id="b-admin"))

# --- Fixed sensors (mobile GNSS trackers live on vehicles, not on the plan) ------------------
sensor("cam-gate-in", "anpr_camera", "Камера КПП-1, въезд", 25, 258, "z-gate")
sensor("cam-gate-out", "anpr_camera", "Камера КПП-1, выезд", 25, 242, "z-gate")

sensor("acs-kpp2", "access_control", "Турникет КПП-2", 60, 415, "b-admin", "b-admin", 1)
for bid, x, y in [("b-wh1", 290, 300), ("b-wh2", 470, 300), ("b-wh3", 650, 300),
                  ("b-prod1", 310, 200), ("b-prod2", 550, 200), ("b-garage", 725, 160)]:
    bname = next(b[1] for b in BUILDINGS if b[0] == bid)
    sensor(f"acs-{bid[2:]}", "access_control", f"СКУД: вход, {bname}", x, y, bid, bid, 1)
sensor("acs-admin-server", "access_control", "СКУД: серверная", 135, 415, "r-admin-f3-east", "b-admin", 3)

for i, (x0, x1) in enumerate([(220, 360), (400, 540), (580, 720)], start=1):
    xc = (x0 + x1) / 2
    sensor(f"clim-wh{i}-storage", "climate", f"Климат, склад №{i}: хранение", xc, 365, f"r-wh{i}-storage", f"b-wh{i}", 1)
    sensor(f"clim-wh{i}-dock", "climate", f"Климат, склад №{i}: отгрузка", xc, 315, f"r-wh{i}-dock", f"b-wh{i}", 1)
    sensor(f"mot-wh{i}", "motion", f"Движение, склад №{i}", x0 + 15, 385, f"r-wh{i}-storage", f"b-wh{i}", 1)
sensor("clim-admin-server", "climate", "Климат, серверная", 145, 440, "r-admin-f3-east", "b-admin", 3)
for i, (x0, x1) in enumerate([(220, 400), (460, 640)], start=1):
    xm = (x0 + x1) / 2
    sensor(f"clim-prod{i}-stock", "climate", f"Климат, цех №{i}: склад сырья", (xm + x1) / 2, 140, f"r-prod{i}-stock", f"b-prod{i}", 1)
    sensor(f"mot-prod{i}", "motion", f"Движение, цех №{i}", (x0 + xm) / 2, 140, f"r-prod{i}-line", f"b-prod{i}", 1)
sensor("mot-garage", "motion", "Движение, ремзона", 725, 110, "r-garage-bay", "b-garage", 1)

for i, x in enumerate((290, 470, 650), start=1):
    sensor(f"cam-dock-wh{i}", "anpr_camera", f"Камера рампы склада №{i}", x + 12, 285, f"z-docks-wh{i}")
sensor("cam-garage", "anpr_camera", "Камера въезда в ремзону", 735, 175, "z-garage-yard")


# =============================================================================================
# Remote sites: a yard along a main road (y = road_y) with the gate on the west side,
# warehouses north of the road with docks in front, an office and a truck parking.
# =============================================================================================
def remote_site(sid: str, code: str, name: str, site_type: str, address: str, sw_lat: float, sw_lon: float,
                size: tuple[float, float], road_y: float, warehouses: list[tuple[str, str, float, float]],
                office: tuple[float, float, float, float], parking: tuple[float, float, float, float]) -> None:
    """warehouses: (suffix, name, x0, x1) — 90 m deep, starting 30 m north of the road."""
    ox, oy = GEOREF.to_local(sw_lat, sw_lon)
    ox, oy = round(ox), round(oy)
    w, h = size
    P = lambda x, y: (ox + x, oy + y)  # noqa: E731
    R = lambda x0, y0, x1, y1: rect(ox + x0, oy + y0, ox + x1, oy + y1)  # noqa: E731
    gate, approach = P(0, road_y), P(-60, road_y)
    add(feature(R(0, 0, w, h), id=f"s-{sid}", kind="site", name=name, site_type=site_type, address=address,
                gate=list(gate), approach=list(approach)))
    site_road(f"road-{code}-main", f"{name}: главный проезд", gate, P(w - 20, road_y), width_m=12)
    site_road(f"road-{code}-access", f"Выезд: {name}", approach, gate, width_m=10, speed_limit_kmh=40)
    add(feature(R(0, road_y - 20, 40, road_y + 20), id=f"z-{code}-gate", kind="geozone", name=f"{name}: КПП",
                zone_type="gate"))
    add(feature(R(0, 0, w, h), id=f"z-{code}-speed", kind="geozone", name=f"Ограничение скорости: {name}",
                zone_type="speed", speed_limit_kmh=20))
    sensor(f"cam-{code}-gate-in", "anpr_camera", f"{name}: камера КПП, въезд", *P(25, road_y + 8), f"z-{code}-gate")
    sensor(f"cam-{code}-gate-out", "anpr_camera", f"{name}: камера КПП, выезд", *P(25, road_y - 8), f"z-{code}-gate")

    y0, y1 = road_y + 30, road_y + 120
    for suffix, bname, x0, x1 in warehouses:
        bid = f"b-{code}-{suffix}"
        add(feature(R(x0, y0, x1, y1), id=bid, kind="building", name=bname, building_type="warehouse", floors=1))
        add(feature(R(x0, y0, x1, y0 + 25), id=f"r-{code}-{suffix}-dock", kind="room", name=f"{bname}: приёмка и отгрузка",
                    building_id=bid, floor=1, room_type="dock"))
        add(feature(R(x0, y0 + 25, x1, y1), id=f"r-{code}-{suffix}-storage", kind="room", name=f"{bname}: хранение",
                    building_id=bid, floor=1, room_type="storage"))
        add(feature(R(x0, road_y + 5, x1, y0), id=f"z-{code}-docks-{suffix}", kind="geozone", name=f"Рампа: {bname}",
                    zone_type="docks"))
        xs = [x0 + (x1 - x0) * k / 4 for k in (1, 2, 3)]
        for k, x in enumerate(xs, start=1):
            site_road(f"road-{code}-dock-{suffix}-{k}", f"Подъезд к рампе: {bname}", P(x, road_y), P(x, y0 - 10),
                      speed_limit_kmh=10)
        sensor(f"acs-{code}-{suffix}", "access_control", f"СКУД: вход, {bname}", *P(xs[0] - 10, y0), bid, bid, 1)
        sensor(f"cam-{code}-dock-{suffix}", "anpr_camera", f"Камера рампы: {bname}", *P(xs[1] + 12, y0 - 5),
               f"z-{code}-docks-{suffix}")
        sensor(f"mot-{code}-{suffix}", "motion", f"Движение: {bname}", *P(x0 + 12, y1 - 12),
               f"r-{code}-{suffix}-storage", bid, 1)

    bx0, by0, bx1, by1 = office
    bid = f"b-{code}-office"
    add(feature(R(*office), id=bid, kind="building", name=f"Офис: {name}", building_type="office", floors=2))
    for floor in (1, 2):
        add(feature(R(*office), id=f"r-{code}-office-f{floor}", kind="room", name=f"Офис {name}, {floor} эт.",
                    building_id=bid, floor=floor, room_type="lobby" if floor == 1 else "office"))
    sensor(f"acs-{code}-office", "access_control", f"Турникет: офис {name}", *P(bx0 + 8, by0), bid, bid, 1)
    sensor(f"clim-{code}-server", "climate", f"Климат, серверная: {name}", *P(bx1 - 8, by1 - 8),
           f"r-{code}-office-f2", bid, 2)

    px0, py0, px1, py1 = parking
    add(feature(R(*parking), id=f"z-{code}-parking", kind="geozone", name=f"Стоянка грузовиков: {name}",
                zone_type="parking"))
    xm = (px0 + px1) / 2
    site_road(f"road-{code}-parking", f"Въезд на стоянку: {name}", P(xm, road_y), P(xm, (py0 + py1) / 2),
              speed_limit_kmh=10)
    add(feature(point(*P(20, road_y)), id=f"cp-{code}", kind="checkpoint", name=f"КПП: {name}",
                checkpoint_type="vehicle", zone_id=f"z-{code}-gate"))


remote_site("domodedovo", "dmd", "РЦ «Домодедово»", "dc", "Домодедово, у трассы М-4 «Дон»",
            sw_lat=55.4525, sw_lon=37.8080, size=(420, 300), road_y=120,
            warehouses=[("xd", "Склад кросс-докинга", 120, 330)],
            office=(40, 220, 95, 260), parking=(340, 20, 410, 100))
# A building drawn without a floor plan: its sensors are registered (deploy/seed/sensors.json) but not placed.
# The lazy-user path: the map shows the sensors in the building card and recommends placing them.
_dmd_x, _dmd_y = (round(v) for v in GEOREF.to_local(55.4525, 37.8080))
add(feature(rect(_dmd_x + 345, _dmd_y + 165, _dmd_x + 405, _dmd_y + 235), id="b-dmd-hangar", kind="building",
            name="Ангар сезонного хранения", building_type="warehouse", floors=1))

remote_site("chekhov", "chk", "Холодильный склад «Чехов»", "cold_store", "Чехов, у трассы М-2 «Крым»",
            sw_lat=55.1700, sw_lon=37.4970, size=(460, 320), road_y=140,
            warehouses=[("cold1", "Холодильная камера №1", 90, 240), ("cold2", "Холодильная камера №2", 270, 420)],
            office=(20, 220, 70, 260), parking=(300, 20, 440, 110))

# Every room gets climate and motion coverage; rooms already equipped above are skipped.
_equipped = {(f["properties"]["sensor_type"], f["properties"]["zone_id"]) for f in features
             if f["properties"]["kind"] == "sensor"}
for f in [f for f in features if f["properties"]["kind"] == "room"]:
    p = f["properties"]
    (x0, y0), _, (x1, y1) = f["geometry"]["coordinates"][0][:3]
    short = p["id"].removeprefix("r-")
    for stype, prefix, label, fx in (("climate", "clim", "Климат", 0.75), ("motion", "mot", "Движение", 0.25)):
        if (stype, p["id"]) not in _equipped:
            sensor(f"{prefix}-{short}", stype, f"{label}: {p['name']}", x0 + (x1 - x0) * fx, (y0 + y1) / 2,
                   p["id"], p["building_id"], p["floor"])


# =============================================================================================
# Public roads between the sites: real highways from OSRM, cached
# =============================================================================================
def _osrm(a: tuple[float, float], b: tuple[float, float]) -> list[list[float]]:
    """[[lon, lat], ...] of the driving route between two local points."""
    ends = [GEOREF.to_wgs84(*p) for p in (a, b)]
    url = OSRM.format(a=f"{ends[0][1]:.6f},{ends[0][0]:.6f}", b=f"{ends[1][1]:.6f},{ends[1][0]:.6f}")
    req = urllib.request.Request(url, headers={"User-Agent": "scada-hackathon-layout-builder"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data["routes"][0]["geometry"]["coordinates"]


sites = {f["id"]: f["properties"] for f in features if f["properties"]["kind"] == "site"}
cache = json.loads(ROUTES.read_text(encoding="utf-8")) if ROUTES.exists() else {}
refresh = "--refresh-routes" in sys.argv
for a, b in combinations(sorted(sites), 2):
    key = f"{a}|{b}"
    if refresh or key not in cache:
        cache[key] = _osrm(tuple(sites[a]["approach"]), tuple(sites[b]["approach"]))
    local = [GEOREF.to_local(lat, lon) for lon, lat in cache[key]]
    # the router snaps to the nearest road: join its ends to our access roads with straight segments
    pts = [tuple(sites[a]["approach"]), *local, tuple(sites[b]["approach"])]
    simple = LineString(pts).simplify(2.0)
    coords = [(round(x, 1), round(y, 1)) for x, y in simple.coords]
    add(feature(line(*coords), id=f"route-{a[2:]}-{b[2:]}", kind="road",
                name=f"Трасса: {sites[a]['name']} — {sites[b]['name']}", width_m=10, speed_limit_kmh=70,
                road_class="public", connects=[a, b]))

def _points(geometry: dict) -> list[list[float]]:
    t, c = geometry["type"], geometry["coordinates"]
    return [c] if t == "Point" else c if t == "LineString" else [p for ring in c for p in ring]


all_pts = [p for f in features for p in _points(f["geometry"])]
MARGIN = 500
extent = [min(p[0] for p in all_pts) - MARGIN, min(p[1] for p in all_pts) - MARGIN,
          max(p[0] for p in all_pts) + MARGIN, max(p[1] for p in all_pts) + MARGIN]
extent = [round(v) for v in extent]

layout = {
    "type": "FeatureCollection",
    "metadata": {
        "units": "m",
        "extent": extent,
        "georef": {"origin_lat": GEOREF.origin_lat, "origin_lon": GEOREF.origin_lon, "rotation_deg": GEOREF.rotation_deg},
    },
    "features": features,
}

if __name__ == "__main__":
    ROUTES.write_text(json.dumps(cache, separators=(",", ":")) + "\n", encoding="utf-8")
    OUT.write_text(json.dumps(layout, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kinds: dict[str, int] = {}
    for f in features:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    routes = {f["id"]: round(LineString(f["geometry"]["coordinates"]).length / 1000, 1)
              for f in features if f["properties"].get("road_class") == "public"}
    print(f"wrote {OUT.name}: {kinds}; extent {extent}; routes km {routes}")
