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

    def test_group_line_run_id_stored_related(self):
        """run_id stored-related on line must equal the group's run_id."""
        run = self.env['roq.forecast.run'].create({})
        group = self.env['roq.shipment.group'].create({
            'run_id': run.id,
            'origin_port': 'CNSHA',
            'container_type': '40HQ',
        })
        supplier = self.env['res.partner'].create({'name': 'T1 Supplier', 'supplier_rank': 1})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': group.id,
            'supplier_id': supplier.id,
        })
        self.assertEqual(line.run_id, run)

    def test_group_line_container_type_related(self):
        """container_type on line reflects parent group container_type."""
        run = self.env['roq.forecast.run'].create({})
        group = self.env['roq.shipment.group'].create({
            'run_id': run.id,
            'origin_port': 'CNSHA',
            'container_type': '40HQ',
        })
        supplier = self.env['res.partner'].create({'name': 'T1b Supplier', 'supplier_rank': 1})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': group.id,
            'supplier_id': supplier.id,
        })
        self.assertEqual(line.container_type, '40HQ')

    def test_raise_po_wizard_raises_if_po_already_linked(self):
        """action_raise_po_wizard raises UserError when purchase_order_id already set."""
        from odoo.exceptions import UserError
        run = self.env['roq.forecast.run'].create({})
        group = self.env['roq.shipment.group'].create({
            'run_id': run.id, 'origin_port': 'CNSHA', 'container_type': '40HQ',
        })
        supplier = self.env['res.partner'].create({'name': 'T2 Supplier', 'supplier_rank': 1})
        existing_po = self.env['purchase.order'].create({'partner_id': supplier.id})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': group.id,
            'supplier_id': supplier.id,
            'purchase_order_id': existing_po.id,
        })
        with self.assertRaises(UserError):
            line.action_raise_po_wizard()

    def test_raise_po_wizard_raises_if_no_run(self):
        """action_raise_po_wizard raises UserError when group has no run_id."""
        from odoo.exceptions import UserError
        group = self.env['roq.shipment.group'].create({
            'origin_port': 'CNSHA', 'container_type': '40HQ',
        })
        supplier = self.env['res.partner'].create({'name': 'T3 Supplier', 'supplier_rank': 1})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': group.id,
            'supplier_id': supplier.id,
        })
        with self.assertRaises(UserError):
            line.action_raise_po_wizard()
