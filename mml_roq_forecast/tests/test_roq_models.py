from odoo.tests.common import TransactionCase


class TestRoqModels(TransactionCase):

    def test_forecast_run_creates_with_sequence(self):
        run = self.env['roq.forecast.run'].create({})
        self.assertTrue(run.name.startswith('ROQ-'))
        self.assertEqual(run.status, 'draft')

    def test_forecast_line_requires_run_product_warehouse(self):
        run = self.env['roq.forecast.run'].create({})
        product = self.env['product.product'].create({'name': 'Test SKU'})
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        line = self.env['roq.forecast.line'].create({
            'run_id': run.id,
            'product_id': product.id,
            'warehouse_id': warehouse.id,
        })
        self.assertEqual(line.run_id, run)

    def test_abc_history_links_to_run_and_product(self):
        run = self.env['roq.forecast.run'].create({})
        product = self.env['product.template'].create({'name': 'Test Template'})
        history = self.env['roq.abc.history'].create({
            'product_id': product.id,
            'run_id': run.id,
            'date': '2026-03-01',
            'tier_calculated': 'A',
            'tier_applied': 'A',
        })
        self.assertEqual(history.tier_applied, 'A')
