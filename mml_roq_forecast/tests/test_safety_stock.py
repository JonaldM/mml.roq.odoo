import math
from odoo.tests.common import TransactionCase
from ..services.safety_stock import calculate_safety_stock


class TestSafetyStock(TransactionCase):

    def test_formula_z_sigma_sqrt_lt(self):
        result = calculate_safety_stock(z_score=1.881, sigma=10.0, lt_weeks=14.28)
        expected = 1.881 * 10.0 * math.sqrt(14.28)
        self.assertAlmostEqual(result, expected, places=2)

    def test_low_sigma_uses_fallback(self):
        result = calculate_safety_stock(z_score=1.645, sigma=2.5, lt_weeks=14.28)
        self.assertGreater(result, 0.0)

    def test_zero_z_score_gives_zero_ss(self):
        result = calculate_safety_stock(z_score=0.0, sigma=5.0, lt_weeks=14.28)
        self.assertEqual(result, 0.0)

    def test_zero_lt_gives_zero_ss(self):
        result = calculate_safety_stock(z_score=1.881, sigma=5.0, lt_weeks=0.0)
        self.assertEqual(result, 0.0)

    def test_result_is_never_negative(self):
        result = calculate_safety_stock(z_score=1.881, sigma=0.0, lt_weeks=14.28)
        self.assertGreaterEqual(result, 0.0)
