# MML ROQ Forecast — Odoo 19 Module

Replaces the Excel-based ROQ (Reorder Quantity) system for MML Consumer Products Ltd with a native Odoo 19 module. Covers demand forecasting, ABCD product tiering, safety stock, container planning, multi-supplier consolidation, and a 12-month forward procurement plan.

**Target:** Self-hosted Odoo 19 · **Company:** MML Consumer Products Ltd (NZ) · ~400 SKUs across 5 brands

---

## Modules

| Module | Purpose | Depends on |
|---|---|---|
| `mml_freight_forwarding` | Freight tender, booking, and tracking (DSV API) | `base`, `purchase`, `stock` |
| `mml_roq_forecast` | Demand forecast, ROQ calculation, consolidation, 12-month plan | `mml_freight_forwarding` + std modules |

Install `mml_freight_forwarding` first — it has no dependency on the ROQ module.

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
# Install both modules
odoo-bin -d <db> -i mml_freight_forwarding,mml_roq_forecast --stop-after-init
```

After install:

1. Go to **Settings → General Settings → ROQ Forecast** and confirm default parameters
2. Set `cbm_per_unit` and `pack_size` on all active products (ROQ / Procurement tab)
3. Set `fob_port` and `min_qty` on supplier records
4. Run a manual ROQ cycle from **MML Operations → ROQ → ROQ Runs** and compare output against the spreadsheet before enabling the weekly cron

---

## Configuration

All settings live in **Settings → General Settings → ROQ Forecast**.

| Setting | Default | Notes |
|---|---|---|
| Default Lead Time (Days) | 100 | Overridden per-supplier |
| Default Review Interval (Days) | 30 | Overridden per-supplier |
| Lookback Weeks | 156 | 3 years of sales history |
| SMA Window (Weeks) | 52 | Falls back to full lookback if < 52 weeks data |
| Min N Value | 8 | Minimum data points for reliable std dev |
| ABC Dampener (Weeks) | 4 | Runs before tier reclassification takes effect |
| Container LCL Threshold (%) | 50 | Below this utilisation → recommend LCL |
| **Enforce Supplier MOQs** | **On** | When off, MOQ flag still shown but quantities unchanged |

Per-supplier overrides (lead time, review interval, service level, FOB port, holiday periods) are set on the supplier record under the **ROQ / Freight** tab.

---

## Data to Populate Before First Run

| Field | Where | Notes |
|---|---|---|
| `cbm_per_unit` | Product → ROQ / Procurement tab | Required for container assignment |
| `pack_size` | Product → ROQ / Procurement tab | Required for pack rounding |
| `is_roq_managed` | Product → ROQ / Procurement tab | Uncheck to exclude a product |
| `fob_port` | Supplier → ROQ / Freight tab | Required for FOB consolidation grouping |
| `min_qty` | Supplier → Vendor Prices tab | Required for MOQ enforcement |
| `supplier_lead_time_days` | Supplier → ROQ / Freight tab | Leave blank to use system default |

SKUs missing `cbm_per_unit` or `pack_size` appear in the **Data Quality** flag on ROQ lines and are excluded from container planning. SKUs missing `min_qty` are flagged by the **Missing MOQ Data** filter in the results view.

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

# With log output
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast --log-level=test
```

---

## Key Design Decisions

| Decision | Rule |
|---|---|
| ABCD tier | Global (not per-warehouse); forecasts, safety stock, ROQ are per-warehouse |
| Override semantics | Replace, never add — supplier override completely replaces system default |
| MOQ uplift distribution | Extra units go to warehouse with lowest weeks-of-cover at delivery |
| Push/pull hard block | `projected_inventory_at_delivery < 0` on **any** SKU → push = 0 (no delay) |
| Dormant (Tier D) | ROQ always 0; excluded from container planning |
| Stock discrepancies | Never auto-corrected — flagged for human review |
| DSV credentials | Stored in `ir.config_parameter` only — never hardcoded |

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

---

## Weekly Cron

The weekly ROQ run is installed with `active=False`. Enable it manually in **Settings → Technical → Automation → Scheduled Actions → ROQ: Weekly Forecast Run** after the first manual run has been validated against the spreadsheet.
