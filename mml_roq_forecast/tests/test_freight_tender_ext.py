from odoo.tests.common import TransactionCase


class TestFreightTenderExt(TransactionCase):

    def test_freight_tender_has_shipment_group_id_field(self):
        self.assertIn('shipment_group_id', self.env['freight.tender']._fields)

    def test_shipment_group_id_is_many2one_to_roq_shipment_group(self):
        field = self.env['freight.tender']._fields['shipment_group_id']
        self.assertEqual(field.comodel_name, 'roq.shipment.group')

    def test_freight_tender_ext_does_not_break_tender_creation(self):
        # Basic sanity: freight.tender still creates normally
        tender = self.env['freight.tender'].create({
            'origin_port': 'Shenzhen, CN',
            'dest_port': 'Auckland, NZ',
        })
        self.assertTrue(tender.id)
        self.assertFalse(tender.shipment_group_id)
