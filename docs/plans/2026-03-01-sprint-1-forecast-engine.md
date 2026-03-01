# Sprint 1: Forecast Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Implement Layer 1 of the ROQ model — ABCD revenue classification with dampener, three demand forecast methods (SMA/EWMA/Holt-Winters) with automatic method selection, and safety stock calculation.

**Architecture:** Pure service layer in `mml_roq_forecast/services/`. Each service is a plain Python class (no Odoo model inheritance). The `roq.forecast.run` model calls services; services receive Odoo recordsets as input and return structured data.

**Tech Stack:** Odoo 19 ORM, numpy, scipy (for Holt-Winters and statistical tests), standard Python math

**Pre-condition:** Sprint 0 complete. Both modules installed cleanly.

---

## Task 1: Demand history query service

**Files:**
- Create: `mml_roq_forecast/services/demand_history.py`
- Create: `mml_roq_forecast/tests/test_demand_history.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_demand_history.py
from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from ..services.demand_history import DemandHistoryService

class TestDemandHistory(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.product = self.env['product.product'].create({
            'name': 'Test SKU DH',
            'type': 'product',
        })
        # Create 10 weeks of confirmed sale order lines
        today = date.today()
        for i in range(10):
            order_date = today - timedelta(weeks=i+1)
            order = self.env['sale.order'].create({
                'partner_id': self.env['res.partner'].search([], limit=1).id,
                'date_order': order_date,
                'warehouse_id': self.warehouse.id,
                'state': 'sale',
            })
            self.env['sale.order.line'].create({
                'order_id': order.id,
                'product_id': self.product.id,
                'product_uom_qty': 10.0,
                'price_unit': 5.0,
            })

    def test_returns_weekly_series(self):
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(self.product, self.warehouse, lookback_weeks=52)
        # result is a list of floats, one per week, oldest first
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_nonzero_weeks_have_demand(self):
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(self.product, self.warehouse, lookback_weeks=52)
        nonzero = [v for v in result if v > 0]
        self.assertGreater(len(nonzero), 0)

    def test_empty_history_returns_zeros(self):
        new_product = self.env['product.product'].create({'name': 'Brand New SKU', 'type': 'product'})
        svc = DemandHistoryService(self.env)
        result = svc.get_weekly_demand(new_product, self.warehouse, lookback_weeks=8)
        self.assertEqual(len(result), 8)
        self.assertEqual(sum(result), 0.0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestDemandHistory
```
Expected: ImportError — service not found.

**Step 3: Implement service**

```python
# mml_roq_forecast/services/demand_history.py
from datetime import date, timedelta
from collections import defaultdict


class DemandHistoryService:
    """
    Queries sale.order.line to build weekly demand time series per SKU per warehouse.

    Design choices:
    - Uses sale.order.line (not stock.move) to capture demand at time of order,
      including unfulfilled/backordered demand that stock.move would miss.
    - Attributes demand to the warehouse that the sale order was assigned to.
    - Returns a list of weekly floats, oldest first, length = lookback_weeks.
      Weeks with no sales are 0.0.
    """

    def __init__(self, env):
        self.env = env

    def get_weekly_demand(self, product, warehouse, lookback_weeks=156):
        """
        Returns list of weekly demand quantities, oldest first, length=lookback_weeks.
        product: product.product recordset
        warehouse: stock.warehouse recordset
        lookback_weeks: int
        """
        today = date.today()
        start_date = today - timedelta(weeks=lookback_weeks)

        # Build week buckets: key = ISO week start (Monday)
        week_demand = defaultdict(float)

        lines = self.env['sale.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', '=', warehouse.id),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
        ])

        for line in lines:
            order_date = line.order_id.date_order.date() if hasattr(
                line.order_id.date_order, 'date'
            ) else line.order_id.date_order
            # ISO week start (Monday)
            week_start = order_date - timedelta(days=order_date.weekday())
            week_demand[week_start] += line.product_uom_qty

        # Build ordered list from start_date to today, one entry per week
        result = []
        current = start_date - timedelta(days=start_date.weekday())  # align to Monday
        while current <= today:
            result.append(week_demand.get(current, 0.0))
            current += timedelta(weeks=1)

        return result[-lookback_weeks:]  # Trim to exact lookback length

    def get_trailing_revenue(self, product_template, weeks=52):
        """
        Returns total revenue for a product.template over trailing `weeks`.
        Sums across all warehouses (ABCD tier is global).
        """
        today = date.today()
        start_date = today - timedelta(weeks=weeks)

        lines = self.env['sale.order.line'].search([
            ('product_id.product_tmpl_id', '=', product_template.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
        ])

        return sum(
            line.product_uom_qty * line.price_unit
            for line in lines
        )
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestDemandHistory
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/demand_history.py \
        mml_roq_forecast/tests/test_demand_history.py
git commit -m "feat(roq): add demand history query service"
```

