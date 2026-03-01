# Sprint 4: 12-Month Forward Plan + Lead Time Feedback — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Implement Layer 3B (12-month forward procurement plan with supplier PDF export and internal cash flow view) and lead time feedback stats from freight delivery data.

**Architecture:**
- Forward plan: `forward_plan_generator.py` service driven by demand forecast, creates `roq.forward.plan` + lines. Proactive consolidation hooks into this to generate future `roq.shipment.group` records.
- Lead time feedback: `res.partner` extended with rolling stats computed from `freight.booking` records (delivered state). DSV API and tracking polling live in `mml_freight_dsv` — out of scope here.

**Tech Stack:** Odoo 19, `reportlab` or Odoo QWeb (PDF), pivot view for cash flow.

**Pre-condition:** Sprint 3 complete. Consolidation engine runs, shipment groups created.

---

## Task 1: 12-month forward demand projection

**Files:**
- Create: `mml_roq_forecast/services/forward_plan_generator.py`
- Create: `mml_roq_forecast/tests/test_forward_plan_generator.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_forward_plan_generator.py
from datetime import date
from odoo.tests.common import TransactionCase
from ..services.forward_plan_generator import ForwardPlanGenerator

class TestForwardPlanGenerator(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.warehouse.is_active_for_roq = True
        self.supplier = self.env['res.partner'].create({
            'name': 'FP Supplier', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.product_tmpl = self.env['product.template'].create({
            'name': 'FP Test SKU', 'type': 'product',
            'is_roq_managed': True, 'cbm_per_unit': 0.05, 'pack_size': 6,
        })
        self.env['product.supplierinfo'].create({
            'partner_id': self.supplier.id,
            'product_tmpl_id': self.product_tmpl.id,
            'price': 15.0,
        })
        self.run = self.env['roq.forecast.run'].create({'status': 'complete'})
        self.gen = ForwardPlanGenerator(self.env)

    def test_generates_12_months_of_lines(self):
        plan = self.gen.generate_for_supplier(
            self.supplier, self.run, horizon_months=12
        )
        # Should have lines per month × per product × per warehouse
        self.assertGreater(len(plan.line_ids), 0)
        months = set(plan.line_ids.mapped('month'))
        self.assertEqual(len(months), 12)

    def test_monthly_demand_is_weekly_times_4_33(self):
        # Create forecast lines on the run with known weekly demand
        product = self.product_tmpl.product_variant_ids[0]
        self.env['roq.forecast.line'].create({
            'run_id': self.run.id,
            'product_id': product.id,
            'warehouse_id': self.warehouse.id,
            'supplier_id': self.supplier.id,
            'forecasted_weekly_demand': 10.0,
            'abc_tier': 'B',
        })
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        if plan.line_ids:
            first_line = plan.line_ids.sorted('month')[0]
            self.assertAlmostEqual(
                first_line.forecasted_monthly_demand, 10.0 * 4.33, places=0
            )

    def test_planned_order_date_is_ship_date_minus_lead_time(self):
        product = self.product_tmpl.product_variant_ids[0]
        self.env['roq.forecast.line'].create({
            'run_id': self.run.id,
            'product_id': product.id,
            'warehouse_id': self.warehouse.id,
            'supplier_id': self.supplier.id,
            'forecasted_weekly_demand': 10.0,
            'lead_time_days': 100,
            'abc_tier': 'B',
        })
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        for line in plan.line_ids:
            if line.planned_ship_date and line.planned_order_date:
                diff = (line.planned_ship_date - line.planned_order_date).days
                self.assertAlmostEqual(diff, 100, delta=7)  # ±1 week tolerance

    def test_total_fob_cost_computed(self):
        product = self.product_tmpl.product_variant_ids[0]
        self.env['roq.forecast.line'].create({
            'run_id': self.run.id,
            'product_id': product.id,
            'warehouse_id': self.warehouse.id,
            'supplier_id': self.supplier.id,
            'forecasted_weekly_demand': 10.0,
            'abc_tier': 'A',
        })
        plan = self.gen.generate_for_supplier(self.supplier, self.run, horizon_months=3)
        self.assertGreater(plan.total_fob_cost, 0.0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestForwardPlanGenerator
```

