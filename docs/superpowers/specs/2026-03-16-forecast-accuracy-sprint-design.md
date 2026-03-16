# Forecast Accuracy Sprint — Design Spec

**Date:** 2026-03-16
**Module:** `mml_roq_forecast`
**Sprint goal:** Port four calibrated improvements from the `mml.out.pro.fix` production sprint into
the `mml_roq_forecast` service layer. Reduces over-forecast bias for intermittent and OOS-affected
products; no schema changes, no new Odoo models.

---

## Background

The `mml.out.pro.fix` sprint reduced median over-forecast bias from 1.54x to ≤1.30x across 318
SKUs by fixing a zero-filter bug, adding Croston/SBA for intermittent demand, adding OOS
imputation, and damping Holt-Winters trend extrapolation. The same root causes exist in
`mml_roq_forecast`; this sprint ports the proven fixes with adaptations for the Odoo 19 service
layer and `sale.order.line` demand signal.

---

## Scope

Single concern: forecast accuracy. No container logic, no UI changes, no new Odoo models.

**Four changes, three files modified, one file created:**

| File | Change |
|---|---|
| `services/forecast_methods.py` | Fix `demand_std_dev()` fallback check; add phi-damping to `forecast_holt_winters()`; add `forecast_croston_sba()`; add Croston routing to `select_forecast_method()` |
| `services/oos_handler.py` | **New.** Pure functions: `detect_oos_weeks()` + `impute_oos_demand()` |
| `services/demand_history.py` | Wire OOS preprocessing into `get_weekly_demand()` |
| `services/roq_pipeline.py` | Pass `croston_std` from Croston forecast into safety stock calculation |
| `tests/test_forecast_methods.py` | Tests for Croston routing, HW damping, stddev fix |
| `tests/test_oos_handler.py` | **New.** Unit tests for OOS detection and imputation |
| `tests/test_demand_history.py` | Integration test for imputed series output |

**`services/__init__.py`:** No changes required. Services are imported directly by callers
(`from .oos_handler import detect_oos_weeks, impute_oos_demand`); the `__init__.py` is intentionally
empty.

---

## Change 1: `demand_std_dev()` fallback check

**File:** `services/forecast_methods.py`

**Problem:** The `min_n` fallback check counts nonzero weeks (`len(nonzero) < min_n`), but the
actual stddev computation uses the full history (`np.std(history, ddof=1)`). This is inconsistent:
a product with 7 non-zero weeks out of 156 total triggers the `0.5 * mean` fallback even though
the full-series stddev is calculable.

**Fix:** Change the fallback guard to `len(history) < min_n`. The `nonzero` local variable is
removed entirely from this function. The `croston_std` override (added in Change 3) handles
intermittent products before this function is called.

**All-zero series behaviour (intentional):** If a product's entire history is zero — which
can occur on a brand-new SKU before OOS imputation has run — `np.std` returns `0.0`. This gives
`sigma=0`, `safety_stock=0`. This is correct: a product with no demand history should not carry
safety stock. OOS imputation (Change 4) will replace zeros caused by stockouts before this path
is reached for established products.

**Updated function signature and body:**
```python
def demand_std_dev(history, min_n=8, croston_std=None):
    """
    Standard deviation of weekly demand.
    If croston_std is not None, returns it directly (Croston products use
    stddev of non-zero demand sizes, not the full series).
    If fewer than min_n data points in history, uses fallback: 0.5 × mean.
    Returns (std_dev, is_fallback).
    """
    if croston_std is not None:
        return croston_std, False
    if len(history) < min_n:
        mean = sum(history) / len(history) if history else 0.0
        return 0.5 * mean, True
    return float(np.std(history, ddof=1)), False
```

Note: `if croston_std is not None` — not `if croston_std` — so that `croston_std=0.0` (a valid
computed value for a single-event product that happens to have all identical demand sizes) is
correctly passed through rather than silently falling back.

---

## Change 2: Holt-Winters trend damping

**File:** `services/forecast_methods.py`

**Problem:** `forecast_holt_winters()` is a manual triple-exponential smoothing implementation.
Without damping, a trending product extrapolates its trend forward in each one-step-ahead
forecast, causing over-forecasting on upward-trending SKUs.

**Fix:** Add a `phi=0.98` damping parameter. Applied only to the final one-step-ahead forecast
output — the fitting loop is unchanged.

**Signature change:**
```python
def forecast_holt_winters(history, seasonal_period=52, alpha=0.3, beta=0.1, gamma=0.1, phi=0.98):
```

**Forecast line change (last computed line before return):**
```python
# Before:
forecast = last_level + last_trend + last_season

# After:
forecast = last_level + phi * last_trend + last_season
```

