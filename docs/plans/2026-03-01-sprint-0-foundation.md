# Sprint 0: Foundation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Scaffold `mml_roq_forecast` Odoo 19 module with all data models, security, settings, and sequences — so it installs cleanly before any business logic is written.

**Architecture:** `mml_roq_forecast` only. The freight module (`mml_freight`) is built by a separate team to the interface contract in `roq-freight-interface-contract.md`. ROQ lists `mml_freight` as a dependency in its manifest and extends `freight.tender` with one relational field.

**Tech Stack:** Odoo 19, Python 3.10+, XML views, `ir.sequence`, `ir.config_parameter`, `res.config.settings`

**Pre-condition:** `mml_freight` is installed on the dev instance before installing `mml_roq_forecast`.

---

## Task 1: Create `mml_roq_forecast` module scaffold

**Files:**
- Create: `mml_roq_forecast/__init__.py`
- Create: `mml_roq_forecast/__manifest__.py`
- Create: `mml_roq_forecast/models/__init__.py`
- Create: `mml_roq_forecast/services/__init__.py`
- Create: `mml_roq_forecast/tests/__init__.py`

**Step 1: Write failing install test**

```python
# mml_roq_forecast/tests/__init__.py
from . import test_install

# mml_roq_forecast/tests/test_install.py
from odoo.tests.common import TransactionCase

class TestRoqInstall(TransactionCase):
    def test_roq_forecast_run_model_exists(self):
        self.assertIn('roq.forecast.run', self.env)

    def test_roq_forecast_line_model_exists(self):
        self.assertIn('roq.forecast.line', self.env)

    def test_product_template_has_abc_tier(self):
        self.assertIn('abc_tier', self.env['product.template']._fields)

    def test_res_partner_has_fob_port(self):
        self.assertIn('fob_port', self.env['res.partner']._fields)

    def test_freight_tender_has_shipment_group_id(self):
        self.assertIn('shipment_group_id', self.env['freight.tender']._fields)
```

**Step 2: Run test to verify it fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestRoqInstall
```
Expected: ImportError — module not found.

**Step 3: Create module files**

```python
# mml_roq_forecast/__init__.py
from . import models
from . import services

# mml_roq_forecast/models/__init__.py
from . import product_template_ext
from . import res_partner_ext
from . import stock_warehouse_ext
from . import purchase_order_ext
from . import freight_tender_ext
from . import roq_forecast_run
from . import roq_forecast_line
from . import roq_abc_history
from . import roq_shipment_group
from . import roq_forward_plan
from . import res_config_settings_ext

