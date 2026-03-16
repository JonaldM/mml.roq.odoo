# Forecast Accuracy Sprint Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port four proven forecast accuracy improvements from the Odoo 15 production module into the `mml_roq_forecast` Odoo 19 service layer.

**Architecture:** Four focused changes: fix `demand_std_dev()` fallback logic, add Croston/SBA for intermittent demand, add Holt-Winters trend damping, and add OOS detection+imputation preprocessing. All changes are in the pure-Python service layer with no schema or model changes required.

**Tech Stack:** Python 3.11+, numpy, scipy, statistics stdlib, pytest, unittest.mock. All new service code is pure Python — no Odoo runtime needed for tests.

**Spec:** `docs/superpowers/specs/2026-03-16-forecast-accuracy-sprint-design.md`

---

## Scene-setting for implementers

**Repo root:** `E:\ClaudeCode\projects\mml.odoo\mml.odoo.apps\mml.roq.model`

**Key files you will touch:**
- `mml_roq_forecast/services/forecast_methods.py` — all forecast math
- `mml_roq_forecast/services/demand_history.py` — ORM queries for demand history
- `mml_roq_forecast/services/roq_pipeline.py` — orchestrates the full ROQ calculation
- `mml_roq_forecast/services/oos_handler.py` — **create new**
- `mml_roq_forecast/tests/test_forecast_methods.py` — existing test file, add to it
- `mml_roq_forecast/tests/test_oos_handler.py` — **create new**
- `mml_roq_forecast/tests/test_demand_history.py` — existing test file, add to it

**Running tests:** From repo root:
```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py -v
python -m pytest mml_roq_forecast/tests/test_oos_handler.py -v
python -m pytest mml_roq_forecast/tests/ -v
```

**Test patterns in this codebase:**
- Pure Python services: use `unittest.TestCase` directly
- Services that need Odoo ORM in their real implementation: mock `self.env` with `unittest.mock.MagicMock`
- Existing tests use `from odoo.tests.common import TransactionCase` — this is stubbed to `unittest.TestCase` by conftest.py so it runs without Odoo
- Relative imports: `from ..services.forecast_methods import ...`

---

## Chunk 1: forecast_methods.py — stddev fix, Croston/SBA, HW damping

### Task 1: Fix `demand_std_dev()` fallback check + add `croston_std` param

**Files:**
- Modify: `mml_roq_forecast/services/forecast_methods.py` (lines 87–97)
- Modify: `mml_roq_forecast/tests/test_forecast_methods.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `mml_roq_forecast/tests/test_forecast_methods.py`:

First, extend the existing top-level import at the top of the file:
```python
# Change this line:
from ..services.forecast_methods import forecast_sma, forecast_ewma
# To:
from ..services.forecast_methods import forecast_sma, forecast_ewma, demand_std_dev
```

Then append this class at the bottom of the file:
```python
import unittest  # already present via TransactionCase import chain, but add if missing


class TestDemandStdDev(unittest.TestCase):

    def test_croston_std_override_returned_directly(self):
        """croston_std kwarg short-circuits all other logic."""
        result, is_fallback = demand_std_dev([1.0, 2.0, 3.0], min_n=8, croston_std=2.5)
        self.assertAlmostEqual(result, 2.5)
        self.assertFalse(is_fallback)

    def test_croston_std_zero_not_treated_as_falsy(self):
        """croston_std=0.0 must pass through — do not use 'if croston_std:'."""
        result, is_fallback = demand_std_dev([1.0, 2.0, 3.0], min_n=8, croston_std=0.0)
        self.assertAlmostEqual(result, 0.0)
        self.assertFalse(is_fallback)

    def test_uses_history_length_not_nonzero_count_for_min_n(self):
        """5 nonzero + 100 zeros = 105 total >= min_n=8: should return computed stddev, not fallback."""
        history = [5.0] * 5 + [0.0] * 100
        result, is_fallback = demand_std_dev(history, min_n=8)
        self.assertFalse(is_fallback)
        self.assertGreater(result, 0.0)

    def test_fallback_when_history_too_short(self):
        """Only 3 data points < min_n=8: should return 0.5 * mean fallback."""
        history = [4.0, 6.0, 8.0]
        result, is_fallback = demand_std_dev(history, min_n=8)
        self.assertTrue(is_fallback)
        self.assertAlmostEqual(result, 3.0)  # 0.5 * mean(4,6,8) = 0.5 * 6 = 3.0
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py::TestDemandStdDev -v
```

Expected: 3 failures (the new tests don't match current behaviour — `demand_std_dev` has no `croston_std` param and uses nonzero count).

- [ ] **Step 3: Implement the fix in `forecast_methods.py`**

Replace the existing `demand_std_dev` function (lines 87–97) with:

```python
def demand_std_dev(history, min_n=8, croston_std=None):
    """
    Standard deviation of weekly demand.
    If croston_std is not None, returns it directly (Croston products use
    stddev of non-zero demand sizes, not the full series).
    If fewer than min_n data points in history, uses fallback: 0.5 x mean.
    Returns (std_dev, is_fallback).
    """
    if croston_std is not None:
        return croston_std, False
    if len(history) < min_n:
        mean = sum(history) / len(history) if history else 0.0
        return 0.5 * mean, True
    return float(np.std(history, ddof=1)), False
