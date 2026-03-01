from odoo import models, fields, api


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

    def _compute_lead_time_stats(self):
        """
        Recompute rolling lead time statistics from freight.booking records
        linked to this supplier's purchase orders.
        """
        import statistics
        for partner in self:
            if not partner.supplier_rank:
                partner.avg_lead_time_actual = 0
                partner.lead_time_std_dev = 0
                partner.lead_time_on_time_pct = 0
                continue

            # Find POs for this supplier
            po_ids = self.env['purchase.order'].search([
                ('partner_id', '=', partner.id),
            ]).ids

            if not po_ids:
                continue

            # Find delivered bookings linked to these POs
            bookings = self.env['freight.booking'].search([
                ('po_ids', 'in', po_ids),
                ('status', '=', 'delivered'),
                ('actual_lead_time_days', '>', 0),
            ])

            lead_times = bookings.mapped('actual_lead_time_days')
            if not lead_times:
                continue

            avg = sum(lead_times) / len(lead_times)
            std = statistics.stdev(lead_times) if len(lead_times) > 1 else 0.0

            # On-time = actual lead time within 10% of assumed
            assumed_lt = partner.supplier_lead_time_days or int(
                self.env['ir.config_parameter'].sudo()
                .get_param('roq.default_lead_time_days', 100)
            )
            tolerance = assumed_lt * 0.1
            on_time = sum(1 for lt in lead_times if abs(lt - assumed_lt) <= tolerance)
            on_time_pct = (on_time / len(lead_times)) * 100

            partner.write({
                'avg_lead_time_actual': avg,
                'lead_time_std_dev': std,
                'lead_time_on_time_pct': on_time_pct,
            })