# mml_roq_forecast/services/__init__.py
# Service modules imported explicitly by the models that use them
```

```python
# mml_roq_forecast/__manifest__.py
{
    'name': 'MML ROQ Forecast & Procurement Planning',
    'version': '19.0.1.0.0',
    'summary': 'Demand forecasting, ROQ calculation, container consolidation, and 12-month procurement planning',
    'author': 'MML Consumer Products',
    'category': 'Inventory/Purchase',
    'depends': [
        'base', 'sale', 'purchase', 'stock',
        'stock_landed_costs',
        'mml_freight',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'views/roq_forecast_run_views.xml',
        'views/roq_forecast_line_views.xml',
        'views/roq_shipment_group_views.xml',
        'views/product_template_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'external_dependencies': {
        'python': ['numpy', 'scipy'],
    },
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
```

**Step 4: Run to verify test still fails (models not yet created)**

```bash
odoo-bin --test-enable -d dev -i mml_roq_forecast --test-tags /mml_roq_forecast:TestRoqInstall
```
Expected: FAIL — model files missing.

**Step 5: Commit scaffold**

```bash
git add mml_roq_forecast/__init__.py mml_roq_forecast/__manifest__.py \
        mml_roq_forecast/models/__init__.py mml_roq_forecast/services/__init__.py \
        mml_roq_forecast/tests/__init__.py mml_roq_forecast/tests/test_install.py
git commit -m "feat(roq): scaffold mml_roq_forecast module"
```

---

## Task 2: Extend standard Odoo models

**Files:**
- Create: `mml_roq_forecast/models/product_template_ext.py`
- Create: `mml_roq_forecast/models/res_partner_ext.py`
- Create: `mml_roq_forecast/models/stock_warehouse_ext.py`
- Create: `mml_roq_forecast/models/purchase_order_ext.py`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_model_extensions.py
from odoo.tests.common import TransactionCase

class TestModelExtensions(TransactionCase):

    def test_product_template_roq_fields(self):
        pt = self.env['product.template']
        for field in ['abc_tier', 'abc_tier_pending', 'abc_weeks_in_pending',
                      'abc_tier_override', 'abc_trailing_revenue',
                      'cbm_per_unit', 'pack_size', 'is_roq_managed']:
            self.assertIn(field, pt._fields, f"Missing field: {field}")

    def test_res_partner_roq_fields(self):
        rp = self.env['res.partner']
        for field in ['fob_port', 'supplier_lead_time_days',
                      'supplier_review_interval_days', 'override_expiry_date']:
            self.assertIn(field, rp._fields, f"Missing field: {field}")

    def test_stock_warehouse_roq_field(self):
        self.assertIn('is_active_for_roq', self.env['stock.warehouse']._fields)

    def test_purchase_order_shipment_group_field(self):
        self.assertIn('shipment_group_id', self.env['purchase.order']._fields)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestModelExtensions
```

**Step 3: Implement extensions**

```python
# mml_roq_forecast/models/product_template_ext.py
from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]


class ProductTemplateRoqExt(models.Model):
    _inherit = 'product.template'

    abc_tier = fields.Selection(ABC_TIERS, string='ABC Tier', readonly=True)
    abc_tier_pending = fields.Selection(ABC_TIERS, string='Pending Tier', readonly=True)
    abc_weeks_in_pending = fields.Integer(string='Weeks in Pending Tier', default=0, readonly=True)
    abc_tier_override = fields.Selection(
        ABC_TIERS, string='Tier Override (Floor)',
        help='Minimum tier floor. The SKU can still be classified higher by revenue.',
    )
    abc_trailing_revenue = fields.Float(
        string='Trailing 12M Revenue', digits=(10, 2), readonly=True,
    )
    abc_cumulative_pct = fields.Float(
        string='Cumulative Revenue %', digits=(6, 2), readonly=True,
    )
    cbm_per_unit = fields.Float(
        string='CBM per Unit', digits=(10, 4),
        help='Cubic metres per sellable unit. Required for container planning.',
    )
    pack_size = fields.Integer(
        string='Pack Size (Units/Carton)', default=1,
        help='Units per carton. ROQ is rounded up to this multiple.',
    )
    is_roq_managed = fields.Boolean(
        string='ROQ Managed', default=True,
        help='Include this product in ROQ forecast calculations.',
    )
```

```python
# mml_roq_forecast/models/res_partner_ext.py
from odoo import models, fields


class ResPartnerRoqExt(models.Model):
    _inherit = 'res.partner'

    fob_port = fields.Char(
        string='FOB Port',
        help='Port of origin for freight consolidation grouping (e.g. Shenzhen, CN).',
    )
    supplier_lead_time_days = fields.Integer(
        string='Lead Time Override (Days)',
        help='Overrides system default. Leave blank to use system default.',
    )
    supplier_review_interval_days = fields.Integer(
        string='Review Interval Override (Days)',
        help='Overrides system default. Leave blank to use system default.',
    )
    supplier_service_level = fields.Float(
        string='Service Level Override',
        digits=(4, 3),
        help='Overrides system default and ABC tier. Leave blank to use tier-based service level.',
    )
    override_expiry_date = fields.Date(
        string='Override Expiry Date',
        help='All overrides auto-revert to system default after this date.',
    )
    supplier_holiday_periods = fields.Text(
        string='Holiday Periods (JSON)',
        help='JSON array of {start, end, reason} objects. e.g. CNY shutdown periods.',
    )
    avg_lead_time_actual = fields.Float(
        string='Avg Actual Lead Time (Days)', digits=(6, 1), readonly=True,
        help='Rolling average from freight.booking records.',
    )
    lead_time_std_dev = fields.Float(
        string='Lead Time Std Dev (Days)', digits=(6, 1), readonly=True,
    )
    lead_time_on_time_pct = fields.Float(
        string='On-Time Delivery %', digits=(5, 1), readonly=True,
    )
```

```python
# mml_roq_forecast/models/stock_warehouse_ext.py
from odoo import models, fields


class StockWarehouseRoqExt(models.Model):
    _inherit = 'stock.warehouse'

    is_active_for_roq = fields.Boolean(
        string='Active for ROQ Forecast', default=True,
        help='Include this warehouse in demand forecasting and ROQ calculations.',
    )
```

```python
# mml_roq_forecast/models/purchase_order_ext.py
from odoo import models, fields


class PurchaseOrderRoqExt(models.Model):
    _inherit = 'purchase.order'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group', string='Shipment Group',
        ondelete='set null',
        help='Consolidation group this PO has been assigned to.',
    )
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestModelExtensions
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/product_template_ext.py \
        mml_roq_forecast/models/res_partner_ext.py \
        mml_roq_forecast/models/stock_warehouse_ext.py \
        mml_roq_forecast/models/purchase_order_ext.py \
        mml_roq_forecast/tests/test_model_extensions.py
git commit -m "feat(roq): extend product.template, res.partner, stock.warehouse, purchase.order"
```

---

## Task 3: Extend `freight.tender` with `shipment_group_id`

**Files:**
- Create: `mml_roq_forecast/models/freight_tender_ext.py`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_freight_tender_ext.py
from odoo.tests.common import TransactionCase

class TestFreightTenderExt(TransactionCase):

    def test_freight_tender_has_shipment_group_id_field(self):
        self.assertIn('shipment_group_id', self.env['freight.tender']._fields)

    def test_shipment_group_id_is_many2one_to_roq_shipment_group(self):
        field = self.env['freight.tender']._fields['shipment_group_id']
        self.assertEqual(field.comodel_name, 'roq.shipment.group')

    def test_freight_tender_ext_does_not_break_tender_creation(self):
        # Basic sanity: freight.tender still creates normally
        tender = self.env['freight.tender'].create({
            'origin_port': 'Shenzhen, CN',
            'dest_port': 'Auckland, NZ',
        })
        self.assertTrue(tender.id)
        self.assertFalse(tender.shipment_group_id)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestFreightTenderExt
```

**Step 3: Implement extension**

```python
# mml_roq_forecast/models/freight_tender_ext.py
from odoo import models, fields


class FreightTenderRoqExtension(models.Model):
    """
    Extends mml_freight's freight.tender with a relational link back to the
    ROQ shipment group that triggered it.

    This keeps mml_freight free of any dependency on mml_roq_forecast.
    mml_freight uses a lightweight shipment_group_ref Char field.
    This module adds the proper Many2one for navigation and ORM queries.

    See: roq-freight-interface-contract.md §2
    """
    _inherit = 'freight.tender'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group',
        string='Shipment Group',
        ondelete='set null',
        index=True,
        help='ROQ consolidation group that triggered this freight tender.',
    )
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestFreightTenderExt
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/freight_tender_ext.py \
        mml_roq_forecast/tests/test_freight_tender_ext.py
