# Sprint 2: ROQ Engine — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Implement Layer 2 — the full ROQ calculation pipeline: (s,S) inventory policy, pack size rounding, container fitting (LCL/FCL with intelligent padding), the orchestration pipeline that ties Layers 1+2 together, weekly cron, alerts/flags, and the ROQ results UI.

**Architecture:** Service layer in `mml_roq_forecast/services/`. Pipeline is orchestrated by `roq.forecast.run` model's `action_run()` method. Services are stateless functions/classes. Results written to `roq.forecast.line`.

**Pre-condition:** Sprint 1 complete. ABCD, forecast, and safety stock services all pass tests.

---

## Task 1: SOH and inventory position query

**Files:**
- Create: `mml_roq_forecast/services/inventory_query.py`
- Create: `mml_roq_forecast/tests/test_inventory_query.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_inventory_query.py
from odoo.tests.common import TransactionCase
from ..services.inventory_query import InventoryQueryService

class TestInventoryQuery(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.product = self.env['product.product'].create({
            'name': 'Test INV SKU', 'type': 'product',
        })

    def test_soh_zero_for_new_product(self):
        svc = InventoryQueryService(self.env)
        soh = svc.get_soh(self.product, self.warehouse)
        self.assertEqual(soh, 0.0)

    def test_confirmed_po_qty_zero_for_no_pos(self):
        svc = InventoryQueryService(self.env)
        qty = svc.get_confirmed_po_qty(self.product, self.warehouse)
        self.assertEqual(qty, 0.0)

    def test_inventory_position_is_soh_plus_po(self):
        svc = InventoryQueryService(self.env)
        soh = svc.get_soh(self.product, self.warehouse)
        po_qty = svc.get_confirmed_po_qty(self.product, self.warehouse)
        pos = svc.get_inventory_position(self.product, self.warehouse)
        self.assertAlmostEqual(pos, soh + po_qty, places=3)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestInventoryQuery
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/inventory_query.py
"""
Queries current stock positions (SOH + confirmed inbound POs) per SKU per warehouse.

SOH source: stock.quant filtered to internal locations of the target warehouse.
Confirmed PO qty: purchase.order.line in 'purchase' state, destination = warehouse.
"""


class InventoryQueryService:

    def __init__(self, env):
        self.env = env

    def _get_internal_locations(self, warehouse):
        """All internal stock locations belonging to this warehouse."""
        return self.env['stock.location'].search([
            ('warehouse_id', '=', warehouse.id),
            ('usage', '=', 'internal'),
        ])

    def get_soh(self, product, warehouse):
        """
        Stock on hand for product at warehouse (internal locations only).
        Returns float.
        """
        locations = self._get_internal_locations(warehouse)
        if not locations:
            return 0.0
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'in', locations.ids),
        ])
        return sum(quants.mapped('quantity'))

    def get_confirmed_po_qty(self, product, warehouse):
        """
        Quantity on confirmed (not yet received) purchase orders destined for this warehouse.
        Only counts PO lines in 'purchase' or 'done' state where qty remaining > 0.
        """
        # Find the warehouse's input/stock location
        dest_locations = self._get_internal_locations(warehouse)
        if not dest_locations:
            return 0.0

        po_lines = self.env['purchase.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.state', 'in', ['purchase', 'done']),
            ('order_id.dest_address_id', '=', False),  # Standard warehouse delivery
        ])

        # Filter to lines delivering to this warehouse via picking destination
        # Note: match by order.picking_type_id.warehouse_id
        total = 0.0
        for line in po_lines:
            order_warehouse = line.order_id.picking_type_id.warehouse_id
            if order_warehouse.id == warehouse.id:
                # qty remaining to receive
                received = line.qty_received or 0.0
                ordered = line.product_qty or 0.0
                total += max(0.0, ordered - received)

        return total

    def get_inventory_position(self, product, warehouse):
        """SOH + confirmed inbound PO qty."""
        return self.get_soh(product, warehouse) + self.get_confirmed_po_qty(product, warehouse)
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestInventoryQuery
```
Expected: PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/inventory_query.py \
        mml_roq_forecast/tests/test_inventory_query.py
git commit -m "feat(roq): add inventory position query service (SOH + confirmed PO)"
```

---

## Task 2: ROQ calculator — (s,S) inventory policy

**Files:**
- Create: `mml_roq_forecast/services/roq_calculator.py`
- Create: `mml_roq_forecast/tests/test_roq_calculator.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_roq_calculator.py
from odoo.tests.common import TransactionCase
from ..services.roq_calculator import (
    calculate_out_level, calculate_order_up_to,
    calculate_roq_raw, calculate_projected_inventory,
    round_to_pack_size, calculate_weeks_of_cover,
)

