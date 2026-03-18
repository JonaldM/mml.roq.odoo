# Pipeline Data Cache — Design Spec

**Date:** 2026-03-19
**Status:** Approved
**Goal:** Reduce ROQ pipeline DB round trips from ~2,400 to ~7 by pre-fetching all required data in bulk queries at pipeline start.

---

## Problem

`RoqPipeline._compute_all_lines()` and `AbcClassifier.classify_all_products()` issue ORM queries inside per-SKU loops. With 400 SKUs × 1 warehouse:

| Location | Query | Count | Note |
|---|---|---|---|
| `get_weekly_demand` | `sale.order.line` | 400 | |
| `get_weekly_demand_raw` | `sale.order.line` | 400 | **Identical domain to above — same data fetched twice** |
| OOS detection in `get_weekly_demand` | `stock.move` | 400 | |
| `get_soh` (active SKUs) | `stock.quant` + `stock.location` | 800 | |
| `get_soh` (dormant SKUs via `_dormant_line`) | `stock.quant` + `stock.location` | ~80 | Same cache needed |
| `get_confirmed_po_qty` | `purchase.order.line` + `stock.location` | 800 | |
| `product.supplierinfo` in `_compute_all_lines` | `product.supplierinfo` | 400 | |
| `get_trailing_revenue_by_warehouse` in ABC | `sale.order.line` | 400 | |
| Global badge revenue in ABC | `sale.order.line` | 400 | Updates `product.template.abc_tier` display only |

**Total: ~3,680 DB round trips per run.**

Note: `get_weekly_demand` and `get_weekly_demand_raw` use an identical SOL domain — one bulk load satisfies both. The "before" count treats them as 400 unique queries that happen to be called twice.

---

## Solution: `PipelineDataCache`

A new class `services/pipeline_data_cache.py` issues 7 bulk queries upfront and builds Python dicts for O(1) per-SKU lookup.

### Bulk Queries

| # | Model | Filter | Dict Key | Value | Replaces |
|---|---|---|---|---|---|
| 1 | `sale.order.line` | all ROQ product variants, all active warehouses, lookback window, confirmed orders | `(product_variant_id, warehouse_id)` | `list[(week_start_date, qty)]` | ~800 SOL queries (demand + raw share this load) |
| 2 | `stock.move` | all ROQ product variants, all active warehouses, `picking_type_id.code='incoming'`, done, lookback window | `(product_variant_id, warehouse_id)` | `list[date]` (receipt dates) | ~400 OOS receipt queries |
| 3 | `stock.quant` | all ROQ product **variants** (not templates), all internal location ids | `(product_variant_id, warehouse_id)` | `float` (SOH sum) | ~480 quant queries (active + dormant) |
| 4 | `purchase.order.line` | all ROQ product variants, purchase/done state | `(product_variant_id, warehouse_id)` | `float` (qty remaining) | ~400 PO queries |
| 5 | `product.supplierinfo` | all ROQ product templates | `product_tmpl_id` | first `supplierinfo` record by sequence | ~400 supplier queries |
| 6 | `sale.order.line` (revenue) | all ROQ product variants, all active warehouses, ABC trailing window (`abc_weeks`), confirmed orders | `(product_tmpl_id, warehouse_id)` | `float` (sum of `product_uom_qty × price_unit`) | ~400 per-warehouse revenue queries in ABC |
| 7 | `sale.order.line` (global revenue) | all ROQ product variants, ABC trailing window, no warehouse filter | `product_tmpl_id` | `float` (total revenue across all warehouses) | ~400 global badge revenue queries in ABC |

**Note — queries 6 and 7 cannot be derived from query 1.** Query 1 stores `(week_start_date, qty)` — unit quantities only, no `price_unit`. Revenue = `qty × price_unit` and price varies per SOL. Queries 6 and 7 must be separate bulk fetches that also load `price_unit`. They remain single queries each (not per-SKU loops), so the total stays at 7 bulk queries.

**Important — query 3 key:** `stock.quant.product_id` is a `product.product` field (variant), not `product.template`. The loader must collect all ROQ product **variant** ids via `products.mapped('product_variant_ids.id')`, not use template ids. Using template ids in the quant domain will return zero results silently.

**Internal warehouse locations** are fetched once per warehouse at cache build time (not per SKU) and stored in `cache.internal_locations`.

### Dict Structures

```python
# Demand history (shared by get_weekly_demand and get_weekly_demand_raw):
# (product_variant_id, warehouse_id) -> list of (week_start_date, qty) sorted oldest-first
self.demand: dict[tuple[int,int], list[tuple[date, float]]]

# Receipt dates for OOS detection:
# (product_variant_id, warehouse_id) -> list of dates
self.receipts: dict[tuple[int,int], list[date]]

# Stock on hand (covers both active and dormant SKUs):
# (product_variant_id, warehouse_id) -> float
self.soh: dict[tuple[int,int], float]

# Confirmed inbound PO qty:
# (product_variant_id, warehouse_id) -> float
self.po_qty: dict[tuple[int,int], float]

# Primary supplier info:
# product_tmpl_id -> supplierinfo record (or empty recordset)
self.supplier: dict[int, Any]

# Per-warehouse trailing revenue for ABC classification:
# (product_tmpl_id, warehouse_id) -> float
self.revenue: dict[tuple[int,int], float]

# Global (all-warehouse) trailing revenue for abc_tier display badge:
# product_tmpl_id -> float
self.global_revenue: dict[int, float]

# Internal stock locations per warehouse (fetched once):
# warehouse_id -> list[int] (location ids)
self.internal_locations: dict[int, list[int]]
```

