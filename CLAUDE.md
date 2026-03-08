# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Read this file AND `E:\ClaudeCode\projects\mml.odoo.apps\CLAUDE.md` before touching any code.
> Full functional spec: `ROQ_Odoo_Module_Spec.md` | Model definitions: `ROQ_Clean_Slate_Model_Mapping.md`

---

## What This Is

A single Odoo 19 module replacing an external Excel-based ROQ (Reorder Quantity) system for MML Consumer Products Ltd (~400 SKUs).

| Module | Purpose | Depends On |
|---|---|---|
| `mml_roq_forecast` | Demand forecast, ROQ calculation, consolidation, 12-month plan | `mml_base`, `sale`, `purchase`, `stock`, `stock_landed_costs` |

Integration with `mml_freight` and `stock_3pl_core` is via the `mml_base` service locator — no hard module dependency. Bridge modules (`mml_roq_freight`, `mml_freight_3pl`) wire them together when both are present.

---

## Commands

```bash
# Install Python dependencies (numpy + scipy for Holt-Winters / z-scores)
pip install -r requirements.txt

# Run all pure-Python tests (no Odoo required — fast, use during development)
pytest -m "not odoo_integration" -q

# Run a single test file
pytest mml_roq_forecast/tests/test_abc_classifier.py -q

# Odoo integration tests (requires live database)
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestAbcClassifier

# Install module
odoo-bin -d <db> -i mml_base,mml_roq_forecast --stop-after-init

# Upgrade module
odoo-bin -d <db> -u mml_roq_forecast --stop-after-init
```

---

## Test Infrastructure

Two-tier strategy — most business logic is testable without Odoo:

| Tier | Marker | Runner | Scope |
|------|--------|--------|-------|
| Pure-Python | *(no marker)* | `pytest` | Service logic, calculations, parsers — no `self.env` |
| Odoo integration | `odoo_integration` | `odoo-bin --test-enable` | ORM operations requiring `self.env` |

`conftest.py` installs lightweight stubs for `odoo.models`, `odoo.fields`, `odoo.api`, `odoo.exceptions`, `odoo.http`, `odoo.tests` into `sys.modules`. Tests inheriting from `odoo.tests.TransactionCase` are auto-marked `odoo_integration` and silently skipped under plain `pytest`.

---

## Module Structure

```
mml_roq_forecast/
├── __manifest__.py
├── models/
│   ├── roq_forecast_run.py          # Run record — triggers pipeline, stores parameter snapshot
│   ├── roq_forecast_line.py         # One line per SKU per warehouse per run
│   ├── roq_abc_history.py           # ABCD tier change history (dampener audit trail)
│   ├── roq_shipment_group.py        # FOB-consolidated shipment group
│   ├── roq_forward_plan.py          # 12-month forward procurement plan
│   ├── roq_warehouse_week_load.py   # Receiving capacity model (CBM/TEU per week)
│   ├── roq_port.py                  # roq.port — UN/LOCODE origin/destination ports
│   ├── roq_raise_po_wizard.py       # Wizard: raise draft POs from Order Dashboard
│   ├── roq_reschedule_wizard.py     # Wizard: reschedule shipment group with proximity check
│   ├── product_template_ext.py      # Adds cbm_per_unit, pack_size, is_roq_managed, abc_tier
│   ├── res_partner_ext.py           # Adds supplier_lead_time_days, fob_port_id, incoterm, etc.
│   ├── res_config_settings_ext.py   # ROQ system defaults in ir.config_parameter
│   ├── stock_warehouse_ext.py       # Adds is_active_for_roq, receiving capacity fields
│   └── purchase_order_ext.py        # Links PO back to shipment group supplier line
├── services/                        # Pure-Python business logic — no self.env
│   ├── roq_pipeline.py              # Orchestrator — calls steps 1-9 in order
│   ├── roq_service.py               # High-level service entry point
│   ├── abc_classifier.py            # ABCD pareto classification with 4-run dampener
│   ├── demand_history.py            # Reads sale.order.line, returns weekly demand list
│   ├── forecast_methods.py          # SMA, EWMA, Holt-Winters; select_forecast_method()
│   ├── safety_stock.py              # Z × σ × √LT; z-scores by tier
│   ├── roq_calculator.py            # (s,S) out-level, order-up-to, ROQ raw, pack rounding
│   ├── inventory_query.py           # SOH, confirmed PO qty, inventory position
│   ├── moq_enforcer.py              # Raises per-SKU supplier total to min_qty; MOQ uplift
│   ├── container_fitter.py          # Greedy CBM → LCL/FCL/20GP/40GP/40HQ assignment
│   ├── consolidation_engine.py      # Creates roq.shipment.group records by FOB port
│   ├── push_pull.py                 # Push/pull optimisation logic
│   ├── forward_plan_generator.py    # 12-month shipment plan generation
│   └── settings_helper.py          # Reads ir.config_parameter with typed defaults
├── views/                           # Standard Odoo XML views + OWL shipment calendar
├── security/ir.model.access.csv
├── data/
│   ├── ir_sequence_data.xml         # ROQ-YYYY-WNN, SG-YYYY-NNNN sequences
│   ├── roq_port_data.xml            # 20 seeded UN/LOCODE ports (noupdate="1")
│   └── ir_cron_data.xml             # Weekly cron (installed active=False)
├── reports/                         # Supplier Order Schedule QWeb report
└── tests/
```

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

