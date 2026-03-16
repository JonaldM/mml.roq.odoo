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
    if len(history) < min_n:
        mean = sum(history) / len(history) if history else 0.0
        return 0.5 * mean, True
    return float(np.std(history, ddof=1)), False


def select_forecast_method(history, min_n=8, seasonal_period=52,
                           seasonality_threshold=0.4, trend_pvalue=0.05):
    """
    Automatically selects the best forecast method for the given history.

    Selection logic:
    1. If < min_n non-zero weeks → SMA (low confidence)
    2. Test for seasonality (strength of seasonal component)
    3. If seasonal → Holt-Winters
    4. Test for trend (Mann-Kendall)
    5. If trending → EWMA
    6. Otherwise → SMA

    Returns: (method_name, confidence)
    """
    nonzero = [v for v in history if v > 0]

    if len(nonzero) < min_n:
        return 'sma', 'low'

    if len(history) >= 2 * seasonal_period:
        seasonality_strength = _seasonal_strength(history, seasonal_period)
        if seasonality_strength > seasonality_threshold:
            return 'holt_winters', 'high'

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
