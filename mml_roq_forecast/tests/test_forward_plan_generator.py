from datetime import date
from odoo.tests.common import TransactionCase
from ..services.forward_plan_generator import ForwardPlanGenerator


class TestForwardPlanGenerator(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.warehouse.is_active_for_roq = True
        self.supplier = self.env['res.partner'].create({
            'name': 'FP Supplier', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.product_tmpl = self.env['product.template'].create({
            'name': 'FP Test SKU', 'type': 'product',
            'is_roq_managed': True, 'cbm_per_unit': 0.05, 'pack_size': 6,
        })
        self.env['product.supplierinfo'].create({
            'partner_id': self.supplier.id,
            'product_tmpl_id': self.product_tmpl.id,
            'price': 15.0,
        })
        self.run = self.env['roq.forecast.run'].create({'status': 'complete'})
        self.gen = ForwardPlanGenerator(self.env)

    def _make_forecast_line(self, weekly_demand, lead_time=100):
        product = self.product_tmpl.product_variant_ids[0]
        return self.env['roq.forecast.line'].create({
            'run_id': self.run.id,
            'product_id': product.id,
            'warehouse_id': self.warehouse.id,
            'supplier_id': self.supplier.id,
            'forecasted_weekly_demand': weekly_demand,
            'lead_time_days': lead_time,
            'abc_tier': 'B',
        })

    def test_generates_12_months_of_lines(self):
        self._make_forecast_line(10.0)
        plan = self.gen.generate_for_supplier(
            self.supplier, self.run, horizon_months=12
        )
        self.assertGreater(len(plan.line_ids), 0)
        months = set(plan.line_ids.mapped('month'))
        self.assertEqual(len(months), 12)

    def test_monthly_demand_is_weekly_times_4_33(self):
        self._make_forecast_line(10.0)
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        if plan.line_ids:
            first_line = plan.line_ids.sorted('month')[0]
            self.assertAlmostEqual(
                first_line.forecasted_monthly_demand, 10.0 * 4.33, places=0
            )

    def test_planned_order_date_is_ship_date_minus_lead_time(self):
        self._make_forecast_line(10.0, lead_time=100)
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        for line in plan.line_ids:
            if line.planned_ship_date and line.planned_order_date:
                diff = (line.planned_ship_date - line.planned_order_date).days
                self.assertAlmostEqual(diff, 100, delta=7)  # ±1 week tolerance

    def test_total_fob_cost_computed(self):
        self._make_forecast_line(10.0)
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        self.assertGreater(plan.total_fob_cost, 0.0)

    def test_no_plan_when_no_lines(self):
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=12)
        self.assertFalse(plan)