class TestRoqCalculator(TransactionCase):

    def test_out_level_formula(self):
        # s = demand × LT_weeks + safety_stock
        result = calculate_out_level(
            weekly_demand=10.0, lt_weeks=14.28, safety_stock=20.0,
        )
        self.assertAlmostEqual(result, 10.0 * 14.28 + 20.0, places=2)

    def test_order_up_to_formula(self):
        # S = demand × (LT_weeks + review_weeks) + safety_stock
        result = calculate_order_up_to(
            weekly_demand=10.0, lt_weeks=14.28, review_weeks=4.28, safety_stock=20.0,
        )
        self.assertAlmostEqual(result, 10.0 * (14.28 + 4.28) + 20.0, places=2)

    def test_roq_raw_is_zero_when_stock_sufficient(self):
        # inventory_position >= S → no order needed
        result = calculate_roq_raw(
            order_up_to=100.0, inventory_position=150.0,
        )
        self.assertEqual(result, 0.0)

    def test_roq_raw_correct_when_stock_low(self):
        result = calculate_roq_raw(
            order_up_to=100.0, inventory_position=40.0,
        )
        self.assertEqual(result, 60.0)

    def test_pack_size_rounding_rounds_up(self):
        # ROQ of 55 with pack size 12 → ceil(55/12) × 12 = 60
        result = round_to_pack_size(roq=55.0, pack_size=12)
        self.assertEqual(result, 60)

    def test_pack_size_rounding_exact_multiple_unchanged(self):
        result = round_to_pack_size(roq=60.0, pack_size=12)
        self.assertEqual(result, 60)

    def test_pack_size_rounding_zero_returns_zero(self):
        result = round_to_pack_size(roq=0.0, pack_size=12)
        self.assertEqual(result, 0)

    def test_projected_inventory_at_delivery(self):
        # inv_position − demand × LT_weeks
        result = calculate_projected_inventory(
            inventory_position=100.0, weekly_demand=10.0, lt_weeks=14.28,
        )
        self.assertAlmostEqual(result, 100.0 - 10.0 * 14.28, places=2)

    def test_negative_projected_inventory_is_oos_signal(self):
        result = calculate_projected_inventory(
            inventory_position=50.0, weekly_demand=10.0, lt_weeks=14.28,
        )
        self.assertLess(result, 0.0)  # OOS signal

    def test_weeks_of_cover_calculation(self):
        result = calculate_weeks_of_cover(projected_inventory=100.0, weekly_demand=10.0)
        self.assertAlmostEqual(result, 10.0, places=2)

    def test_weeks_of_cover_zero_demand_returns_999(self):
        # Avoid division by zero; return sentinel value
        result = calculate_weeks_of_cover(projected_inventory=100.0, weekly_demand=0.0)
        self.assertEqual(result, 999.0)
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestRoqCalculator
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/roq_calculator.py
"""
(s,S) Periodic Review Inventory Policy — ROQ Calculation.

Reorder Point (s) = Out Level = demand × LT_weeks + safety_stock
Order-Up-To (S)   = demand × (LT_weeks + review_weeks) + safety_stock
ROQ (Raw)          = max(0, S − inventory_position)
"""
import math


def calculate_out_level(weekly_demand, lt_weeks, safety_stock):
    """s = demand × LT_weeks + safety_stock"""
    return weekly_demand * lt_weeks + safety_stock


def calculate_order_up_to(weekly_demand, lt_weeks, review_weeks, safety_stock):
    """S = demand × (LT_weeks + review_weeks) + safety_stock"""
    return weekly_demand * (lt_weeks + review_weeks) + safety_stock


def calculate_roq_raw(order_up_to, inventory_position):
    """ROQ = max(0, S − inventory_position)"""
    return max(0.0, order_up_to - inventory_position)


def round_to_pack_size(roq, pack_size):
    """Round ROQ up to nearest multiple of pack_size. Zero stays zero."""
    if roq <= 0:
        return 0
    pack_size = max(1, int(pack_size))
    return math.ceil(roq / pack_size) * pack_size


def calculate_projected_inventory(inventory_position, weekly_demand, lt_weeks):
    """
    Projected inventory at time of delivery.
    Negative = real OOS risk (not just safety stock breach).
    """
    return inventory_position - (weekly_demand * lt_weeks)