---

## Task 2: SMA forecast method

**Files:**
- Create: `mml_roq_forecast/services/forecast_methods.py`
- Create: `mml_roq_forecast/tests/test_forecast_methods.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_forecast_methods.py
from odoo.tests.common import TransactionCase
from ..services.forecast_methods import forecast_sma, forecast_ewma

class TestForecastMethods(TransactionCase):

    def test_sma_returns_average_of_last_n_weeks(self):
        # 10 weeks: 8 zeros + [10, 10]
        history = [0.0] * 8 + [10.0, 10.0]
        result = forecast_sma(history, window=2)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_sma_uses_full_history_when_less_than_window(self):
        history = [5.0, 5.0, 5.0]  # Only 3 weeks, window=52
        result = forecast_sma(history, window=52)
        self.assertAlmostEqual(result, 5.0, places=2)

    def test_sma_zero_history_returns_zero(self):
        history = [0.0] * 10
        result = forecast_sma(history, window=52)
        self.assertEqual(result, 0.0)

    def test_ewma_weights_recent_more(self):
        # Rising trend: older weeks are low, recent weeks are high
        history = [1.0] * 8 + [10.0, 10.0]
        result = forecast_ewma(history, span=4)
        # EWMA should be higher than SMA of full history (which averages ~3.6)
        sma_full = sum(history) / len(history)
        self.assertGreater(result, sma_full)

    def test_ewma_constant_series_matches_value(self):
        history = [5.0] * 20
        result = forecast_ewma(history, span=4)
        self.assertAlmostEqual(result, 5.0, places=2)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestForecastMethods
```

**Step 3: Implement SMA and EWMA**

