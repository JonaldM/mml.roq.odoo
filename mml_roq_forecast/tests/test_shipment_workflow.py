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
