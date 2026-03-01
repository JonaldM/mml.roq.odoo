# Order Dashboard & Draft PO Raise — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an urgency-sorted Order Dashboard menu item to the ROQ module and a one-click wizard to raise draft purchase orders from ROQ results, split by supplier × warehouse.

**Architecture:** Extend `RoqShipmentGroupLine` with a `run_id` stored-related field (enabling a direct One2many from `RoqForecastRun` to all supplier lines), add a `RoqRaisePoWizard` TransientModel that pre-populates from forecast lines and creates `purchase.order` records per warehouse on confirm, and wire a dedicated dashboard form view and server-action-driven menu item showing the latest complete run.

**Tech Stack:** Odoo 19 ORM, standard `purchase.order` model, `ir.actions.server` for menu redirect, standard XML views (no OWL — dashboard is a standard Odoo form view).

**Design doc:** `docs/plans/2026-03-02-order-dashboard-po-raise-design.md`

---

## Task 1: Extend `RoqShipmentGroupLine` — run_id, container_type, wizard launcher

**Files:**
- Modify: `mml_roq_forecast/models/roq_shipment_group.py`
- Test: `mml_roq_forecast/tests/test_shipment_group.py`

### Step 1: Write the failing tests

Add to `tests/test_shipment_group.py`:

```python
def test_group_line_run_id_matches_group(self):
    """run_id stored-related on line must equal the group's run_id."""
    # Assumes setUp creates self.run, self.group (with run_id=self.run), self.sg_line
    self.assertEqual(self.sg_line.run_id, self.run)

def test_group_line_container_type_matches_group(self):
    """container_type related on line must reflect parent group container."""
    self.group.container_type = '40HQ'
    self.assertEqual(self.sg_line.container_type, '40HQ')

def test_raise_po_wizard_raises_error_when_po_already_set(self):
    """action_raise_po_wizard must raise UserError if purchase_order_id already set."""
    existing_po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
    self.sg_line.purchase_order_id = existing_po.id
    with self.assertRaises(Exception):
        self.sg_line.action_raise_po_wizard()

def test_raise_po_wizard_raises_error_when_no_run(self):
    """action_raise_po_wizard must raise UserError if group has no run_id."""
    self.group.run_id = False
    with self.assertRaises(Exception):
        self.sg_line.action_raise_po_wizard()
```

### Step 2: Run — expect failure (fields don't exist yet)

```bash
python -m py_compile mml_roq_forecast/tests/test_shipment_group.py
```
Expected: syntax OK, tests would fail at runtime (fields missing).

### Step 3: Add fields and method to `RoqShipmentGroupLine`

In `models/roq_shipment_group.py`, inside `RoqShipmentGroupLine`, add after the existing fields:

```python
# Stored related — enables One2many from roq.forecast.run directly to supplier lines
run_id = fields.Many2one(
    'roq.forecast.run', string='ROQ Run',
    related='group_id.run_id', store=True, index=True, readonly=True,
)
# Non-stored related — for display in dashboard Tab 2
container_type = fields.Selection(
    CONTAINER_TYPES, string='Container',
    related='group_id.container_type', readonly=True,
)
```

At the top of `roq_shipment_group.py`, the `CONTAINER_TYPES` list is already defined — the related field references it correctly.

Also add `from odoo import models, fields, api, exceptions` if `exceptions` is not already imported (it is — verify).

Add method to `RoqShipmentGroupLine`:

