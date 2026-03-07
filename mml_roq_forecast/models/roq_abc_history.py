from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]


class RoqAbcHistory(models.Model):
    _name = 'roq.abc.history'
    _description = 'ABC Tier Classification History'
    _order = 'date desc, product_id, warehouse_id'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.template', required=True, ondelete='cascade')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True, ondelete='cascade')
    run_id = fields.Many2one('roq.forecast.run', ondelete='set null')
    date = fields.Date(required=True)
    tier_calculated = fields.Selection(ABC_TIERS, string='Calculated Tier', required=True)
    tier_applied = fields.Selection(ABC_TIERS, string='Applied Tier', required=True)
    trailing_revenue = fields.Float(string='Trailing Revenue', digits=(10, 2))
    cumulative_pct = fields.Float(string='Cumulative %', digits=(6, 2))
    override_active = fields.Char(string='Override Active')
    weeks_in_pending = fields.Integer(string='Weeks in Pending', default=0)