git commit -m "feat(roq): extend freight.tender with shipment_group_id relational link"
```

---

## Task 4: Create core ROQ models (`roq.forecast.run`, `roq.forecast.line`, `roq.abc.history`)

**Files:**
- Create: `mml_roq_forecast/models/roq_forecast_run.py`
- Create: `mml_roq_forecast/models/roq_forecast_line.py`
- Create: `mml_roq_forecast/models/roq_abc_history.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_roq_models.py
from odoo.tests.common import TransactionCase

class TestRoqModels(TransactionCase):

    def test_forecast_run_creates_with_sequence(self):
        run = self.env['roq.forecast.run'].create({})
        self.assertTrue(run.name.startswith('ROQ-'))
        self.assertEqual(run.status, 'draft')

    def test_forecast_line_requires_run_product_warehouse(self):
        run = self.env['roq.forecast.run'].create({})
        product = self.env['product.product'].create({'name': 'Test SKU'})
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        line = self.env['roq.forecast.line'].create({
            'run_id': run.id,
            'product_id': product.id,
            'warehouse_id': warehouse.id,
        })
        self.assertEqual(line.run_id, run)

    def test_abc_history_links_to_run_and_product(self):
        run = self.env['roq.forecast.run'].create({})
        product = self.env['product.template'].create({'name': 'Test Template'})
        history = self.env['roq.abc.history'].create({
            'product_id': product.id,
            'run_id': run.id,
            'date': '2026-03-01',
            'tier_calculated': 'A',
            'tier_applied': 'A',
        })
        self.assertEqual(history.tier_applied, 'A')
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestRoqModels
```

**Step 3: Implement models**

```python
# mml_roq_forecast/models/roq_forecast_run.py
from odoo import models, fields, api

RUN_STATUS = [
    ('draft', 'Draft'),
    ('running', 'Running'),
    ('complete', 'Complete'),
    ('error', 'Error'),
]


class RoqForecastRun(models.Model):
    _name = 'roq.forecast.run'
    _description = 'ROQ Forecast Run'
    _order = 'run_date desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.forecast.run'),
    )
    run_date = fields.Datetime(string='Run Date', default=fields.Datetime.now, readonly=True)
    status = fields.Selection(RUN_STATUS, default='draft', required=True)

    # Parameter snapshots — immutable after run completes, for audit
    lookback_weeks = fields.Integer(string='Lookback Weeks (Snapshot)')
    sma_window_weeks = fields.Integer(string='SMA Window (Snapshot)')
    default_lead_time_days = fields.Integer(string='Default Lead Time (Snapshot)')
    default_review_interval_days = fields.Integer(string='Default Review Interval (Snapshot)')
    default_service_level = fields.Float(string='Default Service Level (Snapshot)', digits=(4, 3))

    # Summary stats
    total_skus_processed = fields.Integer(string='SKUs Processed', readonly=True)
    total_skus_reorder = fields.Integer(string='SKUs with ROQ > 0', readonly=True)
    total_skus_oos_risk = fields.Integer(string='SKUs at OOS Risk', readonly=True)

    line_ids = fields.One2many('roq.forecast.line', 'run_id', string='Result Lines')
    notes = fields.Text(string='Run Log / Errors')
```

```python
# mml_roq_forecast/models/roq_forecast_line.py
from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]
FORECAST_METHODS = [
    ('sma', 'SMA'),
    ('ewma', 'EWMA'),
    ('holt_winters', 'Holt-Winters'),
]
FORECAST_CONFIDENCE = [
    ('high', 'High'),
    ('medium', 'Medium'),
    ('low', 'Low (< MIN_N data points)'),
]
CONTAINER_TYPES = [
    ('20GP', "20' Standard"),
    ('40GP', "40' Standard"),
    ('40HQ', "40' High Cube"),
    ('LCL', 'LCL'),
    ('unassigned', 'Unassigned (missing CBM/pack size)'),
]