def calculate_weeks_of_cover(projected_inventory, weekly_demand):
    """
    Weeks of cover at time of delivery.
    Returns 999.0 sentinel if weekly_demand is 0 (prevent division by zero).
    """
    if weekly_demand <= 0:
        return 999.0
    return projected_inventory / weekly_demand
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestRoqCalculator
```
Expected: All 10 tests PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/roq_calculator.py \
        mml_roq_forecast/tests/test_roq_calculator.py
git commit -m "feat(roq): add ROQ (s,S) inventory policy calculator"
```

---

## Task 3: Container fitting algorithm

**Files:**
- Create: `mml_roq_forecast/services/container_fitter.py`
- Create: `mml_roq_forecast/tests/test_container_fitter.py`

**Step 1: Write failing tests**

```python
# mml_roq_forecast/tests/test_container_fitter.py
from odoo.tests.common import TransactionCase
from ..services.container_fitter import ContainerFitter, CONTAINER_SPECS

class TestContainerFitter(TransactionCase):

    def setUp(self):
        super().setUp()
        self.fitter = ContainerFitter(lcl_threshold_pct=50, max_padding_weeks_cover=26)

    def test_lcl_recommended_below_threshold(self):
        # 10 CBM total — well below 50% of any container
        lines = [{'cbm': 10.0, 'roq': 100, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], 'LCL')

    def test_fcl_recommended_above_threshold(self):
        # 15 CBM — above 50% of 20GP (25 CBM)
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertIn(result['container_type'], ['20GP', '40GP', '40HQ'])

    def test_selects_smallest_feasible_container(self):
        # 15 CBM → should choose 20GP (25 CBM) not 40GP
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], '20GP')

    def test_padding_added_for_remaining_capacity(self):
        # 15 CBM in 25 CBM container → 10 CBM padding available
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertGreater(result['total_padding_units'], 0)

    def test_no_padding_when_sku_over_max_cover(self):
        # SKU already has 30 weeks cover — should not receive padding (max=26)
        lines = [{'cbm': 15.0, 'roq': 150, 'cbm_per_unit': 0.1, 'tier': 'B',
                  'weeks_cover': 30.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        # Padding goes to other SKUs — since only 1 SKU here and it's over max, no padding
        self.assertEqual(result['line_results'][0]['padding_units'], 0)

    def test_unassigned_when_cbm_per_unit_missing(self):
        lines = [{'cbm': 0.0, 'roq': 100, 'cbm_per_unit': 0.0, 'tier': 'A',
                  'weeks_cover': 8.0, 'product_id': 1}]
        result = self.fitter.fit(lines)
        self.assertEqual(result['container_type'], 'unassigned')
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestContainerFitter
```

**Step 3: Implement**

