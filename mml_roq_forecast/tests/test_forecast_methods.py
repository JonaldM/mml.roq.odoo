import math
from odoo.tests.common import TransactionCase
from ..services.forecast_methods import forecast_sma, forecast_ewma


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
