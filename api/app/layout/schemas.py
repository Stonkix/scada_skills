from typing import Any, Literal

from pydantic import BaseModel, Field

from app.registry.schemas import Sensor, SensorTypeInfo, Vehicle

Coord = tuple[float, float]


class PolygonGeometry(BaseModel):
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Coord]] = Field(..., description="Кольца полигона в метрах плана; первое — внешнее")


class LineGeometry(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[Coord]


class Georef(BaseModel):
    """Привязка локальных метров к WGS84: точка (0, 0) плана = (origin_lat, origin_lon)."""

    origin_lat: float
    origin_lon: float
    rotation_deg: float = 0.0


class Building(BaseModel):
    id: str = Field(..., examples=["b-wh2"])
    name: str = Field(..., examples=["Склад №2 (холодный)"])
    building_type: Literal["office", "warehouse", "production", "garage"]
    floors: int = Field(..., ge=1)
    geometry: PolygonGeometry


class Room(BaseModel):
    id: str = Field(..., examples=["r-wh2-storage"])
    name: str
    building_id: str
    floor: int
    room_type: str = Field(..., examples=["storage"])
    geometry: PolygonGeometry


class Road(BaseModel):
    id: str = Field(..., examples=["road-main"])
    name: str
    width_m: float
    speed_limit_kmh: float
    geometry: LineGeometry


class Geozone(BaseModel):
    id: str = Field(..., examples=["z-gate"])
    name: str
    zone_type: Literal["gate", "docks", "parking", "restricted", "speed"]
    speed_limit_kmh: float | None = None
    geometry: PolygonGeometry


class Checkpoint(BaseModel):
    id: str = Field(..., examples=["cp-1"])
    name: str
    checkpoint_type: Literal["vehicle", "pedestrian"]
    zone_id: str
    x: float
    y: float


class ObjectsResponse(BaseModel):
    """Вся статика одним ответом: план, реестр датчиков и машин. Фронт кэширует по layout_version."""

    layout_version: int
    units: Literal["m"] = "m"
    extent: tuple[float, float, float, float] = Field(..., description="[x_min, y_min, x_max, y_max]")
    georef: Georef
    site: PolygonGeometry
    buildings: list[Building]
    rooms: list[Room]
    roads: list[Road]
    geozones: list[Geozone]
    checkpoints: list[Checkpoint]
    sensor_types: list[SensorTypeInfo]
    sensors: list[Sensor]
    vehicles: list[Vehicle]


class LayoutDocument(BaseModel):
    version: int
    geojson: dict[str, Any] = Field(..., description="FeatureCollection в формате deploy/seed/layout.geojson")


class LayoutUpdate(BaseModel):
    base_version: int = Field(..., description="Версия, от которой редактировали; при расхождении — 409")
    geojson: dict[str, Any]
