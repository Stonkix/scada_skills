from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ForecastPoint(BaseModel):
    ts: datetime
    value: float


class Prediction(BaseModel):
    sensor_id: str
    name: str
    metric: str
    unit: str | None
    generated_at: datetime
    samples: int = Field(..., description="Минутных точек за последние 24 ч")
    current: float = Field(..., description="Сглаженное (EWMA) значение за последние 15 мин")
    baseline_mean: float | None = Field(None, description="Среднее за сутки без последних 15 мин")
    baseline_std: float | None
    zscore: float | None = Field(None, description="Отклонение текущего значения от суточной нормы, в σ")
    anomaly: bool = Field(..., description="|z| ≥ 3")
    trend_per_hour: float | None = Field(None, description="Наклон линейной регрессии (окно 30 мин, если тренд выражен, иначе 2 ч)")
    trend_r2: float | None
    direction: Literal["rising", "falling", "stable", "unknown"]
    eta_warning_h: float | None = Field(None, description="Часов до выхода за норму при текущем тренде (0 — уже вне нормы)")
    eta_critical_h: float | None = Field(None, description="Часов до критической границы")
    bound_warning: float | None
    bound_critical: float | None
    risk: Literal["ok", "watch", "warning", "critical"] = Field(
        ..., description="critical: до крит. границы ≤ 2 ч; warning: до выхода из нормы ≤ 2 ч или аномалия; watch: ≤ 12 ч или |z| ≥ 2")
    summary: str
    forecast: list[ForecastPoint] = Field(..., description="Проекция тренда на 3 ч вперёд (шаг 15 мин)")


class Maintenance(BaseModel):
    vehicle_id: str
    plate: str
    odometer_km: float
    service_interval_km: float
    next_service_km: float
    remaining_km: float
    km_per_day: float | None = Field(None, description="Средний пробег в сутки по последним 24 ч")
    eta_days: float | None
    summary: str


class PredictResponse(BaseModel):
    prediction: Prediction | None = Field(None, description="null — мало данных для прогноза")
    maintenance: Maintenance | None = Field(None, description="Только для ГЛОНАСС-трекеров")
