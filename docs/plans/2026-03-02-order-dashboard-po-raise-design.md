# Order Dashboard & Draft PO Raise — Design

**Date:** 2026-03-02
**Scope:** `mml_roq_forecast` module
**Status:** Approved

---

## Overview

Two related features:

1. **Order Dashboard** — a dedicated menu item showing the latest completed ROQ run's results as a two-section actionable view: urgency-sorted SKU list + supplier-level order consolidation.
2. **Draft PO Raise** — a one-click wizard to raise `draft` purchase orders from ROQ results, one PO per supplier per warehouse, with a quantity review step before committing.

---

## Feature 1: Order Dashboard

### Location

New menu item under **MML Operations → ROQ Forecast → Order Dashboard**.

Navigation is via a server action (`ir.actions.server`) that finds the latest complete `roq.forecast.run` record and opens it in a dedicated dashboard form view.

```
MML Operations
└── ROQ Forecast
    ├── Order Dashboard  ← NEW
    ├── ROQ Runs
    ├── Shipment Groups
    └── Configuration
```

### Dashboard Form View

A new `ir.ui.view` on `roq.forecast.run` (separate from the existing run form). Header shows run reference, run date, and OOS risk count. Body has two tabs.

#### Tab 1 — Urgency List

Embedded `line_ids` tree on `roq.forecast.line`.

**Sort:** `default_order="weeks_of_cover_at_delivery asc"` — negative values (OOS) naturally float to the top.

**Domain:** `[('abc_tier', '!=', 'D')]` — dormant SKUs excluded.

**Colour coding:**
- `decoration-danger` → `projected_inventory_at_delivery < 0` (OOS risk)
- `decoration-warning` → `weeks_of_cover_at_delivery < 8` and not OOS

**Columns:**
| Field | Widget | Notes |
|---|---|---|
| `abc_tier` | `badge` | |
| `product_id` | — | |
| `supplier_id` | — | |
| `warehouse_id` | — | |
| `weeks_of_cover_at_delivery` | — | Primary sort key |
| `projected_inventory_at_delivery` | — | Negative = OOS |
| `roq_containerized` | — | Suggested order qty |
| `container_type` | — | |
| `notes` | — | OOS / Safety Stock flags |

#### Tab 2 — Order by Supplier

Embedded tree on `roq.shipment.group.line` (via `shipment_group_ids.line_ids` or a dedicated computed One2many on the run). Filtered to shipment groups from this run.

**Columns:**
| Field | Notes |
|---|---|
| `supplier_id` | |
| `product_count` | SKU count |
| `cbm` | CBM contribution |
| `container_type` | Related from parent `roq.shipment.group` (new non-stored related field) |
| `oos_risk_flag` | Boolean toggle widget, readonly |
| `purchase_order_id` | Shows linked PO if already raised |
| Raise PO button | `action_raise_po_wizard()` on `roq.shipment.group.line` |

---

## Feature 2: Draft PO Raise Wizard

### Trigger

"Raise Draft PO" button on each `roq.shipment.group.line` row in the dashboard.

### Wizard Model: `roq.raise.po.wizard`

```
roq.raise.po.wizard
├── run_id          Many2one  roq.forecast.run   (from context, readonly)
├── supplier_id     Many2one  res.partner        (from context, readonly)
├── use_containerized  Boolean  default=True
│   Label: "Include container padding (A-tier)"
│   Help: "Use ROQ (Containerized) qty. Uncheck to use demand-only ROQ (Pack Rounded)."
└── line_ids        One2many  roq.raise.po.wizard.line
```

### Wizard Line Model: `roq.raise.po.wizard.line`

```
roq.raise.po.wizard.line
├── wizard_id           Many2one  roq.raise.po.wizard  (cascade)
├── forecast_line_id    Many2one  roq.forecast.line    (readonly)
├── product_id          Many2one  product.product      (readonly, from forecast line)
├── warehouse_id        Many2one  stock.warehouse      (readonly, from forecast line)
├── qty_containerized   Float     (readonly, from forecast line)
├── qty_pack_rounded    Float     (readonly, from forecast line)
├── qty_to_order        Float     (editable, recomputed from toggle)
└── notes               Char      (readonly, OOS flag / warnings)
```

**Toggle behaviour:** `@api.onchange('use_containerized')` on the wizard header resets all `qty_to_order` values to `qty_containerized` (True) or `qty_pack_rounded` (False). Individual lines remain editable after toggle.

### `action_raise_pos()` Logic

1. Group `line_ids` by `warehouse_id`.
2. For each warehouse group, create one `purchase.order` in `draft` state:
   - `partner_id` = `wizard.supplier_id`
   - `dest_warehouse_id` or notes field = warehouse (via `purchase.order` standard fields)
   - `order_line` for each wizard line in this warehouse:
     - `product_id`, `product_qty` = `qty_to_order`
     - `price_unit` from `product.supplierinfo` for this supplier/product if available, else `0.0`
   - `shipment_group_id` = the shipment group from the source `roq.shipment.group.line`
3. Write `purchase_order_id` back onto each corresponding `roq.shipment.group.line`.
4. Return a multi-record action opening all raised POs.

### Quantity Selection Summary

| User choice | `qty_to_order` source |
|---|---|
| Toggle ON (default) | `roq_containerized` — includes A-tier container padding |
| Toggle OFF | `roq_pack_rounded` — demand-driven, no padding |
| Manual override | Any value typed per line |

---

## Files Changed / Created

| File | Type | Change |
|---|---|---|
| `views/roq_order_dashboard_views.xml` | New | Dashboard form view, server action, menu item |
| `models/roq_raise_po_wizard.py` | New | `RoqRaisePoWizard` + `RoqRaisePoWizardLine` |
| `views/roq_raise_po_wizard_views.xml` | New | Wizard form view |
| `models/roq_shipment_group.py` | Modified | Add `container_type` related on `RoqShipmentGroupLine`; add `action_raise_po_wizard()` method |
| `models/roq_forecast_run.py` | Modified | Add `shipment_group_supplier_line_ids` computed One2many for Tab 2 (or use nested relation in view) |
| `security/ir.model.access.csv` | Modified | Add access rows for wizard models |
| `models/__init__.py` | Modified | Import wizard |
| `__manifest__.py` | Modified | Add new view files + wizard model |

---

## Design Constraints

- No hard imports to/from `mml_edi` or `mml_3pl` — all via standard `purchase.order` model
- POs created in `draft` state only — never auto-confirmed
- `price_unit = 0.0` is acceptable for draft PO; buyer fills in price before confirmation
- Wizard does not create shipment groups — those already exist from the ROQ run
- If `purchase_order_id` is already set on a `roq.shipment.group.line`, the Raise PO button should warn before overwriting
