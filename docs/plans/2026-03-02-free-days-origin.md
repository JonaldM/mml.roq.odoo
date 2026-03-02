# Free Days at Origin — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a per-supplier `free_days_at_origin` integer field that extends the push window in the consolidation engine, surfacing wider consolidation opportunities to the ops team.

**Architecture:** Field on `res.partner` flows through `SettingsHelper.get_free_days_at_origin()` into `calculate_max_push_days(free_days_at_origin=N)` — additive bonus applied after the OOS hard block. Snapshot written to `roq.shipment.group.line` at consolidation time for audit.

**Tech Stack:** Odoo 19, Python, OWL (views only), `odoo.tests.common.TransactionCase`

**Design doc:** `docs/plans/2026-03-02-free-days-origin-design.md`

---

## Task 1: Extend `push_pull.py` — TDD

**Files:**
- Modify: `mml_roq_forecast/tests/test_push_pull.py`
- Modify: `mml_roq_forecast/services/push_pull.py`

---

### Step 1: Add failing tests to `test_push_pull.py`

Append these four test methods to the existing `TestPushPull` class (after the last existing test):

```python
def test_free_days_add_to_push_when_no_oos(self):
    # 8-12 weeks cover → base push 28d; +14 free days = 42
    lines = [
        {'projected_inventory_at_delivery': 80.0, 'weeks_of_cover_at_delivery': 10.0},
    ]
    result = calculate_max_push_days(lines, free_days_at_origin=14)
    self.assertEqual(result, 42)

def test_free_days_do_not_rescue_oos_block(self):
    # OOS hard block is unconditional — free days cannot override it
    lines = [
        {'projected_inventory_at_delivery': -1.0, 'weeks_of_cover_at_delivery': -0.1},
    ]
    result = calculate_max_push_days(lines, free_days_at_origin=30)
    self.assertEqual(result, 0)

def test_free_days_zero_is_backward_compatible(self):
    # Default param = 0 → existing behaviour unchanged
    lines = [
        {'projected_inventory_at_delivery': 80.0, 'weeks_of_cover_at_delivery': 10.0},
    ]
    self.assertEqual(
        calculate_max_push_days(lines),
        calculate_max_push_days(lines, free_days_at_origin=0),
    )

def test_free_days_extend_push_beyond_tier_maximum(self):
    # >12 weeks → base 42d; free days adds on top
    lines = [
        {'projected_inventory_at_delivery': 150.0, 'weeks_of_cover_at_delivery': 15.0},
    ]
    result = calculate_max_push_days(lines, free_days_at_origin=14)
    self.assertEqual(result, 56)  # 42 + 14
```

### Step 2: Run tests to verify they fail

```
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestPushPull
```

Expected: 4 new tests FAIL with `TypeError: calculate_max_push_days() got an unexpected keyword argument`.

### Step 3: Update `push_pull.py`

Replace the `calculate_max_push_days` function signature and final return line only. The OOS block and tier logic are unchanged:

```python
def calculate_max_push_days(lines, free_days_at_origin=0):
    """
    lines: list of dicts with 'projected_inventory_at_delivery' and 'weeks_of_cover_at_delivery'
    free_days_at_origin: int — negotiated free storage days at supplier origin. Added to base
        push after tier calc. OOS hard block (projected_inventory < 0) still returns 0 regardless.
    Returns: int — maximum days the order can be delayed for consolidation
    """
    if not lines:
        return 0

    # Hard block: any item at real OOS — free days cannot override this
    if any(line['projected_inventory_at_delivery'] < 0 for line in lines):
        return 0

    # Find tightest item (minimum weeks cover, ignoring the 999 sentinel for zero demand)
    cover_values = [
        line['weeks_of_cover_at_delivery']
        for line in lines
        if line['weeks_of_cover_at_delivery'] < 999.0
    ]
    min_weeks = min(cover_values) if cover_values else 999.0

    if min_weeks > 12:
        base_push = 42   # 6 weeks
    elif min_weeks >= 8:
        base_push = 28   # 4 weeks
    elif min_weeks >= 4:
        base_push = 14   # 2 weeks
    else:
        return 0  # < 4 weeks cover — no push regardless of free days

    return base_push + free_days_at_origin
```

Note: `< 4 weeks` still returns 0 before adding free days — the coverage constraint
blocks push even if free storage is available.

### Step 4: Run tests to verify they pass

```
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestPushPull
```

Expected: all tests PASS (existing + 4 new).

### Step 5: Commit

```bash
git add mml_roq_forecast/services/push_pull.py \
        mml_roq_forecast/tests/test_push_pull.py
git commit -m "feat(roq): free_days_at_origin param on calculate_max_push_days"
```

---

## Task 2: Add model fields

**Files:**
- Modify: `mml_roq_forecast/models/res_partner_ext.py`
- Modify: `mml_roq_forecast/models/roq_shipment_group.py`

No tests for this task — field existence is verified by the TransactionCase tests in Tasks 3–4.

---

### Step 1: Add `free_days_at_origin` to `res_partner_ext.py`