```python
# mml_roq_forecast/services/forecast_methods.py
"""
Forecast methods for ROQ demand forecasting.

All functions take:
  history: list of float — weekly demand, oldest first
Returns:
  float — forecasted weekly demand for next period
"""
import math
import numpy as np


def forecast_sma(history, window=52):
    """
    Simple Moving Average over the last `window` weeks.
    Falls back to available history if fewer than `window` data points.
    Returns 0.0 if no history.
    """
    if not history:
        return 0.0
    effective_window = min(window, len(history))
    recent = history[-effective_window:]
    return sum(recent) / len(recent) if recent else 0.0


def forecast_ewma(history, span=26):
    """
    Exponentially Weighted Moving Average.
    span: controls decay — higher span = slower decay (less weight on recent).
    Uses pandas-style EWMA: alpha = 2 / (span + 1)
    """
    if not history:
        return 0.0
    alpha = 2.0 / (span + 1)
    result = history[0]
    for val in history[1:]:
        result = alpha * val + (1 - alpha) * result
    return result


def forecast_holt_winters(history, seasonal_period=52, alpha=0.3, beta=0.1, gamma=0.1):
    """
    Triple Exponential Smoothing (Holt-Winters additive model).
    Requires at least 2 full seasonal cycles (2 × seasonal_period data points).

    Returns the one-step-ahead forecast.
    Falls back to SMA if insufficient data.
    """
    n = len(history)
    if n < 2 * seasonal_period:
        return forecast_sma(history, window=52)

    # Initialise level, trend, and seasonal components
    # Level: average of first season
    level = sum(history[:seasonal_period]) / seasonal_period
    # Trend: average change per period across first two seasons
    trend = (sum(history[seasonal_period:2*seasonal_period]) -
             sum(history[:seasonal_period])) / seasonal_period**2
    # Seasonal: deviation from mean in first season
    season = [history[i] - level for i in range(seasonal_period)]

    levels = [level]
    trends = [trend]
    seasons = list(season)

    for i in range(seasonal_period, n):
        prev_level = levels[-1]
        prev_trend = trends[-1]
        prev_season = seasons[i - seasonal_period]

        new_level = alpha * (history[i] - prev_season) + (1 - alpha) * (prev_level + prev_trend)
        new_trend = beta * (new_level - prev_level) + (1 - beta) * prev_trend
        new_season = gamma * (history[i] - new_level) + (1 - gamma) * prev_season

        levels.append(new_level)
        trends.append(new_trend)
        seasons.append(new_season)

    # One-step-ahead forecast
    last_level = levels[-1]
    last_trend = trends[-1]
    last_season = seasons[-seasonal_period]
    forecast = last_level + last_trend + last_season
    return max(0.0, forecast)  # Demand cannot be negative


def demand_std_dev(history, min_n=8):
    """
    Standard deviation of weekly demand.
    If fewer than min_n non-zero data points, uses fallback: 0.5 × mean.
    Returns (std_dev, is_fallback).
    """
    nonzero = [v for v in history if v > 0]
    if len(nonzero) < min_n:
        mean = sum(history) / len(history) if history else 0.0
        return 0.5 * mean, True  # Fallback: CV = 0.5
    return float(np.std(history, ddof=1)), False


def select_forecast_method(history, min_n=8, seasonal_period=52,
                           seasonality_threshold=0.4, trend_pvalue=0.05):
    """
    Automatically selects the best forecast method for the given history.

    Selection logic:
    1. If < min_n non-zero weeks → SMA (low confidence)
    2. Test for seasonality (strength of seasonal component)
    3. If seasonal → Holt-Winters
    4. Test for trend (Mann-Kendall)
    5. If trending → EWMA
    6. Otherwise → SMA

    Returns: (method_name, confidence)
      method_name: 'sma' | 'ewma' | 'holt_winters'
      confidence: 'high' | 'medium' | 'low'
    """
    nonzero = [v for v in history if v > 0]

    if len(nonzero) < min_n:
        return 'sma', 'low'

    # Need at least 2 full seasonal cycles for Holt-Winters test
    if len(history) >= 2 * seasonal_period:
        seasonality_strength = _seasonal_strength(history, seasonal_period)
        if seasonality_strength > seasonality_threshold:
            return 'holt_winters', 'high'

    # Trend detection via Mann-Kendall
    if _has_trend(history, pvalue_threshold=trend_pvalue):
        return 'ewma', 'high' if len(nonzero) >= 26 else 'medium'

    return 'sma', 'high' if len(nonzero) >= 26 else 'medium'


def _seasonal_strength(history, period):
    """
    Measures strength of seasonal component using residual variance approach.
    Returns a float 0–1; higher = stronger seasonality.
    """
    try:
        from scipy import signal
        arr = np.array(history)
        # Detrend
        detrended = signal.detrend(arr)
        # Compute variance explained by seasonal pattern
        n = len(detrended)
        seasonal_pattern = np.array([
            np.mean(detrended[i::period]) for i in range(period)
        ])
        seasonal_full = np.tile(seasonal_pattern, n // period + 1)[:n]
        residuals = detrended - seasonal_full
        var_seasonal = np.var(seasonal_full)
        var_residual = np.var(residuals)
        if var_seasonal + var_residual == 0:
            return 0.0
        return var_seasonal / (var_seasonal + var_residual)
    except Exception:
        return 0.0


def _has_trend(history, pvalue_threshold=0.05):
    """
    Mann-Kendall trend test. Returns True if significant trend detected.
    """
    try:
        from scipy.stats import kendalltau
        n = len(history)
        x = list(range(n))
        _, p = kendalltau(x, history)
        return p < pvalue_threshold
    except Exception:
        return False
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestForecastMethods
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/forecast_methods.py \
        mml_roq_forecast/tests/test_forecast_methods.py
git commit -m "feat(roq): add SMA, EWMA, Holt-Winters forecast methods and selection logic"
```

---

## Task 3: Holt-Winters test (extend test file)

**Step 1: Write failing test for Holt-Winters**

