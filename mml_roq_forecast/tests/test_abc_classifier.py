from odoo.tests.common import TransactionCase
from ..services.abc_classifier import AbcClassifier


class TestAbcClassifier(TransactionCase):

    def setUp(self):
        super().setUp()
        self.classifier = AbcClassifier(self.env)

    def test_high_revenue_sku_is_tier_a(self):
        result = self._classify_with_revenues({'P1': 700, 'P2': 200, 'P3': 100})
        self.assertEqual(result['P1'], 'A')

    def test_mid_revenue_sku_is_tier_b(self):
        result = self._classify_with_revenues({'P1': 700, 'P2': 200, 'P3': 100})
        self.assertEqual(result['P2'], 'B')

    def test_low_revenue_sku_is_tier_c(self):
        result = self._classify_with_revenues({'P1': 700, 'P2': 200, 'P3': 100})
        self.assertEqual(result['P3'], 'C')

    def test_zero_revenue_sku_is_tier_d(self):
        result = self._classify_with_revenues({'P1': 700, 'P2': 0, 'P3': 100})
        self.assertEqual(result['P2'], 'D')

    def test_tier_override_enforces_minimum(self):
        assignments = {'P1': 700, 'P2': 200, 'P3': 50}
        overrides = {'P3': 'B'}
        result = self._classify_with_revenues(assignments, overrides=overrides)
        self.assertEqual(result['P3'], 'B')

    def test_override_cannot_lower_tier(self):
        assignments = {'P1': 700, 'P2': 200, 'P3': 100}
        overrides = {'P1': 'B'}
        result = self._classify_with_revenues(assignments, overrides=overrides)
        self.assertEqual(result['P1'], 'A')

    def test_dampener_holds_tier_for_4_runs(self):
        result = self.classifier.apply_dampener(
            current_tier='C',
            calculated_tier='B',
            weeks_in_pending=3,
            dampener_weeks=4,
        )
        self.assertEqual(result['applied_tier'], 'C')
        self.assertEqual(result['weeks_in_pending'], 4)

    def test_dampener_applies_on_4th_run(self):
        result = self.classifier.apply_dampener(
            current_tier='C',
            calculated_tier='B',
            weeks_in_pending=4,
            dampener_weeks=4,
        )
        self.assertEqual(result['applied_tier'], 'B')
        self.assertEqual(result['weeks_in_pending'], 0)

    def test_dampener_resets_if_tier_reverts(self):
        result = self.classifier.apply_dampener(
            current_tier='C',
            calculated_tier='C',
            weeks_in_pending=2,
            dampener_weeks=4,
        )
        self.assertEqual(result['applied_tier'], 'C')
        self.assertEqual(result['weeks_in_pending'], 0)

    def _classify_with_revenues(self, revenue_map, overrides=None):
        overrides = overrides or {}
        return self.classifier.classify_from_revenues(
            revenue_map,
            band_a_pct=70,
            band_b_pct=20,
            overrides=overrides,
        )
