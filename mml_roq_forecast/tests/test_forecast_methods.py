import math
import unittest
from odoo.tests.common import TransactionCase
from ..services.forecast_methods import (
    forecast_sma, forecast_ewma, demand_std_dev,
    forecast_croston_sba, select_forecast_method,
)
import statistics as _statistics


class TestForecastMethods(TransactionCase):

    def test_sma_returns_average_of_last_n_weeks(self):
        history = [0.0] * 8 + [10.0, 10.0]
        result = forecast_sma(history, window=2)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_sma_uses_full_history_when_less_than_window(self):
        history = [5.0, 5.0, 5.0]
        result = forecast_sma(history, window=52)
        self.assertAlmostEqual(result, 5.0, places=2)

    def test_sma_zero_history_returns_zero(self):
        history = [0.0] * 10
        result = forecast_sma(history, window=52)
        self.assertEqual(result, 0.0)

    def test_ewma_weights_recent_more(self):
        history = [1.0] * 8 + [10.0, 10.0]
        result = forecast_ewma(history, span=4)
        sma_full = sum(history) / len(history)
        self.assertGreater(result, sma_full)

    def test_ewma_constant_series_matches_value(self):
        history = [5.0] * 20
        result = forecast_ewma(history, span=4)
        self.assertAlmostEqual(result, 5.0, places=2)


class TestHoltWinters(TransactionCase):

    def test_holt_winters_seasonal_data(self):
        from ..services.forecast_methods import forecast_holt_winters
        history = [
            10 + 8 * math.sin(2 * math.pi * i / 52)
            for i in range(104)
        ]
        result = forecast_holt_winters(history, seasonal_period=52)
        self.assertGreater(result, 0.0)
        self.assertLess(result, 25.0)

    def test_holt_winters_falls_back_to_sma_insufficient_data(self):
        from ..services.forecast_methods import forecast_holt_winters
        history = [5.0] * 20
        result = forecast_holt_winters(history, seasonal_period=52)
        self.assertAlmostEqual(result, 5.0, places=1)

    def test_method_selection_seasonal_data(self):
        from ..services.forecast_methods import select_forecast_method
        history = [
            10 + 8 * math.sin(2 * math.pi * i / 52)
            for i in range(104)
        ]
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'holt_winters')

    def test_method_selection_insufficient_data(self):
        from ..services.forecast_methods import select_forecast_method
        history = [5.0] * 5
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'sma')
        self.assertEqual(confidence, 'low')

    def test_phi_damping_reduces_trend_contribution(self):
        """phi=0.98 forecast must be strictly less than phi=1.0 on a growing series."""
        from ..services.forecast_methods import forecast_holt_winters
        # Very strong upward trend: start at 100, increase by 10 per week for 2 cycles.
        # beta=0.3 (vs default 0.1) ensures last_trend is positive and large enough that
        # phi=0.98 produces a measurably lower forecast than phi=1.0.
        history = [100.0 + 10.0 * i for i in range(104)]
        undamped = forecast_holt_winters(history, beta=0.3, phi=1.0)
        damped = forecast_holt_winters(history, beta=0.3, phi=0.98)
        self.assertLess(damped, undamped)


class TestDemandStdDev(unittest.TestCase):

    def test_croston_std_override_returned_directly(self):
        """croston_std kwarg short-circuits all other logic."""
        result, is_fallback = demand_std_dev([1.0, 2.0, 3.0], min_n=8, croston_std=2.5)
        self.assertAlmostEqual(result, 2.5)
        self.assertFalse(is_fallback)

    def test_croston_std_zero_not_treated_as_falsy(self):
        """croston_std=0.0 must pass through — do not use 'if croston_std:'."""
        result, is_fallback = demand_std_dev([1.0, 2.0, 3.0], min_n=8, croston_std=0.0)
        self.assertAlmostEqual(result, 0.0)
        self.assertFalse(is_fallback)

    def test_uses_history_length_not_nonzero_count_for_min_n(self):
        """5 nonzero + 100 zeros = 105 total >= min_n=8: should return computed stddev, not fallback."""
        import numpy as np
        history = [5.0] * 5 + [0.0] * 100
        result, is_fallback = demand_std_dev(history, min_n=8)
        self.assertFalse(is_fallback)
        expected = float(np.std(history, ddof=1))
        self.assertAlmostEqual(result, expected, places=4)

    def test_fallback_when_history_too_short(self):
        """Only 3 data points < min_n=8: should return 0.5 * mean fallback."""
        history = [4.0, 6.0, 8.0]
        result, is_fallback = demand_std_dev(history, min_n=8)
        self.assertTrue(is_fallback)
        self.assertAlmostEqual(result, 3.0)  # 0.5 * mean(4,6,8) = 0.5 * 6 = 3.0


class TestCrostonSba(unittest.TestCase):

    def test_basic_forecast_positive_and_below_nonzero_mean(self):
        """SBA correction means result < naive mean of non-zero values."""
        history = [0, 5, 0, 3, 0, 4]
        forecast, std = forecast_croston_sba(history)
        nonzero_mean = _statistics.mean([5, 3, 4])
        self.assertGreater(forecast, 0.0)
        self.assertLess(forecast, nonzero_mean)

    def test_all_zero_returns_zero_and_none(self):
        history = [0.0] * 20
        forecast, std = forecast_croston_sba(history)
        self.assertEqual(forecast, 0.0)
        self.assertIsNone(std)

    def test_single_nonzero_std_is_none(self):
        """Cannot compute stdev with fewer than 2 points."""
        history = [0.0] * 10 + [5.0] + [0.0] * 5
        forecast, std = forecast_croston_sba(history)
        self.assertGreater(forecast, 0.0)
        self.assertIsNone(std)

    def test_multiple_nonzero_std_is_float(self):
        history = [0, 10, 0, 0, 8, 0, 12, 0]
        forecast, std = forecast_croston_sba(history)
        self.assertIsNotNone(std)
        self.assertGreater(std, 0.0)


class TestSelectForecastMethodCroston(unittest.TestCase):

    def test_routes_to_croston_when_sparse(self):
        """< 30% active weeks -> croston regardless of trend or season."""
        # 5 nonzero out of 40 total = 12.5% active
        history = [0.0] * 35 + [1.0, 0.0, 2.0, 0.0, 3.0]
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'croston')

    def test_croston_checked_before_trend(self):
        """Sparse trending series must route to croston, not ewma."""
        # Upward trend but only ~12% active weeks
        history = [float(i) if i % 8 == 0 else 0.0 for i in range(80)]
        method, _ = select_forecast_method(history)
        self.assertEqual(method, 'croston')

    def test_dense_series_does_not_route_to_croston(self):
        """> 30% active weeks: Croston path skipped."""
        history = [5.0] * 50 + [0.0] * 10  # 83% active
        method, _ = select_forecast_method(history)
        self.assertNotEqual(method, 'croston')

    def test_existing_insufficient_data_test_unaffected(self):
        """[5.0]*5: pct_active=100%, not sparse. Still falls to sma/low via min_n."""
        history = [5.0] * 5
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'sma')
        self.assertEqual(confidence, 'low')