```python
# mml_roq_forecast/services/container_fitter.py
"""
Container Fitting Algorithm.

For a given supplier's aggregated ROQ lines:
1. Calculate total CBM.
2. Assign smallest feasible container at >= lcl_threshold_pct utilisation.
3. If below threshold → LCL.
4. If FCL → pad remaining capacity, prioritising A-tier and lowest weeks cover.
5. Exclude SKUs already over max_padding_weeks_cover from padding.

Container capacities (usable CBM):
  20GP:  25.0 CBM
  40GP:  55.0 CBM
  40HQ:  67.5 CBM

Per spec: a single container ships to port; domestic split handled separately.
"""

CONTAINER_SPECS = {
    '20GP': 25.0,
    '40GP': 55.0,
    '40HQ': 67.5,
}

CONTAINER_ORDER = ['20GP', '40GP', '40HQ']  # Smallest first


class ContainerFitter:

    def __init__(self, lcl_threshold_pct=50, max_padding_weeks_cover=26):
        self.lcl_threshold_pct = lcl_threshold_pct / 100.0
        self.max_padding_weeks_cover = max_padding_weeks_cover

    def fit(self, lines):
        """
        lines: list of dicts, each with:
          - product_id: int
          - cbm: float (total CBM for this SKU's ROQ)
          - roq: float (pack-size-rounded ROQ)
          - cbm_per_unit: float
          - tier: str ('A','B','C','D')
          - weeks_cover: float (projected weeks of cover at delivery)

        Returns dict:
          - container_type: str
          - container_cbm: float
          - fill_pct: float
          - total_padding_units: int
          - line_results: list of {product_id, roq_containerized, padding_units}
        """
        # Check for missing CBM data
        if any(line['cbm_per_unit'] <= 0 for line in lines):
            return {
                'container_type': 'unassigned',
                'container_cbm': 0.0,
                'fill_pct': 0.0,
                'total_padding_units': 0,
                'line_results': [
                    {'product_id': l['product_id'], 'roq_containerized': l['roq'], 'padding_units': 0}
                    for l in lines
                ],
            }

        total_cbm = sum(line['cbm'] for line in lines)

        if total_cbm <= 0:
            return self._lcl_result(lines, total_cbm)

        # Find smallest feasible container
        chosen_type = None
        chosen_cbm = None
        for ctype in CONTAINER_ORDER:
            cap = CONTAINER_SPECS[ctype]
            if total_cbm <= cap:
                if total_cbm / cap >= self.lcl_threshold_pct:
                    chosen_type = ctype
                    chosen_cbm = cap
                break
        else:
            # Exceeds 40HQ — use largest container (multiple containers not yet supported)
            chosen_type = '40HQ'
            chosen_cbm = CONTAINER_SPECS['40HQ']

        if chosen_type is None:
            return self._lcl_result(lines, total_cbm)

        # Calculate remaining capacity for padding
        remaining_cbm = chosen_cbm - total_cbm
        fill_pct = (total_cbm / chosen_cbm) * 100.0

        # Allocate padding
        padding_eligible = [
            l for l in lines
            if l['weeks_cover'] < self.max_padding_weeks_cover and l['tier'] != 'D'
        ]
        # Sort: A-tier first, then lowest weeks cover
        tier_rank = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
        padding_eligible.sort(
            key=lambda l: (-tier_rank.get(l['tier'], 0), l['weeks_cover'])
        )

        padding_by_product = {l['product_id']: 0 for l in lines}

        for line in padding_eligible:
            if remaining_cbm <= 0:
                break
            if line['cbm_per_unit'] <= 0:
                continue
            # Pack-size-aligned padding
            pack_size = max(1, int(line.get('pack_size', 1)))
            max_padding_units = int(remaining_cbm / line['cbm_per_unit'])
            max_padding_units = (max_padding_units // pack_size) * pack_size
            if max_padding_units > 0:
                padding_by_product[line['product_id']] = max_padding_units
                remaining_cbm -= max_padding_units * line['cbm_per_unit']

        line_results = []
        total_padding = 0
        for line in lines:
            pad = padding_by_product.get(line['product_id'], 0)
            total_padding += pad
            line_results.append({
                'product_id': line['product_id'],
                'roq_containerized': line['roq'] + pad,
                'padding_units': pad,
            })

        return {
            'container_type': chosen_type,
            'container_cbm': chosen_cbm,
            'fill_pct': fill_pct,
            'total_padding_units': total_padding,
            'line_results': line_results,
        }

    def _lcl_result(self, lines, total_cbm):
        return {
            'container_type': 'LCL',
            'container_cbm': 0.0,
            'fill_pct': 0.0,
            'total_padding_units': 0,
            'line_results': [
                {'product_id': l['product_id'], 'roq_containerized': l['roq'], 'padding_units': 0}
                for l in lines
            ],
        }
```

**Step 4: Run tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestContainerFitter
```
Expected: All tests PASS

**Step 5: Commit**

```bash
git add mml_roq_forecast/services/container_fitter.py \
        mml_roq_forecast/tests/test_container_fitter.py
git commit -m "feat(roq): add container fitting algorithm with LCL/FCL and A-tier padding"
```

---

## Task 4: ROQ pipeline orchestration

**Files:**
- Modify: `mml_roq_forecast/models/roq_forecast_run.py`
- Create: `mml_roq_forecast/services/roq_pipeline.py`
- Create: `mml_roq_forecast/tests/test_roq_pipeline.py`

**Step 1: Write failing integration test**

```python
# mml_roq_forecast/tests/test_roq_pipeline.py
from odoo.tests.common import TransactionCase