```python
# Add to mml_roq_forecast/tests/test_forecast_methods.py

class TestHoltWinters(TransactionCase):

    def test_holt_winters_seasonal_data(self):
        from ..services.forecast_methods import forecast_holt_winters
        # Generate 2 years of seasonal data: peak at weeks 13 and 39
        import math
        history = [
            10 + 8 * math.sin(2 * math.pi * i / 52)
            for i in range(104)
        ]
        result = forecast_holt_winters(history, seasonal_period=52)
        # Should be positive and reasonable (within range of the series)
        self.assertGreater(result, 0.0)
        self.assertLess(result, 25.0)

    def test_holt_winters_falls_back_to_sma_insufficient_data(self):
        from ..services.forecast_methods import forecast_holt_winters
        history = [5.0] * 20  # Only 20 weeks — less than 2×52
        result = forecast_holt_winters(history, seasonal_period=52)
        self.assertAlmostEqual(result, 5.0, places=1)  # Falls back to SMA

    def test_method_selection_seasonal_data(self):
        from ..services.forecast_methods import select_forecast_method
        import math
        history = [
            10 + 8 * math.sin(2 * math.pi * i / 52)
            for i in range(104)
        ]
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'holt_winters')

    def test_method_selection_insufficient_data(self):
        from ..services.forecast_methods import select_forecast_method
        history = [5.0] * 5  # Only 5 weeks
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'sma')
        self.assertEqual(confidence, 'low')
```

**Step 2: Run to verify new tests fail before adding code**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestHoltWinters
```
Expected: FAIL (tests exist but imports exist now — they should pass if implementation is correct)

**Step 3: Run all forecast tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestForecastMethods,/mml_roq_forecast:TestHoltWinters
```
Expected: All PASS

**Step 4: Commit**

```bash
git add mml_roq_forecast/tests/test_forecast_methods.py
git commit -m "test(roq): add Holt-Winters and method selection tests"
```

---

## Task 4: Safety stock calculator

**Files:**
- Create: `mml_roq_forecast/services/safety_stock.py`
- Create: `mml_roq_forecast/tests/test_safety_stock.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_safety_stock.py
import math
from odoo.tests.common import TransactionCase
from ..services.safety_stock import calculate_safety_stock

class TestSafetyStock(TransactionCase):

    def test_formula_z_sigma_sqrt_lt(self):
        # SS = Z × σ × √(LT_weeks)
        result = calculate_safety_stock(z_score=1.881, sigma=10.0, lt_weeks=14.28)
        expected = 1.881 * 10.0 * math.sqrt(14.28)
        self.assertAlmostEqual(result, expected, places=2)

    def test_low_sigma_uses_fallback(self):
        # When is_fallback=True (sigma = 0.5 × mean), result still > 0
        result = calculate_safety_stock(z_score=1.645, sigma=2.5, lt_weeks=14.28)
        self.assertGreater(result, 0.0)

    def test_zero_z_score_gives_zero_ss(self):
        # Tier D: z_score = 0, no safety stock
        result = calculate_safety_stock(z_score=0.0, sigma=5.0, lt_weeks=14.28)
        self.assertEqual(result, 0.0)

    def test_zero_lt_gives_zero_ss(self):
        result = calculate_safety_stock(z_score=1.881, sigma=5.0, lt_weeks=0.0)
        self.assertEqual(result, 0.0)

    def test_result_is_never_negative(self):
        result = calculate_safety_stock(z_score=1.881, sigma=0.0, lt_weeks=14.28)
        self.assertGreaterEqual(result, 0.0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestSafetyStock
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/safety_stock.py
import math


def calculate_safety_stock(z_score, sigma, lt_weeks):
    """
    Safety Stock = Z × σ × √(LT_weeks)

    z_score: float — from ABCD tier (A=1.881, B=1.645, C=1.282, D=0)
    sigma: float — std dev of weekly demand (use fallback if low confidence)
    lt_weeks: float — total lead time in weeks (days / 7)

    Returns: float — safety stock units (never negative)
    """
    if z_score <= 0 or sigma <= 0 or lt_weeks <= 0:
        return 0.0
    return z_score * sigma * math.sqrt(lt_weeks)


Z_SCORES = {
    'A': 1.881,
    'B': 1.645,
    'C': 1.282,
    'D': 0.0,
}


def get_z_score(tier):
    """Returns Z-score for given ABC tier. Tier D = 0 (no safety stock)."""
    return Z_SCORES.get(tier, 0.0)
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestSafetyStock
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/safety_stock.py \
        mml_roq_forecast/tests/test_safety_stock.py
git commit -m "feat(roq): add safety stock calculator"
```

---

## Task 5: ABCD classification service