```

- [ ] **Step 4: Run to confirm all pass**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py -v
```

Expected: all existing tests pass + 4 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add mml_roq_forecast/services/forecast_methods.py mml_roq_forecast/tests/test_forecast_methods.py
git commit -m "fix: demand_std_dev uses history length for min_n check, adds croston_std override"
```

---

### Task 2: Add `forecast_croston_sba()` + update `select_forecast_method()` routing

**Files:**
- Modify: `mml_roq_forecast/services/forecast_methods.py`
- Modify: `mml_roq_forecast/tests/test_forecast_methods.py`

- [ ] **Step 1: Write the failing tests**

First, extend the existing top-level import to add the new functions:
```python
# Change this line:
from ..services.forecast_methods import forecast_sma, forecast_ewma, demand_std_dev
# To:
from ..services.forecast_methods import (
    forecast_sma, forecast_ewma, demand_std_dev,
    forecast_croston_sba, select_forecast_method,
)
import statistics as _statistics
```

Then append these two classes at the bottom of the file:

```python
class TestCrostonSba(unittest.TestCase):

    def test_basic_forecast_positive_and_below_nonzero_mean(self):
        """SBA correction means result < naive mean of non-zero values."""
        history = [0, 5, 0, 3, 0, 4]
        forecast, std = forecast_croston_sba(history)
        nonzero_mean = _statistics.mean([5, 3, 4])
        self.assertGreater(forecast, 0.0)
        self.assertLess(forecast, nonzero_mean)

    def test_all_zero_returns_zero_and_none(self):
        history = [0.0] * 20
        forecast, std = forecast_croston_sba(history)
        self.assertEqual(forecast, 0.0)
        self.assertIsNone(std)

    def test_single_nonzero_std_is_none(self):
        """Cannot compute stdev with fewer than 2 points."""
        history = [0.0] * 10 + [5.0] + [0.0] * 5
        forecast, std = forecast_croston_sba(history)
        self.assertGreater(forecast, 0.0)
        self.assertIsNone(std)

    def test_multiple_nonzero_std_is_float(self):
        history = [0, 10, 0, 0, 8, 0, 12, 0]
        forecast, std = forecast_croston_sba(history)
        self.assertIsNotNone(std)
        self.assertGreater(std, 0.0)


