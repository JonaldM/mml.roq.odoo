from datetime import date, timedelta
from collections import defaultdict
from .oos_handler import detect_oos_weeks, impute_oos_demand


class DemandHistoryService:
    """
    Queries sale.order.line to build weekly demand time series per SKU per warehouse.

    Design choices:
    - Uses sale.order.line (not stock.move) to capture demand at time of order,
      including unfulfilled/backordered demand that stock.move would miss.
    - Attributes demand to the warehouse that the sale order was assigned to.
    - Returns a list of weekly floats, oldest first, length = lookback_weeks.
      Weeks with no sales are 0.0.
    """

    def __init__(self, env):
        self.env = env

    def get_weekly_demand(self, product, warehouse, lookback_weeks=156):
        """
        Returns list of weekly demand quantities, oldest first, length=lookback_weeks.
        Zeros caused by stockouts are imputed using incoming receipt proximity signal.
        product: product.product recordset
        warehouse: stock.warehouse recordset
        lookback_weeks: int
        """
        today = date.today()
        start_date = today - timedelta(weeks=lookback_weeks)

        week_demand = defaultdict(float)

        lines = self.env['sale.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', '=', warehouse.id),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        for line in lines:
            order_date = line.order_id.date_order.date() if hasattr(
                line.order_id.date_order, 'date'
            ) else line.order_id.date_order
            week_start = order_date - timedelta(days=order_date.weekday())
            week_demand[week_start] += line.product_uom_qty

        # Build weekly series (oldest first)
        result = []
        weekly_pairs = []
        current = start_date - timedelta(days=start_date.weekday())
        while current <= today:
            qty = week_demand.get(current, 0.0)
            result.append(qty)
            weekly_pairs.append((current, qty))
            current += timedelta(weeks=1)

        result = result[-lookback_weeks:]
        weekly_pairs = weekly_pairs[-lookback_weeks:]

        # OOS detection — fetch incoming receipts for this product/warehouse
        receipt_moves = self.env['stock.move'].search([
            ('product_id', '=', product.id),
            ('location_dest_id.warehouse_id', '=', warehouse.id),
            ('picking_type_id.code', '=', 'incoming'),
            ('state', '=', 'done'),
            ('date', '>=', start_date.strftime('%Y-%m-%d')),
        ])
        receipt_dates = [
            m.date.date() if hasattr(m.date, 'date') else m.date
            for m in receipt_moves
        ]

        oos_flags = detect_oos_weeks(weekly_pairs, receipt_dates)
        result = impute_oos_demand(result, oos_flags)

        return result

    def get_weekly_demand_raw(self, product, warehouse, lookback_weeks=156):
        """
        Raw weekly demand without OOS imputation — for use by Croston/SBA only.
        Croston natively models inter-demand intervals and requires the full
        series including zeros; imputing zeros compresses interval estimates
        and inflates the forecast.
        """
        today = date.today()
        start_date = today - timedelta(weeks=lookback_weeks)

        week_demand = defaultdict(float)

        lines = self.env['sale.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', '=', warehouse.id),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        for line in lines:
            order_date = line.order_id.date_order.date() if hasattr(
                line.order_id.date_order, 'date'
            ) else line.order_id.date_order
            week_start = order_date - timedelta(days=order_date.weekday())
            week_demand[week_start] += line.product_uom_qty

        result = []
        current = start_date - timedelta(days=start_date.weekday())
        while current <= today:
            result.append(week_demand.get(current, 0.0))
            current += timedelta(weeks=1)

        return result[-lookback_weeks:]

    def get_trailing_revenue(self, product_template, weeks=52):
        """
        Returns total revenue for a product.template over trailing `weeks`.
        Sums across all warehouses — used only for product-level display fields.
        """
        today = date.today()
        start_date = today - timedelta(weeks=weeks)

        lines = self.env['sale.order.line'].search([
            ('product_id.product_tmpl_id', '=', product_template.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        return sum(
            line.product_uom_qty * line.price_unit
            for line in lines
        )

    def get_trailing_revenue_by_warehouse(self, product_template, warehouse, weeks=52):
        """
        Returns total revenue for a product.template over trailing `weeks`,
        filtered to orders originating from the given warehouse.
        Used for per-warehouse ABCD tier classification.
        """
        today = date.today()
        start_date = today - timedelta(weeks=weeks)

        lines = self.env['sale.order.line'].search([
            ('product_id.product_tmpl_id', '=', product_template.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.warehouse_id', '=', warehouse.id),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
            ('company_id', '=', self.env.company.id),
        ])

        return sum(
            line.product_uom_qty * line.price_unit
            for line in lines
        )