In `ResPartnerRoqExt`, add the field after `destination_port_id` (still in the "Port & trade terms" section, before the ROQ overrides block):

```python
free_days_at_origin = fields.Integer(
    string='Free Days at Origin',
    default=0,
    help='Negotiated free storage days at the supplier\'s origin warehouse/port after '
         'manufacturing completion. Extends the safe push window in consolidation planning. '
         'Default 0 = no free storage arranged.',
)
```

### Step 2: Add snapshot field to `roq_shipment_group.py`

In `RoqShipmentGroupLine`, add after `push_pull_reason`:

```python
free_days_at_origin = fields.Integer(
    string='Free Days at Origin',
    readonly=True,
    help='Snapshot of supplier free days at time of shipment group creation.',
)
```

### Step 3: Commit

```bash
git add mml_roq_forecast/models/res_partner_ext.py \
        mml_roq_forecast/models/roq_shipment_group.py
git commit -m "feat(roq): free_days_at_origin field on supplier and shipment group line"
```

---

## Task 3: Extend `SettingsHelper` — TDD

**Files:**
- Modify: `mml_roq_forecast/tests/test_settings_helper.py`
- Modify: `mml_roq_forecast/services/settings_helper.py`

---

### Step 1: Add failing tests to `test_settings_helper.py`

Append to the existing `TestSettingsHelper` class:

```python
def test_free_days_returns_zero_when_not_set(self):
    # Default field value = 0, SettingsHelper returns 0
    result = self.helper.get_free_days_at_origin(self.supplier)
    self.assertEqual(result, 0)

def test_free_days_returns_supplier_value(self):
    self.supplier.free_days_at_origin = 14
    result = self.helper.get_free_days_at_origin(self.supplier)
    self.assertEqual(result, 14)

def test_free_days_returns_zero_when_supplier_is_none(self):
    result = self.helper.get_free_days_at_origin(None)
    self.assertEqual(result, 0)
```

### Step 2: Run tests to verify they fail

```
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestSettingsHelper
```

Expected: 3 new tests FAIL with `AttributeError: 'SettingsHelper' has no attribute 'get_free_days_at_origin'`.

### Step 3: Add `get_free_days_at_origin` to `settings_helper.py`

Add after `get_service_level`:

```python
def get_free_days_at_origin(self, supplier):
    """
    Returns negotiated free storage days at origin for this supplier.
    No system-level default — 0 is the field default on res.partner.
    Not subject to override_expiry_date (commercial fact, not a temp override).
    """
    if supplier:
        return supplier.free_days_at_origin or 0
    return 0
```

### Step 4: Run tests to verify they pass

```
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestSettingsHelper
```

Expected: all tests PASS.

### Step 5: Commit

```bash
git add mml_roq_forecast/services/settings_helper.py \
        mml_roq_forecast/tests/test_settings_helper.py
git commit -m "feat(roq): SettingsHelper.get_free_days_at_origin()"
```

---

## Task 4: Wire into `consolidation_engine.py`

**Files:**
- Modify: `mml_roq_forecast/services/consolidation_engine.py`
- Modify: `mml_roq_forecast/tests/test_consolidation_engine.py`

---

### Step 1: Locate the push/pull block in `create_reactive_shipment_groups`

The relevant block in `consolidation_engine.py` (lines ~130–153):

```python
max_push = calculate_max_push_days(line_data)
review_interval = sh.get_review_interval_days(supplier)
max_pull = calculate_max_pull_days(
    review_interval_days=review_interval,
)

self.env['roq.shipment.group.line'].create({
    ...
    'push_pull_reason': f'Max push: {max_push}d | Max pull: {max_pull}d',
    ...
})
```

### Step 2: Replace that block with the wired version

```python
free_days = sh.get_free_days_at_origin(supplier)
max_push = calculate_max_push_days(line_data, free_days_at_origin=free_days)
review_interval = sh.get_review_interval_days(supplier)
max_pull = calculate_max_pull_days(
    review_interval_days=review_interval,
)

if free_days > 0:
    push_reason = f'Max push: {max_push}d (incl. {free_days}d free origin) | Max pull: {max_pull}d'
else:
    push_reason = f'Max push: {max_push}d | Max pull: {max_pull}d'

self.env['roq.shipment.group.line'].create({
    ...
    'push_pull_reason': push_reason,
    'free_days_at_origin': free_days,
    ...
})
```

The full updated `create` dict for `roq.shipment.group.line` (for reference — only `push_pull_reason` and `free_days_at_origin` change):

```python
self.env['roq.shipment.group.line'].create({
    'group_id': sg.id,
    'supplier_id': supplier.id,
    'cbm': supplier_cbm,
    'push_pull_days': 0,
    'push_pull_reason': push_reason,
    'free_days_at_origin': free_days,
    'oos_risk_flag': supplier_oos,
    'original_ship_date': planned_ship_date,
    'product_count': len(set(l.product_id.id for l in lines)),
})
```

### Step 3: Add a targeted test to `test_consolidation_engine.py`

Look at the existing test file to find the test class and setUp pattern — add a new test that verifies free days flow through to the shipment group line. Add to the existing test class:

