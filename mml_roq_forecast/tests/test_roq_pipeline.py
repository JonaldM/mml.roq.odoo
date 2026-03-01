from odoo.tests.common import TransactionCase


class TestRoqPipeline(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.warehouse.is_active_for_roq = True
        supplier = self.env['res.partner'].create({
            'name': 'Pipeline Supplier', 'supplier_rank': 1,
            'fob_port': 'CNSHA',
        })
        self.product_tmpl = self.env['product.template'].create({
            'name': 'Pipeline Test SKU',
            'type': 'product',
            'is_roq_managed': True,
            'cbm_per_unit': 0.05,
            'pack_size': 6,
        })
        product = self.product_tmpl.product_variant_ids[0]
        self.env['product.supplierinfo'].create({
            'partner_id': supplier.id,
            'product_tmpl_id': self.product_tmpl.id,
            'price': 10.0,
        })

    def test_pipeline_creates_forecast_run(self):
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        self.assertEqual(run.status, 'complete')

    def test_pipeline_creates_forecast_lines(self):
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        self.assertGreater(len(run.line_ids), 0)

    def test_dormant_sku_has_zero_roq(self):
        # Product with no sales history → Tier D → ROQ = 0
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        lines = run.line_ids.filtered(
            lambda l: l.product_id.product_tmpl_id == self.product_tmpl
        )
        for line in lines:
            self.assertEqual(line.roq_raw, 0.0)  # No history = Tier D
