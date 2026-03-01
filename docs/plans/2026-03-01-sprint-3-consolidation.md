# Sprint 3: Consolidation Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Implement Layer 3 reactive consolidation — grouping suppliers by FOB port after each ROQ run, calculating push/pull tolerances, creating `roq.shipment.group` records with OOS-safe order timing, and surfacing the results in a kanban view.

**Architecture:** `consolidation_engine.py` service operates on `roq.forecast.line` records from a completed run. Creates `roq.shipment.group` + `roq.shipment.group.line` records. Called from `roq.forecast.run` after pipeline completes.

**Pre-condition:** Sprint 2 complete. ROQ pipeline runs successfully and creates `roq.forecast.line` records.

---

## Task 1: Push/pull tolerance calculator

**Files:**
- Create: `mml_roq_forecast/services/push_pull.py`
- Create: `mml_roq_forecast/tests/test_push_pull.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_push_pull.py
from odoo.tests.common import TransactionCase
from ..services.push_pull import calculate_max_push_days, calculate_max_pull_days

class TestPushPull(TransactionCase):

    def test_no_push_when_any_item_at_real_oos(self):
        # Any item with projected_inventory < 0 → max_push = 0
        lines = [
            {'projected_inventory_at_delivery': -5.0, 'weeks_of_cover_at_delivery': -0.5},
            {'projected_inventory_at_delivery': 50.0, 'weeks_of_cover_at_delivery': 5.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 0)

    def test_push_12_plus_weeks_cover_allows_6_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 120.0, 'weeks_of_cover_at_delivery': 13.0},
            {'projected_inventory_at_delivery': 100.0, 'weeks_of_cover_at_delivery': 15.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 42)  # 6 weeks × 7 days

    def test_push_8_12_weeks_cover_allows_4_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 80.0, 'weeks_of_cover_at_delivery': 10.0},
            {'projected_inventory_at_delivery': 90.0, 'weeks_of_cover_at_delivery': 12.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 28)  # 4 weeks × 7 days

    def test_push_4_8_weeks_cover_allows_2_weeks(self):
        lines = [
            {'projected_inventory_at_delivery': 50.0, 'weeks_of_cover_at_delivery': 6.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 14)  # 2 weeks × 7 days

    def test_push_below_4_weeks_cover_allows_no_push(self):
        lines = [
            {'projected_inventory_at_delivery': 20.0, 'weeks_of_cover_at_delivery': 3.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 0)

    def test_push_uses_tightest_item(self):
        # Mix: one item at 15 wks, another at 6 wks → constrained by 6 wks → 14 days
        lines = [
            {'projected_inventory_at_delivery': 150.0, 'weeks_of_cover_at_delivery': 15.0},
            {'projected_inventory_at_delivery': 60.0, 'weeks_of_cover_at_delivery': 6.0},
        ]
        result = calculate_max_push_days(lines)
        self.assertEqual(result, 14)  # 6 weeks → 2 week push max

    def test_pull_default_is_review_interval(self):
        result = calculate_max_pull_days(review_interval_days=30)
        self.assertEqual(result, 30)

    def test_pull_capped_at_review_interval(self):
        result = calculate_max_pull_days(review_interval_days=30, override=None)
        self.assertLessEqual(result, 30)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestPushPull
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/push_pull.py
"""
Push/Pull Tolerance Calculator.

Push (delaying an order) is constrained by OOS risk:
- ANY item at real OOS (projected_inventory < 0) → push = 0 (hard block)
- Minimum weeks cover across all SKUs/warehouses in the order determines push headroom

Pull (bringing an order forward) is constrained by cash flow and supplier readiness:
- Default max pull = review interval (30 days)
- Configurable per-supplier

These rules are intentionally conservative:
- Safety stock breach is acceptable for consolidation timing
- Running to zero is not

Per spec §3.2 table:
  > 12 weeks cover → up to 6 weeks push
  8-12 weeks cover → up to 4 weeks push
  4-8 weeks cover  → up to 2 weeks push
  < 4 weeks cover  → 0 push
"""


def calculate_max_push_days(lines):
    """
    lines: list of dicts with 'projected_inventory_at_delivery' and 'weeks_of_cover_at_delivery'
    Returns: int — maximum days the order can be delayed for consolidation
    """
    if not lines:
        return 0

    # Hard block: any item at real OOS
    if any(line['projected_inventory_at_delivery'] < 0 for line in lines):
        return 0

    # Find tightest item (minimum weeks cover, ignoring the 999 sentinel for zero demand)
    min_weeks = min(
        line['weeks_of_cover_at_delivery']
        for line in lines
        if line['weeks_of_cover_at_delivery'] < 999.0
    ) if any(l['weeks_of_cover_at_delivery'] < 999.0 for l in lines) else 999.0

    if min_weeks > 12:
        return 42   # 6 weeks
    elif min_weeks >= 8:
        return 28   # 4 weeks
    elif min_weeks >= 4:
        return 14   # 2 weeks
    else:
        return 0


def calculate_max_pull_days(review_interval_days=30, override=None):
    """
    Returns maximum days an order can be brought forward.
    Defaults to review interval. Supplier override replaces (not adds).
    """
    if override is not None and override > 0:
        return min(override, review_interval_days)
    return review_interval_days


def has_oos_risk(lines):
    """True if any SKU in the supplier's order has projected inventory at delivery < 0."""
    return any(line['projected_inventory_at_delivery'] < 0 for line in lines)
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestPushPull
```
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/push_pull.py \
        mml_roq_forecast/tests/test_push_pull.py
