from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from ..services.demand_history import DemandHistoryService


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
