import logging
import statistics
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ResPartnerRoqExt(models.Model):
    _inherit = 'res.partner'

    # --- Port & trade terms ---
    fob_port_id = fields.Many2one(
        'roq.port', string='FOB Port (Origin)',
        help='Port of origin for freight consolidation. Select from the curated port list.',
    )
    fob_port = fields.Char(
        string='FOB Port Code', related='fob_port_id.code',
        store=True, readonly=True,
        help='UN/LOCODE of the origin port. Derived from FOB Port selection. '
             'Used as the consolidation grouping key — do not set manually.',
    )
    destination_port_id = fields.Many2one(
        'roq.port', string='Destination Port',
        help='Default NZ (or other) port of discharge for this supplier\'s shipments. '
             'Flows into shipment group destination port.',
    )
    purchase_incoterm_id = fields.Many2one(
        'account.incoterms', string='Default Incoterm',
        help='Trade terms for orders from this supplier. '
             'FOB/FCA/EXW = MML arranges main freight → included in consolidation. '
             'CIF/DDP/DAP/etc. = supplier arranges freight → excluded from consolidation.',
    )
    free_days_at_origin = fields.Integer(
        string='Free Days at Origin',
        default=0,
        help='Negotiated free storage days at the supplier\'s origin warehouse/port after '
             'manufacturing completion. Extends the safe push window in consolidation planning. '
             'Default 0 = no free storage arranged.',
    )

    @api.constrains('free_days_at_origin')
    def _check_free_days_at_origin(self):
        for rec in self:
            if rec.free_days_at_origin < 0:
                raise ValidationError(
                    'Free Days at Origin cannot be negative.'
                )

    # --- ROQ parameter overrides ---
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

    # --- Lead time statistics (written by cron / action_update_lead_time_stats) ---
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

    def action_update_lead_time_stats(self):
        """
        Recompute rolling lead time statistics from freight booking records
        linked to this supplier's purchase orders.

        Called by:
          - ir.cron (weekly) — model.search([('supplier_rank', '>', 0)]).sudo().action_update_lead_time_stats()
          - Manual button on the partner form (optional)

        NOTE: This is NOT an @api.depends compute method. It is a manual updater
        that must be triggered explicitly (cron or button). Stats fields are stored
        and readonly — they are only written by this method.

        Uses the service locator to retrieve freight booking lead times — returns None
        per booking if mml_freight is not installed. Partners are skipped (not zeroed)
        when the freight service is unavailable so that previously-written stats are
        preserved until the service comes back online.
        """
        svc = self.env['mml.registry'].service('freight')

        for partner in self:
            if not partner.supplier_rank:
                partner.avg_lead_time_actual = 0
                partner.lead_time_std_dev = 0
                partner.lead_time_on_time_pct = 0
                continue

            po_ids = self.env['purchase.order'].search([
                ('partner_id', '=', partner.id),
            ]).ids

            if not po_ids:
                continue

            # Retrieve lead time data via service locator.
            # Returns a flat list of float transit-day values, e.g. [14.0, 18.0, 12.5],
            # or None when mml_freight is not installed (NullService returns None).
            booking_lead_times = svc.get_delivered_booking_lead_times(po_ids)
            if booking_lead_times is None:
                _logger.warning(
                    "ROQ: freight service unavailable — lead time stats not updated for %s.",
                    partner.name,
                )
                continue

            lead_times = [r for r in booking_lead_times if r and r > 0]
            if not lead_times:
                continue

            avg = sum(lead_times) / len(lead_times)
            std = statistics.stdev(lead_times) if len(lead_times) > 1 else 0.0

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