```python
def action_raise_po_wizard(self):
    """Open the Raise Draft PO wizard pre-populated for this supplier."""
    self.ensure_one()
    run = self.group_id.run_id
    if not run:
        raise exceptions.UserError('This shipment group has no linked ROQ run.')
    if self.purchase_order_id:
        raise exceptions.UserError(
            f'A purchase order ({self.purchase_order_id.name}) is already linked to '
            f'{self.supplier_id.name}. Open it directly or unlink it first.'
        )
    forecast_lines = self.env['roq.forecast.line'].search([
        ('run_id', '=', run.id),
        ('supplier_id', '=', self.supplier_id.id),
        ('roq_containerized', '>', 0),
        ('abc_tier', '!=', 'D'),
    ], order='product_id')
    if not forecast_lines:
        raise exceptions.UserError(
            f'No active order lines found for {self.supplier_id.name} in run {run.name}.'
        )
    wizard = self.env['roq.raise.po.wizard'].create({
        'run_id': run.id,
        'supplier_id': self.supplier_id.id,
        'shipment_group_line_id': self.id,
        'line_ids': [(0, 0, {
            'forecast_line_id': fl.id,
            'product_id': fl.product_id.id,
            'warehouse_id': fl.warehouse_id.id,
            'qty_containerized': fl.roq_containerized,
            'qty_pack_rounded': fl.roq_pack_rounded,
            'qty_to_order': fl.roq_containerized,
            'notes': fl.notes or '',
        }) for fl in forecast_lines],
    })
    return {
        'type': 'ir.actions.act_window',
        'res_model': 'roq.raise.po.wizard',
        'res_id': wizard.id,
        'view_mode': 'form',
        'target': 'new',
        'name': f'Raise PO — {self.supplier_id.name}',
    }
```

### Step 4: Syntax check

```bash
python -m py_compile mml_roq_forecast/models/roq_shipment_group.py
```
Expected: no output (clean).

### Step 5: Commit

```bash
git add mml_roq_forecast/models/roq_shipment_group.py mml_roq_forecast/tests/test_shipment_group.py
git commit -m "feat(roq): extend ShipmentGroupLine with run_id, container_type, wizard launcher"
```

---

## Task 2: Add `supplier_order_line_ids` One2many to `RoqForecastRun`

**Files:**
- Modify: `mml_roq_forecast/models/roq_forecast_run.py`
- Test: `mml_roq_forecast/tests/test_roq_models.py`

### Step 1: Write the failing test

Add to `tests/test_roq_models.py` (or create if absent):

```python
def test_forecast_run_supplier_order_line_ids(self):
    """supplier_order_line_ids must return all group lines belonging to this run."""
    # Create run → group (run_id=run) → line (group_id=group)
    # Assert run.supplier_order_line_ids contains the line
    run = self.env['roq.forecast.run'].create({})
    group = self.env['roq.shipment.group'].create({
        'run_id': run.id,
        'origin_port': 'CNSHA',
        'container_type': '40HQ',
    })
    supplier = self.env['res.partner'].create({'name': 'Test Supplier', 'supplier_rank': 1})
    line = self.env['roq.shipment.group.line'].create({
        'group_id': group.id,
        'supplier_id': supplier.id,
    })
    self.assertIn(line, run.supplier_order_line_ids)
```

### Step 2: Add field to `RoqForecastRun`

In `models/roq_forecast_run.py`, add after `line_ids`:

```python
shipment_group_ids = fields.One2many(
    'roq.shipment.group', 'run_id', string='Shipment Groups',
)
supplier_order_line_ids = fields.One2many(
    'roq.shipment.group.line', 'run_id', string='Supplier Order Lines',
    help='All supplier lines from shipment groups created by this run.',
)
```

Note: `run_id` on `roq.shipment.group.line` is the stored-related field added in Task 1. `run_id` on `roq.shipment.group` already exists (set by the consolidation engine).

### Step 3: Syntax check

```bash
python -m py_compile mml_roq_forecast/models/roq_forecast_run.py
```

### Step 4: Commit

```bash
git add mml_roq_forecast/models/roq_forecast_run.py mml_roq_forecast/tests/test_roq_models.py
git commit -m "feat(roq): add supplier_order_line_ids and shipment_group_ids to RoqForecastRun"
```

---

## Task 3: Create `roq_raise_po_wizard.py`

**Files:**
- Create: `mml_roq_forecast/models/roq_raise_po_wizard.py`
- Create: `mml_roq_forecast/tests/test_raise_po_wizard.py`

