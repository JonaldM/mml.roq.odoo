from datetime import date
from odoo.tests.common import TransactionCase


class TestForwardPlan(TransactionCase):

    def test_forward_plan_creates_with_sequence(self):
        supplier = self.env['res.partner'].create({'name': 'FP Supplier'})
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
        })
        self.assertTrue(plan.name.startswith('FP-'))

    def test_forward_plan_totals_computed(self):
        supplier = self.env['res.partner'].create({'name': 'FP2 Supplier'})
        product = self.env['product.product'].create({'name': 'FP SKU', 'type': 'product'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
        })
        self.env['roq.forward.plan.line'].create({
            'plan_id': plan.id,
            'product_id': product.id,
            'warehouse_id': wh.id,
            'month': date.today().replace(day=1),
            'planned_order_qty': 100,
            'cbm': 5.0,
            'fob_line_cost': 500.0,
        })
        self.assertAlmostEqual(plan.total_cbm, 5.0, places=2)
        self.assertAlmostEqual(plan.total_fob_cost, 500.0, places=2)
