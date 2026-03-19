import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from odoo.tests.common import TransactionCase

from ..services.demand_history import DemandHistoryService
from mml_roq_forecast.services.pipeline_data_cache import PipelineDataCache


class TestDemandHistory(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.product = self.env['product.product'].create({
            'name': 'Test SKU DH',
            'type': 'product',
        })
        today = date.today()
        partner = self.env['res.partner'].search([], limit=1)
        for i in range(10):
            order_date = today - timedelta(weeks=i+1)
            order = self.env['sale.order'].create({
                'partner_id': partner.id,
                'date_order': order_date,
                'warehouse_id': self.warehouse.id,
                'state': 'sale',
            })
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': self.product.id,
                'product_uom_qty': 10.0,
                'price_unit': 5.0,
            })

    def test_returns_weekly_series(self):
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(self.product, self.warehouse, lookback_weeks=52)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_nonzero_weeks_have_demand(self):
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(self.product, self.warehouse, lookback_weeks=52)
        nonzero = [v for v in result if v > 0]
        self.assertGreater(len(nonzero), 0)

    def test_empty_history_returns_zeros(self):
        new_product = self.env['product.product'].create({'name': 'Brand New SKU', 'type': 'product'})
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(new_product, self.warehouse, lookback_weeks=8)
        self.assertEqual(len(result), 8)
        self.assertEqual(sum(result), 0.0)


class TestDemandHistoryOosImputation(unittest.TestCase):
    """
    Tests that get_weekly_demand() imputes OOS zeros using the incoming
    stock.move receipt signal. Uses MagicMock to avoid needing Odoo runtime.
    """

    def test_oos_week_is_imputed_when_receipt_nearby(self):
        """
        Scenario:
          - 8 weeks of history
          - Week 3 (index 2) has zero sale orders
          - An incoming stock.move receipt exists 7 days after week 3 start
          - Weeks 1,2 and 4,5 have demand of 10 each
          Expected: week 3 is imputed to ~10.0 (mean of neighbours)
        """
        from ..services.demand_history import DemandHistoryService

        today = date.today()
        # Build week starts (Monday-anchored)
        base_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=8)
        week_starts = [base_monday + timedelta(weeks=i) for i in range(8)]

        # Sale order lines: demand=10 for all weeks except week index 2
        sale_lines = []
        for i, ws in enumerate(week_starts):
            if i == 2:
                continue  # zero demand this week
            line = MagicMock()
            line.order_id.date_order.date.return_value = ws
            line.product_uom_qty = 10.0
            sale_lines.append(line)

        # Incoming receipt 7 days after the zero week
        receipt_move = MagicMock()
        receipt_date = week_starts[2] + timedelta(days=7)
        receipt_move.date.date.return_value = receipt_date

        # Build mocked env
        env = MagicMock()
        product = MagicMock()
        product.id = 42
        warehouse = MagicMock()
        warehouse.id = 1

        # First search (sale.order.line) returns sale_lines
        # Second search (stock.move incoming) returns [receipt_move]
        env.__getitem__.return_value.search.side_effect = [
            sale_lines,       # sale.order.line search
            [receipt_move],   # stock.move incoming search
        ]

        svc = DemandHistoryService(env)
        result = svc.get_weekly_demand(product, warehouse, lookback_weeks=8)

        # The zero week should be imputed (non-zero)
        self.assertEqual(len(result), 8)
        self.assertGreater(result[2], 0.0)


def _make_cache_with_demand(variant_id, wh_id, week_qty_pairs):
    """Return a PipelineDataCache with demand pre-populated."""
    cache = MagicMock(spec=PipelineDataCache)
    cache.demand = {(variant_id, wh_id): week_qty_pairs}
    cache.receipts = {}
    cache.revenue = {}
    return cache