---

## Service Integration

### `DemandHistoryService`

Add `cache: PipelineDataCache | None = None` to `__init__`.

- `get_weekly_demand(product, warehouse, lookback_weeks)`:
  - Cache hit: read `cache.demand[(product.id, warehouse.id)]`, build weekly series, run OOS imputation using `cache.receipts[(product.id, warehouse.id)]`.
  - Cache miss: log `WARNING`, fall back to **full existing method body** including the `stock.move` receipt query and OOS imputation. A partial fallback that skips receipt detection would silently return unimputed demand history and produce incorrect safety stock.

- `get_weekly_demand_raw(product, warehouse, lookback_weeks)`:
  - Cache hit: read `cache.demand[(product.id, warehouse.id)]`, build weekly series, return without OOS imputation.
  - Cache miss: fall back to existing ORM query.

- `get_trailing_revenue_by_warehouse(product_template, warehouse, weeks)`:
  - Cache hit: return `cache.revenue.get((product_template.id, warehouse.id), 0.0)`.
  - Cache miss: fall back to existing ORM query.

### `InventoryQueryService`

Add `cache: PipelineDataCache | None = None` to `__init__`.

- `get_soh(product, warehouse)`: returns `cache.soh.get((product.id, warehouse.id), 0.0)`. Covers both active SKUs in `_compute_all_lines` and dormant SKUs in `_dormant_line` — no special case needed.
- `get_confirmed_po_qty(product, warehouse)`: returns `cache.po_qty.get((product.id, warehouse.id), 0.0)`.
- `get_inventory_position(product, warehouse)`: calls `get_soh` + `get_confirmed_po_qty` as now — no direct ORM. Cache-backed transitively through the two methods above. No change required to this method.
- `_get_internal_locations(warehouse)`: returns `self.env['stock.location'].browse(cache.internal_locations.get(warehouse.id, []))`.

### `AbcClassifier`

`classify_all_products(run, revenue_cache=None, global_revenue_cache=None)`:

- When `revenue_cache` dict is supplied: replace the `dh.get_trailing_revenue_by_warehouse` loop with a dict lookup. The local `dh = DemandHistoryService(self.env)` instantiation (line 161) becomes unused and can be removed.
- When `global_revenue_cache` dict is supplied: replace the per-product global revenue queries (for updating `product.template.abc_tier` display badge) with dict lookups. These 400 queries are correctness-irrelevant (display badge only) but exist in the current code and must be covered to achieve the ~7 query target.

### `RoqPipeline`

`__init__` builds `PipelineDataCache` once, passes it to service constructors:

```python
def __init__(self, env):
    self.env = env
    self.settings = SettingsHelper(env)
    self.cache = PipelineDataCache(env)
    self.abc = AbcClassifier(env)
    self.dh = DemandHistoryService(env, cache=self.cache)
    self.inv = InventoryQueryService(env, cache=self.cache)
```

Cache is built lazily — `cache.load(products, warehouses, lookback_weeks, abc_weeks)` is called at the start of `_compute_all_lines` once product/warehouse sets are known, before the per-SKU loop begins.

`classify_all_products` is called with both revenue caches:
```python
tier_map = self.abc.classify_all_products(
    forecast_run,
    revenue_cache=self.cache.revenue,
    global_revenue_cache=self.cache.global_revenue,
)
```

---

## Error Handling

- **Cache miss**: log `WARNING` and fall back to existing ORM query path. Not fatal. The full fallback must include all queries the original method issued (e.g. demand miss falls back to SOL + stock.move, not just SOL).
- **Bulk query failure**: propagates as a normal exception — pipeline status → `error`.
- **Memory**: 400 SKUs × 156 weeks × 2 warehouses ≈ 125,000 `(date, float)` tuples in `cache.demand` — well within normal Odoo worker bounds (~50 MB). No risk.
- **Recordset validity**: cache loads within the same cursor/transaction as the pipeline. No cross-cursor recordset invalidation risk.
- Cache is not persisted between runs — rebuilt fresh each time `run()` is called.

---

## Files Changed

| File | Change |
|---|---|
| `services/pipeline_data_cache.py` | **New** — bulk loader and dict builder |
| `services/demand_history.py` | Add `cache` param; cache-first reads with full ORM fallback |
| `services/inventory_query.py` | Add `cache` param; cache-first reads |
| `services/abc_classifier.py` | Accept `revenue_cache` + `global_revenue_cache` dicts in `classify_all_products`; remove unused local `dh` instantiation |
| `services/roq_pipeline.py` | Instantiate cache, pass to services, call `cache.load()` before loop |
| `tests/test_pipeline_data_cache.py` | **New** — unit tests verifying bulk load and O(1) lookups with mock env |

---

## Expected Outcome

| Metric | Before | After |
|---|---|---|
| DB queries per run (400 SKU, 1 WH) | ~3,680 | ~7 |
| Estimated run time | 8–12 min | 1–2 min |
| Code changes | — | 1 new file, 4 modified |
