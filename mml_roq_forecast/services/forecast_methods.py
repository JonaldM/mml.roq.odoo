"""
Forecast methods for ROQ demand forecasting.

All functions take:
  history: list of float — weekly demand, oldest first
Returns:
  float — forecasted weekly demand for next period
"""
import numpy as np

# Hard upper bound on any single-week demand forecast.
# No SKU in a ~400-SKU NZ distributor context will ever legitimately demand
# 1 000 000 units per week.  This prevents pathological trend explosion in
# Holt-Winters from cascading into absurd container calculations.
_MAX_WEEKLY_DEMAND = 1_000_000.0


def forecast_sma(history, window=52):
    """
    Simple Moving Average over the last `window` weeks.
    Falls back to available history if fewer than `window` data points.
    Returns 0.0 if no history.
    """
    if not history:
        return 0.0
    effective_window = min(window, len(history))
    recent = history[-effective_window:]
    return sum(recent) / len(recent) if recent else 0.0


def forecast_ewma(history, span=26):
    """
    Exponentially Weighted Moving Average.
    span: controls decay — higher span = slower decay (less weight on recent).
    Uses pandas-style EWMA: alpha = 2 / (span + 1)
    """
    if not history:
        return 0.0
    alpha = 2.0 / (span + 1)
    result = history[0]
    for val in history[1:]:
        result = alpha * val + (1 - alpha) * result
    return result


def forecast_holt_winters(history, seasonal_period=52, alpha=0.3, beta=0.1, gamma=0.1):
    """
    Triple Exponential Smoothing (Holt-Winters additive model).
    Requires at least 2 full seasonal cycles (2 × seasonal_period data points).

    Returns the one-step-ahead forecast.
    Falls back to SMA if insufficient data.
    """
    n = len(history)
    if n < 2 * seasonal_period:
        return forecast_sma(history, window=52)

    level = sum(history[:seasonal_period]) / seasonal_period
    trend = (sum(history[seasonal_period:2*seasonal_period]) -
             sum(history[:seasonal_period])) / seasonal_period**2
    season = [history[i] - level for i in range(seasonal_period)]

    levels = [level]
    trends = [trend]
    seasons = list(season)

    for i in range(seasonal_period, n):
        prev_level = levels[-1]
        prev_trend = trends[-1]
        prev_season = seasons[i - seasonal_period]

        new_level = alpha * (history[i] - prev_season) + (1 - alpha) * (prev_level + prev_trend)
        new_trend = beta * (new_level - prev_level) + (1 - beta) * prev_trend
        new_season = gamma * (history[i] - new_level) + (1 - gamma) * prev_season

        levels.append(new_level)
        trends.append(new_trend)
        seasons.append(new_season)

    last_level = levels[-1]
    last_trend = trends[-1]
    last_season = seasons[-seasonal_period]
    forecast = last_level + last_trend + last_season
    return max(0.0, min(_MAX_WEEKLY_DEMAND, forecast))


def forecast_croston_sba(history, alpha=0.1):
    """
    Croston/SBA forecast for intermittent demand (<30% active weeks).
    alpha=0.1: slow-adapting EWMA appropriate for sparse data.
    SBA correction factor 0.95 corrects Croston's known upward bias.

    Returns: (forecast: float, std: float | None)
      forecast = (smoothed_size / smoothed_interval) * 0.95
      std      = statistics.stdev(non_zero_sizes) if len >= 2, else None
    Returns (0.0, None) if no positive values in history.
    """
    import statistics
    non_zero = [(i, v) for i, v in enumerate(history) if v > 0]
    if not non_zero:
        return 0.0, None
    sizes = [v for _, v in non_zero]
    intervals = [non_zero[0][0] + 1] + [
        non_zero[i][0] - non_zero[i - 1][0] for i in range(1, len(non_zero))
    ]
    z_size = sizes[0]
    z_interval = intervals[0]
    for s, q in zip(sizes[1:], intervals[1:]):
        z_size = alpha * s + (1 - alpha) * z_size
        z_interval = alpha * q + (1 - alpha) * z_interval
    forecast = (z_size / z_interval * 0.95) if z_interval > 0 else 0.0
    std = statistics.stdev(sizes) if len(sizes) >= 2 else None
    return max(forecast, 0.0), std


def demand_std_dev(history, min_n=8, croston_std=None):
    """
    Standard deviation of weekly demand.
    If croston_std is not None, returns it directly (Croston products use
    stddev of non-zero demand sizes, not the full series).
    If fewer than min_n data points in history, uses fallback: 0.5 x mean.
    Returns (std_dev, is_fallback).
    """
    if croston_std is not None:
        return croston_std, False
    if not history or len(history) < min_n:
        mean = sum(history) / len(history) if history else 0.0
        return 0.5 * mean, True
    return float(np.std(history, ddof=1)), False


def select_forecast_method(history, min_n=8, seasonal_period=52,
                           seasonality_threshold=0.4, trend_pvalue=0.05):
    """
    Automatically selects the best forecast method for the given history.

    Selection logic:
    1. If < 30% of weeks have non-zero demand -> Croston/SBA (intermittent)
    2. If < min_n non-zero weeks -> SMA (low confidence)
    3. Test for seasonality (strength of seasonal component)
    4. If seasonal -> Holt-Winters
    5. Test for trend (Mann-Kendall)
    6. If trending -> EWMA
    7. Otherwise -> SMA

    Returns: (method_name, confidence)
    """
    nonzero = [v for v in history if v > 0]

    # 1. Intermittency check — must come first, before Mann-Kendall / seasonality tests.
    #    Those tests are meaningless on sparse series and would misroute sparse-trending
    #    products to EWMA.
    if history:
        pct_active = len(nonzero) / len(history)
        if pct_active < 0.30:
            return 'croston', ('high' if len(nonzero) >= 2 else 'low')

    # 2. Insufficient data fallback (keeps existing behaviour for short-history products
    #    that are NOT sparse — e.g. a new SKU with 5 non-zero weeks out of 5 total).
    if len(nonzero) < min_n:
        return 'sma', 'low'

    # 3. Seasonality test
    if len(history) >= 2 * seasonal_period:
        seasonality_strength = _seasonal_strength(history, seasonal_period)
        if seasonality_strength > seasonality_threshold:
            return 'holt_winters', 'high'

    # 4. Trend test
    if _has_trend(history, pvalue_threshold=trend_pvalue):
        return 'ewma', 'high' if len(nonzero) >= 26 else 'medium'

    return 'sma', 'high' if len(nonzero) >= 26 else 'medium'


def _seasonal_strength(history, period):
    """
    Measures strength of seasonal component using residual variance approach.
    Returns a float 0–1; higher = stronger seasonality.
    """
    try:
        from scipy import signal
        arr = np.array(history)
        detrended = signal.detrend(arr)
        n = len(detrended)
        seasonal_pattern = np.array([
            np.mean(detrended[i::period]) for i in range(period)
        ])
        seasonal_full = np.tile(seasonal_pattern, n // period + 1)[:n]
        residuals = detrended - seasonal_full
        var_seasonal = np.var(seasonal_full)
        var_residual = np.var(residuals)
        if var_seasonal + var_residual == 0:
            return 0.0
        return var_seasonal / (var_seasonal + var_residual)
    except Exception:
        return 0.0


def _has_trend(history, pvalue_threshold=0.05):
    """Mann-Kendall trend test. Returns True if significant trend detected."""
    try:
        from scipy.stats import kendalltau
        n = len(history)
        x = list(range(n))
        _, p = kendalltau(x, history)
        return p < pvalue_threshold
    except Exception:
        return False
