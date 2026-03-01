from odoo.tests.common import TransactionCase


class TestShipmentWorkflow(TransactionCase):

    def setUp(self):
        super().setUp()
        self.sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shenzhen, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '40GP',
        })

    def test_confirm_creates_freight_tender(self):
        """Per contract §3: action_confirm creates freight.tender and sets state=confirmed."""
        self.sg.action_confirm()
        self.assertEqual(self.sg.state, 'confirmed')
        self.assertTrue(self.sg.freight_tender_id)

    def test_freight_tender_has_correct_ports(self):
        self.sg.action_confirm()
        tender = self.sg.freight_tender_id
        self.assertEqual(tender.origin_port, 'Shenzhen, CN')

    def test_cancel_from_draft_works(self):
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')

    def test_cancel_from_confirmed_works(self):
        self.sg.action_confirm()
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')