**Step 3: Implement generator**

```python
# mml_roq_forecast/services/forward_plan_generator.py
"""
12-Month Forward Procurement Plan Generator.

For each supplier, generates a rolling 12-month procurement schedule:
- Monthly demand = forecasted_weekly_demand × 4.33 (average weeks per month)
- Order qty per cycle = weekly_demand × (review_interval / 7) × num_warehouses
- Planned order date = planned_ship_date − lead_time_days
- FOB cost from product.supplierinfo pricelist

Holiday periods (e.g. CNY) are read from supplier.supplier_holiday_periods (JSON).
Orders falling in a holiday window are pushed to the next available date.
"""
import json
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from .settings_helper import SettingsHelper

WEEKS_PER_MONTH = 4.33


class ForwardPlanGenerator:

    def __init__(self, env):
        self.env = env
        self.settings = SettingsHelper(env)

    def generate_for_supplier(self, supplier, run, horizon_months=12):
        """
        Creates a roq.forward.plan record for the supplier.
        Derives monthly requirements from roq.forecast.line records on the run.
        """
        # Get all forecast lines for this supplier from the run
        lines = self.env['roq.forecast.line'].search([
            ('run_id', '=', run.id),
            ('supplier_id', '=', supplier.id),
            ('abc_tier', '!=', 'D'),
            ('forecasted_weekly_demand', '>', 0),
        ])

        if not lines:
            return self.env['roq.forward.plan']

        lt_days = self.settings.get_lead_time_days(supplier)
        review_days = self.settings.get_review_interval_days(supplier)
        holiday_periods = self._parse_holiday_periods(supplier.supplier_holiday_periods)

        today = date.today()
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': today,
            'run_id': run.id,
            'horizon_months': horizon_months,
        })

        # Group lines by product (sum across warehouses for order qty)
        from collections import defaultdict
        by_product = defaultdict(list)
        for line in lines:
            by_product[line.product_id.id].append(line)

        plan_line_vals = []
        for product_id, prod_lines in by_product.items():
            product = prod_lines[0].product_id

            # Sum weekly demand across warehouses
            total_weekly_demand = sum(l.forecasted_weekly_demand for l in prod_lines)
            monthly_demand = total_weekly_demand * WEEKS_PER_MONTH
            order_qty_per_cycle = total_weekly_demand * (review_days / 7.0)

            # Get FOB unit cost from supplierinfo
            fob_unit_cost = self._get_fob_unit_cost(product, supplier)
            cbm_per_unit = product.product_tmpl_id.cbm_per_unit or 0.0

            # Generate one entry per month in the horizon
            for month_offset in range(horizon_months):
                month_start = (today + relativedelta(months=month_offset)).replace(day=1)
                planned_ship_date = month_start
                planned_order_date = planned_ship_date - timedelta(days=lt_days)

                # Adjust for holiday periods
                planned_order_date = self._adjust_for_holidays(
                    planned_order_date, holiday_periods
                )

                # For each warehouse, add a line
                for line in prod_lines:
                    wh_weekly = line.forecasted_weekly_demand
                    wh_monthly = wh_weekly * WEEKS_PER_MONTH
                    # Order qty for this warehouse = proportional share of cycle qty
                    wh_share = wh_weekly / total_weekly_demand if total_weekly_demand else 0
                    wh_order_qty = round(order_qty_per_cycle * wh_share)

                    # Round to pack size
                    pack_size = product.product_tmpl_id.pack_size or 1
                    if pack_size > 1 and wh_order_qty:
                        import math
                        wh_order_qty = math.ceil(wh_order_qty / pack_size) * pack_size

                    fob_line_cost = wh_order_qty * fob_unit_cost
                    cbm = wh_order_qty * cbm_per_unit

                    plan_line_vals.append({
                        'plan_id': plan.id,
                        'product_id': product.id,
                        'warehouse_id': line.warehouse_id.id,
                        'month': month_start,
                        'forecasted_monthly_demand': wh_monthly,
                        'planned_order_qty': wh_order_qty,
                        'planned_order_date': planned_order_date,
                        'planned_ship_date': planned_ship_date,
                        'cbm': cbm,
                        'fob_unit_cost': fob_unit_cost,
                        'fob_line_cost': fob_line_cost,
                    })

        self.env['roq.forward.plan.line'].create(plan_line_vals)
        return plan

    def generate_all_plans(self, run):
        """Generate forward plans for all suppliers with active ROQ lines."""
        suppliers = self.env['res.partner'].browse(
            self.env['roq.forecast.line'].search([
                ('run_id', '=', run.id),
                ('abc_tier', '!=', 'D'),
                ('supplier_id', '!=', False),
            ]).mapped('supplier_id').ids
        )
        plans = self.env['roq.forward.plan']
        for supplier in suppliers:
            plans |= self.generate_for_supplier(supplier, run)
        return plans

    def _get_fob_unit_cost(self, product, supplier):
        """Get unit cost from product.supplierinfo for this supplier."""
        supplierinfo = self.env['product.supplierinfo'].search([
            ('partner_id', '=', supplier.id),
            ('product_tmpl_id', '=', product.product_tmpl_id.id),
        ], limit=1)
        return supplierinfo.price if supplierinfo else 0.0

    def _parse_holiday_periods(self, holiday_json):
        """Parse supplier holiday periods from JSON string."""
        if not holiday_json:
            return []
        try:
            periods = json.loads(holiday_json)
            return [
                {
                    'start': date.fromisoformat(p['start']),
                    'end': date.fromisoformat(p['end']),
                    'reason': p.get('reason', ''),
                }
                for p in periods
            ]
        except (json.JSONDecodeError, KeyError, ValueError):
            return []

    def _adjust_for_holidays(self, order_date, holiday_periods):
        """
        If order_date falls within a holiday window, push it to the day after.
        """
        for period in holiday_periods:
            if period['start'] <= order_date <= period['end']:
                return period['end'] + timedelta(days=1)
        return order_date
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestForwardPlanGenerator
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/forward_plan_generator.py \
        mml_roq_forecast/tests/test_forward_plan_generator.py
git commit -m "feat(roq): add 12-month forward procurement plan generator"
```

