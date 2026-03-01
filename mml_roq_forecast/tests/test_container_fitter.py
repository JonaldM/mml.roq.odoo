from odoo.tests.common import TransactionCase
from ..services.container_fitter import ContainerFitter, CONTAINER_SPECS


class TestContainerFitter(TransactionCase):

    def setUp(self):
        super().setUp()
        self.fitter = ContainerFitter(lcl_threshold_pct=50, max_padding_weeks_cover=26)

    def test_lcl_recommended_below_threshold(self):
        # 10 CBM total — well below 50% of any container
        lines = [{'cbm': 10.0, 'roq': 100, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], 'LCL')

    def test_fcl_recommended_above_threshold(self):
        # 15 CBM — above 50% of 20GP (25 CBM)
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertIn(result['container_type'], ['20GP', '40GP', '40HQ'])

    def test_selects_smallest_feasible_container(self):
        # 15 CBM → should choose 20GP (25 CBM) not 40GP
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], '20GP')

    def test_padding_added_for_remaining_capacity(self):
        # 15 CBM in 25 CBM container → 10 CBM padding available
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertGreater(result['total_padding_units'], 0)

    def test_no_padding_when_sku_over_max_cover(self):
        # SKU already has 30 weeks cover — should not receive padding (max=26)
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'B',
                  'weeks_cover': 30.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        # Padding goes to other SKUs — since only 1 SKU here and it's over max, no padding
        self.assertEqual(result['line_results'][0]['padding_units'], 0)

    def test_unassigned_when_cbm_per_unit_missing(self):
        lines = [{'cbm': 0.0, 'roq': 100, 'cbm_per_unit': 0.0, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], 'unassigned')