class TestDemandHistoryServiceCachePath(unittest.TestCase):

    def test_get_weekly_demand_uses_cache_when_available(self):
        today = date.today()
        # 2 weeks back: must be strictly inside the lookback_weeks=4 window.
        # Using lookback_weeks > offset ensures the seed is always in the window
        # regardless of what day today is.
        monday = today - timedelta(days=today.weekday()) - timedelta(weeks=2)
        cache = _make_cache_with_demand(101, 1, [(monday, 12.0)])

        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        # Pin the sol_mock reference so the assertion below checks the same object the
        # service would call search() on — prevents vacuous assert_not_called().
        env = MagicMock()
        sol_mock = env['sale.order.line']  # pin reference now

        dh = DemandHistoryService(env, cache=cache)
        result = dh.get_weekly_demand(product, warehouse, lookback_weeks=4)

        assert isinstance(result, list)
        # The cache-hit path should return the seeded 12.0 value somewhere in the series
        assert any(v == 12.0 for v in result)
        # ORM must NOT have been called — assert on pinned reference, not a new auto-created mock
        sol_mock.search.assert_not_called()

    def test_get_weekly_demand_raw_uses_cache_when_available(self):
        today = date.today()
        # 2 weeks back: must be strictly inside the lookback_weeks=4 window.
        # Using lookback_weeks > offset ensures the seed is always in the window
        # regardless of what day today is.
        monday = today - timedelta(days=today.weekday()) - timedelta(weeks=2)
        cache = _make_cache_with_demand(101, 1, [(monday, 7.0)])

        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        # Pin the sol_mock reference so the assertion below checks the same object the
        # service would call search() on — prevents vacuous assert_not_called().
        env = MagicMock()
        sol_mock = env['sale.order.line']  # pin reference now

        dh = DemandHistoryService(env, cache=cache)
        result = dh.get_weekly_demand_raw(product, warehouse, lookback_weeks=4)

        assert isinstance(result, list)
        assert any(v == 7.0 for v in result)
        # ORM must NOT have been called — assert on pinned reference, not a new auto-created mock
        sol_mock.search.assert_not_called()

    def test_get_trailing_revenue_by_warehouse_uses_cache_when_available(self):
        product_template = MagicMock()
        product_template.id = 1
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.revenue = {(1, 1): 5000.0}

        env = MagicMock()
        sol_mock = env['sale.order.line']  # pin reference before service construction — same pattern as demand tests

        dh = DemandHistoryService(env, cache=cache)
        result = dh.get_trailing_revenue_by_warehouse(product_template, warehouse)

        assert result == 5000.0
        sol_mock.search.assert_not_called()

    def test_get_trailing_revenue_by_warehouse_returns_zero_for_missing_key(self):
        product_template = MagicMock()
        product_template.id = 999
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.revenue = {}

        dh = DemandHistoryService(MagicMock(), cache=cache)
        result = dh.get_trailing_revenue_by_warehouse(product_template, warehouse)

        assert result == 0.0

    def test_get_weekly_demand_falls_back_to_orm_on_cache_miss(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        # Cache exists but does not contain this key
        cache = MagicMock(spec=PipelineDataCache)
        cache.demand = {}   # empty — will trigger fallback
        cache.receipts = {}

        sol_mock = MagicMock()
        sol_mock.search.return_value = []
        move_mock = MagicMock()
        move_mock.search.return_value = []

        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = lambda self, key: sol_mock if key == 'sale.order.line' else move_mock

        dh = DemandHistoryService(env, cache=cache)
        result = dh.get_weekly_demand(product, warehouse, lookback_weeks=4)

        # ORM SHOULD have been called because of cache miss
        sol_mock.search.assert_called_once()

    def test_no_cache_uses_orm(self):
        """Passing cache=None preserves the original ORM-only behaviour."""
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        sol_mock = MagicMock()
        sol_mock.search.return_value = []
        move_mock = MagicMock()
        move_mock.search.return_value = []

        env = MagicMock()
        env.company.id = 1
        env.__getitem__ = lambda self, key: sol_mock if key == 'sale.order.line' else move_mock

        dh = DemandHistoryService(env, cache=None)
        dh.get_weekly_demand(product, warehouse, lookback_weeks=4)

        sol_mock.search.assert_called_once()
