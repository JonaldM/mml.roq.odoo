from odoo.tests.common import TransactionCase
from ..services.roq_calculator import (
    calculate_out_level, calculate_order_up_to,
    calculate_roq_raw, calculate_projected_inventory,
    round_to_pack_size, calculate_weeks_of_cover,
)


class TestRoqCalculator(TransactionCase):

    def test_out_level_formula(self):
        # s = demand × LT_weeks + safety_stock
        result = calculate_out_level(
            weekly_demand=10.0, lt_weeks=14.28, safety_stock=20.0,
        )
        self.assertAlmostEqual(result, 10.0 * 14.28 + 20.0, places=2)

    def test_order_up_to_formula(self):
        # S = demand × (LT_weeks + review_weeks) + safety_stock
        result = calculate_order_up_to(
            weekly_demand=10.0, lt_weeks=14.28, review_weeks=4.28, safety_stock=20.0,
        )
        self.assertAlmostEqual(result, 10.0 * (14.28 + 4.28) + 20.0, places=2)

    def test_roq_raw_is_zero_when_stock_sufficient(self):
        # inventory_position >= S → no order needed
        result = calculate_roq_raw(
            order_up_to=100.0, inventory_position=150.0,
        )
        self.assertEqual(result, 0.0)

    def test_roq_raw_correct_when_stock_low(self):
        result = calculate_roq_raw(
            order_up_to=100.0, inventory_position=40.0,
        )
        self.assertEqual(result, 60.0)

    def test_pack_size_rounding_rounds_up(self):
        # ROQ of 55 with pack size 12 → ceil(55/12) × 12 = 60
        result = round_to_pack_size(roq=55.0, pack_size=12)
        self.assertEqual(result, 60)

    def test_pack_size_rounding_exact_multiple_unchanged(self):
        result = round_to_pack_size(roq=60.0, pack_size=12)
        self.assertEqual(result, 60)

    def test_pack_size_rounding_zero_returns_zero(self):
        result = round_to_pack_size(roq=0.0, pack_size=12)
        self.assertEqual(result, 0)

    def test_projected_inventory_at_delivery(self):
        # inv_position − demand × LT_weeks
        result = calculate_projected_inventory(
            inventory_position=100.0, weekly_demand=10.0, lt_weeks=14.28,
        )
        self.assertAlmostEqual(result, 100.0 - 10.0 * 14.28, places=2)

    def test_negative_projected_inventory_is_oos_signal(self):
        result = calculate_projected_inventory(
            inventory_position=50.0, weekly_demand=10.0, lt_weeks=14.28,
        )
        self.assertLess(result, 0.0)  # OOS signal

    def test_weeks_of_cover_calculation(self):
        result = calculate_weeks_of_cover(projected_inventory=100.0, weekly_demand=10.0)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_weeks_of_cover_zero_demand_returns_999(self):
        # Avoid division by zero; return sentinel value
        result = calculate_weeks_of_cover(projected_inventory=100.0, weekly_demand=0.0)
        self.assertEqual(result, 999.0)