### Step 1: Write the failing tests

Create `mml_roq_forecast/tests/test_raise_po_wizard.py`:

```python
from odoo.tests.common import TransactionCase


class TestRaisePoWizard(TransactionCase):
    """Tests for roq.raise.po.wizard — PO creation logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({
            'name': 'Test Wizard Supplier', 'supplier_rank': 1,
        })
        cls.wh1 = cls.env['stock.warehouse'].search([], limit=1)
        cls.wh2 = cls.env['stock.warehouse'].search([('id', '!=', cls.wh1.id)], limit=1)
        cls.product1 = cls.env['product.product'].create({
            'name': 'Widget A', 'type': 'product',
        })
        cls.product2 = cls.env['product.product'].create({
            'name': 'Widget B', 'type': 'product',
        })
        cls.run = cls.env['roq.forecast.run'].create({})

    def _make_wizard(self, lines, use_containerized=True):
        return self.env['roq.raise.po.wizard'].create({
            'run_id': self.run.id,
            'supplier_id': self.supplier.id,
            'use_containerized': use_containerized,
            'line_ids': [(0, 0, d) for d in lines],
        })

    def test_raises_one_po_per_warehouse(self):
        """One draft PO created per warehouse."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80, 'qty_to_order': 100},
            {'product_id': self.product2.id, 'warehouse_id': self.wh2.id,
             'qty_containerized': 60, 'qty_pack_rounded': 50, 'qty_to_order': 60},
        ])
        wizard.action_raise_pos()
        pos = self.env['purchase.order'].search([('partner_id', '=', self.supplier.id)])
        self.assertEqual(len(pos), 2)

    def test_po_lines_have_correct_qty(self):
        """PO line qty equals qty_to_order from wizard line."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80, 'qty_to_order': 100},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search([('partner_id', '=', self.supplier.id)], limit=1)
        self.assertEqual(po.order_line[0].product_qty, 100)

    def test_pos_in_draft_state(self):
        """Raised POs must be in draft state."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 50, 'qty_pack_rounded': 40, 'qty_to_order': 50},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search([('partner_id', '=', self.supplier.id)], limit=1)
        self.assertEqual(po.state, 'draft')

    def test_toggle_off_uses_pack_rounded(self):
        """Toggling use_containerized=False resets qty_to_order to qty_pack_rounded."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 100, 'qty_pack_rounded': 80, 'qty_to_order': 100},
        ], use_containerized=False)
        # After create with use_containerized=False, line qtys should be pack rounded
        self.assertEqual(wizard.line_ids[0].qty_to_order, 80)

    def test_zero_qty_lines_skipped(self):
        """Lines with qty_to_order == 0 do not produce PO lines."""
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 0, 'qty_pack_rounded': 0, 'qty_to_order': 0},
            {'product_id': self.product2.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 50, 'qty_pack_rounded': 40, 'qty_to_order': 50},
        ])
        wizard.action_raise_pos()
        po = self.env['purchase.order'].search([('partner_id', '=', self.supplier.id)], limit=1)
        self.assertEqual(len(po.order_line), 1)

    def test_all_zero_raises_user_error(self):
        """If all lines have qty_to_order == 0, UserError is raised."""
        from odoo.exceptions import UserError
        wizard = self._make_wizard([
            {'product_id': self.product1.id, 'warehouse_id': self.wh1.id,
             'qty_containerized': 0, 'qty_pack_rounded': 0, 'qty_to_order': 0},
        ])
        with self.assertRaises(UserError):
            wizard.action_raise_pos()
```

### Step 2: Run syntax check

```bash
python -m py_compile mml_roq_forecast/tests/test_raise_po_wizard.py
```

### Step 3: Create `models/roq_raise_po_wizard.py`

