from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import to_shape
from scada_db import models as m
from sqlalchemy import select

from app.auth.security import Principal
from app.deps import DB, require
from app.layout import sync
from app.layout.schemas import (
    Building,
    Checkpoint,
    Georef,
    Geozone,
    LayoutDocument,
    LayoutUpdate,
    LineGeometry,
    ObjectsResponse,
    PolygonGeometry,
    Road,
    Room,
)
from app.registry import repo

router = APIRouter(tags=["layout"])
CanView = Annotated[Principal, Depends(require("map:view"))]


def _poly(geom) -> PolygonGeometry:
    s = to_shape(geom)
    return PolygonGeometry(coordinates=[list(s.exterior.coords), *[list(r.coords) for r in s.interiors]])


def _latest(db: DB) -> m.Layout:
    layout = db.scalars(select(m.Layout).order_by(m.Layout.version.desc()).limit(1)).first()
    if layout is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No plan loaded yet (run the seed)")
    return layout


@router.get("/objects", response_model=ObjectsResponse)
def get_objects(db: DB, _: CanView) -> ObjectsResponse:
    """Вся статика одним ответом. Кэшируйте по `layout_version`: она растёт при каждом сохранении плана."""
    layout = _latest(db)
    meta = layout.geojson["metadata"]
    site = next((f for f in layout.geojson["features"] if f["properties"]["kind"] == "site"), None)
    x0, y0, x1, y1 = meta["extent"]
    zones = db.scalars(select(m.Zone).order_by(m.Zone.id)).all()
    return ObjectsResponse(
        layout_version=layout.version,
        extent=meta["extent"],
        georef=Georef(**meta["georef"]),
        site=PolygonGeometry(coordinates=site["geometry"]["coordinates"]) if site else
        PolygonGeometry(coordinates=[[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]),
        buildings=[Building(id=b.id, name=b.name, building_type=b.building_type, floors=b.floors, geometry=_poly(b.geom))
                   for b in db.scalars(select(m.Building).order_by(m.Building.id))],
        rooms=[Room(id=z.id, name=z.name, building_id=z.building_id, floor=z.floor, room_type=z.zone_type,
                    geometry=_poly(z.geom)) for z in zones if z.kind == "room"],
        roads=[Road(id=r.id, name=r.name, width_m=r.width_m, speed_limit_kmh=r.speed_limit_kmh,
                    geometry=LineGeometry(coordinates=list(to_shape(r.geom).coords)))
               for r in db.scalars(select(m.Road).order_by(m.Road.id))],
        geozones=[Geozone(id=z.id, name=z.name, zone_type=z.zone_type, speed_limit_kmh=z.speed_limit_kmh,
                          geometry=_poly(z.geom)) for z in zones if z.kind == "geozone"],
        checkpoints=[Checkpoint(id=c.id, name=c.name, checkpoint_type=c.checkpoint_type, zone_id=c.zone_id,
                                x=to_shape(c.geom).x, y=to_shape(c.geom).y)
                     for c in db.scalars(select(m.Checkpoint).order_by(m.Checkpoint.id))],
        sensor_types=repo.sensor_types(),
        sensors=repo.sensors(db),
        vehicles=repo.vehicles(db),
    )


@router.get("/layout", response_model=LayoutDocument)
def get_layout(db: DB, _: CanView) -> LayoutDocument:
    layout = _latest(db)
    return LayoutDocument(version=layout.version, geojson=layout.geojson)


@router.post("/layout", response_model=LayoutDocument,
             responses={409: {"description": "The plan was saved by someone else since `base_version`"},
                        422: {"description": "Invalid plan; `detail` lists every problem"}})
def save_layout(body: LayoutUpdate, db: DB, user: Annotated[Principal, Depends(require("layout:edit"))]) -> LayoutDocument:
    """Сохраняет новую версию плана и применяет её к реестру: здания, помещения, геозоны, дороги, КПП и
    положение неподвижных датчиков. Новые датчики на плане создаются с типовыми порогами; убранные с плана
    отключаются. Worker, коннекторы и симулятор подхватывают изменения в течение минуты."""
    sync.lock(db)
    current = _latest(db)
    if body.base_version != current.version:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Plan is at version {current.version}, you edited {body.base_version}: reload and merge")
    try:
        sync.apply(db, body.geojson, previous=current.geojson)
    except sync.LayoutError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, e.errors) from e
    row = m.Layout(version=current.version + 1, geojson=body.geojson, created_by=user.id)
    db.add(row)
    db.commit()
    return LayoutDocument(version=row.version, geojson=row.geojson)
