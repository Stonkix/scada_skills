from typing import Annotated

from fastapi import APIRouter, Depends, Query
from scada_common.enums import TripStatus
from scada_db import models as m
from sqlalchemy import select

from app.auth.security import Principal
from app.deps import DB, require
from app.registry.router import mask_name
from app.trips.schemas import Trip

router = APIRouter(tags=["trips"])
CanView = Annotated[Principal, Depends(require("map:view"))]


def _to_model(t: m.Trip, plate: str, routes: dict[frozenset[str], str], pii: bool) -> Trip:
    return Trip(id=t.id, vehicle_id=t.vehicle_id, plate=plate, origin_site_id=t.origin_site_id,
                destination_site_id=t.destination_site_id,
                route_id=routes.get(frozenset((t.origin_site_id, t.destination_site_id))),
                cargo=t.cargo, weight_t=t.weight_t, pallets=t.pallets, temperature_mode=t.temperature_mode,
                driver_name=t.driver_name if pii else mask_name(t.driver_name), status=t.status,
                planned_departure=t.planned_departure, departed_at=t.departed_at, eta=t.eta,
                arrived_at=t.arrived_at, updated_at=t.updated_at)


def _routes(db: DB) -> dict[frozenset[str], str]:
    return {frozenset(r.connects): r.id for r in db.scalars(select(m.Road).where(m.Road.road_class == "public"))
            if r.connects and len(r.connects) == 2}


@router.get("/trips", response_model=list[Trip])
def list_trips(
    db: DB,
    user: CanView,
    vehicle_id: str | None = None,
    active: Annotated[bool | None, Query(description="true — незавершённые рейсы")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[Trip]:
    """Рейсы, новые сверху. Активный рейс у машины — не больше одного."""
    query = select(m.Trip, m.Vehicle.plate).join(m.Vehicle, m.Vehicle.id == m.Trip.vehicle_id)
    if vehicle_id:
        query = query.where(m.Trip.vehicle_id == vehicle_id)
    if active is not None:
        query = query.where((m.Trip.status != TripStatus.DONE) if active else (m.Trip.status == TripStatus.DONE))
    rows = db.execute(query.order_by(m.Trip.updated_at.desc()).limit(limit)).all()
    routes, pii = _routes(db), user.can("people:view_pii")
    return [_to_model(t, plate, routes, pii) for t, plate in rows]


@router.get("/vehicles/{vehicle_id}/trip", response_model=Trip | None)
def current_trip(vehicle_id: str, db: DB, user: CanView) -> Trip | None:
    """Текущий рейс машины (последний незавершённый), иначе последний завершённый; null — рейсов не было."""
    row = db.execute(
        select(m.Trip, m.Vehicle.plate).join(m.Vehicle, m.Vehicle.id == m.Trip.vehicle_id)
        .where(m.Trip.vehicle_id == vehicle_id)
        .order_by((m.Trip.status == TripStatus.DONE).asc(), m.Trip.updated_at.desc()).limit(1)
    ).first()
    return _to_model(row[0], row[1], _routes(db), user.can("people:view_pii")) if row else None
