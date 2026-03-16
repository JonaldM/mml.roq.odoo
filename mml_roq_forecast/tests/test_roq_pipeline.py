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

    def test_multiwarehouse_container_fitting_no_key_collision(self):
        """
        C2 regression test: same product in 2 warehouses must each receive
        their own correct roq_containerized value from container fitting.
        Before the fix, result_by_pid keyed on product_id alone meant the
        second warehouse's result silently overwrote the first, so the first
        warehouse always got the value intended for the second.
        """
        # Create a second warehouse distinct from setUp's warehouse
        wh2 = self.env['stock.warehouse'].create({
            'name': 'Second Warehouse',
            'code': 'WH2',
        })
        wh2.is_active_for_roq = True

        # Create a product with a non-D tier so it goes through container fitting.
        # Force tier A so it won't be classified as Dormant even with no sales history.
        pt = self.env['product.template'].create({
            'name': 'Multi-WH Container Test SKU',
            'type': 'product',
            'is_roq_managed': True,
            'cbm_per_unit': 0.10,
            'pack_size': 12,
            'abc_tier': 'A',
            'abc_tier_override': 'A',
        })
        product = pt.product_variant_ids[0]

        supplier = self.env['res.partner'].create({
            'name': 'Multi-WH Supplier', 'supplier_rank': 1,
        })
        self.env['product.supplierinfo'].create({
            'partner_id': supplier.id,
            'product_tmpl_id': pt.id,
            'price': 10.0,
        })

        # Seed distinct demand history for WH1 and WH2 so their ROQ values differ.
        # We create sale.order.line records so DemandHistoryService sees real demand.
        from datetime import timedelta
        from odoo.fields import Datetime as OdooDatetime

        wh1 = self.warehouse

        def _make_sale(wh, qty, days_ago):
            partner = self.env['res.partner'].create({'name': 'Test Customer'})
            order = self.env['sale.order'].create({
                'partner_id': partner.id,
                'warehouse_id': wh.id,
                'date_order': OdooDatetime.now() - timedelta(days=days_ago),
                'state': 'sale',
            })
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': product.id,
                'product_uom_qty': qty,
                'price_unit': 10.0,
            })

        # WH1: higher demand, WH2: lower demand → different roq_pack_rounded
        for i in range(1, 14):
            _make_sale(wh1, 100, i * 7)   # 100 units/week at WH1
            _make_sale(wh2, 10, i * 7)    # 10 units/week at WH2

        run = self.env['roq.forecast.run'].create({})
        run.action_run()

        wh1_line = run.line_ids.filtered(
            lambda l: l.product_id == product and l.warehouse_id == wh1
        )
        wh2_line = run.line_ids.filtered(
            lambda l: l.product_id == product and l.warehouse_id == wh2
        )

        self.assertEqual(len(wh1_line), 1, "Expected exactly one forecast line for WH1")
        self.assertEqual(len(wh2_line), 1, "Expected exactly one forecast line for WH2")

        # Both lines must have a valid container_type (not False/None)
        self.assertTrue(
            wh1_line.container_type,
            "WH1 line container_type must not be falsy after container fitting",
        )
        self.assertTrue(
            wh2_line.container_type,
            "WH2 line container_type must not be falsy after container fitting",
        )

        # The key assertion: with different demand histories the roq_containerized
        # values must differ (WH1 should be substantially larger than WH2).
        # If C2 key collision were present, both would carry the same value.
        self.assertNotEqual(
            wh1_line.roq_containerized,
            wh2_line.roq_containerized,
            "WH1 and WH2 must have distinct roq_containerized values; "
            "equal values indicate the multi-warehouse key collision (C2) is still present.",
        )


import unittest


class TestRoqPipelineCrostonImport(unittest.TestCase):
    """Verifies forecast_croston_sba is imported in roq_pipeline (wiring smoke test)."""

    def test_forecast_croston_sba_importable_from_pipeline(self):
        from ..services import roq_pipeline
        self.assertTrue(
            hasattr(roq_pipeline, 'forecast_croston_sba'),
            "forecast_croston_sba must be importable from roq_pipeline after Task 6",
        )