class TestSelectForecastMethodCroston(unittest.TestCase):

    def test_routes_to_croston_when_sparse(self):
        """< 30% active weeks -> croston regardless of trend or season."""
        # 5 nonzero out of 40 total = 12.5% active
        history = [0.0] * 35 + [1.0, 0.0, 2.0, 0.0, 3.0]
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'croston')

    def test_croston_checked_before_trend(self):
        """Sparse trending series must route to croston, not ewma."""
        # Upward trend but only ~12% active weeks
        history = [float(i) if i % 8 == 0 else 0.0 for i in range(80)]
        method, _ = select_forecast_method(history)
        self.assertEqual(method, 'croston')

    def test_dense_series_does_not_route_to_croston(self):
        """> 30% active weeks: Croston path skipped."""
        history = [5.0] * 50 + [0.0] * 10  # 83% active
        method, _ = select_forecast_method(history)
        self.assertNotEqual(method, 'croston')

    def test_existing_insufficient_data_test_unaffected(self):
        """[5.0]*5: pct_active=100%, not sparse. Still falls to sma/low via min_n."""
        history = [5.0] * 5
        method, confidence = select_forecast_method(history)
        self.assertEqual(method, 'sma')
        self.assertEqual(confidence, 'low')
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py::TestCrostonSba mml_roq_forecast/tests/test_forecast_methods.py::TestSelectForecastMethodCroston -v
```

Expected: all 8 new tests fail with `ImportError` or `AttributeError` — `forecast_croston_sba` doesn't exist yet.

- [ ] **Step 3: Add `forecast_croston_sba()` to `forecast_methods.py`**

Add this function after `forecast_holt_winters`, before `demand_std_dev`:

```python
def forecast_croston_sba(history, alpha=0.1):
    """
    Croston/SBA forecast for intermittent demand (<30% active weeks).
    alpha=0.1: slow-adapting EWMA appropriate for sparse data.
    SBA correction factor 0.95 corrects Croston's known upward bias.

    Returns: (forecast: float, std: float | None)
      forecast = (smoothed_size / smoothed_interval) * 0.95
      std      = statistics.stdev(non_zero_sizes) if len >= 2, else None
    Returns (0.0, None) if no positive values in history.
    """
    import statistics
    non_zero = [(i, v) for i, v in enumerate(history) if v > 0]
    if not non_zero:
        return 0.0, None
    sizes = [v for _, v in non_zero]
    intervals = [non_zero[0][0] + 1] + [
        non_zero[i][0] - non_zero[i - 1][0] for i in range(1, len(non_zero))
    ]
    z_size = sizes[0]
    z_interval = intervals[0]
    for s, q in zip(sizes[1:], intervals[1:]):
        z_size = alpha * s + (1 - alpha) * z_size
        z_interval = alpha * q + (1 - alpha) * z_interval
    forecast = (z_size / z_interval * 0.95) if z_interval > 0 else 0.0
    std = statistics.stdev(sizes) if len(sizes) >= 2 else None
    return max(forecast, 0.0), std
```

- [ ] **Step 4: Update `select_forecast_method()` — add Croston routing as first check**

Replace the existing `select_forecast_method` function body with:

```python
def select_forecast_method(history, min_n=8, seasonal_period=52,
                           seasonality_threshold=0.4, trend_pvalue=0.05):
    """
    Automatically selects the best forecast method for the given history.

    Selection logic:
    1. If < 30% of weeks have non-zero demand -> Croston/SBA (intermittent)
    2. If < min_n non-zero weeks -> SMA (low confidence)
    3. Test for seasonality (strength of seasonal component)
    4. If seasonal -> Holt-Winters
    5. Test for trend (Mann-Kendall)
    6. If trending -> EWMA
    7. Otherwise -> SMA

    Returns: (method_name, confidence)
    """
    nonzero = [v for v in history if v > 0]

    # 1. Intermittency check — must come first, before Mann-Kendall / seasonality tests.
    #    Those tests are meaningless on sparse series and would misroute sparse-trending
    #    products to EWMA.
    if history:
        pct_active = len(nonzero) / len(history)
        if pct_active < 0.30:
            return 'croston', 'high' if len(nonzero) >= 2 else 'low'

    # 2. Insufficient data fallback (keeps existing behaviour for short-history products
    #    that are NOT sparse — e.g. a new SKU with 5 non-zero weeks out of 5 total).
    if len(nonzero) < min_n:
        return 'sma', 'low'

    # 3. Seasonality test
    if len(history) >= 2 * seasonal_period:
        seasonality_strength = _seasonal_strength(history, seasonal_period)
        if seasonality_strength > seasonality_threshold:
            return 'holt_winters', 'high'

    # 4. Trend test
    if _has_trend(history, pvalue_threshold=trend_pvalue):
        return 'ewma', 'high' if len(nonzero) >= 26 else 'medium'

    return 'sma', 'high' if len(nonzero) >= 26 else 'medium'
```

- [ ] **Step 5: Run to confirm all pass**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py -v
```

Expected: all tests pass including the 8 new ones.

- [ ] **Step 6: Commit**

```bash
git add mml_roq_forecast/services/forecast_methods.py mml_roq_forecast/tests/test_forecast_methods.py
git commit -m "feat: add Croston/SBA for intermittent demand, route sparse products away from SMA"
```

---

### Task 3: Add Holt-Winters trend damping (phi parameter)