class TestRoqPipeline(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.warehouse.is_active_for_roq = True
        supplier = self.env['res.partner'].create({
            'name': 'Pipeline Supplier', 'supplier_rank': 1,
            'fob_port': 'CNSHA',
        })
        self.product_tmpl = self.env['product.template'].create({
            'name': 'Pipeline Test SKU',
            'type': 'product',
            'is_roq_managed': True,
            'cbm_per_unit': 0.05,
            'pack_size': 6,
        })
        product = self.product_tmpl.product_variant_ids[0]
        self.env['product.supplierinfo'].create({
            'partner_id': supplier.id,
            'product_tmpl_id': self.product_tmpl.id,
            'price': 10.0,
        })

    def test_pipeline_creates_forecast_run(self):
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        self.assertEqual(run.status, 'complete')

    def test_pipeline_creates_forecast_lines(self):
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        self.assertGreater(len(run.line_ids), 0)

    def test_dormant_sku_has_zero_roq(self):
        # Product with no sales history → Tier D → ROQ = 0
        run = self.env['roq.forecast.run'].create({})
        run.action_run()
        lines = run.line_ids.filtered(
            lambda l: l.product_id.product_tmpl_id == self.product_tmpl
        )
        for line in lines:
            self.assertEqual(line.roq_raw, 0.0)  # No history = Tier D
```

**Step 2: Run to verify fails**

```bash
odoo-bin --test-enable -d dev --test-tags /mml_roq_forecast:TestRoqPipeline
```

**Step 3: Implement pipeline service**

```python
# mml_roq_forecast/services/roq_pipeline.py
"""
ROQ Pipeline Orchestrator.

Step order (per spec §2.2):
1. ABCD Classification
2. Demand Forecast per SKU per warehouse
3. Safety Stock per SKU per warehouse
4. ROQ Calculation per SKU per warehouse
5. Pack Size Rounding
6. Aggregate by Supplier
7. Container Fitting
8. Write results to roq.forecast.line

Called by roq.forecast.run.action_run()
"""
from .abc_classifier import AbcClassifier
from .demand_history import DemandHistoryService
from .forecast_methods import (
    forecast_sma, forecast_ewma, forecast_holt_winters,
    select_forecast_method, demand_std_dev,
)
from .safety_stock import calculate_safety_stock, get_z_score
from .roq_calculator import (
    calculate_out_level, calculate_order_up_to,
    calculate_roq_raw, round_to_pack_size,
    calculate_projected_inventory, calculate_weeks_of_cover,
)
from .container_fitter import ContainerFitter
from .inventory_query import InventoryQueryService
from .settings_helper import SettingsHelper


class RoqPipeline:

    def __init__(self, env):
        self.env = env
        self.settings = SettingsHelper(env)
        self.abc = AbcClassifier(env)
        self.dh = DemandHistoryService(env)
        self.inv = InventoryQueryService(env)

    def run(self, forecast_run):
        """
        Execute full ROQ pipeline.
        forecast_run: roq.forecast.run record
        Writes results to roq.forecast.line.
        """
        forecast_run.write({'status': 'running'})

        try:
            # Step 1: ABCD Classification
            self.abc.classify_all_products(forecast_run)

            # Step 2-5: Per-SKU per-warehouse forecast + ROQ
            line_vals = self._compute_all_lines(forecast_run)

            # Step 6-7: Aggregate by supplier + container fit
            line_vals = self._apply_container_fitting(line_vals)

            # Write results
            self.env['roq.forecast.line'].create(line_vals)

            # Update run summary
            skus_with_roq = sum(1 for v in line_vals if v.get('roq_containerized', 0) > 0)
            skus_oos = sum(1 for v in line_vals if v.get('projected_inventory_at_delivery', 0) < 0)
            forecast_run.write({
                'status': 'complete',
                'total_skus_processed': len(set(v['product_id'] for v in line_vals)),
                'total_skus_reorder': skus_with_roq,
                'total_skus_oos_risk': skus_oos,
            })

        except Exception as e:
            forecast_run.write({
                'status': 'error',
                'notes': str(e),
            })
            raise

    def _compute_all_lines(self, forecast_run):
        """Compute per-SKU per-warehouse ROQ lines (steps 2-5)."""
        products = self.env['product.template'].search([
            ('is_roq_managed', '=', True),
            ('type', 'in', ['product', 'consu']),
        ])
        warehouses = self.env['stock.warehouse'].search([
            ('is_active_for_roq', '=', True),
        ])

        lookback = self.settings.get_lookback_weeks()
        sma_window = self.settings.get_sma_window_weeks()
        min_n = self.settings.get_min_n_value()
        lcl_threshold = int(
            self.env['ir.config_parameter'].sudo()
            .get_param('roq.container_lcl_threshold_pct', 50)
        )

        line_vals = []

        for pt in products:
            product = pt.product_variant_ids[:1]
            if not product:
                continue

            tier = pt.abc_tier or 'D'
            if tier == 'D':
                # Dormant: write zero-ROQ line for each warehouse and move on
                for wh in warehouses:
                    line_vals.append(self._dormant_line(forecast_run, product, wh, pt))
                continue

            # Get primary supplier
            supplier_info = self.env['product.supplierinfo'].search([
                ('product_tmpl_id', '=', pt.id),
            ], order='sequence asc, id asc', limit=1)
            supplier = supplier_info.partner_id if supplier_info else self.env['res.partner']

            lt_days = self.settings.get_lead_time_days(supplier)
            review_days = self.settings.get_review_interval_days(supplier)
            lt_weeks = lt_days / 7.0
            review_weeks = review_days / 7.0

            z_score = get_z_score(tier)

            for wh in warehouses:
                history = self.dh.get_weekly_demand(product, wh, lookback_weeks=lookback)
                method, confidence = select_forecast_method(history, min_n=min_n)

                if method == 'sma':
                    fwd = forecast_sma(history, window=sma_window)
                elif method == 'ewma':
                    fwd = forecast_ewma(history, span=26)
                else:
                    fwd = forecast_holt_winters(history)

                sigma, is_fallback = demand_std_dev(history, min_n=min_n)
                ss = calculate_safety_stock(z_score, sigma, lt_weeks)

                inv_pos = self.inv.get_inventory_position(product, wh)
                soh = self.inv.get_soh(product, wh)
                po_qty = self.inv.get_confirmed_po_qty(product, wh)

                out_level = calculate_out_level(fwd, lt_weeks, ss)
                order_up_to = calculate_order_up_to(fwd, lt_weeks, review_weeks, ss)
                roq_raw = calculate_roq_raw(order_up_to, inv_pos)
                roq_packed = round_to_pack_size(roq_raw, pt.pack_size or 1)
                proj_inv = calculate_projected_inventory(inv_pos, fwd, lt_weeks)
                weeks_cover = calculate_weeks_of_cover(proj_inv, fwd)
                cbm_total = roq_packed * (pt.cbm_per_unit or 0.0)

                # Build notes/flags
                notes = self._build_notes(
                    proj_inv, ss, weeks_cover, pt.cbm_per_unit, pt.pack_size,
                )

                line_vals.append({
                    'run_id': forecast_run.id,
                    'product_id': product.id,
                    'warehouse_id': wh.id,
                    'supplier_id': supplier.id if supplier else False,
                    'abc_tier': tier,
                    'trailing_12m_revenue': pt.abc_trailing_revenue,
                    'cumulative_revenue_pct': pt.abc_cumulative_pct,
                    'soh': soh,
                    'confirmed_po_qty': po_qty,
                    'inventory_position': inv_pos,
                    'forecasted_weekly_demand': fwd,
                    'forecast_method': method,
                    'forecast_confidence': 'low' if is_fallback else confidence,
                    'demand_std_dev': sigma,
                    'safety_stock': ss,
                    'z_score': z_score,
                    'lead_time_days': lt_days,
                    'review_interval_days': review_days,
                    'out_level': out_level,
                    'order_up_to': order_up_to,
                    'roq_raw': roq_raw,
                    'roq_pack_rounded': roq_packed,
                    'roq_containerized': roq_packed,  # Updated in container fitting step
                    'cbm_per_unit': pt.cbm_per_unit or 0.0,
                    'cbm_total': cbm_total,
                    'pack_size': pt.pack_size or 1,
                    'projected_inventory_at_delivery': proj_inv,
                    'weeks_of_cover_at_delivery': weeks_cover,
                    'container_type': 'unassigned' if not pt.cbm_per_unit else False,
                    'notes': notes,
                    # Carry for container fitting step
                    '_tier_str': tier,
                    '_weeks_cover': weeks_cover,
                })

        return line_vals

    def _apply_container_fitting(self, line_vals):
        """
        Step 6-7: Group lines by supplier, run container fitting,
        update roq_containerized, container_type, fill_pct, padding_units.
        """
        lcl_threshold = int(
            self.env['ir.config_parameter'].sudo()
            .get_param('roq.container_lcl_threshold_pct', 50)
        )
        max_padding = int(
            self.env['ir.config_parameter'].sudo()
            .get_param('roq.max_padding_weeks_cover', 26)
        )
        fitter = ContainerFitter(lcl_threshold, max_padding)

        # Group by supplier_id
        from collections import defaultdict
        by_supplier = defaultdict(list)
        for i, val in enumerate(line_vals):
            sid = val.get('supplier_id') or 0
            by_supplier[sid].append((i, val))

        for sid, indexed_vals in by_supplier.items():
            # Skip dormant lines
            active = [(i, v) for i, v in indexed_vals if v.get('roq_pack_rounded', 0) > 0]
            if not active:
                continue

            fit_input = [{
                'product_id': v['product_id'],
                'cbm': v['cbm_total'],
                'roq': v['roq_pack_rounded'],
                'cbm_per_unit': v['cbm_per_unit'],
                'tier': v.get('_tier_str', 'C'),
                'weeks_cover': v.get('_weeks_cover', 999.0),
                'pack_size': v.get('pack_size', 1),
            } for _, v in active]

            fit_result = fitter.fit(fit_input)

            # Map results back by product_id (first match per product in this supplier group)
            result_by_pid = {r['product_id']: r for r in fit_result['line_results']}

            for idx, val in active:
                pid = val['product_id']
                if pid in result_by_pid:
                    r = result_by_pid[pid]
                    line_vals[idx].update({
                        'roq_containerized': r['roq_containerized'],
                        'padding_units': r['padding_units'],
                        'container_type': fit_result['container_type'],
                        'container_fill_pct': fit_result['fill_pct'],
                    })

        # Remove internal carry fields before writing
        for val in line_vals:
            val.pop('_tier_str', None)
            val.pop('_weeks_cover', None)

        return line_vals

    def _dormant_line(self, run, product, warehouse, product_tmpl):
        return {
            'run_id': run.id,
            'product_id': product.id,
            'warehouse_id': warehouse.id,
            'abc_tier': 'D',
            'soh': self.inv.get_soh(product, warehouse),
            'confirmed_po_qty': 0.0,
            'inventory_position': self.inv.get_soh(product, warehouse),
            'forecasted_weekly_demand': 0.0,
            'forecast_method': 'sma',
            'forecast_confidence': 'low',
            'safety_stock': 0.0,
            'roq_raw': 0.0,
            'roq_pack_rounded': 0.0,
            'roq_containerized': 0.0,
            'notes': 'Tier D (Dormant): no sales in trailing 12 months',
        }

    def _build_notes(self, proj_inv, safety_stock, weeks_cover, cbm_per_unit, pack_size):
        flags = []
        if proj_inv < 0:
            flags.append('REAL OOS RISK')
        elif proj_inv < safety_stock:
            flags.append('Safety Stock Breach')
        if weeks_cover > 52:
            flags.append('Overstock Warning (>52wks)')
        if not cbm_per_unit:
            flags.append('Missing CBM/unit')
        if not pack_size:
            flags.append('Missing Pack Size')
        return ' | '.join(flags) if flags else ''
```

**Step 4: Wire pipeline into `roq.forecast.run` model**

Add to `mml_roq_forecast/models/roq_forecast_run.py`:

```python
    # Add these imports at top of file:
    # from odoo import models, fields, api

    @api.model
    def cron_run_weekly_roq(self):
        """Called by ir.cron weekly trigger."""
        run = self.create({})
        run.action_run()

    def action_run(self):
        """User-triggered or cron-triggered ROQ run."""
        self.ensure_one()
        from ..services.roq_pipeline import RoqPipeline
        # Snapshot current settings on the run header
        get = self.env['ir.config_parameter'].sudo().get_param
        self.write({
            'lookback_weeks': int(get('roq.lookback_weeks', 156)),
            'sma_window_weeks': int(get('roq.sma_window_weeks', 52)),
            'default_lead_time_days': int(get('roq.default_lead_time_days', 100)),
            'default_review_interval_days': int(get('roq.default_review_interval_days', 30)),
            'default_service_level': float(get('roq.default_service_level', 0.97)),
        })
        pipeline = RoqPipeline(self.env)
        pipeline.run(self)
```

**Step 5: Run pipeline tests**

```bash
odoo-bin --test-enable -d dev -u mml_roq_forecast --test-tags /mml_roq_forecast:TestRoqPipeline
```
Expected: PASS

**Step 6: Commit**

```bash
git add mml_roq_forecast/services/roq_pipeline.py \
        mml_roq_forecast/models/roq_forecast_run.py \
        mml_roq_forecast/tests/test_roq_pipeline.py
git commit -m "feat(roq): add full ROQ pipeline orchestrator with container fitting"
```

---

## Task 5: ROQ results UI — tree view with alert coloring

**Files:**
- Create: `mml_roq_forecast/views/roq_forecast_run_views.xml`
- Create: `mml_roq_forecast/views/roq_forecast_line_views.xml`
- Create: `mml_roq_forecast/views/menus.xml`
- Create: `mml_roq_forecast/security/ir.model.access.csv`

**Step 1: No failing test needed for pure views — manual verification**

```bash
# Verify all views render without XML errors:
odoo-bin -d dev -u mml_roq_forecast --stop-after-init
```

**Step 2: Create views**

```xml
<!-- mml_roq_forecast/views/roq_forecast_run_views.xml -->
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_roq_forecast_run_tree" model="ir.ui.view">
        <field name="name">roq.forecast.run.tree</field>
        <field name="model">roq.forecast.run</field>
        <field name="arch" type="xml">
            <tree>
                <field name="name"/>
                <field name="run_date"/>
                <field name="status" widget="badge"
                       decoration-success="status == 'complete'"
                       decoration-warning="status == 'running'"
                       decoration-danger="status == 'error'"/>
                <field name="total_skus_processed"/>
                <field name="total_skus_reorder"/>
                <field name="total_skus_oos_risk"
                       decoration-danger="total_skus_oos_risk &gt; 0"/>
            </tree>
        </field>
    </record>

    <record id="view_roq_forecast_run_form" model="ir.ui.view">
        <field name="name">roq.forecast.run.form</field>
        <field name="model">roq.forecast.run</field>
        <field name="arch" type="xml">
            <form>
                <header>
                    <button name="action_run" string="Run Now"
                            type="object" class="btn-primary"
                            attrs="{'invisible': [('status', 'in', ['running', 'complete'])]}"/>
                    <field name="status" widget="statusbar"
                           statusbar_visible="draft,running,complete"/>
                </header>
                <sheet>
                    <div class="oe_title">
                        <h1><field name="name"/></h1>
                    </div>
                    <group>
                        <group string="Run Info">
                            <field name="run_date"/>
                            <field name="total_skus_processed"/>
                            <field name="total_skus_reorder"/>
                            <field name="total_skus_oos_risk"/>
                        </group>
                        <group string="Parameters (Snapshot)">
                            <field name="lookback_weeks"/>
                            <field name="sma_window_weeks"/>
                            <field name="default_lead_time_days"/>
                            <field name="default_review_interval_days"/>
                        </group>
                    </group>
                    <notebook>
                        <page string="Results">
                            <field name="line_ids">
                                <tree decoration-danger="notes != False and 'REAL OOS' in notes"
                                      decoration-warning="notes != False and 'Safety Stock' in notes"
                                      decoration-muted="abc_tier == 'D'">
                                    <field name="supplier_id"/>
                                    <field name="product_id"/>
                                    <field name="warehouse_id"/>
                                    <field name="abc_tier" widget="badge"/>
                                    <field name="soh"/>
                                    <field name="forecasted_weekly_demand"/>
                                    <field name="forecast_method"/>
                                    <field name="safety_stock"/>
                                    <field name="inventory_position"/>
                                    <field name="roq_pack_rounded"/>
                                    <field name="roq_containerized"/>
                                    <field name="container_type"/>
                                    <field name="container_fill_pct"/>
                                    <field name="projected_inventory_at_delivery"/>
                                    <field name="weeks_of_cover_at_delivery"/>
                                    <field name="notes"/>
                                </tree>
                            </field>
                        </page>
                        <page string="Run Log">
                            <field name="notes" widget="text"/>
                        </page>
                    </notebook>
                </sheet>
            </form>
        </field>
    </record>

    <record id="action_roq_forecast_run" model="ir.actions.act_window">
        <field name="name">ROQ Runs</field>
        <field name="res_model">roq.forecast.run</field>
        <field name="view_mode">tree,form</field>
    </record>
</odoo>
```

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
access_roq_abc_history_user,roq.abc.history user,model_roq_abc_history,base.group_user,1,0,0,0
```

**Step 3: Install and verify**

```bash
odoo-bin -d dev -u mml_roq_forecast --stop-after-init
```
Open browser. Confirm:
- "MML Operations > ROQ Forecast" menu exists
- ROQ Runs list shows with status colors
- Form view shows Results tab with color-coded OOS/SS flags

**Step 4: Commit**

```bash
git add mml_roq_forecast/views/ mml_roq_forecast/security/
git commit -m "feat(roq): add ROQ run and results UI with alert color coding"
```

---

## Sprint 2 Done Checklist

- [ ] All tests pass: `odoo-bin --test-enable -d dev --test-tags mml_roq_forecast`
- [ ] Manual ROQ run via UI button works without error
- [ ] **Validation:** Run against same input data as current spreadsheet; compare line-by-line (demand, safety stock, out level, ROQ, container type) — within rounding tolerance
- [ ] Container fitting: 15 CBM supplier → 20GP with padding (not 40GP)
- [ ] Container fitting: 10 CBM supplier → LCL
- [ ] Dormant (Tier D) SKUs: ROQ = 0 in all lines
- [ ] OOS risk flag (red row) appears when projected inventory < 0
- [ ] Safety stock breach (amber) appears when projected inventory < safety stock
- [ ] Overstock warning appears when weeks cover > 52
- [ ] Missing CBM/pack size flag appears when product data incomplete
- [ ] Weekly cron set to `active=False` — Harold to enable manually after validation
