"""
ABCD Revenue Tier Classification Service.

Classification is PER-WAREHOUSE: each warehouse runs its own pareto ranking.
A product can be A-tier in Auckland and C-tier in Wellington if its sales
are geographically concentrated.

Revenue bands are configurable: default 70/20/10.
Dampener: a tier must be stable for N consecutive runs before taking effect.
  Dampener state is stored in roq.abc.history (latest record per product/warehouse).
Override: floor (minimum tier), never a ceiling.
Tier ordering for comparisons: A > B > C > D

product.template.abc_tier is updated with the global (all-warehouses combined)
pareto tier for UI display purposes only. The pipeline uses the per-warehouse
tier map returned by classify_all_products().
"""

TIER_RANK = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
RANK_TIER = {4: 'A', 3: 'B', 2: 'C', 1: 'D'}


def _safe_int(val, default, lo=None, hi=None):
    """Parse val as int with a fallback default and optional bounds clamping."""
    try:
        result = int(val) if val not in (None, False, '') else default
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def _higher_tier(t1, t2):
    """Returns the higher of two tier strings."""
    return t1 if TIER_RANK.get(t1, 0) >= TIER_RANK.get(t2, 0) else t2


class AbcClassifier:
    """
    Classifies products into ABC tiers based on trailing revenue.
    Handles: dampener logic, override floors, dormant detection.
    """

    def __init__(self, env):
        self.env = env

    def classify_from_revenues(self, revenue_map, band_a_pct=70, band_b_pct=20, overrides=None):
        """
        revenue_map: dict of {identifier: revenue_float}
        overrides: dict of {identifier: tier_floor_string}
        Returns: dict of {identifier: tier_string}
        """
        overrides = overrides or {}
        total_revenue = sum(revenue_map.values())

        result = {}
        active = {k: v for k, v in revenue_map.items() if v > 0}

        for k in revenue_map:
            if revenue_map[k] <= 0:
                result[k] = 'D'

        if not active or total_revenue == 0:
            return result

        sorted_skus = sorted(active.items(), key=lambda x: x[1], reverse=True)

        cumulative = 0.0
        for identifier, revenue in sorted_skus:
            cumulative += revenue
            cumulative_pct = (cumulative / total_revenue) * 100

            if cumulative_pct <= band_a_pct:
                raw_tier = 'A'
            elif cumulative_pct <= band_a_pct + band_b_pct:
                raw_tier = 'B'
            else:
                raw_tier = 'C'

            override_floor = overrides.get(identifier)
            if override_floor:
                raw_tier = _higher_tier(raw_tier, override_floor)

            result[identifier] = raw_tier

        return result

    def apply_dampener(self, current_tier, calculated_tier, weeks_in_pending, dampener_weeks=4):
        """
        Applies the reclassification dampener.

        Rules:
        - If calculated_tier == current_tier: no change, reset pending counter.
        - If calculated_tier != current_tier:
            - Increment weeks_in_pending.
            - If weeks_in_pending >= dampener_weeks: apply new tier, reset counter.
            - Else: keep current_tier.

        Returns: dict with 'applied_tier', 'weeks_in_pending', 'pending_tier'
        """
        if calculated_tier == current_tier:
            return {
                'applied_tier': current_tier,
                'pending_tier': None,
                'weeks_in_pending': 0,
            }

        new_weeks = weeks_in_pending + 1

        # Reclassification fires on the 4th consecutive run in new tier.
        # Run sequence: pending=0→1 (held), 1→2 (held), 2→3 (held), 3→4 >= dampener_weeks (fires).
        # Per spec: "Tier must be stable for 4 consecutive runs before reclassification takes effect."
        if new_weeks >= dampener_weeks:
            return {
                'applied_tier': calculated_tier,
                'pending_tier': None,
                'weeks_in_pending': 0,
            }

        return {
            'applied_tier': current_tier,
            'pending_tier': calculated_tier,
            'weeks_in_pending': new_weeks,
        }

    def get_settings(self):
        """Read ROQ settings from ir.config_parameter with fallback defaults."""
        get = self.env['ir.config_parameter'].sudo().get_param
        return {
            'band_a_pct': _safe_int(get('roq.abc_band_a_pct'), 70, lo=1, hi=99),
            'band_b_pct': _safe_int(get('roq.abc_band_b_pct'), 20, lo=1, hi=98),
            'dampener_weeks': _safe_int(get('roq.abc_dampener_weeks'), 4, lo=1, hi=52),
        }

    def classify_all_products(self, run):
        """
        Runs ABCD classification per warehouse for all ROQ-managed products.

        For each warehouse, builds a per-warehouse revenue map and runs the
        pareto ranking independently. Dampener state is read from (and written
        back to) roq.abc.history — the latest record per (product, warehouse)
        is the authoritative current state.

        product.template.abc_tier is updated with the aggregate (global)
        pareto tier purely for display purposes on the product card.

        Returns:
            dict: {(product_tmpl_id, warehouse_id): {
                'tier': str,
                'revenue': float,
                'cumulative_pct': float,
            }}
        """
        from datetime import date
        from .demand_history import DemandHistoryService

        settings = self.get_settings()
        dh = DemandHistoryService(self.env)

        products = self.env['product.template'].search([
            ('is_roq_managed', '=', True),
            ('type', 'in', ['product', 'consu']),
        ])
        warehouses = self.env['stock.warehouse'].search([
            ('is_active_for_roq', '=', True),
        ])

        trailing_weeks = _safe_int(
            self.env['ir.config_parameter'].sudo()
            .get_param('roq.abc_trailing_revenue_weeks', 52),
            52, lo=1, hi=520,
        )

        overrides = {
            pt.id: pt.abc_tier_override
            for pt in products if pt.abc_tier_override
        }

        # --- Per-warehouse classification ---
        # result_map: {(pt_id, wh_id): {'tier', 'revenue', 'cumulative_pct'}}
        result_map = {}
        history_vals = []
        today = date.today()

        for wh in warehouses:
            # Build revenue map for this warehouse
            wh_revenue_map = {
                pt.id: dh.get_trailing_revenue_by_warehouse(pt, wh, weeks=trailing_weeks)
                for pt in products
            }

            # Pareto ranking within this warehouse
            wh_tier_assignments = self.classify_from_revenues(
                wh_revenue_map,
                band_a_pct=settings['band_a_pct'],
                band_b_pct=settings['band_b_pct'],
                overrides=overrides,
            )

            # Compute cumulative % within this warehouse
            wh_total_rev = sum(wh_revenue_map.values())
            wh_sorted = sorted(wh_revenue_map.items(), key=lambda x: x[1], reverse=True)
            wh_cumulative_map = {}
            cumulative = 0.0
            for pid, rev in wh_sorted:
                cumulative += rev
                wh_cumulative_map[pid] = (cumulative / wh_total_rev * 100) if wh_total_rev else 0.0

            # Fetch latest history records for this warehouse (dampener state)
            # One query per warehouse — not per product — to stay performant.
            last_history_by_product = {}
            history_recs = self.env['roq.abc.history'].search([
                ('warehouse_id', '=', wh.id),
                ('product_id', 'in', products.ids),
            ], order='date desc')
            for h in history_recs:
                pid = h.product_id.id
                if pid not in last_history_by_product:
                    last_history_by_product[pid] = h

            for pt in products:
                calculated = wh_tier_assignments.get(pt.id, 'D')
                last = last_history_by_product.get(pt.id)
                current_tier = last.tier_applied if last else 'C'
                weeks_in_pending = last.weeks_in_pending if last else 0

                dampener_result = self.apply_dampener(
                    current_tier=current_tier,
                    calculated_tier=calculated,
                    weeks_in_pending=weeks_in_pending,
                    dampener_weeks=settings['dampener_weeks'],
                )
                applied = dampener_result['applied_tier']

                result_map[(pt.id, wh.id)] = {
                    'tier': applied,
                    'revenue': wh_revenue_map.get(pt.id, 0.0),
                    'cumulative_pct': wh_cumulative_map.get(pt.id, 0.0),
                }

                history_vals.append({
                    'product_id': pt.id,
                    'warehouse_id': wh.id,
                    'run_id': run.id,
                    'date': today,
                    'tier_calculated': calculated,
                    'tier_applied': applied,
                    'trailing_revenue': wh_revenue_map.get(pt.id, 0.0),
                    'cumulative_pct': wh_cumulative_map.get(pt.id, 0.0),
                    'override_active': overrides.get(pt.id, ''),
                    'weeks_in_pending': dampener_result['weeks_in_pending'],
                })

        # --- Update product.template display fields with global pareto ---
        # Global = sum of revenue across all warehouses, used only for the
        # product card badge and stats (not the pipeline).
        global_revenue_map = {
            pt.id: dh.get_trailing_revenue(pt, weeks=trailing_weeks)
            for pt in products
        }
        global_tier_assignments = self.classify_from_revenues(
            global_revenue_map,
            band_a_pct=settings['band_a_pct'],
            band_b_pct=settings['band_b_pct'],
            overrides=overrides,
        )
        global_total_rev = sum(global_revenue_map.values())
        global_sorted = sorted(global_revenue_map.items(), key=lambda x: x[1], reverse=True)
        global_cumulative_map = {}
        cumulative = 0.0
        for pid, rev in global_sorted:
            cumulative += rev
            global_cumulative_map[pid] = (cumulative / global_total_rev * 100) if global_total_rev else 0.0

        for pt in products:
            global_tier = global_tier_assignments.get(pt.id, 'D')
            # sudo() required: ops-manager users lack write access to product.template
            pt.sudo().write({
                'abc_tier': global_tier,
                'abc_tier_pending': None,
                'abc_weeks_in_pending': 0,
                'abc_trailing_revenue': global_revenue_map.get(pt.id, 0.0),
                'abc_cumulative_pct': global_cumulative_map.get(pt.id, 0.0),
            })

        # sudo() required: ops-manager users lack create rights on roq.abc.history
        self.env['roq.abc.history'].sudo().create(history_vals)

        return result_map
