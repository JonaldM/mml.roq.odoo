from odoo import models, fields, api

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
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.forecast.run'),
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
    notes = fields.Text(string='Run Log / Errors')

    @api.model
    def cron_run_weekly_roq(self):
        """Called by ir.cron weekly trigger."""
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
        })
        pipeline = RoqPipeline(self.env)
        pipeline.run(self)
