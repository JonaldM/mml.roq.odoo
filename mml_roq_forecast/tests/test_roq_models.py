from odoo.tests.common import TransactionCase


class TestRoqForecastRunRelations(TransactionCase):
    """Tests for computed/relational fields on roq.forecast.run."""

    def test_shipment_group_ids_returns_groups_for_run(self):
        """shipment_group_ids must return all groups linked to this run."""
        run = self.env['roq.forecast.run'].create({})
        other_run = self.env['roq.forecast.run'].create({})
        group = self.env['roq.shipment.group'].create({
            'run_id': run.id,
            'origin_port': 'CNSHA',
            'container_type': '40HQ',
        })
        other_group = self.env['roq.shipment.group'].create({
            'run_id': other_run.id,
            'origin_port': 'CNSHA',
            'container_type': 'LCL',
        })
        self.assertIn(group, run.shipment_group_ids)
        self.assertNotIn(other_group, run.shipment_group_ids)

    def test_supplier_order_line_ids_returns_lines_for_run(self):
        """supplier_order_line_ids must return all group lines belonging to this run."""
        run = self.env['roq.forecast.run'].create({})
        other_run = self.env['roq.forecast.run'].create({})
        group = self.env['roq.shipment.group'].create({
            'run_id': run.id,
            'origin_port': 'CNSHA',
            'container_type': '40HQ',
        })
        other_group = self.env['roq.shipment.group'].create({
            'run_id': other_run.id,
            'origin_port': 'CNSHA',
            'container_type': 'LCL',
        })
        supplier = self.env['res.partner'].create({'name': 'T2 Supplier', 'supplier_rank': 1})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': group.id,
            'supplier_id': supplier.id,
        })
        other_line = self.env['roq.shipment.group.line'].create({
            'group_id': other_group.id,
            'supplier_id': supplier.id,
        })
        self.assertIn(line, run.supplier_order_line_ids)
        self.assertNotIn(other_line, run.supplier_order_line_ids)


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
