from datetime import date, datetime, timedelta
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


class TestRescheduleWrite(TransactionCase):

    def setUp(self):
        super().setUp()
        self.group = self.env['roq.shipment.group'].create({
            'origin_port': 'CNSHA',
            'destination_port': 'NZAKL',
            'container_type': '40HQ',
            'target_ship_date': date(2026, 6, 1),
            'target_delivery_date': date(2026, 7, 1),
            'state': 'draft',
        })

    def test_ship_date_shifts_proportionally_when_delivery_changes(self):
        """Shifting delivery by 7 days shifts ship date by 7 days too."""
        original_ship = self.group.target_ship_date
        self.group.write({'target_delivery_date': date(2026, 7, 8)})
        self.assertEqual(
            self.group.target_ship_date,
            original_ship + timedelta(days=7),
        )

    def test_chatter_message_posted_on_date_change(self):
        """A mail.message is posted recording the date change."""
        msg_count_before = len(self.group.message_ids)
        self.group.write({'target_delivery_date': date(2026, 7, 8)})
        self.assertGreater(len(self.group.message_ids), msg_count_before)

    def test_no_chatter_when_non_date_field_changes(self):
        """No date-change message when only notes change."""
        msg_count_before = len(self.group.message_ids)
        self.group.write({'notes': 'updated'})
        self.assertEqual(len(self.group.message_ids), msg_count_before)

    def test_locked_states_ignore_date_shift_logic(self):
        """Tendered/booked groups: write succeeds but no shift logic runs."""
        self.group.state = 'tendered'
        original_ship = self.group.target_ship_date
        self.group.write({'target_delivery_date': date(2026, 7, 15)})
        self.assertEqual(self.group.target_ship_date, original_ship)


class TestConsolidationSuggestion(TransactionCase):

    def setUp(self):
        super().setUp()
        self.group_a = self.env['roq.shipment.group'].create({
            'origin_port': 'CNSHA',
            'destination_port': 'NZAKL',
            'container_type': '40HQ',
            'target_ship_date': '2026-06-01',
            'target_delivery_date': '2026-07-01',
            'state': 'draft',
        })
        self.group_b = self.env['roq.shipment.group'].create({
            'origin_port': 'CNSHA',
            'destination_port': 'NZAKL',
            'container_type': '40HQ',
            'target_ship_date': '2026-06-10',
            'target_delivery_date': '2026-07-10',
            'state': 'draft',
        })

    def test_no_suggestion_when_same_port_but_far_apart(self):
        """Groups 60 days apart — no suggestion."""
        self.assertFalse(self.group_a._find_consolidation_candidates())

    def test_suggestion_when_groups_within_window(self):
        """group_a delivery 2026-07-08 is within 21 days of group_b delivery 2026-07-10."""
        self.group_a.target_delivery_date = date(2026, 7, 8)
        candidates = self.group_a._find_consolidation_candidates()
        self.assertIn(self.group_b, candidates)

    def test_no_suggestion_for_different_origin_port(self):
        """Groups near in time but different FOB port — no suggestion."""
        self.group_b.origin_port = 'CNNGB'
        self.group_a.target_delivery_date = date(2026, 7, 8)
        candidates = self.group_a._find_consolidation_candidates()
        self.assertFalse(candidates)

    def test_no_suggestion_for_locked_states(self):
        """Booked groups are not consolidation candidates."""
        self.group_b.state = 'booked'
        self.group_a.target_delivery_date = date(2026, 7, 8)
        candidates = self.group_a._find_consolidation_candidates()
        self.assertFalse(candidates)
