# MML ROQ Forecast & Procurement Planning — Odoo 19 Native Module

## Project Context

MML Consumer Products Ltd is a New Zealand-based distribution company managing ~400 SKUs across 5 brands (Volere, Annabel Langbein, Enkel, Enduro, Rufus & Coco). The business imports from predominantly Chinese suppliers and distributes to major national retail chains (Briscoes, Harvey Norman, Animates, PetStock).

We currently run the ROQ (Reorder Quantity) calculation via an external Python-generated Excel spreadsheet. This project replaces that with a native Odoo 19 module that adds multi-warehouse support, ABCD tiering, multi-supplier container consolidation, a 12-month forward procurement plan, and integration with our DSV freight forwarding module.

- **Target Odoo version:** 19 (self-hosted)
- **Builder:** Claude Code + Jono
- **Module technical name:** `mml_roq_forecast`
- **Dependency:** `mml_freight_forwarding` (DSV integration — may be built concurrently, see separate sprint spec)

---

## Architecture Overview

Three logical layers, each building on the one below:

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: SHIPMENT PLANNING                                     │
│  Consolidation engine, DSV freight bridge, 12-month plan        │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 2: PROCUREMENT ENGINE                                     │
│  ROQ calculation, container fitting, push/pull optimisation     │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 1: FORECAST ENGINE                                        │
│  Per-SKU per-warehouse demand forecast, ABCD tiering, SS calc   │
└─────────────────────────────────────────────────────────────────┘
```

**Operating Modes:**

1. **Reactive (Phase-in):** Weekly ROQ run generates purchase requirements. Consolidation is applied after the fact to group POs by FOB port. This mirrors the current spreadsheet workflow but inside Odoo.
2. **Proactive (Target state):** The 12-month forecast drives a forward procurement calendar. The consolidation engine plans shipments in advance — grouping suppliers by FOB port and scheduling order placement dates to align with optimal container fill, subject to OOS constraints. Reactive ordering remains as a fallback for exceptions and urgent replenishment.

---

## Layer 1: Forecast Engine

### 1.1 Data Source

- **Sales history:** `sale.order.line` from Odoo, filtered to confirmed/done orders.
  - Open question: confirm whether `sale.order.line` or `stock.move` (delivery-confirmed) is the better source. `sale.order.line` captures demand at time of order; `stock.move` captures actual fulfilment. For forecasting purposes, order-based demand is generally preferred as it reflects true demand including any unfulfilled/backordered demand that `stock.move` would miss.
- **Lookback period:** 156 weeks (3 years), configurable.
- **Granularity:** Weekly demand per SKU per warehouse.
- **Warehouse allocation:** Sales are attributed to the warehouse that fulfilled them (source warehouse on the `stock.picking` / delivery order). All products are stocked at all warehouses.

### 1.2 Warehouse Model

- **N warehouses × N countries.** The system must handle arbitrary warehouse/country combinations for future expansion.
- **Current warehouses:** Hamilton (NZ), Christchurch (NZ).
- **Each warehouse is functionally independent.** Separate SOH, separate demand history, separate safety stock, separate reorder calculations.
- **No inter-warehouse transfers in the model.** If a warehouse is OOS, the nearest warehouse fulfils the order directly to the customer. This means demand "leaks" between warehouses during stockouts — the forecast should use the fulfilling warehouse's history (where the `stock.move` originated), not the customer's nearest warehouse. This is a known limitation; over time, consistent stock availability will normalise demand allocation.

### 1.3 Demand Forecasting

Three forecast methods, automatically selected per SKU per warehouse:

| Method | Selection Criteria | Behaviour |
|---|---|---|
| **SMA** (Simple Moving Average) | Default / fallback | Average weekly demand over trailing window. Use a **52-week window** (not 156) for better recency. Only fall back to 156 weeks if < 52 weeks of data available. |
| **EWMA** (Exponentially Weighted Moving Average) | Demand trend detected, no seasonality | Exponential decay weighting. Captures recent acceleration/deceleration. |
| **Holt-Winters** (Triple Exponential Smoothing) | Seasonal pattern detected | Trend + seasonality decomposition. Appropriate for products with consistent seasonal cycles. |

**Method selection logic:**

1. If fewer than `MIN_N_VALUE` (default: 8) weeks of sales history → insufficient data, use SMA with available history and flag as "Low Confidence."
2. Run seasonal decomposition test (e.g., strength of seasonal component > threshold). If seasonal → Holt-Winters.
3. Run trend detection (e.g., Mann-Kendall or simple slope significance). If trending but not seasonal → EWMA.
4. Otherwise → SMA.

**The method selection logic and its parameters should be surfaced in the UI** so that the user can audit why a SKU received a particular method and override if needed.

**Output:** `forecasted_weekly_demand` per SKU per warehouse, stored as a time series for the forward planning horizon (52 weeks minimum).

### 1.4 ABCD Revenue Tiering

Classification runs weekly, before safety stock calculation.

**Procedure:**

1. Query trailing 52-week revenue per SKU from `sale.order.line` (summed across all warehouses — classification is global, not per-warehouse).
2. SKUs with zero trailing 12-month revenue → **Tier D (Dormant).**
3. Sort remaining SKUs by revenue descending.
4. Compute cumulative revenue percentage running top-down.
5. Assign tiers:
   - **A:** Cumulative ≤ 70%
   - **B:** 70–90%
   - **C:** > 90%
6. Apply override rules (see below).
7. Map tier → Z-Score → feed into safety stock.

**Tier parameters:**

| Tier | Revenue Band | Service Level | Z-Score |
|---|---|---|---|
| A | Top 70% cumulative | 97% | 1.881 |
| B | Next 20% (70–90%) | 95% | 1.645 |
| C | Remaining 10% | 90% | 1.282 |
| D | Zero revenue (12M) | N/A | N/A — ROQ = 0 |

Revenue band thresholds (70/20/10), service levels, and Z-Scores are all configurable in system settings.

**Override rules:**

| Rule | Behaviour | Rationale |
|---|---|---|
| **New Product** | SKUs with < `MIN_N_VALUE` weeks of history default to Tier A | Protect new launches into retail accounts |
| **Ranged Account Floor** | SKUs actively ranged in Briscoes/HN/Animates/PetStock cannot drop below Tier B | Prevent OOS on contractually ranged items. Requires an "actively ranged" flag on the product or a manual override list. |
| **Manual Override** | Per-SKU tier floor via Odoo field | Sets a minimum tier; revenue can still classify higher |
| **Reclassification Dampener** | A SKU must remain in a new tier band for 4 consecutive weekly runs before reclassification takes effect | Prevents single large orders from causing tier volatility |

**Dormant (Tier D) handling:**

- ROQ = 0 regardless of other inputs.
- Excluded from container fill planning.
- Separated in the UI (own tab or filtered view).
- Reactivation: any sale moves the SKU back to active pool, defaults to Tier A until MIN_N_VALUE weeks of history accumulate.

**Persisted fields per SKU (for dampener):**

- `abc_tier_confirmed` — last confirmed tier (CHAR)
- `abc_tier_pending` — current calculated tier if different from confirmed (CHAR, nullable)
- `abc_weeks_in_pending` — weeks the pending tier has been stable (INTEGER)

### 1.5 Safety Stock Calculation

```
Safety Stock = Z × σ_demand × √(LT_weeks)
```

Where:
- `Z` = Z-Score from ABCD tier (or supplier override if configured)
- `σ_demand` = standard deviation of weekly demand over the lookback period, per SKU per warehouse
- `LT_weeks` = supplier lead time in weeks

**Per warehouse.** Each warehouse gets its own safety stock based on its own demand variability.

**Minimum n:** If a SKU has fewer than `MIN_N_VALUE` (default: 8) data points with non-zero demand, σ cannot be reliably estimated. In this case, use a fallback: `σ = 0.5 × mean_demand` (coefficient of variation = 0.5, conservative default).

### 1.6 Configurable Parameters

All parameters below must be configurable at system level with optional per-supplier overrides. **Override behaviour: replace, not add.** A supplier-level override completely replaces the system default for that parameter.

| Parameter | System Default | Per-Supplier Override | Notes |
|---|---|---|---|
| Total Lead Time (Days) | 100 | Yes | Override replaces default |
| Review Interval (Days) | 30 | Yes | Override replaces default |
| Service Level Factor | 0.97 | Yes (also driven by ABCD tier) | Tier takes precedence unless supplier override is explicitly set |
| Lookback Weeks (History) | 156 | No (system-wide) | |
| SMA Window (Weeks) | 52 | No (system-wide) | |
| Min n Value (Std Dev) | 8 | No (system-wide) | |
| ABC Revenue Bands | 70 / 20 / 10 | No (system-wide) | |
| Dampener Weeks | 4 | No (system-wide) | |
| MOQ Enforcement Enabled | True | No (system-wide) | When disabled, MOQ is flagged but orders are not raised to the supplier minimum — useful for scenario comparison or when MOQ data is incomplete |

**Supplier override expiry:** Optional `override_expiry_date` field. If set, the override automatically reverts to system default after the specified date. Use case: setting a 90-day review interval for a supplier ahead of CNY and having it auto-revert without manual intervention.

---

## Layer 2: Procurement Engine

### 2.1 Inventory Policy: (s, S) Periodic Review

The model implements an (s, S) periodic-review inventory policy per SKU per warehouse:

```
Reorder Point (s) = Out Level = Demand × LT_weeks + Safety Stock
Order-Up-To (S)   = Demand × (LT_weeks + Review_weeks) + Safety Stock
ROQ (Raw)          = max(0, S − Inventory Position)
Inventory Position = SOH + Confirmed PO Qty (inbound to this warehouse)
```

Where:
- `Demand` = `forecasted_weekly_demand` for this SKU at this warehouse
- `LT_weeks` = total lead time in weeks (supplier-specific or default)
- `Review_weeks` = review interval in weeks (supplier-specific or default)
- `Safety Stock` = per 1.5 above (warehouse-specific)

**Projected Inventory at Delivery:**

```
Projected Inventory = Inventory Position − (Demand × LT_weeks)
```

Negative values indicate real projected OOS (stock depleted before the next order could arrive). This is distinct from safety stock breach and is used as the urgency signal for consolidation push/pull decisions (see Layer 3).

### 2.2 ROQ Pipeline

Run weekly (or on-demand). Steps in order:

1. **ABCD Classification** (Layer 1.4)
2. **Demand Forecast** per SKU per warehouse (Layer 1.3)
3. **Safety Stock** per SKU per warehouse (Layer 1.5)
4. **ROQ Calculation** per SKU per warehouse (Layer 2.1)
5. **Pack Size Rounding** — round ROQ up to nearest pack size multiple
6. **Aggregate by Supplier** — sum ROQ across warehouses for each supplier, preserving per-warehouse breakdown
7. **MOQ Enforcement** (if `roq.enable_moq_enforcement` is enabled — see 2.2a) — compare per-SKU supplier aggregate against `product.supplierinfo.min_qty`; raise to MOQ if below; distribute uplift across warehouses by cover priority; flag affected lines
8. **Container Fitting** (Layer 2.3)
9. **FOB Port Consolidation** (Layer 3.1)
10. **Output** — ROQ results, consolidation recommendations, freight tender candidates

### 2.2a MOQ Enforcement

Applies after supplier aggregation (Step 6), before container fitting. Only active when `roq.enable_moq_enforcement = True`.

**Source:** `product.supplierinfo.min_qty` — Odoo's native minimum order quantity field per product per supplier. No new field required; if multiple `supplierinfo` records exist for the same supplier, use the one matching the active vendor pricelist.

**Logic per SKU per supplier:**

```
supplier_total_roq = Σ roq_pack_rounded across all warehouses for this SKU + supplier
moq               = product.supplierinfo.min_qty (0 or null = no MOQ enforced)