**Files:**
- Modify: `mml_roq_forecast/services/forecast_methods.py` (lines 46–84)
- Modify: `mml_roq_forecast/tests/test_forecast_methods.py`

- [ ] **Step 1: Write the failing test**

Add to `mml_roq_forecast/tests/test_forecast_methods.py` inside `TestHoltWinters`:

```python
    def test_phi_damping_reduces_trend_contribution(self):
        """phi=0.98 forecast must be strictly less than phi=1.0 on a growing series."""
        from ..services.forecast_methods import forecast_holt_winters
        # Strong upward trend over 2 full seasons so HW has enough data
        history = [float(i) for i in range(104)]
        undamped = forecast_holt_winters(history, phi=1.0)
        damped = forecast_holt_winters(history, phi=0.98)
        self.assertLess(damped, undamped)
```

- [ ] **Step 2: Run to confirm test fails**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py::TestHoltWinters::test_phi_damping_reduces_trend_contribution -v
```

Expected: FAIL — `forecast_holt_winters` has no `phi` parameter yet.

- [ ] **Step 3: Add `phi` parameter to `forecast_holt_winters()`**

Change the function signature (line 46):
```python
# Before:
def forecast_holt_winters(history, seasonal_period=52, alpha=0.3, beta=0.1, gamma=0.1):

# After:
def forecast_holt_winters(history, seasonal_period=52, alpha=0.3, beta=0.1, gamma=0.1, phi=0.98):
```

Change the forecast output line (the line before the `return`, currently reads `forecast = last_level + last_trend + last_season`):
```python
# Before:
forecast = last_level + last_trend + last_season

# After:
forecast = last_level + phi * last_trend + last_season
```

No other changes to this function.

- [ ] **Step 4: Run to confirm all pass**

```bash
python -m pytest mml_roq_forecast/tests/test_forecast_methods.py -v
```

Expected: all tests pass including the new damping test.

- [ ] **Step 5: Commit**

```bash
git add mml_roq_forecast/services/forecast_methods.py mml_roq_forecast/tests/test_forecast_methods.py
git commit -m "feat: add phi=0.98 trend damping to Holt-Winters to prevent runaway extrapolation"
```

---

## Chunk 2: OOS handler, demand_history wiring, pipeline integration

### Task 4: Create `oos_handler.py` with `detect_oos_weeks()` and `impute_oos_demand()`

**Files:**
- Create: `mml_roq_forecast/services/oos_handler.py`
- Create: `mml_roq_forecast/tests/test_oos_handler.py`

- [ ] **Step 1: Create the test file first**

Create `mml_roq_forecast/tests/test_oos_handler.py`:

```python
import unittest
from datetime import date, timedelta


class TestDetectOosWeeks(unittest.TestCase):
    """
    detect_oos_weeks(weekly_pairs, receipt_dates) -> list[bool]

    weekly_pairs: list of (week_start: date, qty: float)
    receipt_dates: list of date
    OOS rule: qty == 0.0 AND any receipt within abs(delta) <= 28 days
    """

    def _import(self):
        from ..services.oos_handler import detect_oos_weeks
        return detect_oos_weeks

    def test_zero_week_with_nearby_receipt_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=14)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertTrue(flags[0])

    def test_zero_week_no_receipt_not_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        flags = detect_oos_weeks([(week, 0.0)], [])
        self.assertFalse(flags[0])

    def test_nonzero_week_never_flagged_even_with_receipt(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=3)
        flags = detect_oos_weeks([(week, 10.0)], [receipt])
        self.assertFalse(flags[0])

    def test_receipt_at_boundary_28_days_is_flagged(self):
        """Inclusive boundary: exactly 28 days away must flag True."""
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=28)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertTrue(flags[0])

    def test_receipt_beyond_boundary_29_days_not_flagged(self):
        """29 days away: just outside the window, must flag False."""
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=29)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertFalse(flags[0])

    def test_receipt_too_far_35_days_not_flagged(self):
        detect_oos_weeks = self._import()
        week = date(2025, 1, 6)
        receipt = week + timedelta(days=35)
        flags = detect_oos_weeks([(week, 0.0)], [receipt])
        self.assertFalse(flags[0])

    def test_no_receipts_anywhere_all_false(self):
        """All zeros but zero receipts: every week genuine zero demand."""
        detect_oos_weeks = self._import()
        weeks = [(date(2025, 1, 6) + timedelta(weeks=i), 0.0) for i in range(10)]
        flags = detect_oos_weeks(weeks, [])
        self.assertTrue(all(f is False for f in flags))

    def test_returns_same_length_as_weekly_pairs(self):
        detect_oos_weeks = self._import()
        weeks = [(date(2025, 1, 6) + timedelta(weeks=i), float(i % 3)) for i in range(20)]
        receipt = date(2025, 1, 20)
        flags = detect_oos_weeks(weeks, [receipt])
        self.assertEqual(len(flags), 20)


