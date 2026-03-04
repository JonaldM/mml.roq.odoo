# MML ROQ Forecast — Odoo 19 Module

Replaces the Excel-based ROQ (Reorder Quantity) system for MML Consumer Products Ltd with a native Odoo 19 module. Covers demand forecasting, ABCD product tiering, safety stock, container planning, multi-supplier consolidation, and a 12-month forward procurement plan.

**Target:** Self-hosted Odoo 19 · **Company:** MML Consumer Products Ltd (NZ) · ~400 SKUs across 5 brands

---

## Modules

| Module | Purpose | Depends on |
|---|---|---|
| `mml_roq_forecast` | Demand forecast, ROQ calculation, consolidation, 12-month plan | `mml_base`, `sale`, `purchase`, `stock`, `stock_landed_costs` |

`mml_roq_forecast` integrates with `mml_freight` (freight orchestration) and `stock_3pl_core` (3PL) via the `mml_base` service locator — no hard module dependency. Install each module independently; the optional bridge modules (`mml_roq_freight`, `mml_freight_3pl`) wire them together when both are present.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  LAYER 3: SHIPMENT PLANNING                              │
│  FOB consolidation · push/pull · 12-month forward plan   │
├──────────────────────────────────────────────────────────┤
│  LAYER 2: PROCUREMENT ENGINE                             │
│  (s,S) policy · MOQ enforcement · container fitting      │
├──────────────────────────────────────────────────────────┤
│  LAYER 1: FORECAST ENGINE                                │
│  ABCD tiering · SMA/EWMA/Holt-Winters · safety stock    │
└──────────────────────────────────────────────────────────┘
```

### ROQ Pipeline (step order)

1. ABCD classification (global, with 4-run dampener)
2. Demand forecast per SKU per warehouse (SMA / EWMA / Holt-Winters)
3. Safety stock per SKU per warehouse (`Z × σ × √LT`)
4. ROQ calculation — (s,S) periodic review policy
5. Pack size rounding
6. Aggregate by supplier
7. **MOQ enforcement** — raise to `product.supplierinfo.min_qty` if below; distribute uplift to lowest-cover warehouse
8. Container fitting — greedy CBM assignment, LCL/FCL recommendation, intelligent padding
9. FOB port consolidation → `roq.shipment.group` records

---

## Pre-Install Checklist

Complete these steps on the Odoo instance **before** installing:

- [ ] Uninstall `roq_forecast`, `custom_purchase_containers`, `freight_tender_email`
- [ ] Confirm `cbm_per_unit` and `pack_size` don't already exist on `product.template`
- [ ] Confirm `fob_port` doesn't already exist on `res.partner`
- [ ] Identify `tt.model.config` / `tt.predicted.results` — keep or remove?
- [ ] **Backup the database**

---

## Installation

```bash
# Install mml_base first, then mml_roq_forecast
odoo-bin -d <db> -i mml_base,mml_roq_forecast --stop-after-init

