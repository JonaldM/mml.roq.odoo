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
