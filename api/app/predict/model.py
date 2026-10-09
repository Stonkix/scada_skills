"""Explainable forecasting on minute aggregates: linear trend, z-score anomaly, time to threshold.

Deliberately simple: every number can be explained to an operator in one sentence,
and the method is stable on the short histories a fresh deployment has.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

TREND_WINDOW = timedelta(hours=2)
FAST_WINDOW = timedelta(minutes=30)  # reacts to a fresh ramp that a 2 h fit would average away
FAST_MIN_R2 = 0.7
FAST_MIN_Z = 2.0  # ...and only when the level has left its daily range: an oscillation looks like a ramp too
RECENT_WINDOW = timedelta(minutes=15)
HORIZON_H = 72.0  # don't promise crossings further out than this
MIN_TREND_POINTS = 10
MIN_BASELINE_POINTS = 30
MIN_TREND_R2 = 0.3  # a line explaining <30 % of the variance is no trend (e.g. an oscillation): don't extrapolate it
STABLE_SLOPE_FRACTION = 0.01  # |slope| below 1 % of the norm band per hour counts as stable


@dataclass(frozen=True)
class Bounds:
    min: float | None = None
    max: float | None = None
    critical_min: float | None = None
    critical_max: float | None = None


@dataclass(frozen=True)
class Fit:
    slope_per_h: float
    intercept: float  # value at t = 0 (hours since the first point)
    r2: float
    t0: datetime


@dataclass(frozen=True)
class Forecast:
    samples: int
    current: float
    baseline_mean: float | None
    baseline_std: float | None
    zscore: float | None
    anomaly: bool
    trend_per_hour: float | None
    trend_r2: float | None
    direction: str  # rising | falling | stable | unknown
    eta_warning_h: float | None
    eta_critical_h: float | None
    bound_warning: float | None
    bound_critical: float | None
    risk: str  # ok | watch | warning | critical
    projection: list[tuple[datetime, float]]


def linear_fit(points: list[tuple[datetime, float]]) -> Fit | None:
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in points]
    ys = [v for _, v in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    return Fit(slope, intercept, 1 - ss_res / ss_tot if ss_tot > 0 else 1.0, t0)


def ewma(values: list[float], alpha: float = 0.3) -> float:
    s = values[0]
    for v in values[1:]:
        s = alpha * v + (1 - alpha) * s
    return s


def mean_std(values: list[float]) -> tuple[float, float]:
    m = sum(values) / len(values)
    return m, math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def eta_hours(current: float, slope_per_h: float, bound: float | None, upper: bool) -> float | None:
    """Hours until `current` moving at `slope_per_h` reaches `bound`; 0 if already beyond, None if never/too far."""
    if bound is None:
        return None
    if (upper and current >= bound) or (not upper and current <= bound):
        return 0.0
    if slope_per_h == 0 or (slope_per_h > 0) != upper:
        return None
    h = (bound - current) / slope_per_h
    return h if 0 <= h <= HORIZON_H else None


def last_segment(points: list[tuple[datetime, float]], band: float) -> list[tuple[datetime, float]]:
    """Points after the last step change (refuelling, recalibration, a door left open): a line through a
    step is not a trend. A step is a one-minute jump far above the series' usual minute-to-minute noise."""
    if len(points) < 3:
        return points
    diffs = sorted(abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
    typical = diffs[len(diffs) // 2]
    limit = max(5 * typical, 0.1 * band)
    for i in range(len(points) - 1, 0, -1):
        if abs(points[i][1] - points[i - 1][1]) > limit:
            return points[i:]
    return points


def trend(points: list[tuple[datetime, float]], now: datetime, z: float | None) -> Fit | None:
    """Short window for a real ramp, otherwise the long, noise-tolerant one.

    Half a period of a normal daily/shift oscillation is as linear as a ramp, so the short window is
    trusted only when the current value is also outside its usual range (|z| >= FAST_MIN_Z).
    """
    fits = []
    for window in (FAST_WINDOW, TREND_WINDOW):
        pts = [(t, v) for t, v in points if t >= now - window]
        fits.append(linear_fit(pts) if len(pts) >= MIN_TREND_POINTS else None)
    fast, slow = fits
    if fast is not None and fast.r2 >= FAST_MIN_R2 and z is not None and abs(z) >= FAST_MIN_Z:
        return fast
    return slow


def forecast(points: list[tuple[datetime, float]], bounds: Bounds, now: datetime) -> Forecast | None:
    """`points`: (bucket start, average) for the last ~24 h, oldest first."""
    if not points:
        return None
    recent = [v for t, v in points if t >= now - RECENT_WINDOW] or [points[-1][1]]
    current = ewma(recent)
    baseline = [v for t, v in points if t < now - RECENT_WINDOW]
    mean = std = z = None
    if len(baseline) >= MIN_BASELINE_POINTS:
        mean, std = mean_std(baseline)
        z = (current - mean) / std if std > 1e-9 else 0.0

    band = (bounds.max - bounds.min) if bounds.max is not None and bounds.min is not None else abs(current) or 1.0
    fit = trend(last_segment(points, band), now, z)
    if fit is None:
        direction = "unknown"
    elif fit.r2 < MIN_TREND_R2 or abs(fit.slope_per_h) < STABLE_SLOPE_FRACTION * band:
        direction = "stable"
    else:
        direction = "rising" if fit.slope_per_h > 0 else "falling"

    upper = direction != "falling"
    slope = fit.slope_per_h if fit and direction in ("rising", "falling") else 0.0
    bound_w = bounds.max if upper else bounds.min
    bound_c = bounds.critical_max if upper else bounds.critical_min
    eta_w = eta_hours(current, slope, bound_w, upper)
    eta_c = eta_hours(current, slope, bound_c, upper)
    # a value already outside the band on the other side is reported as-is
    if bounds.min is not None and current < bounds.min:
        bound_w, eta_w = bounds.min, 0.0
    if bounds.critical_min is not None and current < bounds.critical_min:
        bound_c, eta_c = bounds.critical_min, 0.0
    if bounds.max is not None and current > bounds.max:
        bound_w, eta_w = bounds.max, 0.0
    if bounds.critical_max is not None and current > bounds.critical_max:
        bound_c, eta_c = bounds.critical_max, 0.0

    anomaly = z is not None and abs(z) >= 3
    if eta_c is not None and eta_c <= 2:
        risk = "critical"
    elif (eta_w is not None and eta_w <= 2) or anomaly:
        risk = "warning"
    elif (eta_w is not None and eta_w <= 12) or (z is not None and abs(z) >= 2):
        risk = "watch"
    else:
        risk = "ok"

    projection = []
    if fit is not None and direction in ("rising", "falling"):
        for step in range(0, 13):  # next 3 h every 15 min
            ts = now + timedelta(minutes=15 * step)
            projection.append((ts, current + slope * step / 4))
    return Forecast(
        samples=len(points), current=current, baseline_mean=mean, baseline_std=std, zscore=z, anomaly=anomaly,
        trend_per_hour=fit.slope_per_h if fit else None, trend_r2=fit.r2 if fit else None, direction=direction,
        eta_warning_h=eta_w, eta_critical_h=eta_c, bound_warning=bound_w, bound_critical=bound_c, risk=risk,
        projection=projection,
    )


def fmt_hours(h: float) -> str:
    if h <= 0:
        return "уже"
    if h < 1:
        return f"через {max(1, round(h * 60))} мин"
    if h < 48:
        return f"через {h:.1f} ч".replace(".0 ", " ")
    return f"через {h / 24:.1f} сут"


def summary(f: Forecast, name: str, unit: str | None) -> str:
    u = f" {unit}" if unit else ""
    parts = [f"{name} {f.current:.1f}{u}"]
    if f.direction in ("rising", "falling") and f.trend_per_hour is not None:
        parts.append(f"{'растёт' if f.direction == 'rising' else 'падает'} на {abs(f.trend_per_hour):.2f}{u}/ч")
    elif f.direction == "stable":
        parts.append("стабильно")
    if f.eta_critical_h is not None:
        parts.append(f"критическая граница {f.bound_critical:g}{u} — {fmt_hours(f.eta_critical_h)}")
    elif f.eta_warning_h is not None:
        parts.append(f"выход за норму {f.bound_warning:g}{u} — {fmt_hours(f.eta_warning_h)}")
    if f.anomaly and f.zscore is not None:
        parts.append(f"аномалия: {abs(f.zscore):.1f}σ от суточной нормы")
    return ", ".join(parts)
