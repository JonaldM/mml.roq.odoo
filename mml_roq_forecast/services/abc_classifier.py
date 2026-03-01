"""
ABCD Revenue Tier Classification Service.

Classification is GLOBAL (not per-warehouse).
Revenue bands are configurable: default 70/20/10.
Dampener: a tier must be stable for N weeks before taking effect.
Override: floor (minimum tier), never a ceiling.
Tier ordering for comparisons: A > B > C > D
"""

TIER_RANK = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
RANK_TIER = {4: 'A', 3: 'B', 2: 'C', 1: 'D'}


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
            'band_a_pct': int(get('roq.abc_band_a_pct') or 70),
            'band_b_pct': int(get('roq.abc_band_b_pct') or 20),
            'dampener_weeks': int(get('roq.abc_dampener_weeks') or 4),
        }

    def classify_all_products(self, run):
        """
        Runs full ABCD classification for all ROQ-managed products.
        Updates product.template fields and writes roq.abc.history records.

        run: roq.forecast.run recordset
        """
        from datetime import date
        from .demand_history import DemandHistoryService

        settings = self.get_settings()
        dh = DemandHistoryService(self.env)

        products = self.env['product.template'].search([
            ('is_roq_managed', '=', True),
            ('type', 'in', ['product', 'consu']),
        ])

        revenue_map = {}
        for pt in products:
            revenue_map[pt.id] = dh.get_trailing_revenue(pt, weeks=52)

        overrides = {
            pt.id: pt.abc_tier_override
            for pt in products if pt.abc_tier_override
        }

        tier_assignments = self.classify_from_revenues(
            revenue_map,
            band_a_pct=settings['band_a_pct'],
            band_b_pct=settings['band_b_pct'],
            overrides=overrides,
        )

        total_rev = sum(revenue_map.values())
        sorted_by_rev = sorted(revenue_map.items(), key=lambda x: x[1], reverse=True)
        cumulative_map = {}
        cumulative = 0.0
        for pid, rev in sorted_by_rev:
            cumulative += rev
            cumulative_map[pid] = (cumulative / total_rev * 100) if total_rev else 0.0

        history_vals = []
        for pt in products:
            calculated = tier_assignments.get(pt.id, 'D')
            dampener_result = self.apply_dampener(
                current_tier=pt.abc_tier or 'C',
                calculated_tier=calculated,
                weeks_in_pending=pt.abc_weeks_in_pending or 0,
                dampener_weeks=settings['dampener_weeks'],
            )
            applied = dampener_result['applied_tier']

            pt.write({
                'abc_tier': applied,
                'abc_tier_pending': dampener_result.get('pending_tier'),
                'abc_weeks_in_pending': dampener_result['weeks_in_pending'],
                'abc_trailing_revenue': revenue_map.get(pt.id, 0.0),
                'abc_cumulative_pct': cumulative_map.get(pt.id, 0.0),
            })

            history_vals.append({
                'product_id': pt.id,
                'run_id': run.id,
                'date': date.today(),
                'tier_calculated': calculated,
                'tier_applied': applied,
                'trailing_revenue': revenue_map.get(pt.id, 0.0),
                'cumulative_pct': cumulative_map.get(pt.id, 0.0),
                'override_active': overrides.get(pt.id, ''),
            })

        self.env['roq.abc.history'].create(history_vals)
