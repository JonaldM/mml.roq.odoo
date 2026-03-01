from odoo.tests.common import TransactionCase


class TestShipmentGroup(TransactionCase):

    def test_shipment_group_creates_with_sequence(self):
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shenzhen, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '40GP',
        })
        self.assertTrue(sg.name.startswith('SG-'))
        self.assertEqual(sg.state, 'draft')

    def test_container_type_values_match_contract(self):
        """Contract specifies 20GP/40GP/40HQ/LCL — not 20ft/40ft etc."""
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Ningbo, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '20GP',
        })
        self.assertEqual(sg.container_type, '20GP')

    def test_shipment_group_line_links_to_group(self):
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shanghai, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': 'LCL',
        })
        supplier = self.env['res.partner'].create({'name': 'Test Supplier'})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': sg.id,
            'supplier_id': supplier.id,
            'cbm': 15.5,
        })
        self.assertEqual(line.group_id, sg)

    def test_actual_delivery_date_field_exists(self):
        """Required for freight feedback per interface contract §4."""
        self.assertIn('actual_delivery_date', self.env['roq.shipment.group']._fields)

    def test_po_ids_field_exists(self):
        """Required: freight.tender is created with po_ids from this field."""
        self.assertIn('po_ids', self.env['roq.shipment.group']._fields)