### ROQ Pipeline Step Order (`roq_pipeline.py`)

1. ABCD classification (global, with 4-run dampener)
2. Demand forecast per SKU per warehouse (SMA / EWMA / Holt-Winters)
3. Safety stock per SKU per warehouse (`Z × σ × √LT`)
4. ROQ calculation — (s,S) periodic review policy
5. Pack size rounding
6. Aggregate by supplier
7. MOQ enforcement — raise to `product.supplierinfo.min_qty`; distribute uplift to lowest-cover warehouse
8. Container fitting — greedy CBM assignment, LCL/FCL recommendation, intelligent padding
9. Write results to `roq.forecast.line` + reactive FOB consolidation → `roq.shipment.group`

---

## Critical Design Rules

1. **No hard imports between ROQ ↔ EDI/3PL modules** — use `mml_base` service locator
2. **Parameter snapshots on every run** — `roq.forecast.run` stores all config at execution time
3. **Override semantics = replace, not add** — supplier override completely replaces system default; never sums
4. **Never auto-modify stock/financial data on discrepancy** — flag for human review
5. **Push = 0 if any SKU has real OOS risk** — `projected_inventory_at_delivery < 0` blocks all push
6. **ABCD tier is per-warehouse** — `product.template.abc_tier` stores global tier for display only; pipeline uses per-warehouse tier map from `classify_all_products()`
7. **Dormant (Tier D): ROQ always = 0**, excluded from container planning
8. **Dampener:** Tier must be stable for 4 consecutive runs before reclassification takes effect
9. **`roq.port` model** — UN/LOCODE (5-char, unique, auto-uppercase); no free-text port fields
10. **POs always raised in `draft` state** — never auto-confirmed

---

## Key Configuration Parameters (ir.config_parameter)

| Key | Default | Notes |
|---|---|---|
| `roq.default_lead_time_days` | 100 | Overridden per-supplier |
| `roq.default_review_interval_days` | 30 | Overridden per-supplier |
| `roq.default_service_level` | 0.970 | Z-score input; overridden per-supplier |
| `roq.lookback_weeks` | 156 | 3 years of history |
| `roq.sma_window_weeks` | 52 | |
| `roq.min_n_value` | 8 | Minimum data points for reliable std dev |
| `roq.abc_dampener_weeks` | 4 | Runs before reclassification takes effect |
| `roq.abc_trailing_revenue_weeks` | 52 | Rolling revenue window for ABCD |
| `roq.container_lcl_threshold_pct` | 50 | Below this utilisation → recommend LCL |
| `roq.max_padding_weeks_cover` | 26 | A-tier padding ceiling (weeks of cover) |
| `roq.enable_moq_enforcement` | `True` | When False, MOQ flag shown but quantities unchanged |
| `roq.calendar.consolidation_window_days` | 21 | Proximity check window for reschedule wizard |
| `roq.calendar.reschedule_threshold_days` | 5 | Days shift before proximity check runs |

---

## Odoo 19 Conventions

- Use `_compute_display_name()` — **not** `name_get()` (removed in Odoo 19)
- `res.config.settings` extension; values stored in `ir.config_parameter`
- `ir.sequence` for auto-refs (ROQ-2026-W09, SG-2026-0042)
- `ir.cron` in `data/` XML; weekly ROQ cron installed `active=False`
- Menu under "MML Operations" top-level; `web_icon` → `static/description/icon.png`
- `application = True`; `auto_install = False`

---

## Docs Index

| File | Contents |
|---|---|
| `ROQ_Odoo_Module_Spec.md` | Full functional specification |
| `ROQ_Clean_Slate_Model_Mapping.md` | All models and fields |
| `roq-freight-interface-contract.md` | ROQ ↔ freight module interface |
| `docs/plans/2026-03-01-sprint-roadmap.md` | Sprint overview |
| `docs/plans/2026-03-02-order-dashboard-po-raise.md` | Order Dashboard + Draft PO Raise |
| `docs/plans/2026-03-04-shipment-calendar-implementation.md` | Shipment Calendar |

## Available Commands

- `/plan` — implementation plan before touching pipeline steps or adding new ROQ calculation modes
- `/tdd` — write pure-Python service tests first (`pytest -m "not odoo_integration" -q`)
- `/code-review` — review before running on production Odoo database
- `/build-fix` — diagnose pytest or `odoo-bin --test-enable` failures