```python
import logging
from odoo import models, fields, api, exceptions

_logger = logging.getLogger(__name__)


class RoqRaisePoWizard(models.TransientModel):
    _name = 'roq.raise.po.wizard'
    _description = 'Raise Draft Purchase Orders from ROQ Results'

    run_id = fields.Many2one('roq.forecast.run', string='ROQ Run', readonly=True, required=True)
    supplier_id = fields.Many2one('res.partner', string='Supplier', readonly=True, required=True)
    shipment_group_line_id = fields.Many2one(
        'roq.shipment.group.line', string='Shipment Group Line',
        readonly=True, ondelete='set null',
    )
    use_containerized = fields.Boolean(
        string='Include container padding (A-tier)',
        default=True,
        help='ON = use ROQ (Containerized) qty including A-tier container padding. '
             'OFF = use ROQ (Pack Rounded) demand-only qty.',
    )
    line_ids = fields.One2many('roq.raise.po.wizard.line', 'wizard_id', string='Order Lines')

    @api.onchange('use_containerized')
    def _onchange_use_containerized(self):
        for line in self.line_ids:
            line.qty_to_order = (
                line.qty_containerized if self.use_containerized else line.qty_pack_rounded
            )

    def action_raise_pos(self):
        """Create one draft purchase.order per supplier × warehouse, then close wizard."""
        self.ensure_one()
        if not self.line_ids:
            raise exceptions.UserError('No lines to raise POs for.')

        # Group wizard lines by warehouse
        lines_by_wh: dict = {}
        for line in self.line_ids:
            if line.qty_to_order > 0:
                lines_by_wh.setdefault(line.warehouse_id, []).append(line)

        if not lines_by_wh:
            raise exceptions.UserError(
                'All order quantities are zero — nothing to raise. '
                'Edit quantities or uncheck zero-qty lines.'
            )

        sg_line = self.shipment_group_line_id
        po_ids = []

        for warehouse, wh_lines in lines_by_wh.items():
            order_lines = []
            for wl in wh_lines:
                # Resolve supplier price from product.supplierinfo if available
                supplierinfo = self.env['product.supplierinfo'].search([
                    ('partner_id', '=', self.supplier_id.id),
                    ('product_tmpl_id', '=', wl.product_id.product_tmpl_id.id),
                ], limit=1)
                price_unit = supplierinfo.price if supplierinfo else 0.0
                product_uom = wl.product_id.uom_po_id

                order_lines.append((0, 0, {
                    'product_id': wl.product_id.id,
                    'name': wl.product_id.display_name,
                    'product_qty': wl.qty_to_order,
                    'price_unit': price_unit,
                    'product_uom': product_uom.id,
                    'date_planned': fields.Date.today(),
                }))

            po_vals = {
                'partner_id': self.supplier_id.id,
                'picking_type_id': warehouse.in_type_id.id,
                'order_line': order_lines,
            }
            if sg_line and sg_line.group_id:
                po_vals['shipment_group_id'] = sg_line.group_id.id

            po = self.env['purchase.order'].sudo().create(po_vals)
            po_ids.append(po.id)
            _logger.info(
                'ROQ: raised draft PO %s for supplier=%s warehouse=%s (%d lines)',
                po.name, self.supplier_id.name, warehouse.name, len(order_lines),
            )

        # Write back the first PO to the shipment group supplier line
        if sg_line and po_ids:
            sg_line.sudo().write({'purchase_order_id': po_ids[0]})

        # Return action opening the raised PO(s)
        if len(po_ids) == 1:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'purchase.order',
                'res_id': po_ids[0],
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', po_ids)],
            'name': f'Draft POs — {self.supplier_id.name}',
            'target': 'current',
        }


class RoqRaisePoWizardLine(models.TransientModel):
    _name = 'roq.raise.po.wizard.line'
    _description = 'ROQ Raise PO Wizard Line'
    _order = 'warehouse_id, product_id'

    wizard_id = fields.Many2one('roq.raise.po.wizard', required=True, ondelete='cascade')
    forecast_line_id = fields.Many2one('roq.forecast.line', string='Forecast Line', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True, required=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', readonly=True, required=True)
    qty_containerized = fields.Float(
        string='ROQ Containerized', readonly=True, digits=(10, 3),
        help='Post-container-fitting quantity including A-tier padding.',
    )
    qty_pack_rounded = fields.Float(
        string='ROQ Pack Rounded', readonly=True, digits=(10, 3),
        help='Demand-driven quantity before container fitting.',
    )
    qty_to_order = fields.Float(
        string='Qty to Order', digits=(10, 3),
        help='Final quantity to put on the purchase order. Edit as needed.',
    )
    notes = fields.Char(string='Flags', readonly=True)
```

