"""
Pure-Python tests for PipelineDataCache.

Tests exercise each _load_* helper directly via mock env, verifying that
bulk ORM results are aggregated into the correct dict structures.
No Odoo instance required.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock
from collections import defaultdict

# Odoo stubs provided by conftest.py — import is safe without a running Odoo
from mml_roq_forecast.services.pipeline_data_cache import PipelineDataCache


def _make_env():
    env = MagicMock()
    env.company.id = 1
    return env


def _make_variant(variant_id, tmpl_id):
    v = MagicMock()
    v.id = variant_id
    v.product_tmpl_id.id = tmpl_id
    return v


def _make_product_tmpl(tmpl_id, variant_ids):
    pt = MagicMock()
    pt.id = tmpl_id
    variants = [_make_variant(vid, tmpl_id) for vid in variant_ids]
    pt.product_variant_ids = variants
    pt.ids = [tmpl_id]
    return pt


# ---------------------------------------------------------------------------
# Internal location tests
# ---------------------------------------------------------------------------

class TestLoadInternalLocations:

    def test_stores_location_ids_per_warehouse(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        locs = MagicMock()
        locs.ids = [10, 11]
        env['stock.location'].search.return_value = locs

        wh = MagicMock()
        wh.id = 1
        warehouses = [wh]

        cache._load_internal_locations(warehouses)

        assert cache.internal_locations[1] == [10, 11]

    def test_empty_warehouse_stores_empty_list(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        locs = MagicMock()
        locs.ids = []
        env['stock.location'].search.return_value = locs

        wh = MagicMock()
        wh.id = 99
        cache._load_internal_locations([wh])

        assert cache.internal_locations[99] == []


# ---------------------------------------------------------------------------
# SOH tests
# ---------------------------------------------------------------------------

class TestLoadSoh:

    def test_soh_aggregates_quantities_across_locations_in_same_warehouse(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        quant1 = MagicMock()
        quant1.product_id.id = 101
        quant1.location_id.id = 10
        quant1.quantity = 50.0

        quant2 = MagicMock()
        quant2.product_id.id = 101
        quant2.location_id.id = 11  # same warehouse, different internal location
        quant2.quantity = 30.0

        env['stock.quant'].search.return_value = [quant1, quant2]
        cache._load_soh(variant_ids=[101], all_location_ids=[10, 11],
                        location_to_wh={10: 1, 11: 1})

        assert cache.soh[(101, 1)] == 80.0

    def test_soh_separates_quantities_by_warehouse(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        quant_wh1 = MagicMock()
        quant_wh1.product_id.id = 101
        quant_wh1.location_id.id = 10
        quant_wh1.quantity = 50.0

        quant_wh2 = MagicMock()
        quant_wh2.product_id.id = 101
        quant_wh2.location_id.id = 20
        quant_wh2.quantity = 25.0

        env['stock.quant'].search.return_value = [quant_wh1, quant_wh2]
        cache._load_soh(variant_ids=[101], all_location_ids=[10, 20],
                        location_to_wh={10: 1, 20: 2})

        assert cache.soh[(101, 1)] == 50.0
        assert cache.soh[(101, 2)] == 25.0

    def test_soh_returns_zero_for_missing_key(self):
        env = _make_env()
        cache = PipelineDataCache(env)
        cache.soh = {}
        assert cache.soh.get((999, 1), 0.0) == 0.0

    def test_soh_skips_quant_with_unknown_location(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        quant = MagicMock()
        quant.product_id.id = 101
        quant.location_id.id = 99   # not in location_to_wh
        quant.quantity = 100.0

        env['stock.quant'].search.return_value = [quant]
        cache._load_soh(variant_ids=[101], all_location_ids=[99],
                        location_to_wh={})  # intentionally empty

        assert (101, 1) not in cache.soh

    def test_soh_domain_uses_variant_ids_not_template_ids(self):
        """
        Critical: stock.quant.product_id is a product.product (variant) field.
        Passing template ids silently returns zero results.
        Verify _load_soh is called with variant_ids, not tmpl_ids.
        """
        env = _make_env()
        cache = PipelineDataCache(env)
        env['stock.quant'].search.return_value = []

        variant_ids = [201, 202]  # variant ids
        cache._load_soh(variant_ids=variant_ids, all_location_ids=[10],
                        location_to_wh={10: 1})

        call_args = env['stock.quant'].search.call_args
        domain = call_args[0][0]
        product_domain_clause = next(c for c in domain if c[0] == 'product_id')
        # The domain value must be the variant_ids list, not template ids
        assert product_domain_clause[2] == variant_ids


# ---------------------------------------------------------------------------
# Demand tests
# ---------------------------------------------------------------------------

class TestLoadDemand:

    def _make_sol_line(self, variant_id, wh_id, order_date, qty):
        line = MagicMock()
        line.product_id.id = variant_id
        line.order_id.warehouse_id.id = wh_id
        # Simulate Odoo Datetime: hasattr(..., 'date') is True, .date() returns date obj
        line.order_id.date_order.date.return_value = order_date
        line.product_uom_qty = qty
        return line

    def test_demand_aggregates_two_lines_same_week(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        monday = date(2026, 1, 5)   # weekday() == 0
        wednesday = date(2026, 1, 7)  # same week

        lines = [
            self._make_sol_line(101, 1, monday, 10.0),
            self._make_sol_line(101, 1, wednesday, 5.0),
        ]
        env['sale.order.line'].search.return_value = lines
        today = date.today()

        cache._load_demand(variant_ids=[101], wh_ids=[1],
                           start_date=today - timedelta(weeks=10), today=today)

        pairs = dict(cache.demand.get((101, 1), []))
        week_start = monday - timedelta(days=monday.weekday())
        assert pairs.get(week_start, 0.0) == 15.0

    def test_demand_separates_different_warehouses(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        monday = date(2026, 1, 5)
        today = date.today()
        env['sale.order.line'].search.return_value = [
            self._make_sol_line(101, 1, monday, 8.0),
            self._make_sol_line(101, 2, monday, 3.0),
        ]
        cache._load_demand(variant_ids=[101], wh_ids=[1, 2],
                           start_date=today - timedelta(weeks=10), today=today)

        assert (101, 1) in cache.demand
        assert (101, 2) in cache.demand

    def test_demand_sorted_oldest_first(self):
        """Spec line 57: list of (week_start_date, qty) sorted oldest-first."""
        env = _make_env()
        cache = PipelineDataCache(env)

        week1 = date(2026, 1, 5)   # older
        week2 = date(2026, 1, 12)  # newer
        today = date.today()
        env['sale.order.line'].search.return_value = [
            self._make_sol_line(101, 1, week2, 5.0),  # intentionally out of order
            self._make_sol_line(101, 1, week1, 8.0),
        ]
        cache._load_demand(variant_ids=[101], wh_ids=[1],
                           start_date=today - timedelta(weeks=10), today=today)

        pairs = cache.demand[(101, 1)]
        dates = [p[0] for p in pairs]
        assert dates == sorted(dates), "demand list must be sorted oldest-first"


# ---------------------------------------------------------------------------
# Receipt tests
# ---------------------------------------------------------------------------

class TestLoadReceipts:

    def test_receipts_stored_per_variant_and_warehouse(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        move = MagicMock()
        move.product_id.id = 101
        move.location_dest_id.warehouse_id.id = 1
        move.date.date.return_value = date(2026, 1, 10)

        env['stock.move'].search.return_value = [move]
        cache._load_receipts(variant_ids=[101], wh_ids=[1],
                             start_date=date(2025, 1, 1))

        assert (101, 1) in cache.receipts
        assert date(2026, 1, 10) in cache.receipts[(101, 1)]

    def test_receipts_domain_uses_picking_type_id_code(self):
        """Spec: picking_type_id.code='incoming' (Odoo 19 field name)."""
        env = _make_env()
        cache = PipelineDataCache(env)
        env['stock.move'].search.return_value = []

        cache._load_receipts(variant_ids=[101], wh_ids=[1],
                             start_date=date(2025, 1, 1))

        call_args = env['stock.move'].search.call_args
        domain = call_args[0][0]
        code_clause = next(c for c in domain if c[0] == 'picking_type_id.code')
        assert code_clause[2] == 'incoming'

    def test_receipts_empty_for_product_with_no_moves(self):
        env = _make_env()
        cache = PipelineDataCache(env)
        env['stock.move'].search.return_value = []
        cache._load_receipts(variant_ids=[101], wh_ids=[1],
                             start_date=date(2025, 1, 1))
        assert cache.receipts.get((101, 1), []) == []


# ---------------------------------------------------------------------------
# Supplier info tests
# ---------------------------------------------------------------------------

class TestLoadSupplierInfo:

    def test_first_supplier_by_sequence_is_stored(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        si1 = MagicMock()
        si1.product_tmpl_id.id = 1
        si1.sequence = 1   # primary

        si2 = MagicMock()
        si2.product_tmpl_id.id = 1
        si2.sequence = 2   # secondary — should be ignored

        env['product.supplierinfo'].search.return_value = [si1, si2]

        pt = _make_product_tmpl(tmpl_id=1, variant_ids=[101])
        cache._load_supplier_info(tmpl_ids=[1], products=[pt])

        assert cache.supplier[1] is si1

    def test_product_with_no_supplier_gets_empty_recordset(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        env['product.supplierinfo'].search.return_value = []  # no supplier found
        env['product.supplierinfo'].browse.return_value = MagicMock()  # empty recordset

        pt = _make_product_tmpl(tmpl_id=1, variant_ids=[101])
        cache._load_supplier_info(tmpl_ids=[1], products=[pt])

        # Key must exist even when no supplier found
        assert 1 in cache.supplier


# ---------------------------------------------------------------------------
# PO qty tests
# ---------------------------------------------------------------------------

class TestLoadPoQty:

    def test_po_qty_sums_remaining_qty(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        line = MagicMock()
        line.product_id.id = 101
        line.order_id.picking_type_id.warehouse_id.id = 1
        line.order_id.picking_type_id.warehouse_id.__bool__ = lambda self: True
        line.product_qty = 100.0
        line.qty_received = 40.0

        env['purchase.order.line'].search.return_value = [line]
        cache._load_po_qty(variant_ids=[101], wh_ids=[1])

        assert cache.po_qty.get((101, 1), 0.0) == 60.0

    def test_po_qty_ignores_fully_received_lines(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        line = MagicMock()
        line.product_id.id = 101
        line.order_id.picking_type_id.warehouse_id.id = 1
        line.order_id.picking_type_id.warehouse_id.__bool__ = lambda self: True
        line.product_qty = 100.0
        line.qty_received = 100.0  # fully received

        env['purchase.order.line'].search.return_value = [line]
        cache._load_po_qty(variant_ids=[101], wh_ids=[1])

        assert cache.po_qty.get((101, 1), 0.0) == 0.0

    def test_po_qty_ignores_lines_from_other_warehouses(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        line = MagicMock()
        line.product_id.id = 101
        line.order_id.picking_type_id.warehouse_id.id = 99  # wh not in wh_ids
        line.order_id.picking_type_id.warehouse_id.__bool__ = lambda self: True
        line.product_qty = 50.0
        line.qty_received = 0.0

        env['purchase.order.line'].search.return_value = [line]
        cache._load_po_qty(variant_ids=[101], wh_ids=[1])  # wh 99 not included

        assert (101, 99) not in cache.po_qty


# ---------------------------------------------------------------------------
# Revenue tests
# ---------------------------------------------------------------------------

class TestLoadRevenue:

    def _make_sol_revenue(self, variant_id, wh_id, qty, price):
        line = MagicMock()
        line.product_id.id = variant_id
        line.order_id.warehouse_id.id = wh_id
        line.product_uom_qty = qty
        line.price_unit = price
        return line

    def test_revenue_multiplies_qty_by_price(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        pt = _make_product_tmpl(tmpl_id=1, variant_ids=[101])
        products = [pt]

        today = date.today()
        env['sale.order.line'].search.return_value = [
            self._make_sol_revenue(101, 1, 10.0, 25.0),  # 250.0
        ]

        cache._load_revenue(variant_ids=[101], tmpl_ids=[1], wh_ids=[1],
                            start_date=today - timedelta(weeks=52), today=today,
                            products=products)

        assert cache.revenue.get((1, 1), 0.0) == 250.0

    def test_global_revenue_sums_across_all_warehouses(self):
        env = _make_env()
        cache = PipelineDataCache(env)

        pt = _make_product_tmpl(tmpl_id=1, variant_ids=[101])
        products = [pt]

        today = date.today()
        env['sale.order.line'].search.return_value = [
            self._make_sol_revenue(101, 1, 5.0, 20.0),   # wh1: 100.0
            self._make_sol_revenue(101, 2, 3.0, 20.0),   # wh2: 60.0
        ]

        cache._load_global_revenue(variant_ids=[101], tmpl_ids=[1],
                                   start_date=today - timedelta(weeks=52),
                                   today=today, products=products)

        assert cache.global_revenue.get(1, 0.0) == 160.0