class TestImputeOosDemand(unittest.TestCase):
    """
    impute_oos_demand(sales, oos_flags) -> list[float]

    OOS zeros replaced with mean of up to 4 nearest in-stock neighbours.
    Fallback: in-stock mean if <2 neighbours in window.
    All-OOS: return unchanged.
    """

    def _import(self):
        from ..services.oos_handler import impute_oos_demand
        return impute_oos_demand

    def test_oos_week_replaced_with_neighbour_mean(self):
        impute_oos_demand = self._import()
        sales = [10.0, 0.0, 10.0]
        flags = [False, True, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[1], 10.0)

    def test_non_oos_weeks_unchanged(self):
        impute_oos_demand = self._import()
        sales = [5.0, 0.0, 7.0]
        flags = [False, True, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[0], 5.0)
        self.assertAlmostEqual(result[2], 7.0)

    def test_falls_back_to_in_stock_mean_when_few_neighbours(self):
        """
        Long OOS run in middle. Indices deep in the run have <2 in-stock
        neighbours within ±4 window. Should fall back to series in-stock mean.

        sales: 0 0 0 10 10 | 0 0 0 0 0 0 0 0 | 10 10 0 0
        oos:   F F F  F  F |  T  T  T  T  T  T  T  T |  F  F  F  F
        In-stock values: [10, 10, 10, 10] -> mean = 10.0
        Index 9 (deep in OOS run): window is indices 5-13. Index 13 is the only
        non-OOS neighbour in that window (1 neighbour < 2 required) -> fallback to
        global in-stock mean = 10.0
        """
        impute_oos_demand = self._import()
        sales = [0.0, 0.0, 0.0, 10.0, 10.0,
                 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                 10.0, 10.0, 0.0, 0.0]
        flags = [False, False, False, False, False,
                 True, True, True, True, True, True, True, True,
                 False, False, False, False]
        result = impute_oos_demand(sales, flags)
        # Index 9 is deep in OOS run, neighbours within ±4 (indices 5-13) are all OOS
        self.assertAlmostEqual(result[9], 10.0)

    def test_all_oos_returns_unchanged(self):
        """Cannot impute if entire series is OOS — return as-is."""
        impute_oos_demand = self._import()
        sales = [0.0] * 10
        flags = [True] * 10
        result = impute_oos_demand(sales, flags)
        self.assertEqual(result, sales)

    def test_boundary_clamping_index_zero(self):
        """OOS week at index 0 has no left neighbours — must still impute from right side."""
        impute_oos_demand = self._import()
        sales = [0.0, 8.0, 8.0, 8.0, 8.0]
        flags = [True, False, False, False, False]
        result = impute_oos_demand(sales, flags)
        self.assertAlmostEqual(result[0], 8.0)
```

- [ ] **Step 2: Run to confirm all tests fail**

```bash
python -m pytest mml_roq_forecast/tests/test_oos_handler.py -v
```

Expected: all 13 tests fail with `ImportError` — `oos_handler.py` doesn't exist yet.

- [ ] **Step 3: Create `mml_roq_forecast/services/oos_handler.py`**

```python
"""
OOS (Out-of-Stock) detection and imputation for demand history preprocessing.

Pure functions — no Odoo ORM dependency. Testable without any Odoo runtime.

detect_oos_weeks: flags zero-demand weeks that are likely stockouts based on
  nearby incoming receipt activity.

impute_oos_demand: replaces OOS zeros with the mean of nearby in-stock weeks.
"""
from datetime import timedelta

_OOS_WINDOW_DAYS = 28  # ±28 days — inclusive boundary