### Step 4: Syntax check

```bash
python -m py_compile mml_roq_forecast/models/roq_raise_po_wizard.py
```

### Step 5: Commit

```bash
git add mml_roq_forecast/models/roq_raise_po_wizard.py mml_roq_forecast/tests/test_raise_po_wizard.py
git commit -m "feat(roq): add RoqRaisePoWizard + RoqRaisePoWizardLine TransientModels"
```

---

## Task 4: Wire wizard into module (init + security + manifest)

**Files:**
- Modify: `mml_roq_forecast/models/__init__.py`
- Modify: `mml_roq_forecast/security/ir.model.access.csv`
- Modify: `mml_roq_forecast/__manifest__.py` (partial — views added in later tasks)

### Step 1: Add import to `models/__init__.py`

Add at the end:

```python
from . import roq_raise_po_wizard
```

### Step 2: Add access rows to `security/ir.model.access.csv`

Append (no trailing newline issues — add after last existing line):

```
access_roq_raise_po_wizard_user,roq.raise.po.wizard user,model_roq_raise_po_wizard,base.group_user,1,1,1,1
access_roq_raise_po_wizard_manager,roq.raise.po.wizard manager,model_roq_raise_po_wizard,base.group_system,1,1,1,1
access_roq_raise_po_wizard_line_user,roq.raise.po.wizard.line user,model_roq_raise_po_wizard_line,base.group_user,1,1,1,1
access_roq_raise_po_wizard_line_manager,roq.raise.po.wizard.line manager,model_roq_raise_po_wizard_line,base.group_system,1,1,1,1
```

TransientModel wizard records are created and deleted per-session, so all permission bits are 1 for both groups.

### Step 3: Syntax checks

```bash
python -m py_compile mml_roq_forecast/models/__init__.py
```

### Step 4: Commit

```bash
git add mml_roq_forecast/models/__init__.py mml_roq_forecast/security/ir.model.access.csv
git commit -m "feat(roq): register RaisePoWizard models and add security access rows"
```

---

## Task 5: Create wizard form view

**Files:**
- Create: `mml_roq_forecast/views/roq_raise_po_wizard_views.xml`
- Modify: `mml_roq_forecast/__manifest__.py`

### Step 1: Create `views/roq_raise_po_wizard_views.xml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_roq_raise_po_wizard_form" model="ir.ui.view">
        <field name="name">roq.raise.po.wizard.form</field>
        <field name="model">roq.raise.po.wizard</field>
        <field name="arch" type="xml">
            <form string="Raise Draft Purchase Order">
                <sheet>
                    <group>
                        <group string="Order Details">
                            <field name="run_id" readonly="1"/>
                            <field name="supplier_id" readonly="1"/>
                        </group>
                        <group string="Quantity Mode">
                            <field name="use_containerized" widget="boolean_toggle"/>
                            <div class="text-muted small" colspan="2">
                                <strong>ON:</strong> Use ROQ (Containerized) — includes A-tier container padding.<br/>
                                <strong>OFF:</strong> Use ROQ (Pack Rounded) — demand-driven only, no padding.
                            </div>
                        </group>
                    </group>
                    <field name="line_ids">
                        <tree editable="bottom"
                              decoration-warning="notes != False and notes != ''"
                              decoration-muted="qty_to_order == 0">
                            <field name="product_id" readonly="1"/>
                            <field name="warehouse_id" readonly="1"/>
                            <field name="qty_containerized" readonly="1" optional="show"/>
                            <field name="qty_pack_rounded" readonly="1" optional="show"/>
                            <field name="qty_to_order" string="Qty to Order"/>
                            <field name="notes" readonly="1" optional="show"/>
                        </tree>
                    </field>
                </sheet>
                <footer>
                    <button name="action_raise_pos"
                            string="Raise Draft PO(s)"
                            type="object"
                            class="btn-primary"/>
                    <button string="Cancel" class="btn-secondary" special="cancel"/>
                </footer>
            </form>
        </field>
    </record>
