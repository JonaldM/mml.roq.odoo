from datetime import date, timedelta

from odoo import exceptions
from odoo.tests.common import TransactionCase


class TestShipmentWorkflow(TransactionCase):

    def setUp(self):
        super().setUp()
        self.sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shenzhen, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '40GP',
        })

    def test_confirm_transitions_to_confirmed_state(self):
        """
        action_confirm must move state to 'confirmed' regardless of whether
        mml_freight is installed.  The service locator returns NullService when
        freight is absent, so no tender is created — but state still advances.
        """
        self.sg.action_confirm()
        self.assertEqual(self.sg.state, 'confirmed')

    def test_confirm_posts_chatter_message(self):
        """action_confirm must post a chatter message (audit trail)."""
        self.sg.action_confirm()
        messages = self.sg.message_ids.filtered(lambda m: m.message_type == 'comment')
        self.assertTrue(messages, 'Expected at least one chatter comment after confirm.')

    def test_cancel_from_draft_works(self):
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')

    def test_cancel_from_confirmed_works(self):
        self.sg.action_confirm()
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')

    def test_confirm_does_not_emit_tender_event(self):
        """action_confirm must NOT emit roq.shipment_group.confirmed anymore —
        tender creation is deferred to action_create_tender."""
        before = self.env['mml.event'].search_count([
            ('event_type', '=', 'roq.shipment_group.confirmed'),
            ('res_id', '=', self.sg.id),
        ])
        self.sg.action_confirm()
        after = self.env['mml.event'].search_count([
            ('event_type', '=', 'roq.shipment_group.confirmed'),
            ('res_id', '=', self.sg.id),
        ])
        self.assertEqual(before, after, 'action_confirm must not emit tender event')

    def test_create_tender_requires_confirmed_state(self):
        """action_create_tender must raise on draft groups."""
        with self.assertRaises(exceptions.UserError):
            self.sg.action_create_tender()

    def test_create_tender_blocked_outside_horizon(self):
        """action_create_tender must raise when ship date is beyond the horizon."""
        self.env['ir.config_parameter'].sudo().set_param('roq.tender.horizon_days', '45')
        self.sg.action_confirm()
        self.sg.target_ship_date = date.today() + timedelta(days=60)
        with self.assertRaises(exceptions.UserError):
            self.sg.action_create_tender()

    def test_create_tender_allowed_within_horizon(self):
        """action_create_tender must succeed when ship date is within the horizon."""
        self.env['ir.config_parameter'].sudo().set_param('roq.tender.horizon_days', '45')
        self.sg.action_confirm()
        self.sg.target_ship_date = date.today() + timedelta(days=30)
        # Does not raise — tender event emitted
        self.sg.action_create_tender()

    def test_create_tender_allowed_with_no_ship_date(self):
        """If target_ship_date is unset the horizon check is skipped (fail-open)."""
        self.sg.action_confirm()
        self.sg.target_ship_date = False
        self.sg.action_create_tender()  # must not raise

    def test_confirm_with_no_run_posts_note(self):
        """When no run_id is linked, confirm posts a chatter note and skips PO raising."""
        self.sg.action_confirm()
        self.assertEqual(self.sg.state, 'confirmed')
        body_texts = self.sg.message_ids.mapped('body')
        combined = ' '.join(body_texts)
        self.assertIn('No ROQ run', combined)

    def test_auto_raise_pos_skips_lines_with_existing_po(self):
        """_auto_raise_pos must not create a second PO when purchase_order_id is already set."""
        # Create a minimal existing PO
        supplier = self.env['res.partner'].create({'name': 'Test Supplier', 'supplier_rank': 1})
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        run = self.env['roq.forecast.run'].create({'name': 'ROQ-TEST-001'})
        existing_po = self.env['purchase.order'].create({
            'partner_id': supplier.id,
            'picking_type_id': warehouse.in_type_id.id,
        })
        sg_line = self.env['roq.shipment.group.line'].create({
            'group_id': self.sg.id,
            'supplier_id': supplier.id,
            'purchase_order_id': existing_po.id,
        })
        self.sg.run_id = run
        # Confirm should not create another PO for this line
        po_count_before = self.env['purchase.order'].search_count([])
        self.sg.action_confirm()
        po_count_after = self.env['purchase.order'].search_count([])
        self.assertEqual(po_count_before, po_count_after, 'No new PO should be created for line with existing PO')
