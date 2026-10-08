"""Generate deploy/seed/layout.geojson — the demo enterprise plan.

Coordinates are local metres: origin is the south-west corner of the site,
x grows east, y grows north. `metadata.georef` gives the WGS84 anchor so the
frontend can switch to a real basemap via an affine transform.

Every feature has `id`, `kind`, `name`; the rest of the properties depend on kind:
  site       —
  building   building_type (office|warehouse|production|garage), floors
  room       building_id, floor, room_type
  road       width_m, speed_limit_kmh
  checkpoint checkpoint_type (vehicle|pedestrian), zone_id
  geozone    zone_type (gate|docks|parking|restricted|speed)
  sensor     sensor_type, zone_id, building_id (null outdoors), floor

Run:  python deploy/seed/build_layout.py
"""

import json
from pathlib import Path

OUT = Path(__file__).with_name("layout.geojson")

SITE_W, SITE_H = 800, 500


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

add(feature(rect(0, 0, SITE_W, SITE_H), id="site", kind="site", name="Производственная площадка"))

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
add(feature(line((0, 250), (790, 250)), id="road-main", kind="road", name="Главный проезд", width_m=12, speed_limit_kmh=20))
add(feature(line((190, 30), (190, 470)), id="road-west", kind="road", name="Западный проезд", width_m=8, speed_limit_kmh=20))
add(feature(line((790, 30), (790, 470)), id="road-east", kind="road", name="Восточный проезд", width_m=8, speed_limit_kmh=20))
add(feature(line((190, 30), (790, 30)), id="road-south", kind="road", name="Южный проезд", width_m=8, speed_limit_kmh=20))
add(feature(line((190, 470), (790, 470)), id="road-north", kind="road", name="Северный проезд", width_m=8, speed_limit_kmh=20))
for i, x in enumerate((290, 470, 650), start=1):
    add(feature(line((x, 250), (x, 300)), id=f"road-dock-wh{i}", kind="road", name=f"Подъезд к складу №{i}",
                width_m=8, speed_limit_kmh=10))
for i, x in enumerate((310, 550), start=1):
    add(feature(line((x, 250), (x, 200)), id=f"road-prod{i}", kind="road", name=f"Подъезд к цеху №{i}",
                width_m=8, speed_limit_kmh=10))
add(feature(line((725, 250), (725, 160)), id="road-garage", kind="road", name="Подъезд к гаражу", width_m=8, speed_limit_kmh=10))
add(feature(line((190, 110), (110, 110)), id="road-parking", kind="road", name="Въезд на стоянку", width_m=8, speed_limit_kmh=10))

# --- Geozones -------------------------------------------------------------------------------
add(feature(rect(0, 230, 40, 270), id="z-gate", kind="geozone", name="КПП-1: въездная зона", zone_type="gate"))
for i, (x0, x1) in enumerate([(220, 360), (400, 540), (580, 720)], start=1):
    add(feature(rect(x0, 270, x1, 300), id=f"z-docks-wh{i}", kind="geozone", name=f"Рампа склада №{i}", zone_type="docks"))
add(feature(rect(30, 40, 110, 180), id="z-parking", kind="geozone", name="Стоянка грузовиков", zone_type="parking"))
add(feature(rect(645, 40, 795, 175), id="z-garage-yard", kind="geozone", name="Двор ремзоны", zone_type="restricted"))
add(feature(rect(0, 0, SITE_W, SITE_H), id="z-site-speed", kind="geozone", name="Ограничение скорости по территории",
            zone_type="speed", speed_limit_kmh=20))

# --- Checkpoints ----------------------------------------------------------------------------
add(feature(point(20, 250), id="cp-1", kind="checkpoint", name="КПП-1 (транспорт)", checkpoint_type="vehicle", zone_id="z-gate"))
add(feature(point(50, 415), id="cp-2", kind="checkpoint", name="КПП-2 (проходная)", checkpoint_type="pedestrian", zone_id="b-admin"))

# --- Fixed sensors (mobile GNSS trackers live on vehicles, not on the plan) ------------------
def sensor(sid: str, stype: str, name: str, x: float, y: float, zone_id: str,
           building_id: str | None = None, floor: int | None = None) -> None:
    add(feature(point(x, y), id=sid, kind="sensor", name=name, sensor_type=stype,
                zone_id=zone_id, building_id=building_id, floor=floor))


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

layout = {
    "type": "FeatureCollection",
    "metadata": {
        "units": "m",
        "extent": [0, 0, SITE_W, SITE_H],
        "georef": {"origin_lat": 55.700000, "origin_lon": 37.400000, "rotation_deg": 0.0},
    },
    "features": features,
}

if __name__ == "__main__":
    OUT.write_text(json.dumps(layout, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kinds: dict[str, int] = {}
    for f in features:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    print(f"wrote {OUT.name}: {kinds}")
