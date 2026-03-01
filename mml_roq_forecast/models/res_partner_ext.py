from odoo import models, fields


class ResPartnerRoqExt(models.Model):
    _inherit = 'res.partner'

    fob_port = fields.Char(
        string='FOB Port',
        help='Port of origin for freight consolidation grouping (e.g. Shenzhen, CN).',
    )
    supplier_lead_time_days = fields.Integer(
        string='Lead Time Override (Days)',
        help='Overrides system default. Leave blank to use system default.',
    )
    supplier_review_interval_days = fields.Integer(
        string='Review Interval Override (Days)',
        help='Overrides system default. Leave blank to use system default.',
    )
    supplier_service_level = fields.Float(
        string='Service Level Override',
        digits=(4, 3),
        help='Overrides system default and ABC tier. Leave blank to use tier-based service level.',
    )
    override_expiry_date = fields.Date(
        string='Override Expiry Date',
        help='All overrides auto-revert to system default after this date.',
    )
    supplier_holiday_periods = fields.Text(
        string='Holiday Periods (JSON)',
        help='JSON array of {start, end, reason} objects. e.g. CNY shutdown periods.',
    )
    avg_lead_time_actual = fields.Float(
        string='Avg Actual Lead Time (Days)', digits=(6, 1), readonly=True,
        help='Rolling average from freight.booking records.',
    )
    lead_time_std_dev = fields.Float(
        string='Lead Time Std Dev (Days)', digits=(6, 1), readonly=True,
    )
    lead_time_on_time_pct = fields.Float(
        string='On-Time Delivery %', digits=(5, 1), readonly=True,
    )
