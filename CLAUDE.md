# MML ROQ Forecast & Freight Forwarding — Module Context

> **For Claude:** Read this file AND `E:\ClaudeCode\projects\mml.odoo.apps\CLAUDE.md` before touching any code.
> Full functional spec: `ROQ_Odoo_Module_Spec.md` | Model definitions: `ROQ_Clean_Slate_Model_Mapping.md`

---

## What This Is

Two new Odoo 19 modules replacing an external Excel-based ROQ (Reorder Quantity) system:

| Module | Purpose | Depends On |
|---|---|---|
| `mml_freight_forwarding` | Freight tender, booking, tracking (DSV API) | `base`, `purchase`, `stock` |
| `mml_roq_forecast` | Demand forecast, ROQ calc, consolidation, 12-month plan | `mml_freight_forwarding` + std modules |

**Build `mml_freight_forwarding` first** — it has no dependency on the ROQ module.

---

## Pre-Build Checklist

| # | Action | Status |
|---|---|---|
| 1 | Uninstall `roq_forecast` module | ☐ |
| 2 | Uninstall `custom_purchase_containers` module | ☐ |
| 3 | Uninstall `freight_tender_email` module | ☐ |
| 4 | Confirm no other modules depend on the above three | ☐ |
| 5 | Confirm `cbm_per_unit` / `pack_size` don't already exist on `product.template` | ☐ |
| 6 | Confirm `fob_port` doesn't already exist on `res.partner` | ☐ |
| 7 | Identify `tt.model.config` / `tt.predicted.results` — keep or remove? | ☐ |
| 8 | Backup database before uninstalls | ☐ |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: SHIPMENT PLANNING                                     │
│  Consolidation engine, DSV freight bridge, 12-month plan        │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 2: PROCUREMENT ENGINE                                    │
│  ROQ calculation, container fitting, push/pull optimisation     │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 1: FORECAST ENGINE                                       │
│  Per-SKU per-warehouse demand forecast, ABCD tiering, SS calc   │
└─────────────────────────────────────────────────────────────────┘
```

**Phased rollout (build in this order):**

| Phase | Sprint | Scope |
|---|---|---|
| 0 | Sprint 0 | Module scaffolding, data models, settings |
| 1 | Sprint 1 | Layer 1: ABCD + demand forecast (SMA/EWMA/HW) + safety stock |
| 1 | Sprint 2 | Layer 2: ROQ (s,S) + container fitting + pipeline + UI |
| 2 | Sprint 3 | Layer 3: Reactive consolidation + push/pull + shipment groups |
| 3+4 | Sprint 4 | Layer 3B: 12-month plan + DSV freight bridge |

---

## Key File Paths

```
roq.model/
├── CLAUDE.md                               ← You are here
├── ROQ_Odoo_Module_Spec.md                 ← Full functional spec
├── ROQ_Clean_Slate_Model_Mapping.md        ← Model/field definitions
└── docs/plans/
    ├── 2026-03-01-sprint-roadmap.md        ← Sprint overview
    ├── 2026-03-01-sprint-0-foundation.md
    ├── 2026-03-01-sprint-1-forecast-engine.md
    ├── 2026-03-01-sprint-2-roq-engine.md
    ├── 2026-03-01-sprint-3-consolidation.md
    └── 2026-03-01-sprint-4-forward-plan-freight.md
```

**Module source (to be created by Sprint 0):**

```
mml_odoo/
├── mml_freight_forwarding/
│   ├── __manifest__.py
│   ├── models/
│   │   ├── freight_tender.py
│   │   ├── freight_quote.py
│   │   ├── freight_booking.py
│   │   └── freight_tracking_event.py
│   ├── views/
│   ├── security/
│   └── tests/
└── mml_roq_forecast/
    ├── __manifest__.py
    ├── models/
    │   ├── roq_forecast_run.py
    │   ├── roq_forecast_line.py
    │   ├── roq_abc_history.py
    │   ├── roq_shipment_group.py
    │   ├── roq_forward_plan.py
    │   ├── product_template_ext.py
    │   ├── res_partner_ext.py
    │   ├── stock_warehouse_ext.py
    │   └── purchase_order_ext.py
    ├── services/
    │   ├── abc_classifier.py
    │   ├── demand_forecast.py
    │   ├── safety_stock.py
    │   ├── roq_calculator.py
    │   ├── container_fitter.py
    │   └── consolidation_engine.py
    ├── views/
    ├── security/
    ├── data/
    └── tests/
```

---

## Critical Design Rules

1. **No hard imports between ROQ ↔ EDI/3PL modules** — use Odoo model inheritance + chatter signals
2. **Parameter snapshots on every run** — `roq.forecast.run` stores all config at time of execution (for auditability)
3. **Override semantics = replace, not add** — supplier override completely replaces system default; never sums
4. **Never auto-modify stock/financial data on discrepancy** — flag for human review
5. **Push = 0 if any SKU has real OOS risk** — `projected_inventory_at_delivery < 0` blocks all push for that order
6. **ABCD tier is per-warehouse** — each warehouse runs its own pareto ranking; a product can be A-tier in AKL and C-tier in WLG if its sales are concentrated. `product.template.abc_tier` stores the global (all-warehouses combined) tier for display purposes only — the pipeline uses the per-warehouse tier map from `classify_all_products()`
7. **Dormant (Tier D): ROQ always = 0**, excluded from container planning
8. **Dampener:** Tier must be stable for 4 consecutive runs before reclassification takes effect

---

## Data Sources Summary

| Data Needed | Source Model | Key Fields |
|---|---|---|
| Demand history | `sale.order.line` | `product_id`, `product_uom_qty`, `order_id.date_order` |
| Current SOH | `stock.quant` | `product_id`, `location_id`, `quantity` |
| Inbound PO qty | `purchase.order.line` | `product_id`, `product_qty`, `warehouse_dest_id` |
| Pack size | `product.template.pack_size` (new) or `product.packaging` | Verify in Odoo |
| CBM per unit | `product.template.cbm_per_unit` (new) | Check if already exists |
| Supplier lead time | `res.partner.supplier_lead_time_days` (new, nullable) | Falls back to system setting |
| FOB port | `res.partner.fob_port` (new) | Check if already exists |

---

## Running Tests

```bash
# Full module test
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast
odoo-bin --test-enable -d <db> --test-tags mml_freight_forwarding

# Single test class
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestAbcClassifier

# With log output
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast --log-level=test
```

---

## Odoo 19 Conventions Reminder

- **Python:** Standard ORM, `models.Model`, `@api.depends`, `@api.model`
- **Frontend:** OWL components for any interactive UI beyond standard views
- **Settings:** `res.config.settings` extension, values stored in `ir.config_parameter`
- **Sequences:** `ir.sequence` for auto-generated refs (ROQ-2026-W09, SG-2026-0042)
- **Cron:** `ir.cron` in `data/` XML, weekly ROQ run
- **Access rights:** `security/ir.model.access.csv` per module
- **Menu:** Nested under "MML Operations" top-level menu (defined in `mml_3pl` or create if absent)
