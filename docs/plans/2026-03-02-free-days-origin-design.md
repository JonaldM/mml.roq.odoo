# Free Days at Origin — Design Document

**Date:** 2026-03-02
**Status:** Approved
**Scope:** `mml_roq_forecast` only

---

## Problem

MML can negotiate free storage at origin with factory partners. When goods are
manufactured and held at the supplier's warehouse/port at no cost, MML has more
flexibility to delay shipments while waiting for container consolidation
opportunities. This wider push window increases the chance of combining multiple
suppliers into a single FCL rather than multiple partial/LCL shipments.

The current push calculation has no awareness of origin storage arrangements —
every supplier is treated identically regardless of what's been negotiated.

---

## What This Is Not

- Does **not** reduce NZ safety stock or ROQ quantities directly. The ROQ maths
  are unchanged.
- Does **not** affect the pull window (pull is constrained by manufacturing
  readiness, not storage cost).
- Does **not** override the OOS hard block. If any SKU has
  `projected_inventory_at_delivery < 0`, push remains 0 regardless of free days.

---

## Design

### Intent

`free_days_at_origin` widens the advisory push window surfaced to the ops team
during consolidation planning. A wider push window increases the overlap between
supplier shipment windows at the same FOB port, enabling consolidation that
would otherwise appear impossible.

### Example

Without free days (Supplier A and B at same port):
- Supplier A: target ship Mar 10, max push 14d → window Mar 10–24
- Supplier B: target ship Mar 25, max push 14d → window Mar 25–Apr 8
- No overlap → two separate shipments

With 14 free days at origin on both:
- Supplier A: max push 28d → window Mar 10–Apr 7
- Supplier B: max push 28d → window Mar 25–Apr 22
- Overlap Mar 25–Apr 7 → ops team can consolidate into one FCL

---

## Data Model

### `res.partner` (supplier extension)

New field in `res_partner_ext.py`, alongside existing trade terms:

```python
free_days_at_origin = fields.Integer(
    string='Free Days at Origin',
    default=0,
    help='Negotiated free storage days at the supplier\'s origin warehouse/port. '
         'Extends the safe push window in consolidation planning.',
)
```

- Default 0 — zero behaviour change for all existing suppliers
- No `override_expiry_date` logic — this is a commercial fact, not a temporary
  system override
- Set per-supplier as arrangements are negotiated

### `roq.shipment.group.line` (snapshot)

New field on `RoqShipmentGroupLine`:

```python
free_days_at_origin = fields.Integer(
    string='Free Days at Origin',
    readonly=True,
    help='Snapshot of supplier free days at time of shipment group creation.',
)
```

Written once at consolidation time. Provides audit trail of what was negotiated
when the push decision was made.

---

## Service Layer

### `settings_helper.py`

```python
def get_free_days_at_origin(self, supplier):
    if supplier:
        return supplier.free_days_at_origin or 0
    return 0
```

No system-level default needed. 0 is the field default.

### `push_pull.py`

Signature change — optional param, backward compatible:

```python
def calculate_max_push_days(lines, free_days_at_origin=0):
```

Logic:
1. OOS hard block applied first — if any line has `projected_inventory_at_delivery < 0`,
   return 0 unconditionally (free days cannot rescue a blocked order)
2. Base push calculated from weeks-of-cover table (unchanged)
3. `return base_push + free_days_at_origin`

### `consolidation_engine.py`

In `create_reactive_shipment_groups`, per-supplier line:

```python
free_days = sh.get_free_days_at_origin(supplier)
max_push = calculate_max_push_days(line_data, free_days_at_origin=free_days)
```

`push_pull_reason` updated to surface free days:

```
Max push: 42d (incl. 14d free origin) | Max pull: 30d
```

Snapshot written to `roq.shipment.group.line.free_days_at_origin`.

---

## Files Touched

| File | Change |
|---|---|
| `models/res_partner_ext.py` | Add `free_days_at_origin` field |
| `services/settings_helper.py` | Add `get_free_days_at_origin()` |
| `services/push_pull.py` | Add `free_days_at_origin` param to `calculate_max_push_days` |
| `services/consolidation_engine.py` | Fetch, pass, update reason string, write snapshot |
| `models/roq_shipment_group.py` | Add snapshot field on `RoqShipmentGroupLine` |
| `views/res_partner_ext_views.xml` | Add field to supplier form (ROQ tab) |
| `views/roq_shipment_group_views.xml` | Add snapshot field to group line view |

---

## Out of Scope (Future)

- **Structural NZ footprint reduction:** Feed `free_days_at_origin` as a
  negative offset into the safety stock lead time calculation. Requires separate
  design — changes ROQ quantities, not just timing.
- **Pull window tuning:** Separate `consolidation_pull_days` system setting,
  decoupled from `review_interval_days`, to improve consolidation without
  distorting safety stock. Parked for next design session.
- **Proactive consolidation:** Auto-delay shipments algorithmically based on
  push window rather than surfacing advisory text. Future sprint.