**Files:**
- Create: `mml_roq_forecast/services/abc_classifier.py`
- Create: `mml_roq_forecast/tests/test_abc_classifier.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_abc_classifier.py
from odoo.tests.common import TransactionCase
from ..services.abc_classifier import AbcClassifier

class TestAbcClassifier(TransactionCase):

    def setUp(self):
        super().setUp()
        self.classifier = AbcClassifier(self.env)

    def test_high_revenue_sku_is_tier_a(self):
        # SKU with 70% of total revenue → Tier A
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
        # SKU classified as C but has override floor B → should be B
        assignments = {'P1': 700, 'P2': 200, 'P3': 50}
        overrides = {'P3': 'B'}  # P3 would be C, override to min B
        result = self._classify_with_revenues(assignments, overrides=overrides)
        self.assertEqual(result['P3'], 'B')

    def test_override_cannot_lower_tier(self):
        # SKU classified as A but has override floor B → should stay A
        assignments = {'P1': 700, 'P2': 200, 'P3': 100}
        overrides = {'P1': 'B'}  # P1 is A, override floor is B — should stay A
        result = self._classify_with_revenues(assignments, overrides=overrides)
        self.assertEqual(result['P1'], 'A')

    def test_dampener_holds_tier_for_4_runs(self):
        # Simulate dampener: tier changes to B but stays C for 3 runs, changes on 4th
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
        # Was going C→B (2 weeks in pending), now calculated back to C → reset
        result = self.classifier.apply_dampener(
            current_tier='C',
            calculated_tier='C',
            weeks_in_pending=2,
            dampener_weeks=4,
        )
        self.assertEqual(result['applied_tier'], 'C')
        self.assertEqual(result['weeks_in_pending'], 0)

    def _classify_with_revenues(self, revenue_map, overrides=None):
        """Helper: classify a dict of {name: revenue} and return {name: tier}."""
        overrides = overrides or {}
        tier_map = self.classifier.classify_from_revenues(
            revenue_map,
            band_a_pct=70,
            band_b_pct=20,
            overrides=overrides,
        )
        return tier_map
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestAbcClassifier
```

**Step 3: Implement ABCD classifier**

