from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestRaisePoWizard(TransactionCase):
    """Tests for roq.raise.po.wizard — PO creation logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({
            'name': 'Test Wizard Supplier', 'supplier_rank': 1,
        })
        cls.wh1 = cls.env['stock.warehouse'].search([], limit=1)
        cls.wh2 = cls.env['stock.warehouse'].search([('id', '!=', cls.wh1.id)], limit=1)
        cls.product1 = cls.env['product.product'].create({
            'name': 'Widget A', 'type': 'product',
        })
        cls.product2 = cls.env['product.product'].create({
            'name': 'Widget B', 'type': 'product',
        })
        cls.run = cls.env['roq.forecast.run'].create({})

    def _make_wizard(self, lines, use_containerized=True):
        """Helper: create wizard with given line dicts, auto-setting qty_to_order."""
        line_vals = []
        for d in lines:
            line_d = dict(d)
            if 'qty_to_order' not in line_d:
                line_d['qty_to_order'] = (
                    line_d['qty_containerized'] if use_containerized
                    else line_d['qty_pack_rounded']
                )
            line_vals.append((0, 0, line_d))
        return self.env['roq.raise.po.wizard'].create({
            'run_id': self.run.id,
            'supplier_id': self.supplier.id,
            'use_containerized': use_containerized,
            'line_ids': line_vals,
        })

    def test_raises_one_po_per_warehouse(self):
        """One draft PO created per distinct destination warehouse."""
        if not self.wh2:
            self.skipTest('Only one warehouse configured — skipping multi-warehouse test')
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80},
            {'product_id': self.product2.id, 'warehouse_id': self.wh2.id,
             'qty_containerized': 60, 'qty_pack_rounded': 50},
        ])
        wizard.action_raise_pos()
        pos = self.env['purchase.order'].search([('partner_id', '=', self.supplier.id)])
        self.assertEqual(len(pos), 2)

    def test_po_lines_have_correct_qty(self):
        """PO line qty equals qty_to_order from wizard line."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search(
            [('partner_id', '=', self.supplier.id)], limit=1,
        )
        self.assertEqual(po.order_line[0].product_qty, 100)

    def test_pos_created_in_draft_state(self):
        """Raised POs must be in draft state."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 50, 'qty_pack_rounded': 40},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search(
            [('partner_id', '=', self.supplier.id)], limit=1,
        )
        self.assertEqual(po.state, 'draft')

    def test_toggle_off_uses_pack_rounded_qty(self):
        """Creating wizard with use_containerized=False sets qty_to_order to pack rounded."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80},
        ], use_containerized=False)
        self.assertEqual(wizard.line_ids[0].qty_to_order, 80)

    def test_zero_qty_lines_skipped(self):
        """Lines with qty_to_order == 0 do not produce PO lines."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 0, 'qty_pack_rounded': 0, 'qty_to_order': 0},
            {'product_id': self.product2.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 50, 'qty_pack_rounded': 40},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search(
            [('partner_id', '=', self.supplier.id)], limit=1,
        )
        self.assertEqual(len(po.order_line), 1)

    def test_all_zero_qty_raises_user_error(self):
        """UserError raised if all lines have qty_to_order == 0."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 0, 'qty_pack_rounded': 0, 'qty_to_order': 0},
        ])
        with self.assertRaises(UserError):
            wizard.action_raise_pos()