</odoo>
```

### Step 2: Add to `__manifest__.py` data list

In `__manifest__.py`, insert after `views/roq_shipment_group_views.xml`:

```python
'views/roq_raise_po_wizard_views.xml',
```

### Step 3: Validate XML

```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('mml_roq_forecast/views/roq_raise_po_wizard_views.xml'); print('OK')"
python -m py_compile mml_roq_forecast/__manifest__.py
```

### Step 4: Commit

```bash
git add mml_roq_forecast/views/roq_raise_po_wizard_views.xml mml_roq_forecast/__manifest__.py
git commit -m "feat(roq): add Raise PO wizard form view"
```

---

## Task 6: Create Order Dashboard view + server action + menu item

**Files:**
- Create: `mml_roq_forecast/views/roq_order_dashboard_views.xml`
- Modify: `mml_roq_forecast/views/menus.xml`
- Modify: `mml_roq_forecast/__manifest__.py`

### Step 1: Create `views/roq_order_dashboard_views.xml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Dashboard form view for roq.forecast.run (separate from the standard run form) -->
    <record id="view_roq_order_dashboard_form" model="ir.ui.view">
        <field name="name">roq.forecast.run.order.dashboard</field>
        <field name="model">roq.forecast.run</field>
        <field name="priority">20</field>
        <field name="arch" type="xml">
            <form string="Order Dashboard" create="false" delete="false">
                <header>
                    <button name="action_run" string="Re-run ROQ"
                            type="object" class="btn-secondary"
                            invisible="status in ('running', 'complete')"/>
                </header>
                <sheet>
                    <div class="oe_title">
                        <h1>Order Dashboard — <field name="name" readonly="1"/></h1>
                    </div>
                    <group>
                        <group>
                            <field name="run_date" readonly="1"/>
                            <field name="total_skus_processed" readonly="1"/>
                            <field name="total_skus_reorder" readonly="1"/>
                        </group>
                        <group>
                            <field name="total_skus_oos_risk" readonly="1"
                                   decoration-danger="total_skus_oos_risk &gt; 0"/>
                            <field name="status" readonly="1"/>
                        </group>
                    </group>
                    <notebook>
                        <!-- TAB 1: All active SKUs sorted by urgency (weeks-of-cover asc) -->
                        <page string="Urgency List" name="urgency_list">
                            <p class="text-muted">
                                Showing all active SKUs from this run sorted by weeks of cover
                                at delivery (lowest = most urgent). Dormant (Tier D) excluded.
                            </p>
                            <field name="line_ids"
                                   domain="[('abc_tier', '!=', 'D'), ('roq_containerized', '&gt;', 0)]"
                                   readonly="1">
                                <tree default_order="weeks_of_cover_at_delivery asc"
                                      decoration-danger="projected_inventory_at_delivery &lt; 0"
                                      decoration-warning="weeks_of_cover_at_delivery &lt;= 8 and projected_inventory_at_delivery &gt;= 0">
                                    <field name="abc_tier" widget="badge"
                                           decoration-danger="abc_tier == 'A'"
                                           decoration-warning="abc_tier == 'B'"
                                           decoration-info="abc_tier == 'C'"/>
                                    <field name="product_id"/>
                                    <field name="supplier_id"/>
                                    <field name="warehouse_id"/>
                                    <field name="weeks_of_cover_at_delivery" string="Cover (wks)"/>
                                    <field name="projected_inventory_at_delivery" string="Proj. Inv at Delivery"/>
                                    <field name="soh" optional="hide"/>
                                    <field name="roq_containerized" string="Order Qty"/>
                                    <field name="container_type" optional="show"/>
                                    <field name="moq_flag" widget="boolean" optional="show" string="MOQ Flag"/>
                                    <field name="notes"/>
                                </tree>
                            </field>
                        </page>

                        <!-- TAB 2: Consolidated by supplier — one row per supplier with Raise PO button -->
                        <page string="Order by Supplier" name="order_by_supplier">
                            <p class="text-muted">
                                One row per supplier. Click <strong>Raise Draft PO</strong>
                                to create purchase orders for that supplier, split by destination warehouse.
                            </p>
                            <field name="supplier_order_line_ids" readonly="0">
                                <tree decoration-danger="oos_risk_flag == True">
                                    <field name="supplier_id" readonly="1"/>
                                    <field name="product_count" string="SKUs" readonly="1"/>
                                    <field name="cbm" string="CBM" readonly="1"/>
                                    <field name="container_type" string="Container" readonly="1"/>
                                    <field name="oos_risk_flag" widget="boolean_toggle"
                                           decoration-danger="oos_risk_flag == True"
                                           readonly="1"/>
                                    <field name="purchase_order_id" string="PO" readonly="1"/>
                                    <button name="action_raise_po_wizard"
                                            string="Raise Draft PO"
                                            type="object"
                                            icon="fa-shopping-cart"
                                            class="btn-primary btn-sm"
                                            invisible="purchase_order_id != False"/>
                                </tree>
                            </field>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <!-- Server action: open the latest complete run in dashboard view -->
    <record id="action_roq_order_dashboard" model="ir.actions.server">
        <field name="name">ROQ Order Dashboard</field>
        <field name="model_id" ref="model_roq_forecast_run"/>
        <field name="state">code</field>
        <field name="code">
