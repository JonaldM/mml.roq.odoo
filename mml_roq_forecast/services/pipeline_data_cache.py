"""
PipelineDataCache: Pre-fetches all ROQ pipeline data in 7 bulk queries.

Reduces per-run DB round trips from ~3,680 to ~7 by loading all required
data upfront and building Python dicts for O(1) per-SKU lookup.

See docs/superpowers/specs/2026-03-19-pipeline-data-cache-design.md for full design.
"""
import logging
from datetime import date, timedelta
from collections import defaultdict

_logger = logging.getLogger(__name__)


class PipelineDataCache:
    """
    Bulk pre-fetcher for ROQ pipeline data.

    Usage:
        cache = PipelineDataCache(env)
        cache.load(products, warehouses, lookback_weeks=156, abc_weeks=52)
        # Then pass cache to DemandHistoryService(env, cache=cache) etc.
    """

    def __init__(self, env):
        self.env = env
        self.demand = {}             # (variant_id, wh_id) -> list[(week_start, qty)]
        self.receipts = {}           # (variant_id, wh_id) -> list[date]
        self.soh = {}                # (variant_id, wh_id) -> float
        self.po_qty = {}             # (variant_id, wh_id) -> float
        self.supplier = {}           # tmpl_id -> supplierinfo record (or empty recordset)
        self.revenue = {}            # (tmpl_id, wh_id) -> float
        self.global_revenue = {}     # tmpl_id -> float
        self.internal_locations = {} # wh_id -> list[int]
        self._loaded = False

    def load(self, products, warehouses, lookback_weeks, abc_weeks):
        """
        Issue 7 bulk queries and build O(1) lookup dicts.

        products: product.template recordset (ROQ-managed)
        warehouses: stock.warehouse recordset (active for ROQ)
        lookback_weeks: int — demand history window (e.g. 156)
        abc_weeks: int — ABC trailing revenue window (e.g. 52)
        """
        today = date.today()
        demand_start = today - timedelta(weeks=lookback_weeks)
        abc_start = today - timedelta(weeks=abc_weeks)

        tmpl_ids = products.ids
        variant_ids = list({
            v.id
            for pt in products
            for v in pt.product_variant_ids
        })
        wh_ids = warehouses.ids

        # Fetch internal locations per warehouse (pre-req for SOH query)
        self._load_internal_locations(warehouses)

        all_location_ids = [
            loc_id
            for locs in self.internal_locations.values()
            for loc_id in locs
        ]
        location_to_wh = {}
        for wh_id, loc_ids in self.internal_locations.items():
            for loc_id in loc_ids:
                location_to_wh[loc_id] = wh_id

        # 7 bulk queries
        self._load_demand(variant_ids, wh_ids, demand_start, today)
        self._load_receipts(variant_ids, wh_ids, demand_start)
        self._load_soh(variant_ids, all_location_ids, location_to_wh)
        self._load_po_qty(variant_ids, wh_ids)
        self._load_supplier_info(tmpl_ids, products)
        self._load_revenue(variant_ids, tmpl_ids, wh_ids, abc_start, today, products)
        self._load_global_revenue(variant_ids, tmpl_ids, abc_start, today, products)

        self._loaded = True
        _logger.info(
            'PipelineDataCache loaded: %d demand keys, %d soh keys, '
            '%d po_qty keys, %d supplier keys, %d revenue keys',
            len(self.demand), len(self.soh), len(self.po_qty),
            len(self.supplier), len(self.revenue),
        )

    def _load_internal_locations(self, warehouses):
        for wh in warehouses:
            locs = self.env['stock.location'].search([
                ('warehouse_id', '=', wh.id),
                ('usage', '=', 'internal'),
            ])
            self.internal_locations[wh.id] = locs.ids

    def _load_demand(self, variant_ids, wh_ids, start_date, today):
        """Query 1: SOL demand — shared by get_weekly_demand and get_weekly_demand_raw."""
        lines = self.env['sale.order.line'].search([
            ('product_id', 'in', variant_ids),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', 'in', wh_ids),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        raw = defaultdict(lambda: defaultdict(float))
        for line in lines:
            vid = line.product_id.id
            wh_id = line.order_id.warehouse_id.id
            order_date = (
                line.order_id.date_order.date()
                if hasattr(line.order_id.date_order, 'date')
                else line.order_id.date_order
            )
            week_start = order_date - timedelta(days=order_date.weekday())
            raw[(vid, wh_id)][week_start] += line.product_uom_qty

        for key, week_sums in raw.items():
            self.demand[key] = sorted(week_sums.items())  # oldest first

    def _load_receipts(self, variant_ids, wh_ids, start_date):
        """Query 2: Incoming receipt dates for OOS detection."""
        moves = self.env['stock.move'].search([
            ('product_id', 'in', variant_ids),
            ('location_dest_id.warehouse_id', 'in', wh_ids),
            ('picking_type_id.code', '=', 'incoming'),
            ('state', '=', 'done'),
            ('date', '>=', start_date.strftime('%Y-%m-%d')),
        ])

        raw = defaultdict(list)
        for move in moves:
            vid = move.product_id.id
            wh_id = move.location_dest_id.warehouse_id.id
            move_date = (
                move.date.date() if hasattr(move.date, 'date') else move.date
            )
            raw[(vid, wh_id)].append(move_date)

        self.receipts = dict(raw)

    def _load_soh(self, variant_ids, all_location_ids, location_to_wh):
        """Query 3: Stock on hand — variant ids required (not template ids)."""
        if not all_location_ids:
            return

        quants = self.env['stock.quant'].search([
            ('product_id', 'in', variant_ids),
            ('location_id', 'in', all_location_ids),
        ])

        soh_acc = defaultdict(float)
        for quant in quants:
            wh_id = location_to_wh.get(quant.location_id.id)
            if wh_id is None:
                continue
            soh_acc[(quant.product_id.id, wh_id)] += quant.quantity

        self.soh = dict(soh_acc)

    def _load_po_qty(self, variant_ids, wh_ids):
        """Query 4: Confirmed inbound PO qty remaining."""
        po_lines = self.env['purchase.order.line'].search([
            ('product_id', 'in', variant_ids),
            ('order_id.state', 'in', ['purchase', 'done']),
            ('order_id.dest_address_id', '=', False),
        ])

        po_acc = defaultdict(float)
        for line in po_lines:
            order_wh = line.order_id.picking_type_id.warehouse_id
            if not order_wh or order_wh.id not in wh_ids:
                continue
            remaining = max(0.0, (line.product_qty or 0.0) - (line.qty_received or 0.0))
            if remaining > 0:
                po_acc[(line.product_id.id, order_wh.id)] += remaining

        self.po_qty = dict(po_acc)

    def _load_supplier_info(self, tmpl_ids, products):
        """Query 5: Primary supplier (lowest sequence) per product template."""
        all_si = self.env['product.supplierinfo'].search([
            ('product_tmpl_id', 'in', tmpl_ids),
        ], order='product_tmpl_id asc, sequence asc, id asc')

        seen = set()
        for si in all_si:
            tid = si.product_tmpl_id.id
            if tid not in seen:
                self.supplier[tid] = si
                seen.add(tid)

        empty = self.env['product.supplierinfo'].browse([])
        for pt in products:
            if pt.id not in self.supplier:
                self.supplier[pt.id] = empty

    def _load_revenue(self, variant_ids, tmpl_ids, wh_ids, start_date, today, products):
        """Query 6: Per-warehouse trailing revenue for ABC classification."""
        variant_to_tmpl = {
            v.id: pt.id
            for pt in products
            for v in pt.product_variant_ids
        }

        lines = self.env['sale.order.line'].search([
            ('product_id', 'in', variant_ids),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', 'in', wh_ids),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        rev_acc = defaultdict(float)
        for line in lines:
            tmpl_id = variant_to_tmpl.get(line.product_id.id)
            if tmpl_id is None:
                continue
            rev_acc[(tmpl_id, line.order_id.warehouse_id.id)] += (
                line.product_uom_qty * line.price_unit
            )

        self.revenue = dict(rev_acc)

    def _load_global_revenue(self, variant_ids, tmpl_ids, start_date, today, products):
        """Query 7: Global (all-warehouse) trailing revenue for abc_tier display badge."""
        variant_to_tmpl = {
            v.id: pt.id
            for pt in products
            for v in pt.product_variant_ids
        }

        lines = self.env['sale.order.line'].search([
            ('product_id', 'in', variant_ids),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        rev_acc = defaultdict(float)
        for line in lines:
            tmpl_id = variant_to_tmpl.get(line.product_id.id)
            if tmpl_id is None:
                continue
            rev_acc[tmpl_id] += line.product_uom_qty * line.price_unit

        self.global_revenue = dict(rev_acc)