class RoqForecastLine(models.Model):
    _name = 'roq.forecast.line'
    _description = 'ROQ Forecast Line'
    _order = 'run_id desc, supplier_id, product_id'

    run_id = fields.Many2one('roq.forecast.run', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    warehouse_id = fields.Many2one('stock.warehouse', required=True, string='Warehouse')
    supplier_id = fields.Many2one('res.partner', string='Supplier')
    fob_port = fields.Char(string='FOB Port', related='supplier_id.fob_port', store=True)

    # ABCD
    abc_tier = fields.Selection(ABC_TIERS, string='Tier')
    trailing_12m_revenue = fields.Float(string='Trailing 12M Revenue', digits=(10, 2))
    cumulative_revenue_pct = fields.Float(string='Cumulative %', digits=(6, 2))
    tier_override = fields.Char(string='Override Active')

    # Inventory position
    soh = fields.Float(string='SOH', digits=(10, 3))
    confirmed_po_qty = fields.Float(string='Confirmed PO Qty', digits=(10, 3))
    inventory_position = fields.Float(string='Inventory Position', digits=(10, 3))

    # Forecast
    avg_weekly_demand = fields.Float(string='Avg Weekly Demand', digits=(10, 3))
    forecasted_weekly_demand = fields.Float(string='Forecast Weekly Demand', digits=(10, 3))
    forecast_method = fields.Selection(FORECAST_METHODS, string='Forecast Method')
    forecast_confidence = fields.Selection(FORECAST_CONFIDENCE, string='Confidence')
    demand_std_dev = fields.Float(string='Demand Std Dev (σ)', digits=(10, 3))

    # Safety stock
    safety_stock = fields.Float(string='Safety Stock', digits=(10, 3))
    z_score = fields.Float(string='Z-Score', digits=(6, 3))
    lead_time_days = fields.Integer(string='Lead Time (Days)')
    review_interval_days = fields.Integer(string='Review Interval (Days)')

    # ROQ
    out_level = fields.Float(string='Out Level (s)', digits=(10, 3))
    order_up_to = fields.Float(string='Order-Up-To (S)', digits=(10, 3))
    roq_raw = fields.Float(string='ROQ (Raw)', digits=(10, 3))
    roq_pack_rounded = fields.Float(string='ROQ (Pack Rounded)', digits=(10, 3))
    roq_containerized = fields.Float(string='ROQ (Containerized)', digits=(10, 3))

    # Container
    cbm_per_unit = fields.Float(string='CBM/Unit', digits=(10, 4))
    cbm_total = fields.Float(string='CBM Total', digits=(10, 3))
    pack_size = fields.Integer(string='Pack Size')
    container_type = fields.Selection(CONTAINER_TYPES, string='Container')
    container_fill_pct = fields.Float(string='Fill %', digits=(5, 1))
    padding_units = fields.Float(string='Padding Units', digits=(10, 3))

    # Urgency
    projected_inventory_at_delivery = fields.Float(
        string='Projected Inv at Delivery', digits=(10, 3),
    )
    weeks_of_cover_at_delivery = fields.Float(
        string='Weeks Cover at Delivery', digits=(6, 1),
    )
    notes = fields.Char(string='Flags / Warnings')
```

```python
# mml_roq_forecast/models/roq_abc_history.py
from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]


class RoqAbcHistory(models.Model):
    _name = 'roq.abc.history'
    _description = 'ABC Tier Classification History'
    _order = 'date desc, product_id'

    product_id = fields.Many2one('product.template', required=True, ondelete='cascade')
    run_id = fields.Many2one('roq.forecast.run', ondelete='set null')
    date = fields.Date(required=True)
    tier_calculated = fields.Selection(ABC_TIERS, string='Calculated Tier', required=True)
    tier_applied = fields.Selection(ABC_TIERS, string='Applied Tier', required=True)
    trailing_revenue = fields.Float(string='Trailing 12M Revenue', digits=(10, 2))
    cumulative_pct = fields.Float(string='Cumulative %', digits=(6, 2))
    override_active = fields.Char(string='Override Active')
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestRoqModels
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/roq_forecast_run.py \
        mml_roq_forecast/models/roq_forecast_line.py \
        mml_roq_forecast/models/roq_abc_history.py \
        mml_roq_forecast/tests/test_roq_models.py
git commit -m "feat(roq): add roq.forecast.run, roq.forecast.line, roq.abc.history models"
```

---

## Task 5: Create `roq.shipment.group` and `roq.shipment.group.line` models

> **Interface contract:** Field names and container type values MUST match `roq-freight-interface-contract.md`.
> - `state` (not `status`)
> - Container types: `20GP` / `40GP` / `40HQ` / `LCL`
> - `origin_port` + `destination_port` (not just `fob_port`)
> - `po_ids` Many2many → `purchase.order` (freight needs this on confirm)
> - `actual_delivery_date` + lead time fields (written by freight on delivery)

**Files:**
- Create: `mml_roq_forecast/models/roq_shipment_group.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_shipment_group.py
from odoo.tests.common import TransactionCase

class TestShipmentGroup(TransactionCase):

    def test_shipment_group_creates_with_sequence(self):
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shenzhen, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '40GP',
        })
        self.assertTrue(sg.name.startswith('SG-'))
        self.assertEqual(sg.state, 'draft')

    def test_container_type_values_match_contract(self):
        """Contract specifies 20GP/40GP/40HQ/LCL — not 20ft/40ft etc."""
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Ningbo, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': '20GP',
        })
        self.assertEqual(sg.container_type, '20GP')

    def test_shipment_group_line_links_to_group(self):
        sg = self.env['roq.shipment.group'].create({
            'origin_port': 'Shanghai, CN',
            'destination_port': 'Auckland, NZ',
            'container_type': 'LCL',
        })
        supplier = self.env['res.partner'].create({'name': 'Test Supplier'})
        line = self.env['roq.shipment.group.line'].create({
            'group_id': sg.id,
            'supplier_id': supplier.id,
            'cbm': 15.5,
        })
        self.assertEqual(line.group_id, sg)

    def test_actual_delivery_date_field_exists(self):
        """Required for freight feedback per interface contract §4."""
        self.assertIn('actual_delivery_date', self.env['roq.shipment.group']._fields)

    def test_po_ids_field_exists(self):
        """Required: freight.tender is created with po_ids from this field."""
        self.assertIn('po_ids', self.env['roq.shipment.group']._fields)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestShipmentGroup
