from odoo import models, fields


class StockWarehouseRoqExt(models.Model):
    _inherit = 'stock.warehouse'

    is_active_for_roq = fields.Boolean(
        string='Active for ROQ Forecast', default=True,
        help='Include this warehouse in demand forecasting and ROQ calculations.',
    )
