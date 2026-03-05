# MML ROQ & Freight — Sprint Roadmap

> **For Claude:** Use `superpowers:executing-plans` to implement individual sprint plans.

**Goal:** Replace Excel-based ROQ with native Odoo 19 modules: `mml_freight_forwarding` + `mml_roq_forecast`

**Architecture:** Two modules. Freight module first (no dependencies). ROQ module depends on freight.

**Tech Stack:** Odoo 19, Python 3.10+, OWL (frontend), `ir.cron`, `ir.sequence`, `queue_job` (async)

---

## Pre-Conditions

```
☐ Uninstall roq_forecast, custom_purchase_containers, freight_tender_email
☐ Confirm cbm_per_unit / pack_size don't already exist on product.template
☐ Confirm fob_port doesn't already exist on res.partner
☐ Identify tt.model.config / tt.predicted.results — keep or remove
☐ Backup database
```

---

## Sprint Overview

| Sprint | Name | Odoo Phase | Est. Complexity | Delivers |
|---|---|---|---|---|
| **0** | Foundation | — | Medium | Both modules scaffold + all data models + settings |
| **1** | Forecast Engine | Phase 1 | High | ABCD classification + SMA/EWMA/HW demand + safety stock |
| **2** | ROQ Engine | Phase 1 | High | (s,S) policy + container fitting + pipeline + UI → replaces spreadsheet |
| **3** | Consolidation Engine | Phase 2 | High | FOB consolidation + push/pull + shipment groups |
| **4** | Forward Plan + Freight | Phase 3 + 4 | Very High | 12-month plan + supplier export + DSV API + freight bridge |

**Dependency chain:**
```
Sprint 0 → Sprint 1 → Sprint 2 → Sprint 3 → Sprint 4
                                ↗
               Sprint 0 (freight module) ──────────────┘
```

---

## Sprint 0: Foundation
**Plan file:** `docs/plans/2026-03-01-sprint-0-foundation.md`

### Deliverables
- `mml_freight_forwarding/` module with all models + security + basic views
- `mml_roq_forecast/` module scaffold with extended and new models
- Settings: `res.config.settings` extension + `ir.config_parameter` defaults
- `ir.sequence` for ROQ run refs, shipment group refs, freight tender refs
- Empty service layer stubs (populated in Sprints 1-4)

### Models Created (mml_freight_forwarding)
- `freight.tender`, `freight.quote`, `freight.booking`, `freight.tracking.event`

### Models Created/Extended (mml_roq_forecast)
- Extended: `product.template`, `res.partner`, `stock.warehouse`, `purchase.order`
- New: `roq.forecast.run`, `roq.forecast.line`, `roq.abc.history`
- New: `roq.shipment.group`, `roq.shipment.group.line`
- New: `roq.forward.plan`, `roq.forward.plan.line`

### Done When
- Both modules install cleanly on Odoo 19 dev instance (`-i mml_freight_forwarding,mml_roq_forecast`)
- All models appear in Technical > Models
- Settings page shows ROQ configuration section
- All tests pass

---

## Sprint 1: Forecast Engine
**Plan file:** `docs/plans/2026-03-01-sprint-1-forecast-engine.md`

### Deliverables
- ABCD revenue classification with dampener logic
- Override rules (new product, ranged account floor, manual override)
- Demand history query service (156-week lookback, weekly granularity, per-warehouse)
- Three forecast methods: SMA (52-week window), EWMA, Holt-Winters
- Method selection logic (seasonal test + trend detection)
- Safety stock calculator (Z × σ × √LT, with n<MIN fallback)
- UI: forecast method and confidence displayed on ROQ line; audit trail on product form

### Key Services (in `mml_roq_forecast/services/`)
- `abc_classifier.py` — `classify_all()`, `apply_dampener()`, `apply_overrides()`
- `demand_forecast.py` — `get_demand_history()`, `forecast_sma()`, `forecast_ewma()`, `forecast_holt_winters()`, `select_method()`
- `safety_stock.py` — `calculate(product, warehouse, z_score, lt_weeks)`

### Done When
- Unit tests pass for all three forecast methods against known inputs
- ABCD classification output matches manual calculation for a sample dataset
- Safety stock values match spreadsheet output for the same parameters
- Dampener: changing tier takes 4 runs to propagate

---

## Sprint 2: ROQ Engine
**Plan file:** `docs/plans/2026-03-01-sprint-2-roq-engine.md`

### Deliverables
- (s,S) inventory policy: Out Level, Order-Up-To, ROQ Raw calculation
- Pack size rounding (round up to nearest multiple)
- MOQ enforcement (Step 7 in pipeline): raise per-supplier total to `product.supplierinfo.min_qty` when below; distribute uplift to lowest-cover warehouse; flag all affected lines
- Container fitting algorithm (greedy CBM assignment, LCL/FCL recommendation, padding)
- Full ROQ pipeline: ABCD → forecast → SS → ROQ → pack-round → aggregate → **MOQ enforce** → container fit
- Weekly `ir.cron` trigger
- Alerts & flags (OOS risk, safety stock breach, overstock, missing data, **below MOQ**, **missing MOQ data**)
- ROQ results UI: tree view with color-coded alerts, form view with calculation trace
- Settings toggle: "Enforce Supplier MOQs" (on by default); when off, flag is shown but quantities unchanged
- Dashboard filters: **MOQ Flags** and **Missing MOQ Data** quick-filters on ROQ results tree

