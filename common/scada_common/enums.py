"""Enumerations shared by the API contract and the storage schema."""

from enum import StrEnum


class Role(StrEnum):
    DISPATCHER = "dispatcher"
    SECURITY = "security"
    ADMIN = "admin"


class VehicleKind(StrEnum):
    TRUCK = "truck"
    LOADER = "loader"
    CAR = "car"


class TripStatus(StrEnum):
    PLANNED = "planned"  # путевой лист выписан
    LOADING = "loading"  # погрузка на площадке отправления
    EN_ROUTE = "en_route"  # в пути
    UNLOADING = "unloading"  # разгрузка на площадке назначения
    DONE = "done"


class AlertKind(StrEnum):
    THRESHOLD = "threshold"  # выход метрики за пороги
    GEOZONE = "geozone"  # въезд/выезд/нахождение в геозоне
    SCHEDULE = "schedule"  # активность в нерабочее время
    WHITELIST = "whitelist"  # пропуск или номер вне базы
    SPEED = "speed"  # превышение скорости
    OFFLINE = "offline"  # датчик перестал слать данные
    BREAKDOWN = "breakdown"  # остановка техники вне стоянки с заглушенным двигателем


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACK = "ack"
    RESOLVED = "resolved"


class WhitelistKind(StrEnum):
    CARD = "card"  # пропуск СКУД
    PLATE = "plate"  # гос. номер для камер КПП