def detect_oos_weeks(weekly_pairs, receipt_dates):
    """
    Identify which zero-demand weeks are likely stockouts.

    weekly_pairs:  list of (week_start: date, qty: float) — oldest first
    receipt_dates: list of date — incoming stock.move receipt dates

    Returns: list[bool] — True = OOS week, same length as weekly_pairs.

    OOS rule: week is True if:
      1. qty == 0.0, AND
      2. any receipt_date satisfies abs((receipt_date - week_start).days) <= 28

    If receipt_dates is empty: all flags False (no replenishment signal,
    assume genuine zero demand — manufactured items, no purchase history).
    """
    if not receipt_dates:
        return [False] * len(weekly_pairs)

    flags = []
    for week_start, qty in weekly_pairs:
        if qty != 0.0:
            flags.append(False)
            continue
        oos = any(
            abs((r - week_start).days) <= _OOS_WINDOW_DAYS
            for r in receipt_dates
        )
        flags.append(oos)

    return flags


def impute_oos_demand(sales, oos_flags):
    """
    Replace OOS zero weeks with estimated demand.

    sales:     list[float] — weekly demand, oldest first
    oos_flags: list[bool]  — True = OOS week (same length as sales)

    Returns: new list[float] with OOS zeros replaced.

    Imputation rule (per OOS week at index i):
      - Collect neighbours at indices max(0, i-4) through min(n-1, i+4),
        excluding i, where oos_flags[j] == False.
      - If >= 2 in-stock neighbours: replace with their mean.
      - If < 2 in-stock neighbours in window: fall back to mean of ALL
        in-stock weeks in the full series.
      - If all in-stock weeks are also zero: imputed value is 0.0
        (do not invent demand that doesn't exist).
      - If entire series is OOS: return unchanged copy.
    """
    n = len(sales)

    # If entire series is OOS, nothing to impute
    if all(oos_flags):
        return list(sales)

    # Precompute global in-stock mean (fallback for long OOS runs)
    in_stock_values = [sales[j] for j in range(n) if not oos_flags[j]]
    global_in_stock_mean = sum(in_stock_values) / len(in_stock_values) if in_stock_values else 0.0

    result = list(sales)

    for i in range(n):
        if not oos_flags[i]:
            continue

        # Collect in-stock neighbours within ±4 weeks
        neighbours = [
            sales[j]
            for j in range(max(0, i - 4), min(n, i + 5))
            if j != i and not oos_flags[j]
        ]

        if len(neighbours) >= 2:
            result[i] = sum(neighbours) / len(neighbours)
        else:
            result[i] = global_in_stock_mean

    return result
```

- [ ] **Step 4: Run to confirm all tests pass**

```bash
python -m pytest mml_roq_forecast/tests/test_oos_handler.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add mml_roq_forecast/services/oos_handler.py mml_roq_forecast/tests/test_oos_handler.py
git commit -m "feat: add OOS detection and imputation service (pure Python, receipt-proximity signal)"
```

---

### Task 5: Wire OOS preprocessing into `demand_history.py`

**Files:**
- Modify: `mml_roq_forecast/services/demand_history.py`
- Modify: `mml_roq_forecast/tests/test_demand_history.py`

- [ ] **Step 1: Write the failing integration test**

Add this class to `mml_roq_forecast/tests/test_demand_history.py`:

```python
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


class TestDemandHistoryOosImputation(unittest.TestCase):
    """
    Tests that get_weekly_demand() imputes OOS zeros using the incoming
    stock.move receipt signal. Uses MagicMock to avoid needing Odoo runtime.
    """

    def _make_env(self, sale_lines, stock_moves):
        """Build a minimal mock env that returns the provided records."""
        env = MagicMock()

        # sale.order.line mock
        mock_sol = MagicMock()
        mock_sol.__iter__ = MagicMock(return_value=iter(sale_lines))
        env.__getitem__.return_value.search.return_value = mock_sol

        return env

    def test_oos_week_is_imputed_when_receipt_nearby(self):
        """
        Scenario:
          - 8 weeks of history
          - Week 3 (index 2) has zero sale orders
          - An incoming stock.move receipt exists 7 days after week 3 start
          - Weeks 1,2 and 4,5 have demand of 10 each
          Expected: week 3 is imputed to ~10.0 (mean of neighbours)
        """
        from ..services.demand_history import DemandHistoryService
        from ..services.oos_handler import detect_oos_weeks, impute_oos_demand

        today = date.today()
        # Build week starts (Monday-anchored)
        base_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=8)
        week_starts = [base_monday + timedelta(weeks=i) for i in range(8)]

        # Sale order lines: demand=10 for all weeks except week index 2
        sale_lines = []
        for i, ws in enumerate(week_starts):
            if i == 2:
                continue  # zero demand this week
            line = MagicMock()
            line.order_id.date_order.date.return_value = ws
            line.product_uom_qty = 10.0
            sale_lines.append(line)

        # Incoming receipt 7 days after the zero week
        receipt_move = MagicMock()
        receipt_date = week_starts[2] + timedelta(days=7)
        receipt_move.date.date.return_value = receipt_date

        # Build mocked env
        env = MagicMock()
        product = MagicMock()
        product.id = 42
        warehouse = MagicMock()
        warehouse.id = 1

        # First search (sale.order.line) returns sale_lines
        # Second search (stock.move incoming) returns [receipt_move]
        env.__getitem__.return_value.search.side_effect = [
            sale_lines,       # sale.order.line search
            [receipt_move],   # stock.move incoming search
        ]

        svc = DemandHistoryService(env)
        result = svc.get_weekly_demand(product, warehouse, lookback_weeks=8)

        # The zero week should be imputed (non-zero)
        self.assertEqual(len(result), 8)
        self.assertGreater(result[2], 0.0)
