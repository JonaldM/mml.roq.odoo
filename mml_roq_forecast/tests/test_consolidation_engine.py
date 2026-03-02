from odoo.tests.common import TransactionCase
from ..services.consolidation_engine import ConsolidationEngine


class TestConsolidationEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.engine = ConsolidationEngine(self.env)
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)

        # Two suppliers at same FOB port
        self.supplier_a = self.env['res.partner'].create({
            'name': 'Supplier A', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.supplier_b = self.env['res.partner'].create({
            'name': 'Supplier B', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        # Supplier at different port
        self.supplier_c = self.env['res.partner'].create({
            'name': 'Supplier C', 'supplier_rank': 1, 'fob_port': 'CNNGB',
        })

    def _make_run_with_lines(self, supplier_cbm_map):
        """Helper: create a fake completed ROQ run with lines for given suppliers."""
        run = self.env['roq.forecast.run'].create({'status': 'complete'})
        product = self.env['product.product'].create({'name': 'Test CON SKU', 'type': 'product'})

        for supplier, (cbm_per_unit, roq_qty, proj_inv) in supplier_cbm_map.items():
            self.env['roq.forecast.line'].create({
                'run_id': run.id,
                'product_id': product.id,
                'warehouse_id': self.warehouse.id,
                'supplier_id': supplier.id,
                'abc_tier': 'B',
                'cbm_per_unit': cbm_per_unit,
                'cbm_total': cbm_per_unit * roq_qty,
                'roq_containerized': roq_qty,
                'projected_inventory_at_delivery': proj_inv,
                'weeks_of_cover_at_delivery': proj_inv / 10.0 if proj_inv > 0 else -1,
                'container_type': '20GP',
            })
        return run

    def test_groups_suppliers_by_fob_port(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
            self.supplier_b: (0.05, 100, 60.0),
            self.supplier_c: (0.05, 100, 70.0),
        })
        groups = self.engine.group_by_fob_port(run)
        self.assertIn('CNSHA', groups)
        self.assertIn('CNNGB', groups)
        self.assertEqual(len(groups['CNSHA']), 2)  # supplier_a + supplier_b
        self.assertEqual(len(groups['CNNGB']), 1)  # supplier_c

    def test_creates_shipment_group_for_multi_supplier_port(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 200, 50.0),
            self.supplier_b: (0.05, 200, 60.0),
        })
        self.engine.create_reactive_shipment_groups(run)
        groups = self.env['roq.shipment.group'].search([('run_id', '=', run.id)])
        self.assertGreater(len(groups), 0)
        sha_group = groups.filtered(lambda g: g.fob_port == 'CNSHA')
        self.assertTrue(sha_group)

    def test_oos_risk_flag_set_when_any_item_oos(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, -10.0),  # OOS
            self.supplier_b: (0.05, 100, 50.0),   # OK
        })
        self.engine.create_reactive_shipment_groups(run)
        sha_group = self.env['roq.shipment.group'].search([
            ('run_id', '=', run.id),
            ('fob_port', '=', 'CNSHA'),
        ], limit=1)
        if sha_group:
            oos_lines = sha_group.line_ids.filtered(
                lambda l: l.supplier_id == self.supplier_a
            )
            self.assertTrue(oos_lines[0].oos_risk_flag)

    def test_no_push_when_supplier_has_oos_item(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, -10.0),  # OOS — cannot push
            self.supplier_b: (0.05, 100, 150.0),  # 15 weeks cover
        })
        self.engine.create_reactive_shipment_groups(run)
        sha_group = self.env['roq.shipment.group'].search([
            ('run_id', '=', run.id), ('fob_port', '=', 'CNSHA'),
        ], limit=1)
        if sha_group:
            a_line = sha_group.line_ids.filtered(lambda l: l.supplier_id == self.supplier_a)
            if a_line:
                self.assertEqual(a_line[0].push_pull_days, 0)

    def test_free_days_at_origin_stored_on_group_line(self):
        """free_days_at_origin from supplier propagates to shipment group line."""
        self.supplier_a.write({'free_days_at_origin': 14})
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
        })
        groups = self.engine.create_reactive_shipment_groups(run)
        self.assertTrue(groups)
        line = groups[0].line_ids[0]
        self.assertEqual(line.free_days_at_origin, 14)

    def test_free_days_zero_stored_when_supplier_has_none(self):
        """Supplier with no free days → shipment group line has 0."""
        self.supplier_a.write({'free_days_at_origin': 0})
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
        })
        groups = self.engine.create_reactive_shipment_groups(run)
        self.assertTrue(groups)
        line = groups[0].line_ids[0]
        self.assertEqual(line.free_days_at_origin, 0)

    def test_push_reason_includes_free_days_annotation(self):
        """When free_days_at_origin > 0, reason string includes annotation."""
        self.supplier_a.write({'free_days_at_origin': 14})
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
        })
        groups = self.engine.create_reactive_shipment_groups(run)
        line = groups[0].line_ids[0]
        self.assertIn('free origin', line.push_pull_reason)

    def test_push_reason_no_annotation_when_zero_free_days(self):
        """When free_days_at_origin = 0, reason string has no annotation."""
        self.supplier_a.write({'free_days_at_origin': 0})
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
        })
        groups = self.engine.create_reactive_shipment_groups(run)
        line = groups[0].line_ids[0]
        self.assertNotIn('free origin', line.push_pull_reason)
