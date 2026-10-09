"""Pure forecasting functions (no stack needed)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.predict.model import Bounds, eta_hours, forecast, fmt_hours, linear_fit, summary

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
COLD = Bounds(min=2, max=6, critical_min=0, critical_max=8)


def series(fn, minutes: int = 24 * 60) -> list[tuple[datetime, float]]:
    """One point per minute ending at NOW; fn(minutes_before_now) -> value."""
    return [(NOW - timedelta(minutes=m), fn(m)) for m in range(minutes, -1, -1)]


def test_linear_fit_recovers_slope() -> None:
    fit = linear_fit([(NOW + timedelta(hours=h), 3 + 2 * h) for h in range(5)])
    assert fit.slope_per_h == pytest.approx(2) and fit.intercept == pytest.approx(3) and fit.r2 == pytest.approx(1)


def test_eta_hours() -> None:
    assert eta_hours(4, 1, 8, upper=True) == pytest.approx(4)
    assert eta_hours(4, -1, 8, upper=True) is None  # moving away
    assert eta_hours(9, 0, 8, upper=True) == 0  # already beyond
    assert eta_hours(4, 0.01, 8, upper=True) is None  # beyond the 72 h horizon


def test_stable_series_is_ok() -> None:
    f = forecast(series(lambda m: 4 + 0.05 * ((m * 7919) % 11 - 5) / 5), COLD, NOW)
    assert f.direction == "stable" and f.risk == "ok" and not f.anomaly
    assert f.eta_warning_h is None and f.projection == []


def test_heating_cold_store_predicts_crossings_and_flags_anomaly() -> None:
    # flat 4 °C all day, then +3 °C/h for the last 40 minutes (the overheat scenario)
    f = forecast(series(lambda m: 4 + (3 * (40 - m) / 60 if m < 40 else 0)), COLD, NOW)
    assert f.direction == "rising" and f.trend_per_hour > 0
    assert f.current == pytest.approx(5.8, abs=0.3)
    assert f.eta_warning_h is not None and f.eta_warning_h < 1
    assert f.eta_critical_h is not None and f.eta_critical_h < 2
    assert f.anomaly and f.risk == "critical"
    assert len(f.projection) == 13 and f.projection[-1][1] > f.current
    assert "критическая граница 8" in summary(f, "Температура", "°C")


def test_normal_oscillation_is_not_a_ramp() -> None:
    import math

    # ±0.8 °C sine with a 94-minute period, sampled on its rising half: locally linear, but normal
    srv = Bounds(min=18, max=24, critical_min=15, critical_max=28)
    f = forecast(series(lambda m: 21 + 0.8 * math.sin(2 * math.pi * (-m) / 94 + 1)), srv, NOW)
    assert f.risk == "ok" and not f.anomaly
    assert f.eta_critical_h is None


def test_value_already_outside_is_reported_as_now() -> None:
    f = forecast(series(lambda m: 9.0), COLD, NOW)
    assert f.eta_critical_h == 0 and f.bound_critical == 8 and f.risk == "critical"


def test_falling_fuel_counts_down_to_the_minimum() -> None:
    fuel = Bounds(min=15, critical_min=5)
    f = forecast(series(lambda m: 20 + m / 60, minutes=180), fuel, NOW)  # -1 %/h, now at 20 %
    assert f.direction == "falling"
    assert f.eta_warning_h == pytest.approx(5, rel=0.1) and f.eta_critical_h == pytest.approx(15, rel=0.1)


def test_refuelling_step_is_not_a_trend() -> None:
    fuel = Bounds(min=15, critical_min=5)
    # burning 1 %/h down to 12 %, refuelled to 80 % twenty minutes ago, burning again since
    f = forecast(series(lambda m: 80 - (20 - m) / 60 if m < 20 else 12 + (m - 20) / 60), fuel, NOW)
    assert f.direction != "rising"
    assert f.trend_per_hour is None or f.trend_per_hour <= 0


def test_short_history_has_no_baseline_or_trend() -> None:
    f = forecast(series(lambda m: 5.0, minutes=5), COLD, NOW)
    assert f.zscore is None and f.direction == "unknown" and f.risk == "ok"


def test_fmt_hours() -> None:
    assert fmt_hours(0) == "уже" and fmt_hours(0.25) == "через 15 мин" and fmt_hours(3) == "через 3 ч"
    assert fmt_hours(72) == "через 3.0 сут"