```python
def test_free_days_at_origin_stored_on_group_line(self):
    """free_days_at_origin from supplier propagates to shipment group line."""
    # Set free days on the test supplier (must have fob_port set to be included)
    # Adapt supplier/line setup to match whatever setUp already creates.
    # The assertion: after create_reactive_shipment_groups, the group line
    # has free_days_at_origin matching the supplier field.
    self.supplier.free_days_at_origin = 14
    groups = self.engine.create_reactive_shipment_groups(self.run)
    self.assertTrue(groups)
    line = groups[0].line_ids[0]
    self.assertEqual(line.free_days_at_origin, 14)

def test_free_days_zero_when_supplier_has_none(self):
    """Supplier with no free days → shipment group line has 0."""
    self.supplier.free_days_at_origin = 0
    groups = self.engine.create_reactive_shipment_groups(self.run)
    self.assertTrue(groups)
    line = groups[0].line_ids[0]
    self.assertEqual(line.free_days_at_origin, 0)

def test_push_reason_includes_free_days_annotation(self):
    """When free_days_at_origin > 0, reason string includes annotation."""
    self.supplier.free_days_at_origin = 14
    groups = self.engine.create_reactive_shipment_groups(self.run)
    line = groups[0].line_ids[0]
    self.assertIn('free origin', line.push_pull_reason)

def test_push_reason_no_annotation_when_zero_free_days(self):
    """When free_days_at_origin = 0, reason string has no annotation."""
    self.supplier.free_days_at_origin = 0
    groups = self.engine.create_reactive_shipment_groups(self.run)
    line = groups[0].line_ids[0]
    self.assertNotIn('free origin', line.push_pull_reason)
```

**Note:** Check `test_consolidation_engine.py` to understand the existing `setUp` — the tests above assume `self.supplier`, `self.engine`, and `self.run` are already available from setUp. Adapt if the fixture names differ.

### Step 4: Run tests

```
odoo-bin --test-enable -d <db> --test-tags /mml_roq_forecast:TestConsolidationEngine
```

Expected: all tests PASS.

### Step 5: Commit

```bash
git add mml_roq_forecast/services/consolidation_engine.py \
        mml_roq_forecast/tests/test_consolidation_engine.py
git commit -m "feat(roq): wire free_days_at_origin through consolidation engine"
```

---

## Task 5: Update views

**Files:**
- Modify: `mml_roq_forecast/views/res_partner_views.xml`
- Modify: `mml_roq_forecast/views/roq_shipment_group_views.xml`

No new tests needed — view correctness is verified by the Odoo view XML validation on module install.

---

### Step 1: Add field to supplier form (`res_partner_views.xml`)

In the `"Freight"` group (before the `avg_lead_time_actual` stats), add:

```xml
<field name="free_days_at_origin"
       placeholder="0"/>
```

Full updated Freight group for reference:

```xml
<group string="Freight">
    <field name="purchase_incoterm_id"
           placeholder="FOB assumed if not set"/>
    <field name="fob_port_id"
           placeholder="Select origin port"/>
    <field name="destination_port_id"
           placeholder="Select NZ destination port"/>
    <field name="free_days_at_origin"
           placeholder="0"/>
    <field name="avg_lead_time_actual" readonly="1"/>
    <field name="lead_time_std_dev" readonly="1"/>
    <field name="lead_time_on_time_pct" readonly="1"/>
</group>
```

### Step 2: Add snapshot field to shipment group line view (`roq_shipment_group_views.xml`)

In the `<tree>` for `roq.shipment.group.line` (after `push_pull_reason`), add:

```xml
<field name="free_days_at_origin" optional="hide"/>
```

Use `optional="hide"` — it's audit detail, hidden by default, available via column picker.

Full updated line tree for reference:

```xml
<field name="supplier_id"/>
<field name="cbm"/>
<field name="product_count"/>
<field name="oos_risk_flag" widget="boolean_toggle"
       decoration-danger="oos_risk_flag == True"/>
<field name="push_pull_days"/>
<field name="push_pull_reason"/>
<field name="free_days_at_origin" optional="hide"/>
<field name="original_ship_date"/>
<field name="purchase_order_id"/>
```

### Step 3: Commit

```bash
git add mml_roq_forecast/views/res_partner_views.xml \
        mml_roq_forecast/views/roq_shipment_group_views.xml
git commit -m "feat(roq): free_days_at_origin in supplier form and shipment group line view"
```

---

## Task 6: Full module smoke test

Run the full module test suite to confirm nothing regresses:

```
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast
```

Expected: all tests PASS. If any fail, investigate before proceeding.

Commit not needed — no code changed in this task.

---

## Done

All tasks complete when:
- [ ] `test_push_pull.py` — 4 new tests pass
- [ ] `test_settings_helper.py` — 3 new tests pass
- [ ] `test_consolidation_engine.py` — 4 new tests pass
- [ ] Full suite green
- [ ] `free_days_at_origin` visible on supplier form (ROQ / Freight tab)
- [ ] Snapshot column visible (hidden by default) on shipment group line
