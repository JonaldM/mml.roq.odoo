"""
ROQ edge-case tests: zero demand, never-sold SKUs, pathological container fill.

Pure Python — no Odoo runtime needed.
Run with:  pytest mml.roq.model/mml_roq_forecast/tests/test_edge_cases.py -v
"""
import pytest


class TestZeroDemand:
    """forecast_methods module functions with all-zero weekly sales history."""

    def test_sma_on_all_zeros_returns_zero(self):
        from mml_roq_forecast.services.forecast_methods import forecast_sma
        result = forecast_sma([0.0] * 52, window=12)
        assert result == 0.0

    def test_sma_on_empty_history_returns_zero(self):
        from mml_roq_forecast.services.forecast_methods import forecast_sma
        result = forecast_sma([], window=12)
        assert result == 0.0

    def test_ewma_on_all_zeros_returns_zero(self):
        from mml_roq_forecast.services.forecast_methods import forecast_ewma
        result = forecast_ewma([0.0] * 52, span=26)
        assert result == 0.0

    def test_ewma_on_empty_history_returns_zero(self):
        from mml_roq_forecast.services.forecast_methods import forecast_ewma
        result = forecast_ewma([], span=26)
        assert result == 0.0

    def test_holt_winters_on_all_zeros_returns_nonnegative(self):
        """Holt-Winters on a flat-zero series must not raise and must return >= 0."""
        from mml_roq_forecast.services.forecast_methods import forecast_holt_winters
        result = forecast_holt_winters([0.0] * 104)  # 2 full seasonal cycles required
        assert result >= 0.0

    def test_holt_winters_falls_back_to_sma_with_insufficient_data(self):
        """Fewer than 2 × seasonal_period points must not raise — falls back to SMA."""
        from mml_roq_forecast.services.forecast_methods import forecast_holt_winters
        result = forecast_holt_winters([5.0] * 10)  # Too few for HW
        assert isinstance(result, float)
        assert result >= 0.0


class TestSafetyStock:
    """calculate_safety_stock edge cases."""

    def test_zero_z_score_tier_d_returns_zero(self):
        """Tier D z-score is 0 — safety stock must be 0 regardless of sigma."""
        from mml_roq_forecast.services.safety_stock import calculate_safety_stock
        result = calculate_safety_stock(z_score=0.0, sigma=10.0, lt_weeks=4.0)
        assert result == 0.0

    def test_zero_sigma_returns_zero(self):
        """Zero std dev of demand (perfectly flat demand) → zero safety stock."""
        from mml_roq_forecast.services.safety_stock import calculate_safety_stock
        result = calculate_safety_stock(z_score=1.645, sigma=0.0, lt_weeks=4.0)
        assert result == 0.0

    def test_zero_lead_time_returns_zero(self):
        """Zero lead time → zero safety stock (nothing to buffer)."""
        from mml_roq_forecast.services.safety_stock import calculate_safety_stock
        result = calculate_safety_stock(z_score=1.645, sigma=5.0, lt_weeks=0.0)
        assert result == 0.0

    def test_normal_inputs_return_positive(self):
        """Standard inputs must produce a positive safety stock."""
        from mml_roq_forecast.services.safety_stock import calculate_safety_stock
        result = calculate_safety_stock(z_score=1.645, sigma=5.0, lt_weeks=4.0)
        assert result > 0.0


class TestNeverSoldSKU:
    """AbcClassifier when a SKU has zero revenue contribution."""

    def test_zero_revenue_sku_classified_as_d(self):
        """A SKU with zero revenue must map to D tier."""
        from mml_roq_forecast.services.abc_classifier import AbcClassifier
        from unittest.mock import MagicMock
        classifier = AbcClassifier(env=MagicMock())
        result = classifier.classify_from_revenues({"sku_001": 0.0})
        assert result.get("sku_001") == "D"

    def test_zero_total_revenue_does_not_divide_by_zero(self):
        """If ALL SKUs have zero revenue, must not raise ZeroDivisionError."""
        from mml_roq_forecast.services.abc_classifier import AbcClassifier
        from unittest.mock import MagicMock
        classifier = AbcClassifier(env=MagicMock())
        result = classifier.classify_from_revenues({"sku_001": 0.0, "sku_002": 0.0})
        assert all(v in ("A", "B", "C", "D") for v in result.values())

    def test_single_sku_with_revenue_classifies_as_a(self):
        """A single SKU with all the revenue → 100% contribution → A tier."""
        from mml_roq_forecast.services.abc_classifier import AbcClassifier
        from unittest.mock import MagicMock
        classifier = AbcClassifier(env=MagicMock())
        result = classifier.classify_from_revenues({"sku_001": 1000.0})
        assert result.get("sku_001") == "A"


