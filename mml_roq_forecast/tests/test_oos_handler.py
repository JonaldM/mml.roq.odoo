import unittest
from datetime import date, timedelta


class TestDetectOosWeeks(unittest.TestCase):
    """
    detect_oos_weeks(weekly_pairs, receipt_dates) -> list[bool]

    weekly_pairs: list of (week_start: date, qty: float)
    receipt_dates: list of date
    OOS rule: qty == 0.0 AND any receipt within abs(delta) <= 28 days
    """

    def _import(self):
        from ..services.oos_handler import detect_oos_weeks
        return detect_oos_weeks

    def test_zero_week_with_nearby_receipt_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=14)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertTrue(flags[0])

    def test_zero_week_no_receipt_not_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        flags = detect_oos_weeks([(week, 0.0)], [])
        self.assertFalse(flags[0])

    def test_nonzero_week_never_flagged_even_with_receipt(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=3)
        flags = detect_oos_weeks([(week, 10.0)], [receipt])
        self.assertFalse(flags[0])

    def test_receipt_at_boundary_28_days_is_flagged(self):
        """Inclusive boundary: exactly 28 days away must flag True."""
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=28)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertTrue(flags[0])

    def test_receipt_beyond_boundary_29_days_not_flagged(self):
        """29 days away: just outside the window, must flag False."""
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=29)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertFalse(flags[0])

    def test_receipt_too_far_35_days_not_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=35)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertFalse(flags[0])

    def test_no_receipts_anywhere_all_false(self):
        """All zeros but zero receipts: every week genuine zero demand."""
        detect_oos_weeks = self._import()
        weeks = [(date(2025, 1, 6) + timedelta(weeks=i), 0.0) for i in range(10)]
        flags = detect_oos_weeks(weeks, [])
        self.assertTrue(all(f is False for f in flags))

    def test_returns_same_length_as_weekly_pairs(self):
        detect_oos_weeks = self._import()
        weeks = [(date(2025, 1, 6) + timedelta(weeks=i), float(i % 3)) for i in range(20)]
        receipt = date(2025, 1, 20)
        flags = detect_oos_weeks(weeks, [receipt])
        self.assertEqual(len(flags), 20)


class TestImputeOosDemand(unittest.TestCase):
    """
    impute_oos_demand(sales, oos_flags) -> list[float]

    OOS zeros replaced with mean of up to 4 nearest in-stock neighbours.
    Fallback: mean of non-zero in-stock values if <2 neighbours in window.
    All-OOS: return unchanged.
    """

    def _import(self):
        from ..services.oos_handler import impute_oos_demand
        return impute_oos_demand

    def test_oos_week_replaced_with_neighbour_mean(self):
        impute_oos_demand = self._import()
        sales = [10.0, 0.0, 10.0]
        flags = [False, True, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[1], 10.0)

    def test_non_oos_weeks_unchanged(self):
        impute_oos_demand = self._import()
        sales = [5.0, 0.0, 7.0]
        flags = [False, True, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[0], 5.0)
        self.assertAlmostEqual(result[2], 7.0)

    def test_falls_back_to_in_stock_mean_when_few_neighbours(self):
        """
        Long OOS run in middle. Indices deep in the run have <2 in-stock
        neighbours within ±4 window. Should fall back to non-zero in-stock mean.

        sales: [0, 0, 0, 10, 10, | OOS x8 | 10, 10, 0, 0]
        oos:   [F, F, F,  F,  F, | T  x8  |  F,  F, F, F]
        Non-zero in-stock values (oos=False AND value>0): [10, 10, 10, 10] -> mean = 10.0
        Index 9: window indices 5-13, only index 13 is non-OOS (1 neighbour < 2) -> fallback = 10.0
        """
        impute_oos_demand = self._import()
        sales = [0.0, 0.0, 0.0, 10.0, 10.0,
                 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                 10.0, 10.0, 0.0, 0.0]
        flags = [False, False, False, False, False,
                 True, True, True, True, True, True, True, True,
                 False, False, False, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[9], 10.0)

    def test_all_oos_returns_unchanged(self):
        """Cannot impute if entire series is OOS — return as-is."""
        impute_oos_demand = self._import()
        sales = [0.0] * 10
        flags = [True] * 10
        result = impute_oos_demand(sales, flags)
        self.assertEqual(result, sales)

    def test_boundary_clamping_index_zero(self):
        """OOS week at index 0 has no left neighbours — must still impute from right side."""
        impute_oos_demand = self._import()
        sales = [0.0, 8.0, 8.0, 8.0, 8.0]
        flags = [True, False, False, False, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[0], 8.0)
