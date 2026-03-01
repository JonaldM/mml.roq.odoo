from odoo.tests.common import TransactionCase
from ..services.moq_enforcer import MoqEnforcer


class TestMoqEnforcer(TransactionCase):
    """
    Tests for MoqEnforcer.enforce().

    The service is stateless — no DB reads, pure logic on list-of-dicts.
    Tests use warehouse IDs for realism but the service only uses them as identifiers.
    """

    def setUp(self):
        super().setUp()
        warehouses = self.env['stock.warehouse'].search([])
        self.wh1_id = warehouses[0].id if warehouses else 1
        self.wh2_id = warehouses[1].id if len(warehouses) > 1 else 2

    def _lines(self, supplier_moq, lines_data):
        """Helper: inject supplier_moq into each line dict."""
        return [dict(d, supplier_moq=supplier_moq) for d in lines_data]

    # ── enforce=True ──────────────────────────────────────────────────────────

    def test_no_uplift_when_total_meets_moq(self):
        """Combined ROQ ≥ MOQ → no change, no flag."""
        lines = self._lines(100, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 60.0, 'weeks_of_cover_at_delivery': 8.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 50.0, 'weeks_of_cover_at_delivery': 10.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True)
        self.assertEqual(result[0]['moq_uplift_qty'], 0.0)
        self.assertEqual(result[1]['moq_uplift_qty'], 0.0)
        self.assertFalse(result[0]['moq_flag'])
        self.assertFalse(result[1]['moq_flag'])

    def test_uplift_goes_to_lowest_cover_warehouse(self):
        """Total=80, MOQ=100 → uplift=20 → wh1 (cover=4) gets all uplift, wh2 (cover=12) gets none."""
        lines = self._lines(100, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 40.0, 'weeks_of_cover_at_delivery': 4.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 40.0, 'weeks_of_cover_at_delivery': 12.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True)
        wh1 = next(r for r in result if r['warehouse_id'] == self.wh1_id)
        wh2 = next(r for r in result if r['warehouse_id'] == self.wh2_id)
        self.assertEqual(wh1['moq_uplift_qty'], 20.0)
        self.assertEqual(wh2['moq_uplift_qty'], 0.0)
        self.assertEqual(wh1['roq_pack_rounded'], 60.0)  # 40 + 20 uplift
        self.assertEqual(wh2['roq_pack_rounded'], 40.0)  # unchanged

    def test_all_lines_flagged_when_sku_below_moq(self):
        """moq_flag is set on ALL lines for the SKU, not just the one receiving uplift."""
        lines = self._lines(100, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 40.0, 'weeks_of_cover_at_delivery': 4.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 40.0, 'weeks_of_cover_at_delivery': 12.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True)
        self.assertTrue(result[0]['moq_flag'])
        self.assertTrue(result[1]['moq_flag'])

    def test_uplift_skips_warehouse_over_cover_cap(self):
        """wh1 at 30 wks (> cap 26) is skipped; wh2 at 8 wks absorbs the uplift."""
        lines = self._lines(200, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 10.0, 'weeks_of_cover_at_delivery': 30.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 10.0, 'weeks_of_cover_at_delivery': 8.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True, max_padding_weeks_cover=26)
        wh1 = next(r for r in result if r['warehouse_id'] == self.wh1_id)
        wh2 = next(r for r in result if r['warehouse_id'] == self.wh2_id)
        self.assertEqual(wh1['moq_uplift_qty'], 0.0)
        self.assertGreater(wh2['moq_uplift_qty'], 0.0)
        self.assertEqual(wh2['moq_uplift_qty'], 180.0)  # 200 - 20 total = 180 uplift

    def test_zero_moq_means_no_enforcement_no_flag(self):
        """supplier_moq = 0 → treated as 'no minimum', no flag, no uplift."""
        lines = self._lines(0, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 5.0, 'weeks_of_cover_at_delivery': 2.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True)
        self.assertEqual(result[0]['moq_uplift_qty'], 0.0)
        self.assertFalse(result[0]['moq_flag'])

    def test_safety_valve_all_warehouses_over_cap(self):
        """All warehouses over cover cap → uplift goes to tightest (no quantity silently lost)."""
        lines = self._lines(100, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 10.0, 'weeks_of_cover_at_delivery': 28.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 10.0, 'weeks_of_cover_at_delivery': 30.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=True, max_padding_weeks_cover=26)
        total_uplift = sum(r['moq_uplift_qty'] for r in result)
        total_roq = sum(r['roq_pack_rounded'] for r in result)
        self.assertEqual(total_uplift, 80.0)   # 100 MOQ - 20 total = 80 uplift
        self.assertEqual(total_roq, 100.0)     # Total raised to MOQ

    # ── enforce=False ─────────────────────────────────────────────────────────

    def test_flag_set_but_no_uplift_when_enforcement_disabled(self):
        """enforce=False: moq_flag is set, but quantities and moq_uplift_qty unchanged."""
        lines = self._lines(100, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 30.0, 'weeks_of_cover_at_delivery': 5.0},
            {'warehouse_id': self.wh2_id, 'roq_pack_rounded': 30.0, 'weeks_of_cover_at_delivery': 6.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=False)
        self.assertTrue(result[0]['moq_flag'])
        self.assertTrue(result[1]['moq_flag'])
        self.assertEqual(result[0]['moq_uplift_qty'], 0.0)
        self.assertEqual(result[1]['moq_uplift_qty'], 0.0)
        self.assertEqual(result[0]['roq_pack_rounded'], 30.0)  # Qty unchanged
        self.assertEqual(result[1]['roq_pack_rounded'], 30.0)

    def test_no_flag_when_above_moq_and_enforcement_disabled(self):
        """enforce=False, total ≥ MOQ → no flag, no change."""
        lines = self._lines(50, [
            {'warehouse_id': self.wh1_id, 'roq_pack_rounded': 60.0, 'weeks_of_cover_at_delivery': 8.0},
        ])
        result = MoqEnforcer.enforce(lines, enforce=False)
        self.assertFalse(result[0]['moq_flag'])
        self.assertEqual(result[0]['moq_uplift_qty'], 0.0)
