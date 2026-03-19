from odoo.tests.common import TransactionCase
from ..services.inventory_query import InventoryQueryService


class TestInventoryQuery(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.product = self.env['product.product'].create({
            'name': 'Test INV SKU', 'type': 'product',
        })

    def test_soh_zero_for_new_product(self):
        svc = InventoryQueryService(self.env)
        soh = svc.get_soh(self.product, self.warehouse)
        self.assertEqual(soh, 0.0)

    def test_confirmed_po_qty_zero_for_no_pos(self):
        svc = InventoryQueryService(self.env)
        qty = svc.get_confirmed_po_qty(self.product, self.warehouse)
        self.assertEqual(qty, 0.0)

    def test_inventory_position_is_soh_plus_po(self):
        svc = InventoryQueryService(self.env)
        soh = svc.get_soh(self.product, self.warehouse)
        po_qty = svc.get_confirmed_po_qty(self.product, self.warehouse)
        pos = svc.get_inventory_position(self.product, self.warehouse)
        self.assertAlmostEqual(pos, soh + po_qty, places=3)


"""
Tests for cache-aware paths in InventoryQueryService.
"""
from unittest.mock import MagicMock
from mml_roq_forecast.services.inventory_query import InventoryQueryService
from mml_roq_forecast.services.pipeline_data_cache import PipelineDataCache


class TestInventoryQueryServiceCachePath:

    def test_get_soh_returns_cached_value(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.soh = {(101, 1): 75.0}
        cache.internal_locations = {1: [10, 11]}

        inv = InventoryQueryService(MagicMock(), cache=cache)
        assert inv.get_soh(product, warehouse) == 75.0

    def test_get_soh_returns_zero_for_missing_key(self):
        product = MagicMock()
        product.id = 999
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.soh = {}

        inv = InventoryQueryService(MagicMock(), cache=cache)
        assert inv.get_soh(product, warehouse) == 0.0

    def test_get_soh_does_not_call_orm_when_cache_present(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.soh = {(101, 1): 10.0}

        env = MagicMock()
        inv = InventoryQueryService(env, cache=cache)
        inv.get_soh(product, warehouse)

        env['stock.quant'].search.assert_not_called()

    def test_get_confirmed_po_qty_returns_cached_value(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.po_qty = {(101, 1): 200.0}

        inv = InventoryQueryService(MagicMock(), cache=cache)
        assert inv.get_confirmed_po_qty(product, warehouse) == 200.0

    def test_get_confirmed_po_qty_does_not_call_orm_when_cache_present(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.po_qty = {(101, 1): 50.0}

        env = MagicMock()
        inv = InventoryQueryService(env, cache=cache)
        inv.get_confirmed_po_qty(product, warehouse)

        env['purchase.order.line'].search.assert_not_called()

    def test_get_inventory_position_sums_soh_and_po(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        cache = MagicMock(spec=PipelineDataCache)
        cache.soh = {(101, 1): 60.0}
        cache.po_qty = {(101, 1): 40.0}

        inv = InventoryQueryService(MagicMock(), cache=cache)
        assert inv.get_inventory_position(product, warehouse) == 100.0

    def test_no_cache_uses_orm_for_soh(self):
        product = MagicMock()
        product.id = 101
        warehouse = MagicMock()
        warehouse.id = 1

        env = MagicMock()
        locs = MagicMock()
        locs.ids = [10]
        locs.__bool__ = lambda self: True
        env['stock.location'].search.return_value = locs
        env['stock.quant'].search.return_value = []

        inv = InventoryQueryService(env, cache=None)
        inv.get_soh(product, warehouse)

        env['stock.quant'].search.assert_called_once()