`phi=0.98` reduces the trend contribution by 2% in the one-step-ahead forecast (`phi^1 = 0.98`).
For iterative multi-step forecasting the cumulative damping compounds — at h=52 steps ahead it
would reach `sum(phi^1 .. phi^52) * trend` — but this module only produces one-step-ahead
forecasts, so damping is applied once. The effect is mild but prevents the trend from being
treated as undiscounted.

---

## Change 3: Croston/SBA for intermittent demand

**File:** `services/forecast_methods.py`

**Problem:** `select_forecast_method()` routes products with fewer than `min_n` non-zero weeks to
SMA with `'low'` confidence. SMA on sparse data is unreliable — the industry standard for
intermittent demand is Croston/SBA, which estimates demand interval and demand size separately.

### New function: `forecast_croston_sba(history, alpha=0.1)`

```python
def forecast_croston_sba(history, alpha=0.1):
    """
    Croston/SBA forecast for intermittent demand.
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

- `alpha=0.1`: slower-adapting than EWMA — appropriate for sparse data where each demand event
  carries high signal.
- SBA correction `0.95` corrects Croston's known upward bias.
- `std` uses stddev of non-zero demand sizes (not full series). Zeros represent no-demand
  intervals, not low demand. This std is passed to `demand_std_dev()` as `croston_std`.

### Updated `select_forecast_method()` decision tree

The full updated logic (replace existing body):

```python
def select_forecast_method(history, min_n=8, seasonal_period=52,
                           seasonality_threshold=0.4, trend_pvalue=0.05):
    nonzero = [v for v in history if v > 0]

    # 1. Intermittency check — must come first, before Mann-Kendall / seasonality tests.
    #    Those tests are meaningless on sparse series and would misroute sparse-trending
    #    products to EWMA.
    if history:
        pct_active = len(nonzero) / len(history)
        if pct_active < 0.30:
            return 'croston', 'high' if len(nonzero) >= 2 else 'low'

    # 2. Insufficient data fallback (keeps existing behaviour for short-history products
    #    that are NOT sparse — e.g., a new SKU with 5 non-zero weeks out of 5 total).
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

**Existing test compatibility:** The existing `test_method_selection_insufficient_data` uses
`history = [5.0] * 5` (5 non-zero weeks out of 5 total, `pct_active = 1.0`). This does NOT hit
the Croston path. It falls through to the `len(nonzero) < min_n` check (5 < 8 = True) and still
returns `'sma', 'low'`. No regression.

### Pipeline integration (`services/roq_pipeline.py`)

Import addition:
```python
from .forecast_methods import (
    forecast_sma, forecast_ewma, forecast_holt_winters, forecast_croston_sba,
    select_forecast_method, demand_std_dev,
)
```

Updated call site in `_compute_all_lines()`:
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

sigma, is_fallback = demand_std_dev(history, min_n=min_n, croston_std=croston_std)
ss = calculate_safety_stock(z_score, sigma, lt_weeks)
```

**Single-event Croston products (`croston_std=None`):** When `forecast_croston_sba` returns
`std=None` (only one non-zero demand event in history), `demand_std_dev` receives
`croston_std=None` and falls through to the `len(history)` path. For a long sparse series
(e.g., 155 zeros + 1 non-zero), `np.std` returns a small but non-zero sigma. This is
acceptable — it provides a conservative safety stock rather than zero. No special case needed.

---

## Change 4: OOS detection and imputation

### New file: `services/oos_handler.py`

Pure functions — no Odoo ORM dependency. Testable without any mock.

#### `detect_oos_weeks(weekly_pairs, receipt_dates)`

```python
def detect_oos_weeks(weekly_pairs, receipt_dates):
    """
    weekly_pairs:  list of (week_start: date, qty: float) — oldest first
    receipt_dates: list of date — incoming stock.move receipt dates for this product/warehouse
    Returns:       list[bool] — True = OOS week, same length as weekly_pairs

    OOS rule: week is flagged True if:
      1. qty == 0.0, AND
      2. any receipt_date falls within abs(receipt_date - week_start) <= 28 days
         (inclusive boundary — a receipt exactly 28 days away counts)

    No receipts in receipt_dates at all → all flags False (manufactured items /
    no purchase history — genuine zero demand assumed).
    """
```

**Boundary:** `abs((receipt_date - week_start).days) <= 28` — inclusive at exactly 28 days,
False at 29 days. This is pinned explicitly so tests can verify the boundary.

#### `impute_oos_demand(sales, oos_flags)`

```python
def impute_oos_demand(sales, oos_flags):
    """
    sales:     list[float] — weekly demand, oldest first
    oos_flags: list[bool]  — True = OOS week (same length as sales)
    Returns:   list[float] — imputed series (OOS zeros replaced)
    """
