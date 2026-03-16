"""
Pure-Python tests for Holt-Winters upper bound clamp.
No Odoo runtime needed.
"""
from mml_roq_forecast.services.forecast_methods import (
    forecast_holt_winters,
    HW_BIAS_FLOOR,
    HW_BIAS_CEILING,
)


def test_hw_bias_constants_correct():
    """HW_BIAS_FLOOR and HW_BIAS_CEILING must have the correct values."""
    assert HW_BIAS_FLOOR == 0.50
    assert HW_BIAS_CEILING == 1.50


def test_holt_winters_2x_recent_max_cap():
    """HW forecast must be capped at 2x the max of the last 13 weeks."""
    # 104-week series: first 91 weeks = 5, last 13 weeks = 20 -> recent_max = 20 -> cap = 40.
    # Use a strongly upward trend so uncapped HW would exceed 40.
    history = [5.0] * 91 + [20.0] * 13
    result = forecast_holt_winters(history, seasonal_period=52)
    assert result <= 40.0, (
        f"HW forecast {result:.2f} exceeds 2x recent_max cap of 40.0"
    )


def test_holt_winters_clamps_explosive_trend():
    """Holt-Winters must not produce absurdly large forecasts regardless of input.

    An exponentially-escalating 104-week history (10^(i/10)) combined with a
    high beta (trend weight = 0.9) causes the trend component to explode.
    Without an upper-bound clamp the raw forecast exceeds 21 billion units.
    With the clamp it must be <= 1 000 000.
    """
    history = [10.0 ** (i / 10) for i in range(104)]
    result = forecast_holt_winters(
        history, seasonal_period=52, alpha=0.3, beta=0.9, gamma=0.1
    )

    assert result <= 1_000_000, (
        f"Holt-Winters produced an explosive forecast of {result:.2f} units — "
        f"add an upper bound clamp (_MAX_WEEKLY_DEMAND)"
    )
