import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

RUN_STATUS = [
    ('draft', 'Draft'),
    ('running', 'Running'),
    ('complete', 'Complete'),
    ('error', 'Error'),
]


class RoqForecastRun(models.Model):
    _name = 'roq.forecast.run'
    _description = 'ROQ Forecast Run'
    _order = 'run_date desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: (
            self.env['ir.sequence'].next_by_code('roq.forecast.run')
            or fields.Date.today().strftime('ROQ-%Y-W%W')
        ),
    )
    run_date = fields.Datetime(string='Run Date', default=fields.Datetime.now, readonly=True)
    status = fields.Selection(RUN_STATUS, default='draft', required=True)

    # Parameter snapshots — immutable after run completes, for audit
    lookback_weeks = fields.Integer(string='Lookback Weeks (Snapshot)')
    sma_window_weeks = fields.Integer(string='SMA Window (Snapshot)')
    default_lead_time_days = fields.Integer(string='Default Lead Time (Snapshot)')
    default_review_interval_days = fields.Integer(string='Default Review Interval (Snapshot)')
    default_service_level = fields.Float(string='Default Service Level (Snapshot)', digits=(4, 3))

    # Summary stats
    total_skus_processed = fields.Integer(string='SKUs Processed', readonly=True)
    total_skus_reorder = fields.Integer(string='SKUs with ROQ > 0', readonly=True)
    total_skus_oos_risk = fields.Integer(string='SKUs at OOS Risk', readonly=True)

    enable_moq_enforcement = fields.Boolean(
        string='MOQ Enforcement Active', default=True,
        help='Parameter snapshot: was MOQ enforcement active for this run.',
    )

    line_ids = fields.One2many('roq.forecast.line', 'run_id', string='Result Lines')
    shipment_group_ids = fields.One2many(
        'roq.shipment.group', 'run_id', string='Shipment Groups',
    )
    supplier_order_line_ids = fields.One2many(
        'roq.shipment.group.line', 'run_id', string='Supplier Order Lines',
        help='All supplier lines from shipment groups created by this run.',
    )
    notes = fields.Text(string='Run Log / Errors')

    @api.model
    def cron_run_weekly_roq(self):
        """Called by ir.cron weekly trigger."""
        # Warn if the sequence is missing — the name field fallback will handle it,
        # but this makes the misconfiguration visible in the server log immediately.
        if not self.env['ir.sequence'].search([('code', '=', 'roq.forecast.run')], limit=1):
            _logger.warning(
                "ROQ: ir.sequence with code 'roq.forecast.run' not found — "
                "falling back to date-based reference. Install sequence data to fix."
            )
        run = self.create({})
        run.action_run()

    def action_run(self):
        """User-triggered or cron-triggered ROQ run."""
        self.ensure_one()
        from ..services.roq_pipeline import RoqPipeline
        # Snapshot current settings on the run header
        get = self.env['ir.config_parameter'].sudo().get_param
        self.write({
            'lookback_weeks': int(get('roq.lookback_weeks', 156)),
            'sma_window_weeks': int(get('roq.sma_window_weeks', 52)),
            'default_lead_time_days': int(get('roq.default_lead_time_days', 100)),
            'default_review_interval_days': int(get('roq.default_review_interval_days', 30)),
            'default_service_level': float(get('roq.default_service_level', 0.97)),
            'enable_moq_enforcement': get('roq.enable_moq_enforcement', 'True') == 'True',
        })
        pipeline = RoqPipeline(self.env)
        pipeline.run(self)