git commit -m "feat(roq): add push/pull tolerance calculator with OOS hard-block"
```

---

## Task 2: FOB port grouping query

**Files:**
- Create: `mml_roq_forecast/services/consolidation_engine.py`
- Create: `mml_roq_forecast/tests/test_consolidation_engine.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_consolidation_engine.py
from odoo.tests.common import TransactionCase
from ..services.consolidation_engine import ConsolidationEngine

class TestConsolidationEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.engine = ConsolidationEngine(self.env)
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)

        # Two suppliers at same FOB port
        self.supplier_a = self.env['res.partner'].create({
            'name': 'Supplier A', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.supplier_b = self.env['res.partner'].create({
            'name': 'Supplier B', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        # Supplier at different port
        self.supplier_c = self.env['res.partner'].create({
            'name': 'Supplier C', 'supplier_rank': 1, 'fob_port': 'CNNGB',
        })

    def _make_run_with_lines(self, supplier_cbm_map):
        """Helper: create a fake completed ROQ run with lines for given suppliers."""
        run = self.env['roq.forecast.run'].create({'status': 'complete'})
        product = self.env['product.product'].create({'name': 'Test CON SKU', 'type': 'product'})

        for supplier, (cbm_per_unit, roq_qty, proj_inv) in supplier_cbm_map.items():
            self.env['roq.forecast.line'].create({
                'run_id': run.id,
                'product_id': product.id,
                'warehouse_id': self.warehouse.id,
                'supplier_id': supplier.id,
                'abc_tier': 'B',
                'cbm_per_unit': cbm_per_unit,
                'cbm_total': cbm_per_unit * roq_qty,
                'roq_containerized': roq_qty,
                'projected_inventory_at_delivery': proj_inv,
                'weeks_of_cover_at_delivery': proj_inv / 10.0 if proj_inv > 0 else -1,
                'container_type': '20GP',
            })
        return run

    def test_groups_suppliers_by_fob_port(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, 50.0),
            self.supplier_b: (0.05, 100, 60.0),
            self.supplier_c: (0.05, 100, 70.0),
        })
        groups = self.engine.group_by_fob_port(run)
        self.assertIn('CNSHA', groups)
        self.assertIn('CNNGB', groups)
        self.assertEqual(len(groups['CNSHA']), 2)  # supplier_a + supplier_b
        self.assertEqual(len(groups['CNNGB']), 1)  # supplier_c

    def test_creates_shipment_group_for_multi_supplier_port(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 200, 50.0),
            self.supplier_b: (0.05, 200, 60.0),
        })
        self.engine.create_reactive_shipment_groups(run)
        groups = self.env['roq.shipment.group'].search([('run_id', '=', run.id)])
        self.assertGreater(len(groups), 0)
        sha_group = groups.filtered(lambda g: g.fob_port == 'CNSHA')
        self.assertTrue(sha_group)

    def test_oos_risk_flag_set_when_any_item_oos(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, -10.0),  # OOS
            self.supplier_b: (0.05, 100, 50.0),   # OK
        })
        self.engine.create_reactive_shipment_groups(run)
        sha_group = self.env['roq.shipment.group'].search([
            ('run_id', '=', run.id),
            ('fob_port', '=', 'CNSHA'),
        ], limit=1)
        if sha_group:
            oos_lines = sha_group.line_ids.filtered(
                lambda l: l.supplier_id == self.supplier_a
            )
            self.assertTrue(oos_lines[0].oos_risk_flag)

    def test_no_push_when_supplier_has_oos_item(self):
        run = self._make_run_with_lines({
            self.supplier_a: (0.05, 100, -10.0),  # OOS — cannot push
            self.supplier_b: (0.05, 100, 150.0),  # 15 weeks cover
        })
        self.engine.create_reactive_shipment_groups(run)
        sha_group = self.env['roq.shipment.group'].search([
            ('run_id', '=', run.id), ('fob_port', '=', 'CNSHA'),
        ], limit=1)
        if sha_group:
            a_line = sha_group.line_ids.filtered(lambda l: l.supplier_id == self.supplier_a)
            if a_line:
                self.assertEqual(a_line[0].push_pull_days, 0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestConsolidationEngine
```

**Step 3: Implement consolidation engine**

```python
# mml_roq_forecast/services/consolidation_engine.py
"""
FOB Port Consolidation Engine — Reactive Mode.

After a ROQ run completes, groups supplier orders by FOB port.
Creates roq.shipment.group records with push/pull analysis.

Reactive mode (Phase 2):
  - Runs post-ROQ, groups existing supplier lines
  - Links to existing POs where they exist

Proactive mode (Phase 3/4 — stub here, implemented in Sprint 4):
  - Driven by 12-month forward plan
  - Creates future shipment groups before POs exist
"""
from collections import defaultdict
from datetime import date, timedelta
from .push_pull import calculate_max_push_days, calculate_max_pull_days, has_oos_risk


class ConsolidationEngine:

    def __init__(self, env):
        self.env = env

    def group_by_fob_port(self, run):
        """
        Returns dict: {fob_port: [roq.forecast.line records per supplier]}
        Groups lines from a completed ROQ run by FOB port.
        Only includes lines with roq_containerized > 0 and a supplier with fob_port set.
        """
        lines = self.env['roq.forecast.line'].search([
            ('run_id', '=', run.id),
            ('roq_containerized', '>', 0),
            ('supplier_id.fob_port', '!=', False),
        ])

        by_port = defaultdict(lambda: defaultdict(list))
        for line in lines:
            fob = line.supplier_id.fob_port
            sid = line.supplier_id.id
            by_port[fob][sid].append(line)

        # Convert to: {port: [{supplier: record, lines: [...]}]}
        result = {}
        for port, supplier_dict in by_port.items():
            result[port] = [
                {'supplier': self.env['res.partner'].browse(sid), 'lines': supplier_lines}
                for sid, supplier_lines in supplier_dict.items()
            ]
        return result

    def create_reactive_shipment_groups(self, run):
        """
        Creates roq.shipment.group records from a completed ROQ run.
        One group per FOB port (if multiple suppliers share a port).
        Single-supplier ports still get a group — useful for freight tender.
        """
        grouped = self.group_by_fob_port(run)
        warehouses = self.env['stock.warehouse'].search([('is_active_for_roq', '=', True)])

        created = self.env['roq.shipment.group']

        for fob_port, supplier_groups in grouped.items():
            total_cbm = sum(
                sum(line.cbm_total for line in sg['lines'])
                for sg in supplier_groups
            )

            # Determine container type from total CBM
            container_type = self._assign_container_type(total_cbm)

            # Calculate planned ship date (today + average lead time for this port)
            planned_ship_date = self._estimate_ship_date(supplier_groups)

            sg = self.env['roq.shipment.group'].create({
                'fob_port': fob_port,
                'planned_ship_date': planned_ship_date,
                'container_type': container_type,
                'total_cbm': total_cbm,
                'fill_percentage': self._fill_pct(total_cbm, container_type),
                'state': 'draft',
                'mode': 'reactive',
                'run_id': run.id,
                'destination_warehouse_ids': [(6, 0, warehouses.ids)],
            })

            # Create per-supplier lines
            for supplier_group in supplier_groups:
                supplier = supplier_group['supplier']
                lines = supplier_group['lines']

                supplier_cbm = sum(line.cbm_total for line in lines)
                supplier_oos = has_oos_risk([{
                    'projected_inventory_at_delivery': l.projected_inventory_at_delivery,
                } for l in lines])

                # Push/pull calculation
                line_data = [{
                    'projected_inventory_at_delivery': l.projected_inventory_at_delivery,
                    'weeks_of_cover_at_delivery': l.weeks_of_cover_at_delivery,
                } for l in lines]
                max_push = calculate_max_push_days(line_data)
                max_pull = calculate_max_pull_days(
                    self.env['ir.config_parameter'].sudo()
                    .get_param('roq.max_pull_days', 30),
                )

                # Find linked POs for this supplier
                po_ids = self.env['purchase.order'].search([
                    ('partner_id', '=', supplier.id),
                    ('state', 'in', ['purchase', 'done']),
                    ('shipment_group_id', '=', False),
                ]).ids

                self.env['roq.shipment.group.line'].create({
                    'group_id': sg.id,
                    'supplier_id': supplier.id,
                    'cbm': supplier_cbm,
                    'push_pull_days': 0,  # User sets actual push/pull days
                    'push_pull_reason': f'Max push: {max_push}d | Max pull: {max_pull}d',
                    'oos_risk_flag': supplier_oos,
                    'original_ship_date': planned_ship_date,
                    'product_count': len(set(l.product_id.id for l in lines)),
                })

            created |= sg

        return created

    def _assign_container_type(self, total_cbm):
        from .container_fitter import CONTAINER_SPECS, CONTAINER_ORDER
        for ctype in CONTAINER_ORDER:
            if total_cbm <= CONTAINER_SPECS[ctype]:
                return ctype
        return '40HQ'  # Largest available

    def _fill_pct(self, total_cbm, container_type):
        from .container_fitter import CONTAINER_SPECS
        cap = CONTAINER_SPECS.get(container_type, 0)
        return (total_cbm / cap * 100.0) if cap > 0 else 0.0

    def _estimate_ship_date(self, supplier_groups):
        """
        Estimate planned ship date as today + median supplier lead time.
        Defaults to 100 days if no lead time data.
        """
        lead_times = []
        for sg in supplier_groups:
            lt = sg['supplier'].supplier_lead_time_days
            if lt:
                lead_times.append(lt)
        avg_lt = sum(lead_times) / len(lead_times) if lead_times else 100
        return date.today() + timedelta(days=avg_lt)
```

**Step 4: Wire consolidation into ROQ pipeline**

Modify `mml_roq_forecast/services/roq_pipeline.py` — add to end of `run()` method:

```python
        # Step 8: Reactive consolidation (creates shipment groups)
        from .consolidation_engine import ConsolidationEngine
        con_engine = ConsolidationEngine(self.env)
        con_engine.create_reactive_shipment_groups(forecast_run)
```

**Step 5: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestConsolidationEngine
```
Expected: PASS

**Step 6: Commit**

```bash
git add mml_roq_forecast/services/consolidation_engine.py \
        mml_roq_forecast/services/roq_pipeline.py \
        mml_roq_forecast/tests/test_consolidation_engine.py
git commit -m "feat(roq): add FOB port consolidation engine with reactive grouping"
```

---

## Task 3: Shipment group status workflow

**Files:**
- Modify: `mml_roq_forecast/models/roq_shipment_group.py`
- Create: `mml_roq_forecast/tests/test_shipment_workflow.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_shipment_workflow.py
from odoo.tests.common import TransactionCase

class TestShipmentWorkflow(TransactionCase):

    def setUp(self):
        super().setUp()
        self.sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shenzhen, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '40GP',
        })

    def test_confirm_creates_freight_tender(self):
        """Per contract §3: action_confirm creates freight.tender and sets state=confirmed."""
        self.sg.action_confirm()
        self.assertEqual(self.sg.state, 'confirmed')
        self.assertTrue(self.sg.freight_tender_id)

    def test_freight_tender_has_correct_ports(self):
        self.sg.action_confirm()
        tender = self.sg.freight_tender_id
        self.assertEqual(tender.origin_port, 'Shenzhen, CN')

    def test_cancel_from_draft_works(self):
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')

    def test_cancel_from_confirmed_works(self):
        self.sg.action_confirm()
        self.sg.action_cancel()
        self.assertEqual(self.sg.state, 'cancelled')
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestShipmentWorkflow
```

**Step 3: Add workflow methods to shipment group model**

Add to `mml_roq_forecast/models/roq_shipment_group.py`:

```python
    from odoo import models, fields, api, exceptions

    # action_confirm and action_cancel are defined in Sprint 0 (roq_shipment_group.py)
    # and already follow the interface contract. No additional code needed here.
    # Sprint 3 Task 3 verifies the behaviour works end-to-end with the consolidation engine.
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestShipmentWorkflow
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/roq_shipment_group.py \
        mml_roq_forecast/tests/test_shipment_workflow.py
git commit -m "feat(roq): add shipment group status workflow (confirm/tender/book/cancel)"
```

---

## Task 4: Consolidation kanban view and shipment group form

**Files:**
- Create: `mml_roq_forecast/views/roq_shipment_group_views.xml`

**Step 1: No test needed — verify manually after install**

**Step 2: Create views**

```xml
<!-- mml_roq_forecast/views/roq_shipment_group_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Kanban View -->
    <record id="view_roq_shipment_group_kanban" model="ir.ui.view">
        <field name="name">roq.shipment.group.kanban</field>
        <field name="model">roq.shipment.group</field>
        <field name="arch" type="xml">
            <kanban default_group_by="status" class="o_kanban_small_column">
                <field name="name"/>
                <field name="fob_port"/>
                <field name="container_type"/>
                <field name="total_cbm"/>
                <field name="fill_percentage"/>
                <field name="planned_ship_date"/>
                <field name="status"/>
                <field name="mode"/>
                <templates>
                    <t t-name="kanban-box">
                        <div class="oe_kanban_card oe_kanban_global_click">
                            <div class="o_kanban_record_top">
                                <div class="o_kanban_record_headings">
                                    <strong><field name="name"/></strong>
                                </div>
                                <span t-att-class="'badge ' + (record.mode.raw_value == 'proactive' ? 'text-bg-info' : 'text-bg-secondary')">
                                    <field name="mode"/>
                                </span>
                            </div>
                            <div class="o_kanban_record_body">
                                <div>
                                    <i class="fa fa-ship"/> <strong><field name="fob_port"/></strong>
                                </div>
                                <div>
                                    Container: <field name="container_type"/> |
                                    Fill: <field name="fill_percentage" widget="float" digits="[4,1]"/>%
                                </div>
                                <div>
                                    <field name="total_cbm" widget="float" digits="[6,2]"/> CBM
                                </div>
                                <div t-if="record.planned_ship_date.raw_value">
                                    ETD: <field name="planned_ship_date"/>
                                </div>
                            </div>
                            <div class="o_kanban_record_bottom">
                                <div class="oe_kanban_bottom_left">
                                    <field name="line_ids" widget="many2many_tags"
                                           options="{'color_field': 'oos_risk_flag'}"/>
                                </div>
                            </div>
                        </div>
                    </t>
                </templates>
            </kanban>
        </field>
    </record>

    <!-- Tree View -->
    <record id="view_roq_shipment_group_tree" model="ir.ui.view">
        <field name="name">roq.shipment.group.tree</field>
        <field name="model">roq.shipment.group</field>
        <field name="arch" type="xml">
            <tree>
                <field name="name"/>
                <field name="fob_port"/>
                <field name="planned_ship_date"/>
                <field name="container_type"/>
                <field name="total_cbm"/>
                <field name="fill_percentage"/>
                <field name="mode" widget="badge"/>
                <field name="state" widget="badge"
                       decoration-info="state == 'draft'"
                       decoration-success="state in ('booked', 'delivered')"
                       decoration-warning="state == 'tendered'"
                       decoration-danger="state == 'cancelled'"/>
            </tree>
        </field>
    </record>

    <!-- Form View -->
    <record id="view_roq_shipment_group_form" model="ir.ui.view">
        <field name="name">roq.shipment.group.form</field>
        <field name="model">roq.shipment.group</field>
        <field name="arch" type="xml">
            <form>
                <header>
                    <button name="action_confirm" string="Confirm &amp; Create Tender"
                            type="object" class="btn-primary"
                            attrs="{'invisible': [('state', '!=', 'draft')]}"/>
                    <button name="action_cancel" string="Cancel"
                            type="object"
                            attrs="{'invisible': [('state', 'in', ['delivered', 'cancelled'])]}"/>
                    <!-- Tender/booking progression is managed by mml_freight module -->
                    <field name="state" widget="statusbar"
                           statusbar_visible="draft,confirmed,tendered,booked,delivered"/>
                </header>
                <sheet>
                    <div class="oe_title">
                        <h1><field name="name"/></h1>
                    </div>
                    <group>
                        <group string="Shipment">
                            <field name="fob_port"/>
                            <field name="container_type"/>
                            <field name="total_cbm"/>
                            <field name="fill_percentage"/>
                            <field name="mode"/>
                        </group>
                        <group string="Timing">
                            <field name="planned_ship_date"/>
                            <field name="run_id"/>
                            <field name="freight_tender_id"/>
                        </group>
                    </group>
                    <notebook>
                        <page string="Suppliers">
                            <field name="line_ids">
                                <tree editable="bottom">
                                    <field name="supplier_id"/>
                                    <field name="cbm"/>
                                    <field name="product_count"/>
                                    <field name="oos_risk_flag" widget="boolean_toggle"
                                           decoration-danger="oos_risk_flag == True"/>
                                    <field name="push_pull_days"/>
                                    <field name="push_pull_reason"/>
                                    <field name="original_ship_date"/>
                                    <field name="purchase_order_id"/>
                                </tree>
                            </field>
                        </page>
                        <page string="Warehouses">
                            <field name="destination_warehouse_ids" widget="many2many_list">
                                <tree>
                                    <field name="name"/>
                                    <field name="code"/>
                                </tree>
                            </field>
                        </page>
                        <page string="Notes">
                            <field name="notes"/>
                        </page>
                    </notebook>
                </sheet>
                <div class="oe_chatter">
                    <field name="message_follower_ids"/>
                    <field name="message_ids"/>
                </div>
            </form>
        </field>
    </record>

    <record id="action_roq_shipment_group" model="ir.actions.act_window">
        <field name="name">Shipment Groups</field>
        <field name="res_model">roq.shipment.group</field>
        <field name="view_mode">kanban,tree,form</field>
        <field name="context">{'default_mode': 'reactive'}</field>
    </record>
</odoo>
```

**Step 3: Add `mail.thread` and `mail.activity.mixin` to shipment group model**

In `roq_shipment_group.py`, update class:

```python
class RoqShipmentGroup(models.Model):
    _name = 'roq.shipment.group'
    _description = 'Shipment Consolidation Group'
    _order = 'planned_ship_date desc'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']  # Add chatter + activity support
```

**Step 4: Install and verify**

```bash
odoo-bin -d dev -u mml_roq_forecast --stop-after-init
```
Open browser. Check:
- "MML Operations > ROQ > Shipment Groups" — kanban by status column
- Confirm button transitions draft → confirmed
- DSV tender button visible only in confirmed state
- OOS risk flag shown in orange/red on supplier lines

**Step 5: Commit**

```bash
git add mml_roq_forecast/views/roq_shipment_group_views.xml \
        mml_roq_forecast/models/roq_shipment_group.py
git commit -m "feat(roq): add shipment group kanban and form views with workflow buttons"
```

---

## Task 5: Menus and product/supplier form extensions

**Files:**
- Modify: `mml_roq_forecast/views/menus.xml`
- Create: `mml_roq_forecast/views/product_template_views.xml`
- Create: `mml_roq_forecast/views/res_partner_views.xml`

**Step 1: Create menu structure**

```xml
<!-- mml_roq_forecast/views/menus.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_roq_root"
              name="ROQ Forecast"
              parent="mml_freight_forwarding.menu_mml_operations_root"
              sequence="10"/>

    <menuitem id="menu_roq_runs"
              name="ROQ Runs"
              parent="menu_roq_root"
              action="action_roq_forecast_run"
              sequence="10"/>

    <menuitem id="menu_roq_shipment_groups"
              name="Shipment Groups"
              parent="menu_roq_root"
              action="action_roq_shipment_group"
              sequence="20"/>

    <menuitem id="menu_roq_config"
              name="Configuration"
              parent="menu_roq_root"
              sequence="90"/>
</odoo>
```

**Step 2: Product form — ABC + ROQ fields tab**

```xml
<!-- mml_roq_forecast/views/product_template_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_product_template_roq_ext" model="ir.ui.view">
        <field name="name">product.template.roq.ext</field>
        <field name="model">product.template</field>
        <field name="inherit_id" ref="product.product_template_only_form_view"/>
        <field name="arch" type="xml">
            <notebook position="inside">
                <page string="ROQ / Procurement" name="roq_tab">
                    <group>
                        <group string="ABC Tier">
                            <field name="abc_tier" readonly="1"/>
                            <field name="abc_trailing_revenue" readonly="1"/>
                            <field name="abc_cumulative_pct" readonly="1"/>
                            <field name="abc_tier_override"/>
                            <field name="abc_tier_pending" readonly="1"/>
                            <field name="abc_weeks_in_pending" readonly="1"/>
                        </group>
                        <group string="Container Planning">
                            <field name="cbm_per_unit"/>
                            <field name="pack_size"/>
                            <field name="is_roq_managed"/>
                        </group>
                    </group>
                </page>
            </notebook>
        </field>
    </record>
</odoo>
```

**Step 3: Supplier form — FOB + override fields**

```xml
<!-- mml_roq_forecast/views/res_partner_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_res_partner_roq_ext" model="ir.ui.view">
        <field name="name">res.partner.roq.ext</field>
        <field name="model">res.partner</field>
        <field name="inherit_id" ref="base.view_partner_form"/>
        <field name="arch" type="xml">
            <notebook position="inside">
                <page string="ROQ / Freight" name="roq_freight_tab"
                      attrs="{'invisible': [('supplier_rank', '=', 0)]}">
                    <group>
                        <group string="Freight">
                            <field name="fob_port"/>
                            <field name="avg_lead_time_actual" readonly="1"/>
                            <field name="lead_time_std_dev" readonly="1"/>
                            <field name="lead_time_on_time_pct" readonly="1"/>
                        </group>
                        <group string="ROQ Parameter Overrides">
                            <field name="supplier_lead_time_days"
                                   placeholder="Leave blank = system default"/>
                            <field name="supplier_review_interval_days"
                                   placeholder="Leave blank = system default"/>
                            <field name="supplier_service_level"
                                   placeholder="Leave blank = ABC tier rate"/>
                            <field name="override_expiry_date"/>
                            <field name="supplier_holiday_periods"
                                   placeholder='[{"start": "2026-01-28", "end": "2026-02-10", "reason": "CNY"}]'/>
                        </group>
                    </group>
                </page>
            </notebook>
        </field>
    </record>
</odoo>
```

**Step 4: Install and verify**

```bash
odoo-bin -d dev -u mml_roq_forecast --stop-after-init
```
- Product form shows "ROQ / Procurement" tab with ABC tier (read-only) + CBM/pack size editable
- Supplier form shows "ROQ / Freight" tab (only visible if supplier_rank > 0)

**Step 5: Commit**

```bash
git add mml_roq_forecast/views/menus.xml \
        mml_roq_forecast/views/product_template_views.xml \
        mml_roq_forecast/views/res_partner_views.xml
git commit -m "feat(roq): add product and supplier form extensions for ROQ/freight fields"
```

---

## Sprint 3 Done Checklist

- [ ] All tests pass: `odoo-bin --test-enable -d dev --test-tags mml_roq_forecast`
- [ ] Push calculator: OOS item in order → push = 0 (confirmed by test)
- [ ] Push calculator: 6-week push only when min cover > 12 weeks (confirmed by test)
- [ ] Consolidation engine groups suppliers correctly by FOB port
- [ ] OOS risk flag set on shipment group line when supplier has OOS item
- [ ] Running a ROQ run creates shipment groups automatically
- [ ] Kanban view shows groups in correct status columns
- [ ] Confirm button moves draft → confirmed
- [ ] Chatter on shipment group records status transitions
- [ ] Product form: ABC tier is read-only (calculated), override is editable
- [ ] Supplier form: ROQ overrides only visible for supplier records
- [ ] Push/pull reason text shows max push/pull days on each supplier line
- [ ] Consolidation `cbm_total` uses MOQ-adjusted quantities (`roq_containerized` which already reflects uplift from Sprint 2 Task 6/7 — no consolidation-layer change required, this is a dependency check)
