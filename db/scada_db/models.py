"""Postgres schema: registry, plan, rules, alerts, auth.

Geometry uses SRID 0: local plan metres, the same coordinates as layout.geojson
and the event contract. `zone_id` columns may hold a building, room or geozone
id (occupancy is counted per building), so they carry no foreign key; the seed
and the API validate them against the plan.
"""

from datetime import datetime
from enum import StrEnum

from geoalchemy2 import Geometry
from scada_common import SensorType
from scada_common.enums import AlertKind, AlertStatus, Role, Severity, TripStatus, VehicleKind, WhitelistKind
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SRID = 0  # local plan metres


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def str_enum(enum: type[StrEnum]) -> Enum:
    """Store enum values (not names) as varchar + CHECK; easier to migrate than native PG enums."""
    return Enum(enum, native_enum=False, length=32, name=enum.__name__.lower(),
                values_callable=lambda e: [m.value for m in e], validate_strings=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --- auth -----------------------------------------------------------------------------------------


class RoleRow(Base):
    __tablename__ = "roles"

    id: Mapped[Role] = mapped_column(str_enum(Role), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    permissions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), server_default="{}")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(256), comment="argon2id")
    role_id: Mapped[Role] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()


class ApiKey(Base):
    """Keys for connectors / sensor vendors. Only a SHA-256 of the high-entropy key is stored."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    key_prefix: Mapped[str] = mapped_column(String(16), unique=True, comment="First chars of the key, to find it")
    key_hash: Mapped[str] = mapped_column(String(64))
    sensor_types: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)), comment="null = any type")
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, server_default="6000")
    created_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- plan ------------------------------------------------------------------------------------------


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    building_type: Mapped[str] = mapped_column(String(32))
    floors: Mapped[int] = mapped_column(Integer)
    geom = mapped_column(Geometry("POLYGON", srid=SRID), nullable=False)


class Zone(Base):
    """Rooms inside buildings and outdoor geozones (gate, docks, parking, restricted, speed)."""

    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), comment="room | geozone")
    zone_type: Mapped[str] = mapped_column(String(32))
    building_id: Mapped[str | None] = mapped_column(ForeignKey("buildings.id", ondelete="CASCADE"), index=True)
    floor: Mapped[int | None] = mapped_column(Integer)
    speed_limit_kmh: Mapped[float | None] = mapped_column(Float)
    geom = mapped_column(Geometry("POLYGON", srid=SRID), nullable=False)


class Road(Base):
    __tablename__ = "roads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    width_m: Mapped[float] = mapped_column(Float)
    speed_limit_kmh: Mapped[float] = mapped_column(Float)
    road_class: Mapped[str] = mapped_column(String(16), server_default="site", comment="site | public")
    connects: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)), comment="Public roads: the two sites")
    geom = mapped_column(Geometry("LINESTRING", srid=SRID), nullable=False)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    checkpoint_type: Mapped[str] = mapped_column(String(16), comment="vehicle | pedestrian")
    zone_id: Mapped[str] = mapped_column(String(64))
    geom = mapped_column(Geometry("POINT", srid=SRID), nullable=False)


class Layout(Base):
    """Every saved version of the plan (GeoJSON). The current one has the highest version."""

    __tablename__ = "layouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    geojson: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()


# --- registry --------------------------------------------------------------------------------------


class SensorTypeRow(Base):
    __tablename__ = "sensor_types"

    id: Mapped[SensorType] = mapped_column(str_enum(SensorType), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    is_mobile: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[list[dict]] = mapped_column(JSONB, comment="[{key, name, unit, kind}]")
    payload_schema: Mapped[dict] = mapped_column(JSONB, comment="JSON Schema of the event payload")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plate: Mapped[str] = mapped_column(String(16), unique=True)
    kind: Mapped[VehicleKind] = mapped_column(str_enum(VehicleKind))
    model: Mapped[str] = mapped_column(String(128))
    carrier: Mapped[str | None] = mapped_column(String(128))
    home_site_id: Mapped[str | None] = mapped_column(String(64), comment="Site from the plan where it is based")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Sensor(Base):
    __tablename__ = "sensors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[SensorType] = mapped_column(ForeignKey("sensor_types.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    is_mobile: Mapped[bool] = mapped_column(Boolean, comment="Position comes with events (CH/Redis), geom is null")
    building_id: Mapped[str | None] = mapped_column(ForeignKey("buildings.id", ondelete="SET NULL"), index=True)
    zone_id: Mapped[str | None] = mapped_column(String(64), index=True)
    floor: Mapped[int | None] = mapped_column(Integer)
    geom = mapped_column(Geometry("POINT", srid=SRID))
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Threshold(Base):
    """Versioned limits of one metric. The current version has valid_to IS NULL."""

    __tablename__ = "thresholds"
    __table_args__ = (
        UniqueConstraint("sensor_id", "metric", "version"),
        Index("uq_thresholds_current", "sensor_id", "metric", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_id: Mapped[str] = mapped_column(ForeignKey("sensors.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(64))
    nominal: Mapped[float | None] = mapped_column(Float)
    min: Mapped[float | None] = mapped_column(Float)
    max: Mapped[float | None] = mapped_column(Float)
    critical_min: Mapped[float | None] = mapped_column(Float)
    critical_max: Mapped[float | None] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- rules -----------------------------------------------------------------------------------------


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/Moscow")
    intervals: Mapped[list[dict]] = mapped_column(
        JSONB, comment='[{"days": [1..7 ISO weekday], "start": "HH:MM", "end": "HH:MM"}]; end < start crosses midnight')


class WhitelistEntry(Base):
    """Allowed passes and plates. History is kept by closing valid_to instead of deleting."""

    __tablename__ = "whitelist"
    __table_args__ = (Index("ix_whitelist_kind_value", "kind", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[WhitelistKind] = mapped_column(str_enum(WhitelistKind))
    value: Mapped[str] = mapped_column(String(64), comment="card_id or plate")
    holder_name: Mapped[str | None] = mapped_column(String(128), comment="ПДн: маскируется для ролей без доступа")
    holder_org: Mapped[str | None] = mapped_column(String(128))
    allowed_building_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)), comment="null = everywhere")
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertRule(Base):
    """Versioned rule. Editing creates a new version; alerts keep the version they fired under."""

    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("uq_alert_rules_current", "id", unique=True, postgresql_where=text("is_current")),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[AlertKind] = mapped_column(str_enum(AlertKind))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity), comment="Default; threshold rules escalate per value")
    params: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"))
    escalate_after_s: Mapped[int | None] = mapped_column(Integer, comment="Raise escalation_level if not acked")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        ForeignKeyConstraint(["rule_id", "rule_version"], ["alert_rules.id", "alert_rules.version"],
                             name="fk_alerts_rule"),
        Index("ix_alerts_status_opened", "status", text("opened_at DESC")),
        # one live alert per (rule, object): the DB backs up the worker's Redis dedup
        Index("uq_alerts_active_dedup", "dedup_key", unique=True, postgresql_where=text("status <> 'resolved'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[AlertKind] = mapped_column(str_enum(AlertKind))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity))
    status: Mapped[AlertStatus] = mapped_column(str_enum(AlertStatus), server_default=AlertStatus.OPEN.value)
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text)
    sensor_id: Mapped[str | None] = mapped_column(ForeignKey("sensors.id", ondelete="SET NULL"), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id", ondelete="SET NULL"))
    building_id: Mapped[str | None] = mapped_column(String(64))
    zone_id: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    dedup_key: Mapped[str] = mapped_column(String(160), comment="rule_id:object_id, same as Redis alert:open key")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    escalation_level: Mapped[int] = mapped_column(Integer, server_default="0")


# --- logistics ------------------------------------------------------------------------------------


class Trip(Base):
    """A trip by waybill (путевой лист / ЭТрН), pushed by the transport system through connectors."""

    __tablename__ = "trips"
    __table_args__ = (Index("ix_trips_vehicle_updated", "vehicle_id", text("updated_at DESC")),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Waybill number")
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    origin_site_id: Mapped[str] = mapped_column(String(64))
    destination_site_id: Mapped[str] = mapped_column(String(64))
    cargo: Mapped[str] = mapped_column(String(256))
    weight_t: Mapped[float] = mapped_column(Float)
    pallets: Mapped[int | None] = mapped_column(Integer)
    temperature_mode: Mapped[str | None] = mapped_column(String(64), comment="Reefer cargo, e.g. «-18…-22 °C»")
    driver_name: Mapped[str | None] = mapped_column(String(128), comment="ПДн: маскируется для ролей без доступа")
    status: Mapped[TripStatus] = mapped_column(str_enum(TripStatus))
    planned_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