# To connect with freight and 3PL, install bridge modules too:
odoo-bin -d <db> -i mml_roq_freight,mml_freight_3pl --stop-after-init
```

After install:

1. Go to **Settings → General Settings → ROQ Forecast** and confirm default parameters
2. Set `cbm_per_unit` and `pack_size` on all active products (ROQ / Procurement tab)
3. Set `fob_port` and `min_qty` on supplier records
4. Run a manual ROQ cycle from **ROQ Forecast → ROQ Runs** and compare output against the spreadsheet before enabling the weekly cron

---

## Configuration

All settings live in **Settings → General Settings → ROQ Forecast**.

| Setting | Default | Notes |
|---|---|---|
| Default Lead Time (Days) | 100 | Overridden per-supplier |
| Default Review Interval (Days) | 30 | Overridden per-supplier |
| Default Service Level | 0.970 | Z-score input for safety stock; overridden per-supplier |
| Lookback Weeks | 156 | 3 years of sales history |
| SMA Window (Weeks) | 52 | Falls back to full lookback if < 52 weeks data |
| Min N Value | 8 | Minimum data points for reliable std dev |
| ABC Dampener (Weeks) | 4 | Runs before tier reclassification takes effect |
| ABCD Trailing Revenue Weeks | 52 | Rolling revenue window used for ABCD tier classification |
| Container LCL Threshold (%) | 50 | Below this utilisation → recommend LCL |
| Max Padding Weeks Cover | 26 | A-tier container padding ceiling (weeks of cover) |
| **Enforce Supplier MOQs** | **On** | When off, MOQ flag still shown but quantities unchanged |

Per-supplier overrides (lead time, review interval, service level, FOB port, holiday periods) are set on the supplier record under the **ROQ / Freight** tab.

---

## Data to Populate Before First Run

| Field | Where | Notes |
|---|---|---|
| `cbm_per_unit` | Product → ROQ / Procurement tab | Required for container assignment |
| `pack_size` | Product → ROQ / Procurement tab | Required for pack rounding |
| `is_roq_managed` | Product → ROQ / Procurement tab | Uncheck to exclude a product |
| `purchase_incoterm_id` | Supplier → ROQ / Freight tab | Controls consolidation inclusion — FOB/FCA/EXW included; CIF/DDP/etc. excluded |
| `fob_port_id` | Supplier → ROQ / Freight tab | Select origin port from the curated UN/LOCODE list — required for consolidation grouping |
| `destination_port_id` | Supplier → ROQ / Freight tab | Default NZ port of discharge — flows into shipment group |
| `min_qty` | Supplier → Vendor Prices tab | Required for MOQ enforcement |
| `supplier_lead_time_days` | Supplier → ROQ / Freight tab | Leave blank to use system default |

SKUs missing `cbm_per_unit` or `pack_size` appear in the **Data Quality** flag on ROQ lines and are excluded from container planning. SKUs missing `min_qty` are flagged by the **Missing MOQ Data** filter in the results view.

Suppliers missing `fob_port_id` are silently excluded from FOB consolidation. Suppliers with a seller-freight incoterm (CIF, CFR, CIP, CPT, DAP, DPU, DDP) are also excluded — the seller is arranging the main freight leg so there is nothing for MML to consolidate.

### Freight Ports

20 ports are seeded on install (**ROQ Forecast → Configuration → Freight Ports**):

| Region | Ports |
|---|---|
| China (origin) | CNSHA Shanghai · CNNGB Ningbo-Zhoushan · CNSZX Shenzhen · CNNSA Nansha · CNQIN Qingdao · CNTXG Tianjin · CNXMN Xiamen |
| Vietnam (origin) | VNHPH Hai Phong · VNSGN Ho Chi Minh City |
| India (origin) | INNSA Nhava Sheva · INCHP Chennai |
| Australia | AUSYD Sydney · AUMEL Melbourne · AUBNE Brisbane |
| New Zealand (destination) | NZAKL Auckland · NZTRG Tauranga · NZNSN Nelson · NZCHC Christchurch · NZDUD Dunedin |

Port seed records use `noupdate="1"` — manual edits in Odoo survive module upgrades. Add new ports via the Configuration menu.

---

## Shipment Calendar

**ROQ Forecast → Shipment Calendar**

A planning calendar showing all `roq.shipment.group` records by ship date (ETD) through to delivery date. Designed for the procurement planner to space out inbound shipments and avoid saturating a receiving warehouse in a single week.

### Calendar Layout

Each shipment group renders as a card spanning from `target_ship_date` to `target_delivery_date`. Cards are colour-coded by state:

| Colour | State |
|---|---|
| Slate | Draft |
| Ocean blue | Confirmed |
| Amber | Tendered |
| Emerald | Booked |
| Rose | Delivered |
| Neutral | Cancelled |

Cards display origin port, container type, destination warehouse abbreviations (e.g. HLZ / CHC), and live freight ETA when `mml_freight` is installed.

### Drag-and-Drop Rescheduling

Dragging a card shifts `target_delivery_date`. The module automatically:
- Shifts `target_ship_date` by the same delta to preserve the transit window
- Re-evaluates `oos_risk_flag` on all supplier lines
- Posts a chatter message recording the date change ("pushed out / pulled forward by N days")
- **Only draft and confirmed groups are draggable.** Tendered, booked, delivered, and cancelled groups are locked.

For shifts exceeding the configurable threshold (default 5 days), the system checks for nearby same-origin groups within the consolidation window (default 21 days). If any are found, it sets a `consolidation_suggestion` badge on the group and surfaces a wizard offering the option to consolidate or keep separate.

### Warehouse Receiving Capacity

Set a weekly inbound capacity limit per warehouse under **Inventory → Configuration → Warehouses → ROQ Receiving Capacity**. Choose either CBM or TEU as the limit unit.

The `roq.warehouse.week.load` model calculates arriving volume per warehouse per week and returns a status:

| Status | Threshold |
|---|---|
| Green | < 70 % of capacity |
| Amber | 70 – 90 % of capacity |
| Red | ≥ 90 % of capacity |
| None | No capacity configured |

### Filters

The calendar ships with four default search filters:

| Filter | Domain |
|---|---|
| Draft | state = draft |
| Confirmed | state = confirmed |
| In Transit | state ∈ {tendered, booked} |
| Active (default) | state ∉ {delivered, cancelled} |

### Live Freight Status

When `mml_freight` is installed, each group's popover shows `freight_eta`, `freight_status`, and `freight_last_update` pulled live via the service locator. When `mml_freight` is not installed, these fields are empty — the calendar degrades gracefully.

### Calendar Configuration

| Parameter | Key | Default |
|---|---|---|
| Consolidation window | `roq.calendar.consolidation_window_days` | 21 |
| Reschedule threshold (days before proximity check runs) | `roq.calendar.reschedule_threshold_days` | 5 |

---

## Order Dashboard & Draft PO Raise

**ROQ Forecast → Order Dashboard**

Opens the latest completed ROQ run as an actionable two-tab view:

### Tab 1 — Urgency List

All active (non-dormant) SKUs sorted by weeks of cover at delivery, lowest first. OOS-risk rows (projected inventory at delivery < 0) highlighted in red; under-8-weeks rows in orange.

| Column | Notes |
|---|---|
| ABC Tier | Badge — A/B/C colour-coded |
| Product | |
| Supplier | |
| Warehouse | |
| Cover (wks) | Primary sort — negative = OOS risk |
| Proj. Inv at Delivery | Negative = OOS |
| Order Qty | ROQ (Containerized) suggested quantity |
| Container | LCL / 20GP / 40GP / 40HQ |
| Flags | OOS / Safety Stock / MOQ notes |

### Tab 2 — Order by Supplier

One row per supplier from this run's shipment groups. Shows SKU count, CBM contribution, container type, OOS risk flag, and linked PO (if already raised).

**Raise Draft PO** button on each supplier row opens a wizard pre-populated with all order lines for that supplier. The wizard:

- Defaults to **ROQ (Containerized)** quantities — includes A-tier container padding
- Toggle **"Include container padding"** OFF to switch all lines to **ROQ (Pack Rounded)** demand-only quantities
- Individual line quantities remain editable after toggling
- On confirm, creates one `draft` purchase order per destination warehouse, resolves `price_unit` from vendor pricelists (falls back to 0.00 for buyer to fill in)
- Links the raised PO back to the shipment group supplier line

> **Note:** POs are always raised in `draft` state. They are never auto-confirmed.

---

## Running Tests

```bash
# Full module test suite
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast
odoo-bin --test-enable -d <db> --test-tags mml_freight_forwarding

