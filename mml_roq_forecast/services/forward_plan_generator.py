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
import math
from collections import defaultdict
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
        by_product = defaultdict(list)
        for line in lines:
            by_product[line.product_id.id].append(line)

        plan_line_vals = []
        for product_id, prod_lines in by_product.items():
            product = prod_lines[0].product_id

            # Sum weekly demand across warehouses
            total_weekly_demand = sum(l.forecasted_weekly_demand for l in prod_lines)
            order_qty_per_cycle = total_weekly_demand * (review_days / 7.0)

            # Get FOB unit cost from supplierinfo
            fob_unit_cost = self._get_fob_unit_cost(product, supplier)
            cbm_per_unit = product.product_tmpl_id.cbm_per_unit or 0.0
            pack_size = product.product_tmpl_id.pack_size or 1

            # Generate one entry per month in the horizon
            for month_offset in range(horizon_months):
                month_start = (today + relativedelta(months=month_offset)).replace(day=1)
                planned_ship_date = month_start
                planned_order_date = planned_ship_date - timedelta(days=lt_days)

                # Adjust for holiday periods
                planned_order_date = self._adjust_for_holidays(
                    planned_order_date, holiday_periods
                )

                # For each warehouse, add a line (proportional share)
                for line in prod_lines:
                    wh_weekly = line.forecasted_weekly_demand
                    wh_monthly = wh_weekly * WEEKS_PER_MONTH
                    wh_share = wh_weekly / total_weekly_demand if total_weekly_demand else 0
                    wh_order_qty = round(order_qty_per_cycle * wh_share)

                    # Round to pack size
                    if pack_size > 1 and wh_order_qty:
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
        supplier_ids = self.env['roq.forecast.line'].search([
            ('run_id', '=', run.id),
            ('abc_tier', '!=', 'D'),
            ('supplier_id', '!=', False),
        ]).mapped('supplier_id').ids
        suppliers = self.env['res.partner'].browse(list(set(supplier_ids)))
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
        If order_date falls within a holiday window, push it to the day after the
        window ends. Iterates until the adjusted date is clear of all holiday periods,
        handling adjacent or overlapping windows correctly.

        Uses an iterative (not recursive) approach to avoid stack depth issues when
        many holiday periods are configured.
        """
        if not holiday_periods:
            return order_date
        adjusted = order_date
        max_iterations = len(holiday_periods) + 1  # safety cap
        for _ in range(max_iterations):
            hit = False
            for period in holiday_periods:
                if period['start'] <= adjusted <= period['end']:
                    adjusted = period['end'] + timedelta(days=1)
                    hit = True
                    break
            if not hit:
                return adjusted
        return adjusted  # fallback after max iterations