if moq > 0 and supplier_total_roq < moq:
    moq_uplift_qty = moq − supplier_total_roq
    supplier_total_roq = moq
    moq_flag = True
```

**Uplift distribution** — the additional units are allocated back to warehouses in this priority order:
1. Warehouse with the lowest `weeks_of_cover_at_delivery` (most cover-constrained)
2. Proportionally across remaining warehouses if cover is equal
3. Never allocate uplift to a warehouse that would exceed 26 weeks of cover at delivery (configurable `roq.max_padding_weeks_cover`)

**When enforcement is disabled:** MOQ is still computed and stored in `supplier_moq` and `moq_flag` is set, but `moq_uplift_qty` is 0 and the order quantity is not raised. This allows side-by-side comparison of what enforcement would have changed.

**Missing MOQ data:** If `product.supplierinfo.min_qty` is null or zero for an active ROQ-managed SKU, the SKU is excluded from MOQ enforcement (treated as no minimum) and flagged in the Data Quality Report.

**Proactive mode (12-month plan):** If a planned order for a given month is below MOQ, the engine flags the line as **MOQ-Gated** rather than automatically rolling quantity forward. A human decision is required — either pull demand forward, skip the cycle, or accept an unplanned early order.

### 2.3 Container Fitting

Per supplier, the containerisation logic:

1. Calculate total CBM for the supplier's aggregated ROQ (all warehouses combined — a single container ships to port, then splits domestically).
2. Greedily assign SKUs to the smallest feasible container type.
3. If utilisation < threshold (configurable, default: 50%) → recommend LCL.
4. If utilisation ≥ threshold → recommend FCL, padding remaining capacity by proportionally increasing quantities of the ordered SKUs.
5. Track and display:
   - Container type (20', 40', 40'HQ, or LCL)
   - Fill percentage
   - Excess units added for container fill (padding)
   - Padding as % of demand-driven ROQ

**Padding allocation:** When filling remaining container capacity, prioritise padding to:
1. A-tier SKUs first (highest service level benefit from extra stock)
2. SKUs with the longest projected weeks-of-cover shortfall
3. Avoid padding SKUs that already have > X weeks cover at delivery (configurable, default: 26 weeks)

**Per-warehouse split:** Container ships to the nearest NZ port. Contents are split to destination warehouses per the per-warehouse ROQ breakdown. The container fitting operates on the combined volume; the per-warehouse allocation is a downstream split.

**Prerequisite data:** CBM per unit and Pack Size must be populated on the product record. SKUs missing this data are flagged in the output as "Unassigned" and excluded from container planning. The UI should surface a list of active SKUs with missing CBM/Pack Size for data cleanup.

### 2.4 Output Columns

The weekly ROQ output should include (per SKU, per warehouse):

| Column | Description |
|---|---|
| Supplier | Supplier name |
| FOB Port | Supplier's FOB port (for consolidation grouping) |
| Product Code | SKU code |
| Product Name | SKU description |
| Warehouse | Destination warehouse |
| ABC Tier | A/B/C/D |
| Trailing 12M Revenue | From ABCD classification |
| Cumulative Revenue % | Running total |
| Tier Override | Active override if any |
| SOH | Stock on hand at this warehouse |
| Confirmed PO Qty | Inbound POs for this warehouse |
| Inventory Position | SOH + Confirmed PO |
| Forecasted Weekly Demand | Per warehouse |
| Forecast Method | SMA / EWMA / Holt-Winters |
| Safety Stock | Per warehouse |
| Z-Score (Tiered) | From ABCD tier |
| Out Level (s) | Reorder point |
| Order-Up-To (S) | Target level |
| ROQ (Raw) | Before rounding |
| ROQ (Pack-size Rounded) | After pack size rounding |
| Final ROQ (Containerized) | After container fill padding |
| CBM/Unit | From product master |
| Pack Size | From product master |
| Container Type | Assigned container or LCL |
| Container Fill % | Utilisation |
| Padding Units | Excess from container fill |
| Projected Inventory at Delivery | Urgency signal |
| Weeks of Cover at Delivery | Projected Inv / Weekly Demand |
| Supplier MOQ | Minimum order quantity from `product.supplierinfo.min_qty` (0 = not set) |
| MOQ Uplift Units | Units added to this warehouse line due to MOQ uplift (0 if none or enforcement disabled) |
| MOQ Flag | Y if this SKU's supplier aggregate was below MOQ before uplift |
| Notes | Missing data flags, warnings |

### 2.5 Alerts & Flags

| Condition | Alert |
|---|---|
| Projected Inventory at Delivery < 0 | **Real OOS Risk** — red flag |
| Projected Inventory < Safety Stock but > 0 | **Safety Stock Breach** — amber |
| Weeks of Cover at Delivery > 52 | **Overstock Warning** — flag |
| Confirmed PO pushes cover > 52 weeks | **Excess PO Warning** |
| Missing CBM or Pack Size on active SKU | **Data Gap** — blocks container assignment |
| Container fill < threshold | **LCL Recommended** |
| Supplier override expiry within 14 days | **Override Expiring** |
| Supplier aggregate ROQ < `supplier_moq` (enforcement enabled) | **Below MOQ** — order raised to MOQ; flag which warehouse(s) absorbed uplift |
| Active ROQ-managed SKU has no `product.supplierinfo.min_qty` set (enforcement enabled) | **Missing MOQ Data** — data quality flag; SKU excluded from MOQ enforcement this run |

---

## Layer 3: Shipment Planning & Consolidation

### 3.1 FOB Port Consolidation Engine

This is the key differentiator from the spreadsheet model. Instead of treating each supplier independently, the system groups purchase requirements by FOB port and optimises across suppliers.

**Consolidation key:** `fob_port` field on the supplier record. All suppliers sharing a FOB port are candidates for consolidation.

**Two operating modes:**

#### 3.1.1 Reactive Consolidation (Phase-in)

Triggered after the weekly ROQ run:

1. Group all supplier ROQs by FOB port.
2. For each port, sum total CBM across all suppliers.
3. If combined CBM fits a container at ≥ threshold utilisation → **recommend consolidated FCL.**
4. Evaluate push/pull tolerance (see 3.2) to determine if order timing can be aligned.
5. Output: consolidation recommendation with supplier grouping, combined CBM, container type, and any required push/pull adjustments.

#### 3.1.2 Proactive Consolidation (Target State)

Driven by the 12-month forecast:

1. For each FOB port, project monthly purchase requirements across all suppliers (using the forecast × review cycle).
2. Identify months where individual suppliers produce sub-container volumes but combined volumes are container-viable.
3. Generate a **consolidated shipment calendar** — planned shipment dates per FOB port with supplier/PO groupings.
4. Adjust individual supplier order placement dates to align with consolidated shipment windows.
5. The push/pull tolerance (3.2) constrains how far orders can be shifted.

### 3.2 Push/Pull Tolerance

The maximum amount an order can be pushed (delayed) or pulled (brought forward) to enable consolidation is determined by OOS urgency:

**Push (delay an order):**

```
max_push_days = f(min_projected_oos_days across all items in this supplier's order, across all warehouses)
```

Logic:
- If **any SKU in the order** at **any warehouse** has `Projected Inventory at Delivery < 0` (real OOS, not just safety stock breach) → **max push = 0 days.** Cannot delay.
- If no items are at real OOS risk, push tolerance is based on the tightest item:

| Minimum Weeks of Cover at Delivery (across all SKUs/warehouses in order) | Max Push |
|---|---|
| > 12 weeks | Up to 6 weeks |
| 8–12 weeks | Up to 4 weeks |
| 4–8 weeks | Up to 2 weeks |
| < 4 weeks | 0 (no push) |

**Pull (bring forward an order):**

Pulling forward is generally safer (ordering early means more stock, not less). Constraints:
- Supplier production readiness (can they deliver earlier?). This is an external constraint not modellable — default assumption is that pull is possible up to the review interval (30 days) unless the supplier indicates otherwise.
- Cash flow impact — pulling forward increases working capital earlier. The system should flag the cash impact of any pull recommendation.
- Max pull = review interval (default 30 days), configurable.

**OOS determination is "real OOS" only.** Dipping into safety stock is acceptable for consolidation timing — running to zero is not. The threshold is `Projected Inventory at Delivery < 0`, not `< Safety Stock`.

### 3.3 Consolidation Group Model

When the engine recommends a consolidation, it creates a **Shipment Group** record:

```
shipment_group
├── id (auto)
├── fob_port
├── planned_ship_date
├── container_type (20' / 40' / 40'HQ / LCL)
├── total_cbm
├── fill_percentage
├── status (draft / confirmed / tendered / booked)
├── freight_tender_id (FK → mml_freight_forwarding, nullable)
├── destination_warehouses[] (which warehouses receive from this shipment)
├── notes
│
├── shipment_group_line[]
│   ├── supplier_id
│   ├── purchase_order_id (nullable — PO may not exist yet in proactive mode)
│   ├── cbm
│   ├── push_pull_days (positive = pushed, negative = pulled, 0 = as planned)
│   ├── push_pull_reason
│   └── oos_risk_flag (boolean — any item in this supplier's order at real OOS risk)
```

**Status flow:**

```
draft → confirmed (user approves grouping)
      → tendered (freight tender sent via DSV API)
      → booked (freight booking confirmed)
```

In **proactive mode**, shipment groups are created in `draft` status from the 12-month plan. They represent planned future consolidations. As the ship date approaches, the user confirms and the system generates POs and freight tenders.

In **reactive mode**, shipment groups are created from the weekly ROQ output. They link to existing POs and are ready to tender immediately.

### 3.4 DSV Freight Bridge

The `shipment_group` integrates with the `mml_freight_forwarding` module (see separate sprint spec):

1. When a shipment group moves to `confirmed`, the system can auto-generate a freight tender request via the DSV API.
2. The tender includes: FOB port, destination port(s), total CBM, container type, number of suppliers/POs, target ship date.
3. Multiple POs from different suppliers are linked to a single freight booking via the shipment group.
4. The freight module returns booking confirmation, vessel details, ETD/ETA — which feeds back into the ROQ model for lead time tracking.

**If the freight module is not yet available:** The shipment group still functions as an internal planning tool. The `freight_tender_id` remains null, and the user manually arranges freight. The consolidation logic and its value (better container utilisation, freight cost saving) are independent of the API integration.

---

## Layer 3B: 12-Month Forward Plan

### 3B.1 Purpose

Generate a rolling 12-month procurement plan that serves two audiences:

1. **Supplier-facing:** Per-supplier production/order schedule showing expected order quantities and timing. Exported as PDF/spreadsheet and emailed to suppliers to help them plan production.
2. **Internal:** Cash flow and working capital forecast based on projected landed costs of upcoming orders.

### 3B.2 Calculation

For each supplier, for each month in the forward 12-month window:

```
monthly_demand(sku, warehouse) = forecasted_weekly_demand × 4.33
monthly_requirement(sku) = Σ monthly_demand(sku, wh) across all warehouses
```

Planned order quantities are driven by the review cycle:

```
orders_per_year = 365 / review_interval_days
order_qty_per_cycle(sku) = forecasted_weekly_demand × (review_interval_days / 7) × num_warehouses
```

This is then grouped by supplier and scheduled across the 12-month window, accounting for:
- Lead time (when to place order to receive by target date)
- Container consolidation opportunities (align with other suppliers at same FOB port)
- Known constraints (CNY shutdown — configurable supplier-level holiday periods)

### 3B.3 Supplier-Facing Export

Per-supplier PDF/spreadsheet containing:

| Month | SKU | Product Name | Expected Order Qty | Estimated Order Date | Estimated Ship Date | Notes |
|---|---|---|---|---|---|---|
| Mar 2026 | vcm24 | Volere Milano 24pc | 784 | 15 Feb 2026 | 01 Mar 2026 | Consolidated with Binzhou |
| Apr 2026 | vcm56 | Volere Milano 56pc | 606 | 15 Mar 2026 | 01 Apr 2026 | |

Include a summary header with:
- Supplier name and contact
- FOB port
- Lead time assumption used
- Currency / pricing basis
- Total units and CBM for the 12-month period

### 3B.4 Internal Cash Flow View

Monthly summary across all suppliers:

| Month | Total Purchase Cost (FOB) | Estimated Freight | Estimated Duty/GST | Total Landed Cost | Container Count |
|---|---|---|---|---|---|
| Mar 2026 | $X | $Y | $Z | $Total | 3 × 20', 1 × 40' |

- **Purchase cost:** from Odoo purchase price on supplier pricelist.
- **Freight estimate:** from historical $/CBM rates per route or DSV rate card if available.
- **Duty/GST:** from landed cost tracking in Odoo (product-level duty rates).
- **Container count:** from the consolidation engine's planned shipments.

---

## Data Model Summary

### New Models

| Model | Purpose |
|---|---|
| `roq.forecast.run` | Header record for each weekly ROQ run |
| `roq.forecast.line` | Per-SKU per-warehouse ROQ result line |
| `roq.abc.classification` | ABCD tier history per SKU (for dampener logic) |
| `roq.shipment.group` | Consolidation group linking multiple suppliers/POs |
| `roq.shipment.group.line` | Per-supplier line within a consolidation group |
| `roq.forward.plan` | 12-month forward plan header per supplier |
| `roq.forward.plan.line` | Monthly per-SKU line within the forward plan |
| `roq.system.settings` | System-level configurable parameters |

### Extended Models (fields added to existing Odoo models)

| Model | Fields Added |
|---|---|
| `product.template` | `abc_tier`, `abc_tier_confirmed`, `abc_tier_pending`, `abc_weeks_in_pending`, `abc_tier_override`, `abc_trailing_revenue`, `cbm_per_unit`, `pack_size`, `abc_override_floor` |
| `res.partner` (supplier) | `fob_port`, `supplier_lead_time_days`, `supplier_review_interval_days`, `supplier_service_level`, `override_expiry_date`, `supplier_holiday_periods` |
| `stock.warehouse` | `country_code` (if not already present), `is_active_for_roq` (boolean to include/exclude from forecast) |
| `purchase.order` | `shipment_group_id` (FK to consolidation group) |

### Configuration (roq.system.settings)

| Setting | Default | Type |
|---|---|---|
| `default_lead_time_days` | 100 | Integer |
| `default_review_interval_days` | 30 | Integer |
| `default_service_level` | 0.97 | Float |
| `lookback_weeks` | 156 | Integer |
| `sma_window_weeks` | 52 | Integer |
| `min_n_value` | 8 | Integer |
| `abc_band_a_pct` | 70 | Integer |
| `abc_band_b_pct` | 20 | Integer |
| `abc_dampener_weeks` | 4 | Integer |
| `container_lcl_threshold_pct` | 50 | Integer |
| `max_padding_weeks_cover` | 26 | Integer |
| `max_pull_days` | 30 | Integer |

---

## UI Views

### Dashboard (Tree/List)

- **ROQ Run Results:** Filterable by supplier, warehouse, ABC tier, container type. Sortable by OOS risk, weeks of cover, ROQ quantity. Quick-filter button: **MOQ Flags** — shows only lines where `moq_flag = True`. The run header summary bar displays whether MOQ enforcement was active for that run (snapshot from `roq.forecast.run.enable_moq_enforcement`).
- **Consolidation Groups:** Kanban view by status (draft / confirmed / tendered / booked). Card shows FOB port, supplier count, CBM, fill %, ship date.
- **12-Month Plan:** Pivot table by supplier × month, showing quantities and costs. MOQ-Gated lines (proactive mode, below MOQ) shown with a distinct indicator and excluded from container fill calculations until resolved.

### Forms

- **Supplier form extension:** FOB port, lead time/review interval overrides, holiday periods, override expiry.
- **Product form extension:** ABC tier (read-only, auto-calculated), tier override, CBM, pack size. Supplier MOQ is read from the standard `product.supplierinfo` tab — no new field, but the Vendor Prices tab should surface `min_qty` with a clear label ("Min Order Qty") for data quality awareness.
- **Shipment Group form:** Consolidation detail with linked POs, push/pull annotations, freight tender status.
- **ROQ Settings (`res.config.settings`):** MOQ Enforcement toggle (checkbox, enabled by default). When unchecked, orders are not raised to MOQ minimums — the flag and uplift columns are still computed for visibility but quantities are unchanged. Intended for scenario comparison runs or during initial MOQ data population.

### Reports

- **Supplier Order Schedule (PDF):** Per-supplier 12-month plan export for emailing.
- **Cash Flow Projection (XLSX):** Monthly landed cost forecast across all suppliers.
- **Data Quality Report:** SKUs missing CBM, pack size, or with low-confidence forecasts.
- **ABC Distribution Report:** SKU count and revenue by tier, total safety stock per tier.

---

## Phased Rollout

| Phase | Scope | Dependency |
|---|---|---|
| **Phase 1** | Layer 1 (Forecast Engine) + Layer 2 (ROQ calculation) — replaces the spreadsheet. Single-warehouse mode matching current functionality, then extend to multi-warehouse. ABCD tiering included. | None |
| **Phase 2** | Layer 3 reactive consolidation — FOB port grouping, push/pull, shipment groups. | Phase 1 |
| **Phase 3** | Layer 3B forward plan — 12-month procurement calendar, supplier exports, cash flow view. | Phase 1 |
| **Phase 4** | DSV freight bridge — auto-tender from shipment groups. | Phase 2 + `mml_freight_forwarding` module |

Phases 2/3 can run in parallel. Phase 4 depends on the freight module (see separate sprint spec).

---

## Testing & Validation

### Phase 1 Validation

- Run the Odoo ROQ engine against the same input data as the current spreadsheet.
- Compare output line-by-line: forecasted demand, safety stock, out level, ROQ, container assignment should match (within rounding tolerance) for the same parameters.
- Verify ABCD tier assignments by manually checking cumulative revenue sort.
- Verify multi-warehouse: same SKU should have different ROQ at each warehouse if demand patterns differ.

### Regression Checks

- Changing a supplier's lead time override should only affect that supplier's SKUs.
- Expiring an override should revert to system default on the next run.
- Dormant SKUs should never generate ROQ > 0.
- New SKUs should default to Tier A and use the conservative σ fallback.
- Push/pull should never recommend delaying an order when any item is at real OOS risk.
- When MOQ enforcement is disabled, `moq_flag` is still populated but `moq_uplift_qty` is always 0 and final quantities are unchanged.
- When MOQ enforcement is enabled, no SKU's supplier aggregate should be less than `product.supplierinfo.min_qty` (where set).
- MOQ uplift should always be distributed first to the warehouse with the lowest `weeks_of_cover_at_delivery`.
- SKUs without `product.supplierinfo.min_qty` set should appear in the Data Quality Report as **Missing MOQ Data** when enforcement is enabled — they must not be silently skipped.
- Disabling MOQ enforcement mid-session and re-running should produce a run with `enable_moq_enforcement = False` in the snapshot, distinct from a prior enforcement-enabled run.

---

## Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| Per-warehouse independent forecast | Each warehouse serves different customers/regions; demand patterns differ. Combined forecasting would understate variance at each location. |
| 52-week SMA window (not 156) | 156-week average is too slow to react. A product declining over 2 years still shows inflated demand. 52 weeks captures a full seasonal cycle with better recency. |
| ABCD tier is global (not per-warehouse) | Revenue classification should be consistent — a product is either strategically important or it isn't, regardless of which warehouse sells more of it. |
| FOB port as consolidation key | Physically the only viable grouping — containers are packed at port. Consolidation across ports would require cross-docking which is a different (and expensive) operation. |
| Push/pull based on real OOS not SS breach | Safety stock exists to absorb variability. Dipping into it is normal. Consolidation should optimise for cost without creating actual stockouts. Using SS breach as the trigger would be too conservative and would rarely allow any push. |
| Override replaces, never sums | Adding override + default caused a real bug (CNY review interval). Replace semantics are unambiguous and match user expectation. |
| Supplier holiday periods (e.g., CNY) | Rather than manual override + remember to revert, model the known shutdown periods and automatically adjust lead times and order timing around them. |
