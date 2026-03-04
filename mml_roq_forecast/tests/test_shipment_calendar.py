from odoo.tests.common import TransactionCase


class TestWarehouseReceivingCapacityFields(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].create({
            'name': 'Hamilton DC',
            'code': 'HLZ',
        })

    def test_default_capacity_unit_is_cbm(self):
        self.assertEqual(self.warehouse.roq_capacity_unit, 'cbm')

    def test_set_cbm_capacity(self):
        self.warehouse.roq_weekly_capacity_cbm = 120.0
        self.assertEqual(self.warehouse.roq_weekly_capacity_cbm, 120.0)

    def test_set_teu_capacity(self):
        self.warehouse.roq_weekly_capacity_teu = 4.0
        self.assertEqual(self.warehouse.roq_weekly_capacity_teu, 4.0)

    def test_set_capacity_unit_to_teu(self):
        self.warehouse.roq_capacity_unit = 'teu'
        self.assertEqual(self.warehouse.roq_capacity_unit, 'teu')
