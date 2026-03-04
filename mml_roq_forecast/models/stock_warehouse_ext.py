from odoo import models, fields


class StockWarehouseRoqExt(models.Model):
    _inherit = 'stock.warehouse'

    is_active_for_roq = fields.Boolean(
        string='Active for ROQ Forecast', default=True,
        help='Include this warehouse in demand forecasting and ROQ calculations.',
    )
    roq_weekly_capacity_cbm = fields.Float(
        string='Weekly Capacity (CBM)',
        default=0.0,
        help='Maximum CBM arriving per week. 0 = no limit configured.',
    )
    roq_weekly_capacity_teu = fields.Float(
        string='Weekly Capacity (TEU)',
        default=0.0,
        help='Maximum TEU arriving per week. 1 TEU = 1×20GP; 40GP/40HQ = 2 TEU. 0 = no limit.',
    )
    roq_capacity_unit = fields.Selection(
        selection=[('cbm', 'CBM'), ('teu', 'TEU')],
        string='Capacity Unit',
        default='cbm',
        required=True,
    )