class TestContainerFitter:
    """ContainerFitter.fit() edge cases."""

    def _make_line(self, product_id=1, cbm=5.0, roq=100, cbm_per_unit=0.05,
                   tier="B", weeks_cover=8.0):
        return {
            "product_id": product_id,
            "cbm": cbm,
            "roq": roq,
            "cbm_per_unit": cbm_per_unit,
            "tier": tier,
            "weeks_cover": weeks_cover,
        }

    def test_single_small_sku_is_lcl(self):
        """
        A single SKU with 5 CBM total is below the 50% LCL threshold
        for the smallest container (20GP = 25 CBM, threshold = 12.5 CBM). Must return LCL.
        """
        from mml_roq_forecast.services.container_fitter import ContainerFitter
        fitter = ContainerFitter(lcl_threshold_pct=50)
        result = fitter.fit([self._make_line(cbm=5.0)])
        assert result["container_type"] == "LCL", (
            "5 CBM is below 50% of 20GP (12.5 CBM threshold) — expected LCL"
        )

    def test_missing_cbm_per_unit_returns_unassigned(self):
        """Lines with cbm_per_unit <= 0 must return 'unassigned', not raise."""
        from mml_roq_forecast.services.container_fitter import ContainerFitter
        fitter = ContainerFitter(lcl_threshold_pct=50)
        line = self._make_line(cbm=30.0, cbm_per_unit=0.0)
        result = fitter.fit([line])
        assert result["container_type"] == "unassigned"

    def test_fill_pct_is_between_zero_and_one(self):
        """fill_pct in fit() result must always be in [0.0, 1.0]."""
        from mml_roq_forecast.services.container_fitter import ContainerFitter
        fitter = ContainerFitter(lcl_threshold_pct=50)
        line = self._make_line(cbm=20.0, cbm_per_unit=0.2, roq=100)
        result = fitter.fit([line])
        assert 0.0 <= result["fill_pct"] <= 1.0

    def test_large_shipment_selects_largest_container(self):
        """60 CBM must be assigned to 40HQ (67.5 CBM), not 40GP (55 CBM)."""
        from mml_roq_forecast.services.container_fitter import ContainerFitter
        fitter = ContainerFitter(lcl_threshold_pct=50)
        line = self._make_line(cbm=60.0, cbm_per_unit=0.3, roq=200)
        result = fitter.fit([line])
        assert result["container_type"] == "40HQ", (
            "60 CBM exceeds 40GP (55 CBM) — must select 40HQ (67.5 CBM)"
        )


class TestNegativeDemand:
    """Guard against returns/credits producing negative demand in forecasts."""

    def test_sma_with_negative_history_returns_nonnegative(self):
        """SMA on a history that includes returns (negative weeks) must be >= 0."""
        from mml_roq_forecast.services.forecast_methods import forecast_sma
        history = [10.0, -5.0, 8.0, -2.0, 15.0, 0.0] * 8 + [10.0, 12.0, 8.0, 9.0]
        result = forecast_sma(history, window=12)
        assert result >= 0.0

    def test_ewma_with_negative_history_returns_nonnegative(self):
        from mml_roq_forecast.services.forecast_methods import forecast_ewma
        history = [10.0, -5.0, 8.0, -2.0, 15.0, 0.0] * 8 + [10.0, 12.0, 8.0, 9.0]
        result = forecast_ewma(history, span=26)
        assert result >= 0.0
