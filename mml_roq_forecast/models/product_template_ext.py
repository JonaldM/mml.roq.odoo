from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]


class ProductTemplateRoqExt(models.Model):
    _inherit = 'product.template'

    abc_tier = fields.Selection(ABC_TIERS, string='ABC Tier', readonly=True)
    abc_tier_pending = fields.Selection(ABC_TIERS, string='Pending Tier', readonly=True)
    abc_weeks_in_pending = fields.Integer(string='Weeks in Pending Tier', default=0, readonly=True)
    abc_tier_override = fields.Selection(
        ABC_TIERS, string='Tier Override (Floor)',
        help='Minimum tier floor. The SKU can still be classified higher by revenue.',
    )
    abc_trailing_revenue = fields.Float(
        string='Trailing 12M Revenue', digits=(10, 2), readonly=True,
    )
    abc_cumulative_pct = fields.Float(
        string='Cumulative Revenue %', digits=(6, 2), readonly=True,
    )
    cbm_per_unit = fields.Float(
        string='CBM per Unit', digits=(10, 4),
        help='Cubic metres per sellable unit. Required for container planning.',
    )
    pack_size = fields.Integer(
        string='Pack Size (Units/Carton)', default=1,
        help='Units per carton. ROQ is rounded up to this multiple.',
    )
    is_roq_managed = fields.Boolean(
        string='ROQ Managed', default=True,
        help='Include this product in ROQ forecast calculations.',
    )
