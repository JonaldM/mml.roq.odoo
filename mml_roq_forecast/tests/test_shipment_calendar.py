from unittest.mock import MagicMock, patch

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


class TestFreightStatusFields(TransactionCase):

    def setUp(self):
        super().setUp()
        self.group = self.env['roq.shipment.group'].create({
            'origin_port': 'CNSHA',
            'destination_port': 'NZAKL',
            'container_type': '40HQ',
            'target_ship_date': '2026-06-01',
            'target_delivery_date': '2026-07-01',
        })

    def test_freight_fields_empty_when_service_returns_none(self):
        """NullService returns None — fields stay empty."""
        null_svc = MagicMock()
        null_svc.get_booking_status.return_value = None
        with patch.object(
            type(self.group.env['mml.registry']), 'service', return_value=null_svc
        ):
            self.group._compute_freight_status()
        self.assertFalse(self.group.freight_eta)
        self.assertFalse(self.group.freight_status)

    def test_freight_fields_populated_when_booking_exists(self):
        """Real FreightService returns booking data."""
        from datetime import datetime
        eta = datetime(2026, 7, 1, 10, 0, 0)
        mock_svc = MagicMock()
        mock_svc.get_booking_status.return_value = {
            'eta': eta,
            'status': 'in_transit',
            'last_update': eta,
        }
        with patch.object(
            type(self.group.env['mml.registry']), 'service', return_value=mock_svc
        ):
            self.group._compute_freight_status()
        self.assertEqual(self.group.freight_eta, eta)
        self.assertEqual(self.group.freight_status, 'in_transit')
