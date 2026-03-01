from datetime import date, timedelta
from collections import defaultdict


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
        Sums across all warehouses (ABCD tier is global).
        """
        today = date.today()
        start_date = today - timedelta(weeks=weeks)

        lines = self.env['sale.order.line'].search([
            ('product_id.product_tmpl_id', '=', product_template.id),
            ('order_id.state', 'in', ['sale', 'done']),
            ('order_id.date_order', '>=', start_date.strftime('%Y-%m-%d')),
        ])

        return sum(
            line.product_uom_qty * line.price_unit
            for line in lines
        )
