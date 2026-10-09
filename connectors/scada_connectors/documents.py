"""Transport documents (путевой лист / ЭТрН) from a TMS: not sensor events, they go straight to the registry.

A waybill is a business document about a trip: who drives which truck from which site
to which, with what cargo. The TMS re-sends it on every status change; the latest copy
wins (upsert by waybill number).
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from scada_common.enums import TripStatus
from scada_db import models as m
from scada_db.postgres import engine
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


class Waybill(BaseModel):
    waybill_no: str = Field(..., min_length=1, max_length=64, examples=["ПЛ-2026-000123"])
    plate: str = Field(..., examples=["А123ВС77"], description="Гос. номер машины из реестра")
    origin_site_id: str = Field(..., examples=["s-podolsk"])
    destination_site_id: str = Field(..., examples=["s-chekhov"])
    cargo: str = Field(..., min_length=1, max_length=256, examples=["Металлоконструкции"])
    weight_t: float = Field(..., gt=0, le=60, examples=[12.4])
    pallets: int | None = Field(None, ge=0, le=100)
    temperature_mode: str | None = Field(None, max_length=64, examples=["-18…-22 °C"])
    driver_name: str | None = Field(None, max_length=128)
    status: TripStatus
    planned_departure: datetime | None = None
    departed_at: datetime | None = None
    eta: datetime | None = None
    arrived_at: datetime | None = None


EXAMPLE = {"waybill_no": "ПЛ-2026-000123", "plate": "А123ВС77", "origin_site_id": "s-podolsk",
           "destination_site_id": "s-chekhov", "cargo": "Металлоконструкции", "weight_t": 12.4, "pallets": 18,
           "driver_name": "Иванов Сергей Петрович", "status": "en_route",
           "departed_at": "2026-10-09T09:15:00+03:00", "eta": "2026-10-09T10:05:00+03:00"}


class DocumentError(ValueError):
    pass


def store_waybill(w: Waybill) -> str:
    """Validate references against the registry and upsert the trip. Returns the trip id."""
    with Session(engine()) as s, s.begin():
        vehicle_id = s.scalar(select(m.Vehicle.id).where(m.Vehicle.plate == w.plate))
        if vehicle_id is None:
            raise DocumentError(f"unknown plate {w.plate}")
        layout = s.scalars(select(m.Layout.geojson).order_by(m.Layout.version.desc()).limit(1)).first() or {}
        sites = {f["id"] for f in layout.get("features", []) if f["properties"]["kind"] == "site"}
        for site in (w.origin_site_id, w.destination_site_id):
            if site not in sites:
                raise DocumentError(f"unknown site {site}")
        values = w.model_dump(exclude={"waybill_no", "plate"}) | {"vehicle_id": vehicle_id,
                                                                  "updated_at": datetime.now(UTC)}
        stmt = insert(m.Trip).values(id=w.waybill_no, **values)
        s.execute(stmt.on_conflict_do_update(index_elements=[m.Trip.id], set_=values))
        return w.waybill_no