run = env['roq.forecast.run'].search(
    [('status', '=', 'complete')],
    order='run_date desc',
    limit=1,
)
if run:
    action = {
        'type': 'ir.actions.act_window',
        'res_model': 'roq.forecast.run',
        'res_id': run.id,
        'view_mode': 'form',
        'view_id': env.ref('mml_roq_forecast.view_roq_order_dashboard_form').id,
        'target': 'main',
    }
else:
    action = {
        'type': 'ir.actions.client',
        'tag': 'display_notification',
        'params': {
            'title': 'No Completed ROQ Runs',
            'message': 'Run an ROQ forecast first (MML Operations → ROQ Forecast → ROQ Runs).',
            'type': 'warning',
            'sticky': False,
        },
    }
        </field>
    </record>
</odoo>
```

### Step 2: Add menu item to `views/menus.xml`

Add after `menu_roq_root`, before `menu_roq_runs`:

```xml
<menuitem id="menu_roq_order_dashboard"
          name="Order Dashboard"
          parent="menu_roq_root"
          action="action_roq_order_dashboard"
          sequence="5"/>
```

### Step 3: Add dashboard views to `__manifest__.py` data list

Insert `'views/roq_order_dashboard_views.xml'` **before** `'views/menus.xml'` (server action must be defined before the menu references it):

```python
'views/roq_order_dashboard_views.xml',
'views/menus.xml',
```

### Step 4: Validate

```bash
python -c "import xml.etree.ElementTree as ET; ET.parse('mml_roq_forecast/views/roq_order_dashboard_views.xml'); print('XML OK')"
python -c "import xml.etree.ElementTree as ET; ET.parse('mml_roq_forecast/views/menus.xml'); print('XML OK')"
python -m py_compile mml_roq_forecast/__manifest__.py
```

### Step 5: Commit

```bash
git add mml_roq_forecast/views/roq_order_dashboard_views.xml mml_roq_forecast/views/menus.xml mml_roq_forecast/__manifest__.py
git commit -m "feat(roq): add Order Dashboard view, server action, and menu item"
```

---

## Task 7: Full syntax sweep + final commit

Verify every file changed in this sprint compiles cleanly.

### Step 1: Bulk syntax check

```bash
cd E:\ClaudeCode\projects\mml.odoo.apps\roq.model
python -m py_compile mml_roq_forecast/models/roq_shipment_group.py && echo OK
python -m py_compile mml_roq_forecast/models/roq_forecast_run.py && echo OK
python -m py_compile mml_roq_forecast/models/roq_raise_po_wizard.py && echo OK
python -m py_compile mml_roq_forecast/models/__init__.py && echo OK
python -m py_compile mml_roq_forecast/__manifest__.py && echo OK
python -c "
import xml.etree.ElementTree as ET
for f in [
    'mml_roq_forecast/views/roq_raise_po_wizard_views.xml',
    'mml_roq_forecast/views/roq_order_dashboard_views.xml',
    'mml_roq_forecast/views/menus.xml',
]:
    ET.parse(f)
    print(f'OK: {f}')