### Key Services
- `roq_calculator.py` — `calculate_roq(forecast_line)`, `aggregate_by_supplier()`
- `container_fitter.py` — `fit_containers(supplier_lines)`, `allocate_padding()`
- `moq_enforcer.py` — `MoqEnforcer.enforce(lines, enforce, max_padding_weeks_cover)`

### Done When
- ROQ output matches spreadsheet line-by-line (same parameters, same input data)
- All 5 original alert types display correctly in UI
- Below MOQ alert and Missing MOQ Data alert display correctly
- Weekly cron runs without error on dev instance
- Dormant SKUs never generate ROQ > 0
- MOQ toggle off → flag still computed, quantities not raised

---

## Sprint 3: Consolidation Engine
**Plan file:** `docs/plans/2026-03-01-sprint-3-consolidation.md`

### Deliverables
- FOB port grouping query (aggregate ROQ results by port)
- Push/pull tolerance calculator (OOS-safe push limits)
- Reactive consolidation engine (post-ROQ-run grouping)
- `roq.shipment.group` creation with status workflow
- Proactive mode stub (calendar-based, detailed in Sprint 4)
- Consolidation kanban view (by status: draft/confirmed/tendered/booked)
- Shipment group form view with push/pull annotations

### Key Services
- `consolidation_engine.py` — `group_by_fob_port()`, `calculate_push_pull()`, `create_shipment_groups()`

### Done When
- Reactive consolidation correctly groups suppliers by FOB port after a ROQ run
- Push is blocked (0 days) when any SKU has projected inventory < 0
- Push tolerance table (from spec §3.2) implemented and tested
- Shipment group status transitions work correctly
- Kanban view displays all active shipment groups

---

## Sprint 4: Forward Plan + DSV Freight Bridge
**Plan file:** `docs/plans/2026-03-01-sprint-4-forward-plan-freight.md`

### Deliverables

**Layer 3B — 12-Month Forward Plan:**
- Monthly demand projection (forecast × 4.33)
- Forward plan generation per supplier (accounting for review cycle + CNY holidays)
- Proactive consolidation calendar (align supplier orders to FOB port windows)
- Supplier Order Schedule PDF report (per-supplier, emailable)
- Internal Cash Flow view (monthly landed cost projection, pivot table)

**Layer 4 — DSV Freight Bridge:**
- DSV API client (`services/dsv_api_client.py`) with retry/backoff
- Freight tender auto-generation from confirmed shipment groups
- Quote comparison and acceptance flow
- Booking confirmation → vessel/ETD/ETA back to ROQ
- Tracking event polling (milestone updates on booking)
- Actual lead time feedback to improve future forecasts

### Key Services
- `forward_plan_generator.py` — `generate_forward_plan(supplier, horizon_months=12)`
- `dsv_api_client.py` — `submit_tender()`, `get_quotes()`, `accept_quote()`, `get_tracking()`

### Done When
- 12-month plan generates and exports to PDF for a sample supplier
- Cash flow pivot shows monthly landed cost estimate
- DSV tender submission works against staging API (or mock)
- Booking confirmation updates shipment group status to `booked`
- Actual lead time recorded on `freight.booking` and surfaced on supplier stats

---

## Validation Milestones

### After Sprint 2 (Phase 1 complete — spreadsheet replacement)
- [ ] Run Odoo ROQ against same input data as current spreadsheet
- [ ] Compare output line-by-line: demand, safety stock, out level, ROQ, container
- [ ] Verify ABCD tier assignments match manual calculation
- [ ] Verify multi-warehouse: same SKU has different ROQ at each warehouse

### Regression Checks (run after each sprint)
- [ ] Changing supplier lead time override only affects that supplier's SKUs
- [ ] Expiring an override reverts to system default on next run
- [ ] Dormant SKUs: ROQ always = 0
- [ ] New SKUs: default Tier A, use σ fallback
- [ ] Push/pull never delays an order with real OOS risk
- [ ] MOQ enforcement on: no supplier total less than `product.supplierinfo.min_qty` (where set)
- [ ] MOQ enforcement off: `moq_flag` still set where applicable, `moq_uplift_qty` always 0
- [ ] `supplier_moq = 0` on a line → no flag, no uplift regardless of toggle state
- [ ] SKUs without `product.supplierinfo.min_qty` appear in Missing MOQ Data filter when enforcement is on

---

## Implementation Notes

1. **Odoo dev setup:** Run with `--dev=all` during development to avoid manual module updates
2. **Test database:** Use a separate dev database with real sales history data for validation
3. **numpy/scipy dependency:** Required for EWMA, Holt-Winters, and statistical tests. Add to `external_dependencies` in `__manifest__.py`
4. **DSV API credentials:** Store in `ir.config_parameter` (`dsv.api_key`, `dsv.api_url`). Never in code.
5. **First run:** Will be slow (156-week history scan). Add progress indicator to `roq.forecast.run`.
