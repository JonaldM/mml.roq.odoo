# ROQ ↔ Freight Interface Contract

**For:** `mml_roq_forecast` sprint planner
**From:** `mml_freight` sprint
**Date:** 2026-03-01
**Status:** Contract locked — freight module is being built to this interface

---

## Overview

`mml_freight` handles freight tender, booking, and tracking. `mml_roq_forecast` handles demand forecasting and purchase order consolidation. These modules never import each other's Python — they communicate via shared Odoo models and a small set of fields.

This document defines exactly what ROQ needs to build to plug into the freight system.

---

## What the Freight Module Provides

`mml_freight` exposes:

| Model | Purpose |
|---|---|
| `freight.tender` | One tender per shipment group — holds cargo details, linked POs, quotes, and booking |
| `freight.booking` | Confirmed booking — holds tracking state, actual delivery date |

These already exist. ROQ does not define them.

---

## What ROQ Needs to Build

### 1. `roq.shipment.group` model

The consolidation record that groups multiple POs into a single planned shipment. Minimum required fields for freight integration:

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto-sequence ref, e.g. `SG-2026-0012` |
| `state` | Selection | Must include `confirmed` — freight tender is triggered on confirm |
| `origin_port` | Char | FOB port, e.g. `"Shenzhen, CN"` |
| `destination_port` | Char | NZ destination, e.g. `"Auckland, NZ"` |
| `container_type` | Selection | `20GP` / `40GP` / `40HQ` / `LCL` |
| `total_cbm` | Float | Aggregate CBM across all POs in the group |
| `total_weight_kg` | Float | Aggregate weight |
| `target_ship_date` | Date | Planned ETD |
| `target_delivery_date` | Date | Required at warehouse |
| `po_ids` | Many2many → `purchase.order` | The POs being consolidated |
| `freight_tender_id` | Many2one → `freight.tender` | **Written by ROQ** when tender is created — link back |

ROQ owns `roq.shipment.group`. Freight never writes to it except via the feedback fields described below.

---

### 2. Extension fields on `freight.tender`

`mml_freight` defines `freight.tender` with a `shipment_group_ref` Char field as a lightweight reference. ROQ must **extend** `freight.tender` to add the proper relational link:

```python
# In mml_roq_forecast — extends freight.tender
class FreightTenderRoqExtension(models.Model):
    _inherit = 'freight.tender'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group',
        string='Shipment Group',
        ondelete='set null',
        index=True,
    )
```

This keeps `mml_freight` free of any dependency on `mml_roq_forecast`.

---

### 3. Triggering a freight tender on shipment group confirm

When a shipment group is confirmed, ROQ creates a `freight.tender` and links the POs. This is ROQ's responsibility:

```python
def action_confirm(self):
    self.ensure_one()
    # ... ROQ's own confirm logic ...

    tender = self.env['freight.tender'].create({
        'shipment_group_ref': self.name,       # lightweight ref (always)
        'shipment_group_id':  self.id,         # relational link (ROQ extension field)
        'origin_port':        self.origin_port,
        'dest_port':          self.destination_port,
        'requested_pickup_date':   self.target_ship_date,
        'requested_delivery_date': self.target_delivery_date,
        'po_ids': [(4, po.id) for po in self.po_ids],
        # origin_partner_id, origin_country_id — populate from PO supplier if uniform
        # leave blank if mixed suppliers (consolidated from multiple)
    })

    self.freight_tender_id = tender
    self.state = 'confirmed'
```

**Note:** Package lines (`freight.tender.package`) are populated separately — either by the user in the tender form, or by a future automation that reads PO line weights/dimensions. ROQ does not need to populate package lines.

---

### 4. Feedback: actual delivery date → ROQ

When a `freight.booking` reaches `delivered` state, the freight module needs to write the actual delivery date back to the shipment group for lead time tracking. Two options:

**Option A (recommended): Freight writes back**
The freight module checks if `roq.shipment.group` exists in the environment and writes back if so:

```python
# In freight.booking — called when state transitions to 'delivered'
def _notify_roq_delivery(self):
    if 'roq.shipment.group' not in self.env:
        return
    tender = self.tender_id
    if not tender or not tender.shipment_group_id:
        return
    tender.shipment_group_id.write({
        'actual_delivery_date': self.actual_delivery_date,
        'state': 'delivered',
    })
```

For this to work, ROQ must add `actual_delivery_date` (Date) and a `delivered` state to `roq.shipment.group`.

**Option B: ROQ polls**
ROQ cron reads `freight.booking` records linked to its shipment groups and checks state. Looser coupling but slower feedback.

**Recommendation:** Option A. Freight already has the delivery event; it's one write call. ROQ just needs the fields.

---

### 5. Lead time fields ROQ should expose (for freight feedback)

On `roq.shipment.group` or aggregated on `res.partner` (supplier):

| Field | Type | Written by | Purpose |
|---|---|---|---|
| `actual_delivery_date` | Date | Freight (Option A) | When goods arrived at warehouse |
| `actual_lead_time_days` | Integer | Freight or ROQ | Days from first PO date to delivery |
| `lead_time_variance_days` | Integer | Freight or ROQ | Actual minus assumed (+ = late) |

Supplier-level aggregates (rolling stats) are ROQ's domain — freight provides the raw events.

---

## What ROQ Does NOT Need to Do

- Define `freight.tender`, `freight.booking`, `freight.tender.quote` — these are freight's models
- Handle DSV API calls — entirely in `mml_freight_dsv`
- Create inward orders for Mainfreight — `mml_freight` queues one `3pl.message` per PO on booking confirm
- Populate package lines (CBM/weight per SKU) — this is a freight user task or future automation

---

## Dependency Direction

```
mml_roq_forecast  →  depends on  →  mml_freight
mml_freight       →  no dependency on  →  mml_roq_forecast
```

`mml_roq_forecast` lists `mml_freight` in its `__manifest__.py` `depends`. Not the other way.

---

## Integration Checklist for ROQ Sprint

- [ ] Define `roq.shipment.group` model with fields listed above
- [ ] Add `freight_tender_id` Many2one on `roq.shipment.group`
- [ ] Add `actual_delivery_date` Date field on `roq.shipment.group`
- [ ] Add `delivered` to `roq.shipment.group` state selection
- [ ] Add `shipment_group_id` extension field on `freight.tender` (in ROQ module)
- [ ] Implement `action_confirm` on `roq.shipment.group` — creates `freight.tender`, links POs
- [ ] Add `mml_freight` to ROQ manifest `depends`
- [ ] Verify: confirming a shipment group in ROQ creates a `freight.tender` in `draft` state with correct `po_ids`
- [ ] Verify: when freight booking reaches `delivered`, `roq.shipment.group.actual_delivery_date` is set