# Individual test classes
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestAbcClassifier
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestMoqEnforcer
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestContainerFitter
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestRaisePoWizard
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestWarehouseReceivingCapacityFields
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestFreightStatusFields
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestRescheduleWrite
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestConsolidationSuggestion
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestWarehouseWeekLoad

# With log output
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast --log-level=test
```

---

## Key Design Decisions

| Decision | Rule |
|---|---|
| ABCD tier | Per-warehouse — each warehouse runs its own pareto ranking; a product can be A in AKL and C in WLG. `product.template.abc_tier` stores the global (all-warehouses combined) tier for display only. |
| Override semantics | Replace, never add — supplier override completely replaces system default |
| MOQ uplift distribution | Extra units go to warehouse with lowest weeks-of-cover at delivery |
| Push/pull hard block | `projected_inventory_at_delivery < 0` on **any** SKU → push = 0 (no delay) |
| Dormant (Tier D) | ROQ always 0; excluded from container planning |
| Stock discrepancies | Never auto-corrected — flagged for human review |
| DSV credentials | Stored in `ir.config_parameter` only — never hardcoded |
| Port model | `roq.port` with UN/LOCODE (5-char, unique, auto-uppercase) — no free-text port fields |
| `fob_port` Char | Stored related from `fob_port_id.code` — consolidation grouping key unchanged downstream |
| Incoterm filter | CIF/CFR/CIP/CPT/DAP/DPU/DDP excluded from consolidation; unset = assumed FOB |

---

## Docs

| File | Contents |
|---|---|
| `ROQ_Odoo_Module_Spec.md` | Full functional specification |
| `ROQ_Clean_Slate_Model_Mapping.md` | All models and fields |
| `roq-freight-interface-contract.md` | ROQ ↔ freight module interface |
| `docs/plans/2026-03-01-sprint-roadmap.md` | Sprint overview and validation milestones |
| `docs/plans/2026-03-01-sprint-0-foundation.md` | Foundation sprint |
| `docs/plans/2026-03-01-sprint-1-forecast-engine.md` | Forecast engine sprint |
| `docs/plans/2026-03-01-sprint-2-roq-engine.md` | ROQ engine + MOQ enforcement sprint |
| `docs/plans/2026-03-01-sprint-3-consolidation.md` | Consolidation engine sprint |
| `docs/plans/2026-03-01-sprint-4-forward-plan-freight.md` | Forward plan + DSV freight bridge sprint |
| `docs/plans/2026-03-02-order-dashboard-po-raise-design.md` | Order Dashboard + Draft PO Raise design |
| `docs/plans/2026-03-02-order-dashboard-po-raise.md` | Order Dashboard + Draft PO Raise implementation plan |
| `docs/plans/2026-03-04-shipment-calendar-design.md` | Shipment Calendar design |
| `docs/plans/2026-03-04-shipment-calendar-implementation.md` | Shipment Calendar implementation plan |


---

## Weekly Cron

The weekly ROQ run is installed with `active=False`. Enable it manually in **Settings → Technical → Automation → Scheduled Actions → ROQ: Weekly Forecast Run** after the first manual run has been validated against the spreadsheet.