```

- [ ] **Step 2: Run to confirm test fails**

```bash
python -m pytest mml_roq_forecast/tests/test_demand_history.py::TestDemandHistoryOosImputation -v
```

Expected: FAIL — `get_weekly_demand` returns unchanged zero for week 2 (no OOS preprocessing yet).

- [ ] **Step 3: Update `demand_history.py`**

Add the import at the top of the file (after existing imports):

```python
from .oos_handler import detect_oos_weeks, impute_oos_demand
```

Replace `get_weekly_demand` with the updated version that adds OOS preprocessing after the existing loop:

```python
def get_weekly_demand(self, product, warehouse, lookback_weeks=156):
    """
    Returns list of weekly demand quantities, oldest first, length=lookback_weeks.
    Zeros caused by stockouts are imputed using incoming receipt proximity signal.
    product: product.product recordset
    warehouse: stock.warehouse recordset
    lookback_weeks: int
    """
    today = date.today()
    start_date = today - timedelta(weeks=lookback_weeks)

    week_demand = defaultdict(float)

    lines = self.env['sale.order.line'].search([
        ('product_id', '=', product.id),
        ('order_id.state', 'in', ['sale', 'done']),
        ('order_id.warehouse_id', '=', warehouse.id),
        ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
        ('company_id', '=', self.env.company.id),
    ])

    for line in lines:
        order_date = line.order_id.date_order.date() if hasattr(
            line.order_id.date_order, 'date'
        ) else line.order_id.date_order
        week_start = order_date - timedelta(days=order_date.weekday())
        week_demand[week_start] += line.product_uom_qty

    # Build weekly series (oldest first)
    result = []
    weekly_pairs = []
    current = start_date - timedelta(days=start_date.weekday())
    while current <= today:
        qty = week_demand.get(current, 0.0)
        result.append(qty)
        weekly_pairs.append((current, qty))
        current += timedelta(weeks=1)

    result = result[-lookback_weeks:]
    weekly_pairs = weekly_pairs[-lookback_weeks:]

    # OOS detection — fetch incoming receipts for this product/warehouse
    receipt_moves = self.env['stock.move'].search([
        ('product_id', '=', product.id),
        ('location_dest_id.warehouse_id', '=', warehouse.id),
        ('picking_type_code', '=', 'incoming'),
        ('state', '=', 'done'),
        ('date', '>=', start_date.strftime('%Y-%m-%d')),
    ])
    receipt_dates = [
        m.date.date() if hasattr(m.date, 'date') else m.date
        for m in receipt_moves
    ]

    oos_flags = detect_oos_weeks(weekly_pairs, receipt_dates)
    result = impute_oos_demand(result, oos_flags)

    return result