```python
# mml_roq_forecast/services/abc_classifier.py
"""
ABCD Revenue Tier Classification Service.

Classification is GLOBAL (not per-warehouse).
Revenue bands are configurable: default 70/20/10.
Dampener: a tier must be stable for N weeks before taking effect.
Override: floor (minimum tier), never a ceiling.
Tier ordering for comparisons: A > B > C > D
"""

TIER_RANK = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
RANK_TIER = {4: 'A', 3: 'B', 2: 'C', 1: 'D'}


def _higher_tier(t1, t2):
    """Returns the higher of two tier strings."""
    return t1 if TIER_RANK.get(t1, 0) >= TIER_RANK.get(t2, 0) else t2


class AbcClassifier:
    """
    Classifies products into ABC tiers based on trailing revenue.
    Handles: dampener logic, override floors, dormant detection.
    """

    def __init__(self, env):
        self.env = env

    def classify_from_revenues(self, revenue_map, band_a_pct=70, band_b_pct=20, overrides=None):
        """
        revenue_map: dict of {identifier: revenue_float}
        overrides: dict of {identifier: tier_floor_string}
        Returns: dict of {identifier: tier_string}
        """
        overrides = overrides or {}
        total_revenue = sum(revenue_map.values())

        # Dormant: zero revenue
        result = {}
        active = {k: v for k, v in revenue_map.items() if v > 0}

        for k in revenue_map:
            if revenue_map[k] <= 0:
                result[k] = 'D'

        if not active or total_revenue == 0:
            return result

        # Sort by revenue descending
        sorted_skus = sorted(active.items(), key=lambda x: x[1], reverse=True)

        cumulative = 0.0
        for identifier, revenue in sorted_skus:
            cumulative += revenue
            cumulative_pct = (cumulative / total_revenue) * 100

            if cumulative_pct <= band_a_pct:
                raw_tier = 'A'
            elif cumulative_pct <= band_a_pct + band_b_pct:
                raw_tier = 'B'
            else:
                raw_tier = 'C'

            # Apply override (floor only — cannot lower a tier)
            override_floor = overrides.get(identifier)
            if override_floor:
                raw_tier = _higher_tier(raw_tier, override_floor)

            result[identifier] = raw_tier

        return result

    def apply_dampener(self, current_tier, calculated_tier, weeks_in_pending, dampener_weeks=4):
        """
        Applies the reclassification dampener.

        Rules:
        - If calculated_tier == current_tier: no change, reset pending counter.
        - If calculated_tier != current_tier:
            - Increment weeks_in_pending.
            - If weeks_in_pending >= dampener_weeks: apply new tier, reset counter.
            - Else: keep current_tier.

        Returns: dict with 'applied_tier', 'weeks_in_pending', 'pending_tier'
        """
        if calculated_tier == current_tier:
            return {
                'applied_tier': current_tier,
                'pending_tier': None,
                'weeks_in_pending': 0,
            }

        new_weeks = weeks_in_pending + 1

        if new_weeks >= dampener_weeks:
            return {
                'applied_tier': calculated_tier,
                'pending_tier': None,
                'weeks_in_pending': 0,
            }

        return {
            'applied_tier': current_tier,
            'pending_tier': calculated_tier,
            'weeks_in_pending': new_weeks,
        }

    def get_settings(self):
        """Read ROQ settings from ir.config_parameter with fallback defaults."""
        get = self.env['ir.config_parameter'].sudo().get_param
        return {
            'band_a_pct': int(get('roq.abc_band_a_pct', 70)),
            'band_b_pct': int(get('roq.abc_band_b_pct', 20)),
            'dampener_weeks': int(get('roq.abc_dampener_weeks', 4)),
        }

    def classify_all_products(self, run):
        """
        Runs full ABCD classification for all ROQ-managed products.
        Updates product.template fields and writes roq.abc.history records.

        run: roq.forecast.run recordset
        """
        from datetime import date
        from .demand_history import DemandHistoryService

        settings = self.get_settings()
        dh = DemandHistoryService(self.env)

        products = self.env['product.template'].search([
            ('is_roq_managed', '=', True),
            ('type', 'in', ['product', 'consu']),
        ])

        # Build revenue map
        revenue_map = {}
        for pt in products:
            revenue_map[pt.id] = dh.get_trailing_revenue(pt, weeks=52)

        # Build overrides map
        overrides = {
            pt.id: pt.abc_tier_override
            for pt in products if pt.abc_tier_override
        }

        # Classify
        tier_assignments = self.classify_from_revenues(
            revenue_map,
            band_a_pct=settings['band_a_pct'],
            band_b_pct=settings['band_b_pct'],
            overrides=overrides,
        )

        # Compute total for cumulative %
        total_rev = sum(revenue_map.values())

        # Sort by revenue for cumulative pct
        sorted_by_rev = sorted(revenue_map.items(), key=lambda x: x[1], reverse=True)
        cumulative_map = {}
        cumulative = 0.0
        for pid, rev in sorted_by_rev:
            cumulative += rev
            cumulative_map[pid] = (cumulative / total_rev * 100) if total_rev else 0.0

        # Apply dampener and persist
        history_vals = []
        for pt in products:
            calculated = tier_assignments.get(pt.id, 'D')
            dampener_result = self.apply_dampener(
                current_tier=pt.abc_tier or 'C',
                calculated_tier=calculated,
                weeks_in_pending=pt.abc_weeks_in_pending or 0,
                dampener_weeks=settings['dampener_weeks'],
            )
            applied = dampener_result['applied_tier']

            pt.write({
                'abc_tier': applied,
                'abc_tier_pending': dampener_result.get('pending_tier'),
                'abc_weeks_in_pending': dampener_result['weeks_in_pending'],
                'abc_trailing_revenue': revenue_map.get(pt.id, 0.0),
                'abc_cumulative_pct': cumulative_map.get(pt.id, 0.0),
            })

            history_vals.append({
                'product_id': pt.id,
                'run_id': run.id,
                'date': date.today(),
                'tier_calculated': calculated,
                'tier_applied': applied,
                'trailing_revenue': revenue_map.get(pt.id, 0.0),
                'cumulative_pct': cumulative_map.get(pt.id, 0.0),
                'override_active': overrides.get(pt.id, ''),
            })

        self.env['roq.abc.history'].create(history_vals)
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestAbcClassifier
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/abc_classifier.py \
        mml_roq_forecast/tests/test_abc_classifier.py
git commit -m "feat(roq): add ABCD classification service with dampener and override logic"
```

---

## Task 6: Settings helper — get effective parameters for a supplier

