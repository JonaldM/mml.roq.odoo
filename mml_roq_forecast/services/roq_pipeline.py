"""
ROQ Pipeline Orchestrator.

Step order (per spec §2.2):
1. ABCD Classification
2. Demand Forecast per SKU per warehouse
3. Safety Stock per SKU per warehouse
4. ROQ Calculation per SKU per warehouse
5. Pack Size Rounding
6. Aggregate by Supplier
7. MOQ Enforcement (raise per-SKU supplier total to product.supplierinfo.min_qty if below)
8. Container Fitting
9. Write results to roq.forecast.line

Called by roq.forecast.run.action_run()
"""
import logging
from collections import defaultdict

_logger = logging.getLogger(__name__)

from .abc_classifier import AbcClassifier
from .demand_history import DemandHistoryService
from .forecast_methods import (
    forecast_sma, forecast_ewma, forecast_holt_winters, forecast_croston_sba,
    select_forecast_method, demand_std_dev,
    HW_BIAS_FLOOR, HW_BIAS_CEILING,
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
from .pipeline_data_cache import PipelineDataCache


class RoqPipeline:

    def __init__(self, env):
        self.env = env
        self.settings = SettingsHelper(env)
        self.cache = PipelineDataCache(env)
        self.abc = AbcClassifier(env)
        self.dh = DemandHistoryService(env, cache=self.cache)
        self.inv = InventoryQueryService(env, cache=self.cache)

    def run(self, forecast_run):
        """
        Execute full ROQ pipeline.
        forecast_run: roq.forecast.run record
        Writes results to roq.forecast.line.
        """
        forecast_run.write({'status': 'running'})

        try:
            # Fetch products + warehouses once — reused by cache load and _compute_all_lines
            products = self.env['product.template'].search([
                ('is_roq_managed', '=', True),
                ('type', 'in', ['product', 'consu']),
            ])
            warehouses = self.env['stock.warehouse'].search([
                ('is_active_for_roq', '=', True),
            ])

            lookback = self.settings.get_lookback_weeks()
            abc_weeks = max(1, int(
                self.env['ir.config_parameter'].sudo()
                .get_param('roq.abc_trailing_revenue_weeks', 52) or 52
            ))

            # Step 0: Bulk pre-fetch — replaces ~3,680 per-SKU queries with 7 bulk queries
            self.cache.load(products, warehouses, lookback, abc_weeks)

            # Step 1: ABCD Classification — uses pre-loaded revenue caches (no per-product queries)
            tier_map = self.abc.classify_all_products(
                forecast_run,
                revenue_cache=self.cache.revenue,
                global_revenue_cache=self.cache.global_revenue,
            )

            # Step 2-5: Per-SKU per-warehouse forecast + ROQ
            line_vals = self._compute_all_lines(forecast_run, tier_map, products, warehouses)

            # Step 6-7: Aggregate by supplier + container fit
            line_vals = self._apply_container_fitting(line_vals)

            # Write results (sudo: pipeline creates its own computed output records)
            self.env['roq.forecast.line'].sudo().create(line_vals)

            # Update run summary
            skus_with_roq = sum(1 for v in line_vals if v.get('roq_containerized', 0) > 0)
            skus_oos = sum(1 for v in line_vals if v.get('projected_inventory_at_delivery', 0) < 0)
            forecast_run.write({
                'status': 'complete',
                'total_skus_processed': len(set(v['product_id'] for v in line_vals)),
                'total_skus_reorder': skus_with_roq,
                'total_skus_oos_risk': skus_oos,
            })

            # Step 8: Reactive consolidation (creates shipment groups by FOB port)
            from .consolidation_engine import ConsolidationEngine
            con_engine = ConsolidationEngine(self.env)
            con_engine.create_reactive_shipment_groups(forecast_run)

        except Exception as e:
            _logger.exception('ROQ pipeline failed for run %s', forecast_run.id)
            forecast_run.write({
                'status': 'error',
                'notes': f'Pipeline error: {type(e).__name__}. Check server logs for details.',
            })
            raise

    def _compute_all_lines(self, forecast_run, tier_map, products, warehouses):
        """
        Compute per-SKU per-warehouse ROQ lines (steps 2-5).

        tier_map: {(product_tmpl_id, warehouse_id): {'tier', 'revenue', 'cumulative_pct'}}
                  Returned by AbcClassifier.classify_all_products().
                  Each (product, warehouse) pair has its own tier from the
                  per-warehouse pareto ranking.
        products, warehouses: pre-fetched recordsets from run() — avoids duplicate ORM searches.
        """
        lookback = self.settings.get_lookback_weeks()
        sma_window = self.settings.get_sma_window_weeks()
        min_n = self.settings.get_min_n_value()

        line_vals = []

        for pt in products:
            product = pt.product_variant_ids[:1]
            if not product:
                continue

            # Get primary supplier and MOQ once per product (same across warehouses)
            # self.cache._loaded is True when cache.load() completed successfully;
            # False only if cache construction failed — ORM fallback fires as before.
            if self.cache._loaded:
                supplier_info = self.cache.supplier.get(
                    pt.id, self.env['product.supplierinfo'].browse([])
                )
            else:
                supplier_info = self.env['product.supplierinfo'].search([
                    ('product_tmpl_id', '=', pt.id),
                ], order='sequence asc, id asc', limit=1)
            supplier = supplier_info.partner_id if supplier_info else self.env['res.partner'].browse([])
            supplier_moq = supplier_info.min_qty if supplier_info else 0.0

            lt_days = self.settings.get_lead_time_days(supplier)
            review_days = self.settings.get_review_interval_days(supplier)
            lt_weeks = lt_days / 7.0
            review_weeks = review_days / 7.0

            for wh in warehouses:
                # Per-warehouse tier — a product can be A in one warehouse, C in another
                wh_data = tier_map.get((pt.id, wh.id), {})
                tier = wh_data.get('tier', 'D')

                if tier == 'D':
                    # Dormant in this warehouse: zero-ROQ line, skip forecast
                    line_vals.append(self._dormant_line(forecast_run, product, wh, pt))
                    continue

                z_score = get_z_score(tier)

                history = self.dh.get_weekly_demand(product, wh, lookback_weeks=lookback)
                # Raw history (no OOS imputation) for routing and Croston only.
                # Croston natively models inter-demand intervals and requires zeros
                # to correctly estimate demand frequency; imputing zeros inflates
                # forecasts by compressing the estimated inter-demand interval.
                raw_history = self.dh.get_weekly_demand_raw(product, wh, lookback_weeks=lookback)
                method, confidence = select_forecast_method(raw_history, min_n=min_n)

                if method == 'croston':
                    fwd, croston_std = forecast_croston_sba(raw_history)
                elif method == 'sma':
                    fwd, croston_std = forecast_sma(history, window=sma_window), None
                elif method == 'ewma':
                    fwd, croston_std = forecast_ewma(history, span=26), None
                else:  # holt_winters
                    hw_raw = forecast_holt_winters(history)
                    fwd = hw_raw
                    # Post-hoc SMA bias override: if HW forecast is outside
                    # [HW_BIAS_FLOOR, HW_BIAS_CEILING] × sma_13w, replace with a
                    # recency-weighted SMA (last 4w × 2, prior 9w × 1) to correct
                    # momentum lag on demand collapses and surges.
                    sma_13w = (sum(history[-13:]) / 13) if len(history) >= 13 else (
                        sum(history) / len(history) if history else 0.0
                    )
                    if sma_13w > 0:
                        hw_ratio = hw_raw / sma_13w
                        if hw_ratio > HW_BIAS_CEILING or hw_ratio < HW_BIAS_FLOOR:
                            recent = history[-4:]
                            prior = history[-13:-4]
                            weighted_sum = sum(recent) * 2 + sum(prior)
                            weighted_count = len(recent) * 2 + len(prior)
                            fwd = weighted_sum / weighted_count if weighted_count > 0 else sma_13w
                            method = 'hw_sma_override'
                            _logger.info(
                                "HW bias override %s wh=%s: ratio=%.2fx sma_13w=%.1f"
                                " hw=%.1f -> override=%.1f",
                                getattr(pt, 'default_code', getattr(pt, 'id', '?')),
                                getattr(wh, 'name', getattr(wh, 'id', '?')),
                                hw_ratio, sma_13w, hw_raw, fwd,
                            )
                    croston_std = None

                avg_demand = sum(history) / len(history) if history else 0.0
                sigma, is_fallback = demand_std_dev(history, min_n=min_n, croston_std=croston_std)
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

                notes = self._build_notes(
                    proj_inv, ss, weeks_cover, pt.cbm_per_unit, pt.pack_size,
                )

                line_vals.append({
                    'run_id': forecast_run.id,
                    'product_id': product.id,
                    'warehouse_id': wh.id,
                    'supplier_id': supplier.id if supplier else False,
                    'abc_tier': tier,
                    # Per-warehouse revenue stats from tier_map
                    'trailing_12m_revenue': wh_data.get('revenue', 0.0),
                    'cumulative_revenue_pct': wh_data.get('cumulative_pct', 0.0),
                    'soh': soh,
                    'confirmed_po_qty': po_qty,
                    'inventory_position': inv_pos,
                    'avg_weekly_demand': avg_demand,
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
                    'container_type': 'unassigned',
                    'notes': notes,
                    # MOQ — snapshot at run time; enforcement applied in step 7
                    'supplier_moq': supplier_moq,
                    'moq_uplift_qty': 0.0,
                    'moq_flag': False,
                    # Internal carry fields for container fitting — removed before write
                    '_tier_str': tier,
                    '_weeks_cover': weeks_cover,
                })

        return line_vals

    def _apply_container_fitting(self, line_vals):
        """
        Steps 6-8: Group lines by supplier, enforce MOQ (step 7), run container fitting (step 8),
        update roq_containerized, container_type, fill_pct, padding_units.
        """
        from .moq_enforcer import MoqEnforcer

        get = self.env['ir.config_parameter'].sudo().get_param
        lcl_threshold = max(1, min(100, int(get('roq.container_lcl_threshold_pct', 50) or 50)))
        max_padding = max(1, min(52, int(get('roq.max_padding_weeks_cover', 26) or 26)))
        enforce_moq = get('roq.enable_moq_enforcement', 'True') == 'True'
        fitter = ContainerFitter(lcl_threshold, max_padding)

        # Group by supplier_id
        by_supplier = defaultdict(list)
        for i, val in enumerate(line_vals):
            sid = val.get('supplier_id') or 0
            by_supplier[sid].append((i, val))

        for sid, indexed_vals in by_supplier.items():
            # Skip dormant lines
            active = [(i, v) for i, v in indexed_vals if v.get('roq_pack_rounded', 0) > 0]
            if not active:
                continue

            # Step 7: MOQ Enforcement — per SKU within this supplier group
            # Group warehouse lines by product, apply MoqEnforcer, then update cbm_total
            by_product = defaultdict(list)
            for _, v in active:
                by_product[v['product_id']].append(v)

            for pid_lines in by_product.values():
                MoqEnforcer.enforce(
                    pid_lines,
                    enforce=enforce_moq,
                    max_padding_weeks_cover=max_padding,
                )
                # Recompute cbm_total from MOQ-adjusted roq_pack_rounded so container
                # fitting and downstream consolidation use the correct volume
                for v in pid_lines:
                    v['cbm_total'] = v['roq_pack_rounded'] * v.get('cbm_per_unit', 0.0)

            # Step 8: Container Fitting — uses MOQ-adjusted quantities
            # active is a list of (original_line_vals_index, val_dict) in stable order.
            # ContainerFitter.fit() preserves input order in line_results, so we match
            # results back by position rather than by product_id — this avoids the
            # multi-warehouse key collision where the same product_id appears in multiple
            # warehouse lines and a dict keyed on product_id alone would drop all but the last.
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

            # Match results back by position (fit_result['line_results'] is in the same
            # order as fit_input, which is the same order as active).
            for pos, (idx, _val) in enumerate(active):
                r = fit_result['line_results'][pos]
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
        soh = self.inv.get_soh(product, warehouse)
        return {
            'run_id': run.id,
            'product_id': product.id,
            'warehouse_id': warehouse.id,
            'abc_tier': 'D',
            'soh': soh,
            'confirmed_po_qty': 0.0,
            'inventory_position': soh,
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
