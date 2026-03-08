"""
Pure-Python tests for Holt-Winters upper bound clamp.
No Odoo runtime needed.
"""
from mml_roq_forecast.services.forecast_methods import forecast_holt_winters


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
