from odoo.tests.common import TransactionCase
from ..services.push_pull import calculate_max_push_days, calculate_max_pull_days


class TestPushPull(TransactionCase):

    def test_no_push_when_any_item_at_real_oos(self):
        # Any item with projected_inventory < 0 → max_push = 0
        lines = [
            {'projected_inventory_at_delivery': -5.0, 'weeks_of_cover_at_delivery': -0.5},
            {'projected_inventory_at_delivery': 50.0, 'weeks_of_cover_at_delivery': 5.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 0)

    def test_push_12_plus_weeks_cover_allows_6_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 120.0, 'weeks_of_cover_at_delivery': 13.0},
            {'projected_inventory_at_delivery': 100.0, 'weeks_of_cover_at_delivery': 15.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 42)  # 6 weeks × 7 days

    def test_push_8_12_weeks_cover_allows_4_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 80.0, 'weeks_of_cover_at_delivery': 10.0},
            {'projected_inventory_at_delivery': 90.0, 'weeks_of_cover_at_delivery': 12.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 28)  # 4 weeks × 7 days

    def test_push_4_8_weeks_cover_allows_2_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 50.0, 'weeks_of_cover_at_delivery': 6.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 14)  # 2 weeks × 7 days

    def test_push_below_4_weeks_cover_allows_no_push(self):
        lines = [
            {'projected_inventory_at_delivery': 20.0, 'weeks_of_cover_at_delivery': 3.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 0)

    def test_push_uses_tightest_item(self):
        # Mix: one item at 15 wks, another at 6 wks → constrained by 6 wks → 14 days
        lines = [
            {'projected_inventory_at_delivery': 150.0, 'weeks_of_cover_at_delivery': 15.0},
            {'projected_inventory_at_delivery': 60.0, 'weeks_of_cover_at_delivery': 6.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 14)  # 6 weeks → 2 week push max

    def test_pull_default_is_review_interval(self):
        result = calculate_max_pull_days(review_interval_days=30)
        self.assertEqual(result, 30)

    def test_pull_capped_at_review_interval(self):
        result = calculate_max_pull_days(review_interval_days=30, override=None)
        self.assertLessEqual(result, 30)