```

- [ ] **Step 4: Run full test suite to confirm nothing regressed**

```bash
python -m pytest mml_roq_forecast/tests/test_demand_history.py -v
python -m pytest mml_roq_forecast/tests/ -v
```

Expected: all tests pass including the new OOS imputation test. The existing three `TestDemandHistory` tests are `odoo_integration` (marked by conftest) and will be skipped without Odoo runtime — that is expected.

- [ ] **Step 5: Commit**

```bash
git add mml_roq_forecast/services/demand_history.py mml_roq_forecast/tests/test_demand_history.py
git commit -m "feat: wire OOS detection and imputation into DemandHistoryService.get_weekly_demand"
```

---

### Task 6: Update `roq_pipeline.py` — Croston call site + `croston_std` passthrough

**Files:**
- Modify: `mml_roq_forecast/services/roq_pipeline.py` (lines 22–28 imports, lines 146–156 call site)
- Modify: `mml_roq_forecast/tests/test_roq_pipeline.py`

Note: all existing `test_roq_pipeline.py` tests use `TransactionCase` (need live Odoo, skipped in unit test run). We add one standalone `unittest.TestCase` to verify the Croston import is wired and accessible from the pipeline module — the minimal check that catches a missing import before deployment.

- [ ] **Step 1: Add import smoke test to `test_roq_pipeline.py`**

Append this class at the bottom of `mml_roq_forecast/tests/test_roq_pipeline.py`:

```python
import unittest


class TestRoqPipelineCrostonImport(unittest.TestCase):
    """Verifies forecast_croston_sba is imported in roq_pipeline (wiring smoke test)."""

    def test_forecast_croston_sba_importable_from_pipeline(self):
        from ..services import roq_pipeline
        self.assertTrue(
            hasattr(roq_pipeline, 'forecast_croston_sba'),
            "forecast_croston_sba must be importable from roq_pipeline after Task 6",
        )
```

- [ ] **Step 2: Run to confirm it fails**

```bash
python -m pytest mml_roq_forecast/tests/test_roq_pipeline.py::TestRoqPipelineCrostonImport -v
```

Expected: FAIL — `forecast_croston_sba` is not in `roq_pipeline` namespace yet.

- [ ] **Step 3: Update imports in `roq_pipeline.py`**

Find the existing import block:
```python
from .forecast_methods import (
    forecast_sma, forecast_ewma, forecast_holt_winters,
    select_forecast_method, demand_std_dev,
)
```

Replace with:
```python
from .forecast_methods import (
    forecast_sma, forecast_ewma, forecast_holt_winters, forecast_croston_sba,
    select_forecast_method, demand_std_dev,
)
```

- [ ] **Step 5: Update the forecast + stddev call site in `_compute_all_lines()`**

Find the existing block (approximately lines 146–156):
```python
method, confidence = select_forecast_method(history, min_n=min_n)

if method == 'sma':
    fwd = forecast_sma(history, window=sma_window)
elif method == 'ewma':
    fwd = forecast_ewma(history, span=26)
else:
    fwd = forecast_holt_winters(history)

avg_demand = sum(history) / len(history) if history else 0.0
sigma, is_fallback = demand_std_dev(history, min_n=min_n)
```

Replace with:
```python
method, confidence = select_forecast_method(history, min_n=min_n)

if method == 'croston':
    fwd, croston_std = forecast_croston_sba(history)
elif method == 'sma':
    fwd, croston_std = forecast_sma(history, window=sma_window), None
elif method == 'ewma':
    fwd, croston_std = forecast_ewma(history, span=26), None
else:  # holt_winters
    fwd, croston_std = forecast_holt_winters(history), None

avg_demand = sum(history) / len(history) if history else 0.0
sigma, is_fallback = demand_std_dev(history, min_n=min_n, croston_std=croston_std)
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest mml_roq_forecast/tests/ -v
```

Expected: all non-odoo_integration tests pass including the new import smoke test.

- [ ] **Step 7: Commit**

```bash
git add mml_roq_forecast/services/roq_pipeline.py mml_roq_forecast/tests/test_roq_pipeline.py
git commit -m "feat: wire Croston/SBA and croston_std into ROQ pipeline call site"
```

---

## Final verification

- [ ] **Run the complete test suite one last time**

```bash
python -m pytest mml_roq_forecast/tests/ -v --tb=short
```

Expected: all non-`odoo_integration` tests pass. New test count: 28 additional tests across `TestDemandStdDev` (4), `TestCrostonSba` (4), `TestSelectForecastMethodCroston` (4), `TestHoltWinters` (+1 new = 4 total in class), `TestDetectOosWeeks` (8), `TestImputeOosDemand` (5), `TestDemandHistoryOosImputation` (1), `TestRoqPipelineCrostonImport` (1).

- [ ] **Push to remote**

```bash
git push origin master
```