**Files:**
- Create: `mml_roq_forecast/services/settings_helper.py`
- Create: `mml_roq_forecast/tests/test_settings_helper.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_settings_helper.py
from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from ..services.settings_helper import SettingsHelper

class TestSettingsHelper(TransactionCase):

    def setUp(self):
        super().setUp()
        self.helper = SettingsHelper(self.env)
        self.supplier = self.env['res.partner'].create({
            'name': 'Test Supplier',
            'supplier_rank': 1,
        })

    def test_returns_system_default_when_no_override(self):
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 100)  # System default

    def test_supplier_override_replaces_default(self):
        self.supplier.supplier_lead_time_days = 60
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 60)

    def test_expired_override_reverts_to_default(self):
        self.supplier.supplier_lead_time_days = 60
        self.supplier.override_expiry_date = date.today() - timedelta(days=1)
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 100)  # Override expired

    def test_future_expiry_override_is_still_active(self):
        self.supplier.supplier_lead_time_days = 60
        self.supplier.override_expiry_date = date.today() + timedelta(days=30)
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 60)  # Still active
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestSettingsHelper
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/settings_helper.py
"""
Resolves effective ROQ parameters for a given supplier and/or product.

Override semantics: REPLACE, never add. Supplier override completely replaces
system default. If override_expiry_date is set and passed, reverts to default.
"""
from datetime import date


SYSTEM_DEFAULTS = {
    'lead_time_days': 100,
    'review_interval_days': 30,
    'service_level': 0.97,
    'lookback_weeks': 156,
    'sma_window_weeks': 52,
    'min_n_value': 8,
}


class SettingsHelper:

    def __init__(self, env):
        self.env = env
        self._param_cache = {}

    def _get_param(self, key, default):
        if key not in self._param_cache:
            val = self.env['ir.config_parameter'].sudo().get_param(f'roq.{key}')
            self._param_cache[key] = type(default)(val) if val is not None else default
        return self._param_cache[key]

    def _override_active(self, supplier):
        """Returns True if supplier override is active (not expired)."""
        expiry = supplier.override_expiry_date
        if not expiry:
            return True
        return expiry >= date.today()

    def get_lead_time_days(self, supplier):
        default = self._get_param('default_lead_time_days', 100)
        if supplier and supplier.supplier_lead_time_days and self._override_active(supplier):
            return supplier.supplier_lead_time_days
        return default

    def get_review_interval_days(self, supplier):
        default = self._get_param('default_review_interval_days', 30)
        if supplier and supplier.supplier_review_interval_days and self._override_active(supplier):
            return supplier.supplier_review_interval_days
        return default

    def get_service_level(self, supplier, tier):
        """
        Service level resolution order:
        1. Supplier override (if active) → replaces everything
        2. ABC tier mapping
        3. System default
        """
        TIER_SERVICE_LEVELS = {
            'A': 0.97, 'B': 0.95, 'C': 0.90, 'D': 0.0,
        }
        if supplier and supplier.supplier_service_level and self._override_active(supplier):
            return supplier.supplier_service_level
        return TIER_SERVICE_LEVELS.get(tier, self._get_param('default_service_level', 0.97))

    def get_lookback_weeks(self):
        return self._get_param('lookback_weeks', 156)

    def get_sma_window_weeks(self):
        return self._get_param('sma_window_weeks', 52)

    def get_min_n_value(self):
        return self._get_param('min_n_value', 8)
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestSettingsHelper
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/settings_helper.py \
        mml_roq_forecast/tests/test_settings_helper.py
git commit -m "feat(roq): add settings helper with supplier override and expiry logic"
```

---

## Sprint 1 Done Checklist

- [ ] All tests pass: `odoo-bin --test-enable -d dev --test-tags mml_roq_forecast`
- [ ] `classify_from_revenues()` matches manual Pareto calculation for sample data
- [ ] Dampener confirmed: changing tier takes exactly 4 runs to propagate
- [ ] SMA with 52-week window: verify against spreadsheet formula for same input
- [ ] EWMA: verify weighted average for a known decay rate
- [ ] Holt-Winters: seasonal series produces a plausible forecast (not wild extrapolation)
- [ ] Method selection: seasonal data → `holt_winters`, trend data → `ewma`, flat → `sma`
- [ ] Safety stock: `Z × σ × √LT` matches manual calculation
- [ ] Expired override reverts to system default: confirmed by test
- [ ] New SKU (< min_n data): gets `sma` method + `low` confidence flag