---

## Task 2: Proactive consolidation calendar (forward plan → shipment groups)

**Files:**
- Modify: `mml_roq_forecast/services/consolidation_engine.py`
- Create: `mml_roq_forecast/tests/test_proactive_consolidation.py`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_proactive_consolidation.py
from datetime import date
from odoo.tests.common import TransactionCase

class TestProactiveConsolidation(TransactionCase):

    def setUp(self):
        super().setUp()
        from ..services.consolidation_engine import ConsolidationEngine
        self.engine = ConsolidationEngine(self.env)
        self.run = self.env['roq.forecast.run'].create({'status': 'complete'})

        self.s1 = self.env['res.partner'].create({
            'name': 'Pro Sup 1', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.s2 = self.env['res.partner'].create({
            'name': 'Pro Sup 2', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })

        # Build forward plans for both suppliers, same month
        plan1 = self.env['roq.forward.plan'].create({
            'supplier_id': self.s1.id, 'generated_date': date.today(),
            'run_id': self.run.id,
        })
        plan2 = self.env['roq.forward.plan'].create({
            'supplier_id': self.s2.id, 'generated_date': date.today(),
            'run_id': self.run.id,
        })
        product = self.env['product.product'].create({'name': 'Pro SKU', 'type': 'product'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        month = date.today().replace(day=1)
        for plan, cbm in [(plan1, 10.0), (plan2, 12.0)]:
            self.env['roq.forward.plan.line'].create({
                'plan_id': plan.id, 'product_id': product.id,
                'warehouse_id': wh.id, 'month': month,
                'planned_order_qty': 100, 'cbm': cbm,
                'planned_ship_date': month,
            })

    def test_creates_proactive_shipment_group(self):
        self.engine.create_proactive_shipment_groups(self.run)
        groups = self.env['roq.shipment.group'].search([
            ('run_id', '=', self.run.id), ('mode', '=', 'proactive'),
        ])
        self.assertGreater(len(groups), 0)

    def test_proactive_group_has_two_supplier_lines(self):
        self.engine.create_proactive_shipment_groups(self.run)
        groups = self.env['roq.shipment.group'].search([
            ('run_id', '=', self.run.id), ('mode', '=', 'proactive'),
            ('fob_port', '=', 'CNSHA'),
        ])
        if groups:
            self.assertGreaterEqual(len(groups[0].line_ids), 2)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestProactiveConsolidation
```

**Step 3: Add proactive method to consolidation engine**

Add to `mml_roq_forecast/services/consolidation_engine.py`:

```python
    def create_proactive_shipment_groups(self, run):
        """
        Creates proactive shipment groups from roq.forward.plan records.
        Groups forward plan lines by FOB port and month.
        No POs exist yet — shipment_group_line.purchase_order_id = False.
        """
        plans = self.env['roq.forward.plan'].search([('run_id', '=', run.id)])
        if not plans:
            return self.env['roq.shipment.group']

        # Group plan lines by (fob_port, month)
        from collections import defaultdict
        by_port_month = defaultdict(list)

        for plan in plans:
            fob = plan.fob_port or plan.supplier_id.fob_port
            if not fob:
                continue
            for line in plan.line_ids:
                key = (fob, line.month)
                by_port_month[key].append((plan.supplier_id, line))

        created = self.env['roq.shipment.group']
        warehouses = self.env['stock.warehouse'].search([('is_active_for_roq', '=', True)])

        for (fob_port, month), supplier_lines in by_port_month.items():
            # Group by supplier within this month/port
            by_supplier = defaultdict(list)
            for supplier, line in supplier_lines:
                by_supplier[supplier.id].append((supplier, line))

            if not by_supplier:
                continue

            total_cbm = sum(line.cbm for _, line in supplier_lines)
            container_type = self._assign_container_type(total_cbm)

            sg = self.env['roq.shipment.group'].create({
                'fob_port': fob_port,
                'planned_ship_date': month,
                'container_type': container_type,
                'total_cbm': total_cbm,
                'fill_percentage': self._fill_pct(total_cbm, container_type),
                'status': 'draft',
                'mode': 'proactive',
                'run_id': run.id,
                'destination_warehouse_ids': [(6, 0, warehouses.ids)],
            })

            for sid, s_lines in by_supplier.items():
                supplier = s_lines[0][0]
                supplier_cbm = sum(line.cbm for _, line in s_lines)
                product_count = len(set(line.product_id.id for _, line in s_lines))

                self.env['roq.shipment.group.line'].create({
                    'group_id': sg.id,
                    'supplier_id': supplier.id,
                    'cbm': supplier_cbm,
                    'push_pull_days': 0,
                    'push_pull_reason': 'Proactive — no OOS data yet',
                    'oos_risk_flag': False,
                    'original_ship_date': month,
                    'product_count': product_count,
                })

            created |= sg

        return created
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestProactiveConsolidation
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/consolidation_engine.py \
        mml_roq_forecast/tests/test_proactive_consolidation.py
git commit -m "feat(roq): add proactive consolidation calendar from forward plan"
```

---

## Task 3: Supplier Order Schedule PDF report

**Files:**
- Create: `mml_roq_forecast/reports/supplier_order_schedule.xml`
- Create: `mml_roq_forecast/reports/supplier_order_schedule_template.xml`
- Modify: `mml_roq_forecast/__manifest__.py` (add report to data)

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_reports.py
from datetime import date
from odoo.tests.common import TransactionCase

class TestSupplierOrderScheduleReport(TransactionCase):

    def test_report_generates_without_error(self):
        supplier = self.env['res.partner'].create({
            'name': 'Report Supplier', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        run = self.env['roq.forecast.run'].create({'status': 'complete'})
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
            'run_id': run.id,
            'horizon_months': 3,
        })
        # Render the report — should not raise
        report = self.env.ref('mml_roq_forecast.action_report_supplier_order_schedule')
        html, _ = report._render_qweb_html([plan.id])
        self.assertTrue(html)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestSupplierOrderScheduleReport
```

**Step 3: Create report**

```xml
<!-- mml_roq_forecast/reports/supplier_order_schedule.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_report_supplier_order_schedule" model="ir.actions.report">
        <field name="name">Supplier Order Schedule</field>
        <field name="model">roq.forward.plan</field>
        <field name="report_type">qweb-pdf</field>
        <field name="report_name">mml_roq_forecast.report_supplier_order_schedule</field>
        <field name="report_file">mml_roq_forecast.report_supplier_order_schedule</field>
        <field name="binding_model_id" ref="model_roq_forward_plan"/>
        <field name="binding_type">report</field>
    </record>
</odoo>
```

```xml
<!-- mml_roq_forecast/reports/supplier_order_schedule_template.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <template id="report_supplier_order_schedule">
        <t t-call="web.html_container">
            <t t-foreach="docs" t-as="plan">
                <t t-call="web.external_layout">
                    <div class="page">
                        <h2>Supplier Order Schedule — 12 Month Forward Plan</h2>
                        <table class="table table-sm">
                            <tr>
                                <td><strong>Supplier:</strong></td>
                                <td><t t-esc="plan.supplier_id.name"/></td>
                                <td><strong>FOB Port:</strong></td>
                                <td><t t-esc="plan.fob_port or '—'"/></td>
                            </tr>
                            <tr>
                                <td><strong>Generated:</strong></td>
                                <td><t t-esc="plan.generated_date"/></td>
                                <td><strong>Horizon:</strong></td>
                                <td><t t-esc="plan.horizon_months"/> months</td>
                            </tr>
                            <tr>
                                <td><strong>Total Units:</strong></td>
                                <td><t t-esc="int(plan.total_units)"/></td>
                                <td><strong>Total CBM:</strong></td>
                                <td><t t-esc="'%.2f' % plan.total_cbm"/></td>
                            </tr>
                            <tr>
                                <td><strong>Total FOB Cost:</strong></td>
                                <td colspan="3">
                                    $<t t-esc="'%.2f' % plan.total_fob_cost"/>
                                </td>
                            </tr>
                        </table>

                        <h3>Order Schedule</h3>
                        <table class="table table-bordered table-sm">
                            <thead>
                                <tr>
                                    <th>Month</th>
                                    <th>SKU Code</th>
                                    <th>Product Name</th>
                                    <th>Warehouse</th>
                                    <th>Order Qty</th>
                                    <th>Order Date</th>
                                    <th>Ship Date</th>
                                    <th>CBM</th>
                                    <th>FOB Cost</th>
                                    <th>Notes</th>
                                </tr>
                            </thead>
                            <tbody>
                                <t t-foreach="plan.line_ids.sorted(key=lambda l: (l.month, l.product_id.default_code or ''))" t-as="line">
                                    <tr>
                                        <td><t t-esc="line.month.strftime('%b %Y')"/></td>
                                        <td><t t-esc="line.product_id.default_code or '—'"/></td>
                                        <td><t t-esc="line.product_id.name"/></td>
                                        <td><t t-esc="line.warehouse_id.name"/></td>
                                        <td class="text-right"><t t-esc="int(line.planned_order_qty)"/></td>
                                        <td><t t-esc="line.planned_order_date"/></td>
                                        <td><t t-esc="line.planned_ship_date"/></td>
                                        <td class="text-right"><t t-esc="'%.2f' % line.cbm"/></td>
                                        <td class="text-right">$<t t-esc="'%.2f' % line.fob_line_cost"/></td>
                                        <td><t t-esc="line.consolidation_note or ''"/></td>
                                    </tr>
                                </t>
                            </tbody>
                        </table>

                        <p class="text-muted" style="margin-top: 20px; font-size: 10px;">
                            This schedule is based on demand forecasts and is subject to change.
                            Lead time assumption: <t t-esc="plan.supplier_id.supplier_lead_time_days or 100"/> days.
                            Generated by MML ROQ Forecast System.
                        </p>
                    </div>
                </t>
            </t>
        </t>
    </template>
</odoo>
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestSupplierOrderScheduleReport
```
Expected: PASS

**Step 5: Manual verification**

Open a `roq.forward.plan` record → Print → "Supplier Order Schedule" → verify PDF renders.

**Step 6: Commit**

```bash
git add mml_roq_forecast/reports/
git commit -m "feat(roq): add supplier order schedule PDF report"
```

---

## Task 4: Actual lead time feedback loop to supplier stats

**Files:**
- Modify: `mml_roq_forecast/models/res_partner_ext.py`
- Create: `mml_roq_forecast/tests/test_lead_time_feedback.py`

**Step 1: Write failing test**

```python
# mml_roq_forecast/tests/test_lead_time_feedback.py
from odoo.tests.common import TransactionCase

class TestLeadTimeFeedback(TransactionCase):

    def test_supplier_stats_update_from_delivered_bookings(self):
        supplier = self.env['res.partner'].create({
            'name': 'LT Feedback Supplier', 'supplier_rank': 1,
        })
        # Create two completed bookings with known actual lead times
        for days in [95, 105]:
            booking = self.env['freight.booking'].create({
                'carrier': 'Test Carrier',
                'atd': f'2026-01-01',
                'delivered_date': f'2026-01-{1+days:02d}',
                'status': 'delivered',
            })
            po = self.env['purchase.order'].create({
                'partner_id': supplier.id,
                'date_order': '2026-01-01',
            })
            booking.po_ids = [(4, po.id)]

        supplier._compute_lead_time_stats()
        self.assertAlmostEqual(supplier.avg_lead_time_actual, 100.0, delta=5)
        self.assertGreater(supplier.lead_time_std_dev, 0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestLeadTimeFeedback
```

**Step 3: Add computed stats to res.partner extension**

Add to `mml_roq_forecast/models/res_partner_ext.py`:

```python
    def _compute_lead_time_stats(self):
        """
        Recompute rolling lead time statistics from freight.booking records
        linked to this supplier's purchase orders.
        """
        import statistics
        for partner in self:
            if not partner.supplier_rank:
                partner.avg_lead_time_actual = 0
                partner.lead_time_std_dev = 0
                partner.lead_time_on_time_pct = 0
                continue

            # Find POs for this supplier
            po_ids = self.env['purchase.order'].search([
                ('partner_id', '=', partner.id),
            ]).ids

            if not po_ids:
                continue

            # Find delivered bookings linked to these POs
            bookings = self.env['freight.booking'].search([
                ('po_ids', 'in', po_ids),
                ('status', '=', 'delivered'),
                ('actual_lead_time_days', '>', 0),
            ])

            lead_times = bookings.mapped('actual_lead_time_days')
            if not lead_times:
                continue

            avg = sum(lead_times) / len(lead_times)
            std = statistics.stdev(lead_times) if len(lead_times) > 1 else 0.0

            # On-time = actual lead time within 10% of assumed (use supplier override or default)
            assumed_lt = partner.supplier_lead_time_days or int(
                self.env['ir.config_parameter'].sudo()
                .get_param('roq.default_lead_time_days', 100)
            )
            tolerance = assumed_lt * 0.1
            on_time = sum(1 for lt in lead_times if abs(lt - assumed_lt) <= tolerance)
            on_time_pct = (on_time / len(lead_times)) * 100

            partner.write({
                'avg_lead_time_actual': avg,
                'lead_time_std_dev': std,
                'lead_time_on_time_pct': on_time_pct,
            })
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast \
  --test-tags /mml_roq_forecast:TestLeadTimeFeedback
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/models/res_partner_ext.py \
        mml_roq_forecast/tests/test_lead_time_feedback.py
git commit -m "feat(roq): add actual lead time feedback loop to supplier stats"
```

---

## Sprint 4 Done Checklist

**Forward Plan:**
- [ ] `generate_for_supplier()` creates 12 months of lines for a sample supplier
- [ ] Monthly demand = weekly × 4.33 (verified manually)
- [ ] Order date = ship date − lead time days (verified manually)
- [ ] CNY holiday period: order falling in Jan 28 – Feb 10 pushed to Feb 11
- [ ] Supplier Order Schedule PDF renders with correct data
- [ ] PDF accessible from Print menu on `roq.forward.plan` form
- [ ] Proactive shipment groups created (mode = 'proactive', state = 'draft')

**Lead Time Feedback:**
- [ ] Supplier `avg_lead_time_actual` updated from delivered bookings
- [ ] `lead_time_std_dev` computed correctly (stdev of actual lead times)
- [ ] `lead_time_on_time_pct` uses 10% tolerance around assumed lead time
- [ ] Stats only computed for suppliers with freight.booking records in delivered state

**All tests passing:**
```bash
odoo-bin --test-enable -d dev \
  --test-tags mml_roq_forecast
```