```

**Imputation rule:**
- For each OOS week at index `i`: collect neighbours at indices
  `max(0, i-4)` through `min(len(sales)-1, i+4)` exclusive of `i` itself,
  where `oos_flags[j] == False`.
- If ≥ 2 in-stock neighbours found: replace with their mean.
- If < 2 neighbours in window: fall back to mean of all in-stock weeks in the series
  (all `j` where `oos_flags[j] == False`). If all in-stock weeks are also zero (extremely
  rare — a product with genuine zero demand that happened to have receipts), imputed value
  is 0.0. This is correct: do not invent demand that doesn't exist.
- If entire series is OOS (`all(oos_flags)`): return copy of `sales` unchanged.

### Changes to `services/demand_history.py`

`get_weekly_demand()` gains OOS preprocessing after building `week_demand`. Import at top of
file: `from .oos_handler import detect_oos_weeks, impute_oos_demand`.

1. Fetch incoming receipts in one batched query after the `sale.order.line` search:
   ```python
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
   ```

2. Build `weekly_pairs` from the existing week loop (collect `(week_start, qty)` tuples alongside
   the `result` list).

3. Call `detect_oos_weeks(weekly_pairs, receipt_dates)` → `oos_flags`.

4. Call `impute_oos_demand(result, oos_flags)` → return `imputed[-lookback_weeks:]`.

**No change to `get_weekly_demand()` signature.** All callers are unaffected.

---

## Testing

### `tests/test_forecast_methods.py` additions

| Test | Assertion |
|---|---|
| `test_croston_sba_basic` | `[0,5,0,3,0,4]`: `forecast > 0` AND `forecast < mean([5,3,4])` (SBA correction active — result must be below naive non-zero mean) |
| `test_croston_sba_all_zero` | Returns `(0.0, None)` |
| `test_croston_sba_single_nonzero` | `std is None` (cannot compute stdev with 1 point) |
| `test_select_method_routes_croston` | Series with <30% active weeks → `'croston'` |
| `test_select_method_croston_before_mann_kendall` | Sparse trending series (e.g., `[0]*70 + [1,0,2,0,3,0,4,0]`) → `'croston'`, not `'ewma'` |
| `test_holt_winters_phi_damping` | Growing trend series: `forecast_holt_winters(history, phi=0.98)` < `forecast_holt_winters(history, phi=1.0)` |
| `test_demand_std_dev_uses_history_count` | `[5.0]*5 + [0.0]*100` (`min_n=8`, 5 nonzero but 105 total ≥ 8): returns computed stddev not fallback |
| `test_demand_std_dev_croston_override` | `croston_std=2.5` passed → returns `(2.5, False)` directly |
| `test_demand_std_dev_croston_zero_not_falsy` | `croston_std=0.0` passed → returns `(0.0, False)`, not fallback |

### `tests/test_oos_handler.py` (new file)

| Test | Assertion |
|---|---|
| `test_detect_zero_with_nearby_receipt` | Zero week + receipt 14 days later → True |
| `test_detect_zero_no_receipt` | Zero week, no receipts → False |
| `test_detect_nonzero_week_never_flagged` | Positive demand week → False regardless of receipts |
| `test_detect_receipt_at_boundary_28_days` | Receipt exactly 28 days from week start → True (inclusive) |
| `test_detect_receipt_beyond_boundary_29_days` | Receipt 29 days from week start → False |
| `test_detect_receipt_too_far` | Receipt 35 days away → False |
| `test_detect_no_receipts_anywhere` | All zeros but no receipts → all False |
| `test_impute_replaces_oos_zeros` | OOS week gets mean of neighbours |
| `test_impute_falls_back_to_in_stock_mean` | `sales=[0,0,0,10,10,0,0,0,0,0,0,0,10,10]`, middle 8 weeks OOS-flagged (indices 4–11), in-stock weeks are indices 3,4 (values 10,10) and 12,13 (values 10,10) → imputed values for OOS indices where window has <2 in-stock neighbours equal `10.0` (mean of all 4 in-stock weeks) |
| `test_impute_all_oos_unchanged` | Entire series OOS → returned unchanged |
| `test_impute_boundary_clamping` | OOS week at index 0 (no i-4 neighbours) → still imputes from available right-side neighbours |

### `tests/test_demand_history.py` additions

| Test | Assertion |
|---|---|
| `test_get_weekly_demand_imputes_oos` | Mock env: zero `sale.order.line` for one week + incoming `stock.move` receipt within 28 days → returned series has non-zero imputed value for that week |

---

## Verification targets

After implementation, run the full test suite:

```bash
cd E:/ClaudeCode/projects/mml.odoo/mml.odoo.apps/mml.roq.model
pytest mml_roq_forecast/tests/ -v
```

All existing tests must continue to pass (no regressions). New tests: 19 total.

---

## Out of scope

- Container allocation logic
- ABC classifier (revenue-based with dampener is superior — no change)
- EWMA span (stay at 26; different tuning context from the production sprint)
- Z-score values (A=1.881, B=1.645, C=1.282 — validate against MML service level targets separately)
- Any Odoo model changes
- Any UI changes