"
```

Expected: all `OK`.

### Step 2: Verify manifest data order

Open `__manifest__.py` and confirm the `'data'` list is in this order:

```python
'data': [
    'security/ir.model.access.csv',
    'data/ir_sequence_data.xml',
    'data/ir_cron_data.xml',
    'views/roq_forecast_run_views.xml',
    'views/roq_forecast_line_views.xml',
    'views/roq_shipment_group_views.xml',
    'views/roq_raise_po_wizard_views.xml',    # Task 5
    'views/product_template_views.xml',
    'views/res_partner_views.xml',
    'views/res_config_settings_views.xml',
    'views/roq_order_dashboard_views.xml',    # Task 6 — before menus
    'views/menus.xml',
    'reports/supplier_order_schedule.xml',
    'reports/supplier_order_schedule_template.xml',
],
```

`roq_order_dashboard_views.xml` must be loaded before `menus.xml` so the server action `action_roq_order_dashboard` exists when the menu item references it.

### Step 3: Final commit

```bash
git add -A
git commit -m "feat(roq): Order Dashboard + Draft PO Raise sprint complete"
```

---

## Odoo Install / Upgrade Command

After all tasks are committed, upgrade the module on the Odoo instance:

```bash
odoo-bin -d <db> -u mml_roq_forecast --stop-after-init
```

Then run the full test suite:

```bash
odoo-bin --test-enable -d <db> --test-tags mml_roq_forecast
```

Key test classes to watch:
- `TestRaisePoWizard` — 6 tests, all covering PO creation logic
- `TestShipmentGroup` — existing + 4 new tests for new fields/method
- `TestRoqModels` — existing + 1 new test for `supplier_order_line_ids`

---

## Manual Smoke Test Checklist

After module upgrade:

1. **MML Operations → ROQ Forecast → Order Dashboard** — if no complete run exists, should show a warning notification
2. Run a ROQ cycle (ROQ Runs → New → Run Now), wait for completion
3. Return to **Order Dashboard** — should open the completed run's form
4. **Urgency List tab** — verify rows sorted by Cover (wks) ascending; OOS rows in red
5. **Order by Supplier tab** — verify supplier rows with CBM, container type, Raise Draft PO button
6. Click **Raise Draft PO** on one supplier — wizard opens with lines pre-populated
7. Toggle "Include container padding" OFF — verify all `qty_to_order` values flip to `qty_pack_rounded`
8. Edit one line qty manually — verify it stays at the edited value after toggling
9. Click **Raise Draft PO(s)** — POs created in draft state, redirected to PO view
10. Return to **Order by Supplier** tab — PO number should now appear in the PO column; Raise button should be hidden for that row