```

**Step 3: Implement model**

```python
# mml_roq_forecast/models/roq_shipment_group.py
from odoo import models, fields, api, exceptions

# Contract-specified container type values — do NOT change these keys
CONTAINER_TYPES = [
    ('20GP', "20' Standard (20GP)"),
    ('40GP', "40' Standard (40GP)"),
    ('40HQ', "40' High Cube (40HQ)"),
    ('LCL', 'LCL'),
]

SHIPMENT_STATE = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),   # action_confirm() → creates freight.tender
    ('tendered', 'Tendered'),     # freight module transitions this
    ('booked', 'Booked'),         # freight module transitions this
    ('delivered', 'Delivered'),   # freight module writes back actual_delivery_date
    ('cancelled', 'Cancelled'),
]

SHIPMENT_MODE = [
    ('reactive', 'Reactive'),    # Created from weekly ROQ run
    ('proactive', 'Proactive'),  # Created from 12-month forward plan
]


class RoqShipmentGroup(models.Model):
    _name = 'roq.shipment.group'
    _description = 'Shipment Consolidation Group'
    _order = 'target_ship_date desc'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.shipment.group'),
    )

    # Interface contract required fields
    state = fields.Selection(SHIPMENT_STATE, default='draft', required=True, tracking=True)
    origin_port = fields.Char(string='Origin Port (FOB)')
    destination_port = fields.Char(string='Destination Port')
    container_type = fields.Selection(CONTAINER_TYPES, string='Container Type')
    total_cbm = fields.Float(string='Total CBM', digits=(10, 3))
    total_weight_kg = fields.Float(string='Total Weight (kg)', digits=(10, 2))
    target_ship_date = fields.Date(string='Target Ship Date (ETD)')
    target_delivery_date = fields.Date(string='Target Delivery Date')
    po_ids = fields.Many2many(
        'purchase.order', string='Purchase Orders',
        help='POs consolidated in this shipment. Passed to freight.tender on confirm.',
    )
    freight_tender_id = fields.Many2one(
        'freight.tender', string='Freight Tender',
        ondelete='set null', readonly=True,
        help='Created by ROQ on action_confirm. Managed by mml_freight thereafter.',
    )

    # Delivery feedback — written by freight module (Option A per contract)
    actual_delivery_date = fields.Date(
        string='Actual Delivery Date', readonly=True,
        help='Written by mml_freight when freight.booking reaches delivered state.',
    )
    actual_lead_time_days = fields.Integer(
        string='Actual Lead Time (Days)', readonly=True,
    )
    lead_time_variance_days = fields.Integer(
        string='Lead Time Variance (Days)', readonly=True,
        help='Positive = late. Negative = early.',
    )

    # ROQ internal fields
    fill_percentage = fields.Float(string='Fill %', digits=(5, 1))
    mode = fields.Selection(SHIPMENT_MODE, default='reactive', required=True)
    destination_warehouse_ids = fields.Many2many(
        'stock.warehouse', string='Destination Warehouses',
    )
    run_id = fields.Many2one(
        'roq.forecast.run', string='Source ROQ Run', ondelete='set null',
    )
    line_ids = fields.One2many('roq.shipment.group.line', 'group_id', string='Supplier Lines')
    notes = fields.Text(string='Notes')

    # Derived for display — fob_port is the canonical key used internally for grouping
    fob_port = fields.Char(
        string='FOB Port (Key)', related='origin_port', store=True,
        help='Alias for origin_port used by consolidation grouping logic.',
    )

    def action_confirm(self):
        """
        Confirm this shipment group and create a freight.tender.
        Per interface contract §3: ROQ creates the tender; freight manages it thereafter.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise exceptions.UserError('Only draft shipment groups can be confirmed.')

        tender = self.env['freight.tender'].create({
            'shipment_group_ref': self.name,        # lightweight ref (always present)
            'shipment_group_id': self.id,           # relational link (our extension field)
            'origin_port': self.origin_port or '',
            'dest_port': self.destination_port or '',
            'requested_pickup_date': self.target_ship_date,
            'requested_delivery_date': self.target_delivery_date,
            'po_ids': [(4, po.id) for po in self.po_ids],
        })

        self.write({
            'freight_tender_id': tender.id,
            'state': 'confirmed',
        })
        self.message_post(
            body=f'Shipment group confirmed. Freight tender created: {tender.name}',
        )

    def action_cancel(self):
        self.ensure_one()
        if self.state in ('delivered', 'cancelled'):
            raise exceptions.UserError('Cannot cancel a delivered or already cancelled group.')
        self.state = 'cancelled'
        self.message_post(body='Shipment group cancelled.')


class RoqShipmentGroupLine(models.Model):
    _name = 'roq.shipment.group.line'
    _description = 'Shipment Group Supplier Line'

    group_id = fields.Many2one('roq.shipment.group', required=True, ondelete='cascade')
    supplier_id = fields.Many2one('res.partner', required=True, string='Supplier')
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order', ondelete='set null',
        help='Nullable — PO may not exist yet in proactive mode.',
    )
    cbm = fields.Float(string='CBM Contribution', digits=(10, 3))
    weight_kg = fields.Float(string='Weight (kg)', digits=(10, 2))
    push_pull_days = fields.Integer(
        string='Push/Pull Days',
        help='Positive = pushed (delayed). Negative = pulled (brought forward). 0 = as planned.',
    )
    push_pull_reason = fields.Char(string='Push/Pull Reason')
    oos_risk_flag = fields.Boolean(
        string='OOS Risk',
        help='True if any SKU in this supplier order has projected inventory at delivery < 0.',
    )
    original_ship_date = fields.Date(string='Original Ship Date')
    product_count = fields.Integer(string='SKU Count')
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestShipmentGroup
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/roq_shipment_group.py \
        mml_roq_forecast/tests/test_shipment_group.py
git commit -m "feat(roq): add roq.shipment.group with contract-compliant fields (state, 20GP/40GP/40HQ/LCL, po_ids)"
```

---

## Task 6: Create `roq.forward.plan` and `roq.forward.plan.line` models

**Files:**
- Create: `mml_roq_forecast/models/roq_forward_plan.py`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_forward_plan.py
from datetime import date
from odoo.tests.common import TransactionCase

class TestForwardPlan(TransactionCase):

    def test_forward_plan_creates_with_sequence(self):
        supplier = self.env['res.partner'].create({'name': 'FP Supplier'})
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
        })
        self.assertTrue(plan.name.startswith('FP-'))

    def test_forward_plan_totals_computed(self):
        supplier = self.env['res.partner'].create({'name': 'FP2 Supplier'})
        product = self.env['product.product'].create({'name': 'FP SKU', 'type': 'product'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
        })
        self.env['roq.forward.plan.line'].create({
            'plan_id': plan.id,
            'product_id': product.id,
            'warehouse_id': wh.id,
            'month': date.today().replace(day=1),
            'planned_order_qty': 100,
            'cbm': 5.0,
            'fob_line_cost': 500.0,
        })
        self.assertAlmostEqual(plan.total_cbm, 5.0, places=2)
        self.assertAlmostEqual(plan.total_fob_cost, 500.0, places=2)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestForwardPlan
```

**Step 3: Implement**

```python
# mml_roq_forecast/models/roq_forward_plan.py
from odoo import models, fields, api


class RoqForwardPlan(models.Model):
    _name = 'roq.forward.plan'
    _description = '12-Month Forward Procurement Plan'
    _order = 'generated_date desc, supplier_id'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.forward.plan'),
    )
    supplier_id = fields.Many2one('res.partner', required=True, string='Supplier')
    fob_port = fields.Char(string='FOB Port', related='supplier_id.fob_port', store=True)
    generated_date = fields.Date(string='Generated Date', required=True)
    run_id = fields.Many2one('roq.forecast.run', string='Source ROQ Run', ondelete='set null')
    horizon_months = fields.Integer(string='Horizon (Months)', default=12)
    total_units = fields.Float(
        string='Total Units', compute='_compute_totals', store=True, digits=(10, 0),
    )
    total_cbm = fields.Float(
        string='Total CBM', compute='_compute_totals', store=True, digits=(10, 3),
    )
    total_fob_cost = fields.Float(
        string='Total FOB Cost', compute='_compute_totals', store=True, digits=(10, 2),
    )
    line_ids = fields.One2many('roq.forward.plan.line', 'plan_id', string='Plan Lines')

    @api.depends('line_ids.planned_order_qty', 'line_ids.cbm', 'line_ids.fob_line_cost')
    def _compute_totals(self):
        for rec in self:
            rec.total_units = sum(rec.line_ids.mapped('planned_order_qty'))
            rec.total_cbm = sum(rec.line_ids.mapped('cbm'))
            rec.total_fob_cost = sum(rec.line_ids.mapped('fob_line_cost'))


class RoqForwardPlanLine(models.Model):
    _name = 'roq.forward.plan.line'
    _description = 'Forward Plan Line'
    _order = 'plan_id, month, product_id'

    plan_id = fields.Many2one('roq.forward.plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse')
    month = fields.Date(string='Month (1st of Month)', required=True)
    forecasted_monthly_demand = fields.Float(string='Forecast Monthly Demand', digits=(10, 3))
    planned_order_qty = fields.Float(string='Planned Order Qty', digits=(10, 0))
    planned_order_date = fields.Date(string='Order Placement Date')
    planned_ship_date = fields.Date(string='Planned Ship Date')
    cbm = fields.Float(string='CBM', digits=(10, 3))
    fob_unit_cost = fields.Float(string='FOB Unit Cost', digits=(10, 4))
    fob_line_cost = fields.Float(string='FOB Line Cost', digits=(10, 2))
    consolidation_note = fields.Char(string='Consolidation Note')
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestForwardPlan
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/roq_forward_plan.py \
        mml_roq_forecast/tests/test_forward_plan.py
git commit -m "feat(roq): add roq.forward.plan and roq.forward.plan.line models"
```

---

## Task 7: Settings, sequences, security, and stub views

**Files:**
- Create: `mml_roq_forecast/models/res_config_settings_ext.py`
- Create: `mml_roq_forecast/data/ir_sequence_data.xml`
- Create: `mml_roq_forecast/data/ir_cron_data.xml`
- Create: `mml_roq_forecast/security/ir.model.access.csv`
- Create: `mml_roq_forecast/views/roq_forecast_run_views.xml` (stub)
- Create: `mml_roq_forecast/views/roq_forecast_line_views.xml` (stub)
- Create: `mml_roq_forecast/views/roq_shipment_group_views.xml` (stub)
- Create: `mml_roq_forecast/views/product_template_views.xml` (stub)
- Create: `mml_roq_forecast/views/res_partner_views.xml` (stub)
- Create: `mml_roq_forecast/views/res_config_settings_views.xml`
- Create: `mml_roq_forecast/views/menus.xml`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_settings.py
from odoo.tests.common import TransactionCase

class TestRoqSettings(TransactionCase):

    def test_roq_run_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.forecast.run')
        self.assertTrue(ref.startswith('ROQ-'))

    def test_shipment_group_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.shipment.group')
        self.assertTrue(ref.startswith('SG-'))

    def test_forward_plan_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.forward.plan')
        self.assertTrue(ref.startswith('FP-'))

    def test_all_roq_models_have_access_rules(self):
        models_to_check = [
            'roq.forecast.run', 'roq.forecast.line', 'roq.abc.history',
            'roq.shipment.group', 'roq.shipment.group.line',
            'roq.forward.plan', 'roq.forward.plan.line',
        ]
        for model_name in models_to_check:
            access = self.env['ir.model.access'].search([
                ('model_id.model', '=', model_name),
            ])
            self.assertTrue(access, f"No access rules for {model_name}")
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestRoqSettings
```

**Step 3: Create settings model extension**

```python
# mml_roq_forecast/models/res_config_settings_ext.py
from odoo import models, fields


class ResConfigSettingsRoqExt(models.TransientModel):
    _inherit = 'res.config.settings'

    roq_default_lead_time_days = fields.Integer(
        string='Default Lead Time (Days)', default=100,
        config_parameter='roq.default_lead_time_days',
    )
    roq_default_review_interval_days = fields.Integer(
        string='Default Review Interval (Days)', default=30,
        config_parameter='roq.default_review_interval_days',
    )
    roq_lookback_weeks = fields.Integer(
        string='Lookback Weeks', default=156,
        config_parameter='roq.lookback_weeks',
    )
    roq_sma_window_weeks = fields.Integer(
        string='SMA Window (Weeks)', default=52,
        config_parameter='roq.sma_window_weeks',
    )
    roq_min_n_value = fields.Integer(
        string='Min N Value', default=8,
        config_parameter='roq.min_n_value',
    )
    roq_abc_dampener_weeks = fields.Integer(
        string='ABC Dampener (Weeks)', default=4,
        config_parameter='roq.abc_dampener_weeks',
    )
    roq_container_lcl_threshold_pct = fields.Integer(
        string='Container LCL Threshold (%)', default=50,
        config_parameter='roq.container_lcl_threshold_pct',
    )
    roq_max_pull_days = fields.Integer(
        string='Max Pull Days', default=30,
        config_parameter='roq.max_pull_days',
    )
```

**Step 4: Create sequence data**

```xml
<!-- mml_roq_forecast/data/ir_sequence_data.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="seq_roq_forecast_run" model="ir.sequence">
        <field name="name">ROQ Forecast Run</field>
        <field name="code">roq.forecast.run</field>
        <field name="prefix">ROQ-%(year)s-W</field>
        <field name="padding">2</field>
    </record>
    <record id="seq_roq_shipment_group" model="ir.sequence">
        <field name="name">ROQ Shipment Group</field>
        <field name="code">roq.shipment.group</field>
        <field name="prefix">SG-%(year)s-</field>
        <field name="padding">4</field>
    </record>
    <record id="seq_roq_forward_plan" model="ir.sequence">
        <field name="name">ROQ Forward Plan</field>
        <field name="code">roq.forward.plan</field>
        <field name="prefix">FP-%(year)s-</field>
        <field name="padding">4</field>
    </record>
</odoo>
```

**Step 5: Create cron stub**

```xml
<!-- mml_roq_forecast/data/ir_cron_data.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="cron_roq_weekly_run" model="ir.cron">
        <field name="name">ROQ: Weekly Forecast Run</field>
        <field name="model_id" ref="model_roq_forecast_run"/>
        <field name="state">code</field>
        <field name="code">model.cron_run_weekly_roq()</field>
        <field name="interval_number">1</field>
        <field name="interval_type">weeks</field>
        <field name="numbercall">-1</field>
        <field name="active">False</field>
    </record>
</odoo>
```

**Step 6: Create security CSV**

```csv
# mml_roq_forecast/security/ir.model.access.csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_roq_forecast_run_user,roq.forecast.run user,model_roq_forecast_run,base.group_user,1,0,0,0
access_roq_forecast_run_manager,roq.forecast.run manager,model_roq_forecast_run,base.group_system,1,1,1,1
access_roq_forecast_line_user,roq.forecast.line user,model_roq_forecast_line,base.group_user,1,0,0,0
access_roq_forecast_line_manager,roq.forecast.line manager,model_roq_forecast_line,base.group_system,1,1,1,1
access_roq_abc_history_user,roq.abc.history user,model_roq_abc_history,base.group_user,1,0,0,0
access_roq_abc_history_manager,roq.abc.history manager,model_roq_abc_history,base.group_system,1,1,1,1
access_roq_shipment_group_user,roq.shipment.group user,model_roq_shipment_group,base.group_user,1,1,1,0
access_roq_shipment_group_manager,roq.shipment.group manager,model_roq_shipment_group,base.group_system,1,1,1,1
access_roq_shipment_group_line_user,roq.shipment.group.line user,model_roq_shipment_group_line,base.group_user,1,1,1,0
access_roq_shipment_group_line_manager,roq.shipment.group.line manager,model_roq_shipment_group_line,base.group_system,1,1,1,1
access_roq_forward_plan_user,roq.forward.plan user,model_roq_forward_plan,base.group_user,1,0,0,0
access_roq_forward_plan_manager,roq.forward.plan manager,model_roq_forward_plan,base.group_system,1,1,1,1
access_roq_forward_plan_line_user,roq.forward.plan.line user,model_roq_forward_plan_line,base.group_user,1,0,0,0
access_roq_forward_plan_line_manager,roq.forward.plan.line manager,model_roq_forward_plan_line,base.group_system,1,1,1,1
```

**Step 7: Create stub views (enough for module to load — full views in Sprint 2)**

```xml
<!-- mml_roq_forecast/views/roq_forecast_run_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_roq_forecast_run" model="ir.actions.act_window">
        <field name="name">ROQ Runs</field>
        <field name="res_model">roq.forecast.run</field>
        <field name="view_mode">tree,form</field>
    </record>
</odoo>
```

```xml
<!-- mml_roq_forecast/views/roq_forecast_line_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_roq_forecast_line" model="ir.actions.act_window">
        <field name="name">ROQ Lines</field>
        <field name="res_model">roq.forecast.line</field>
        <field name="view_mode">tree</field>
    </record>
</odoo>
```

```xml
<!-- mml_roq_forecast/views/roq_shipment_group_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_roq_shipment_group" model="ir.actions.act_window">
        <field name="name">Shipment Groups</field>
        <field name="res_model">roq.shipment.group</field>
        <field name="view_mode">tree,form</field>
    </record>
</odoo>
```

```xml
<!-- mml_roq_forecast/views/product_template_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Full product tab added in Sprint 3 -->
</odoo>
```

```xml
<!-- mml_roq_forecast/views/res_partner_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <!-- Full supplier tab added in Sprint 3 -->
</odoo>
```

```xml
<!-- mml_roq_forecast/views/res_config_settings_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="res_config_settings_view_roq" model="ir.ui.view">
        <field name="name">res.config.settings.view.roq</field>
        <field name="model">res.config.settings</field>
        <field name="inherit_id" ref="base_setup.action_general_configuration"/>
        <field name="arch" type="xml">
            <xpath expr="//div[hasclass('settings')]" position="inside">
                <div class="app_settings_block" string="ROQ Forecast" data-string="ROQ Forecast">
                    <h2>ROQ Forecast Parameters</h2>
                    <div class="row mt16 o_settings_container">
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="Default Lead Time (Days)" for="roq_default_lead_time_days"/>
                                <div class="text-muted">System-wide default. Overridden per-supplier.</div>
                                <field name="roq_default_lead_time_days"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="Default Review Interval (Days)" for="roq_default_review_interval_days"/>
                                <field name="roq_default_review_interval_days"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="Lookback Weeks" for="roq_lookback_weeks"/>
                                <div class="text-muted">156 = 3 years history</div>
                                <field name="roq_lookback_weeks"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="SMA Window (Weeks)" for="roq_sma_window_weeks"/>
                                <field name="roq_sma_window_weeks"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="Min N Value" for="roq_min_n_value"/>
                                <div class="text-muted">Min data points for reliable std dev</div>
                                <field name="roq_min_n_value"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="ABC Dampener (Weeks)" for="roq_abc_dampener_weeks"/>
                                <field name="roq_abc_dampener_weeks"/>
                            </div>
                        </div>
                        <div class="col-12 col-lg-6 o_setting_box">
                            <div class="o_setting_right_pane">
                                <label string="Container LCL Threshold (%)" for="roq_container_lcl_threshold_pct"/>
                                <field name="roq_container_lcl_threshold_pct"/>
                            </div>
                        </div>
                    </div>
                </div>
            </xpath>
        </field>
    </record>
</odoo>
```

```xml
<!-- mml_roq_forecast/views/menus.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_mml_operations_root"
              name="MML Operations"
              sequence="10"/>

    <menuitem id="menu_roq_root"
              name="ROQ Forecast"
              parent="menu_mml_operations_root"
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
</odoo>
```

**Step 8: Full install test**

```bash
odoo-bin -d dev -i mml_roq_forecast --stop-after-init
```
Expected: Module installs without error.

**Step 9: Run all Sprint 0 tests**

```bash
odoo-bin --test-enable -d dev --test-tags mml_roq_forecast
```
Expected: All tests PASS.

**Step 10: Commit**

```bash
git add mml_roq_forecast/models/res_config_settings_ext.py \
        mml_roq_forecast/data/ \
        mml_roq_forecast/security/ \
        mml_roq_forecast/views/
git commit -m "feat(roq): add settings, sequences, security, and stub views — Sprint 0 complete"
```

---

## Sprint 0 Done Checklist

- [ ] `mml_roq_forecast` installs cleanly: `odoo-bin -d dev -i mml_roq_forecast`
- [ ] All 7 ROQ models visible in Technical > Models
- [ ] `freight.tender` has `shipment_group_id` field (our extension)
- [ ] "MML Operations > ROQ Forecast" menu appears in UI
- [ ] ROQ settings section appears in Settings > General Settings
- [ ] Sequences work: `ROQ-`, `SG-`, `FP-` prefixes
- [ ] Container type values are `20GP/40GP/40HQ/LCL` (not 20ft etc)
- [ ] `roq.shipment.group` uses `state` field (not `status`)
- [ ] `roq.shipment.group` has `po_ids`, `actual_delivery_date`, `origin_port`, `destination_port`
- [ ] All tests pass: `odoo-bin --test-enable -d dev --test-tags mml_roq_forecast`
