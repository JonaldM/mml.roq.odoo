import logging
from odoo import models, fields, api, exceptions, _

_logger = logging.getLogger(__name__)


def _safe_int(val, default):
    try:
        return int(val) if val not in (None, '', False) else default
    except (ValueError, TypeError):
        return default


def _safe_float(val, default):
    try:
        return float(val) if val not in (None, '', False) else default
    except (ValueError, TypeError):
        return default

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
            self.env['ir.sequence'].sudo().next_by_code('roq.forecast.run')
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
        if not self.env['ir.sequence'].sudo().search([('code', '=', 'roq.forecast.run')], limit=1):
            _logger.warning(
                "ROQ: ir.sequence with code 'roq.forecast.run' not found — "
                "falling back to date-based reference. Install sequence data to fix."
            )
        # Guard against concurrent runs: if a run is already in 'running' state,
        # a previous cron tick or worker is still executing. Skip to avoid
        # parallel pipeline executions that would corrupt forecast line results.
        stuck = self.search([('status', '=', 'running')], limit=1)
        if stuck:
            _logger.warning(
                'ROQ cron skipped: run %s is still in running state. '
                'If this persists, set status to error manually.', stuck.id,
            )
            return

        try:
            run = self.create({})
            run.action_run()
        except Exception as exc:
            _logger.exception('ROQ weekly cron failed')
            self._send_cron_alert(
                'mml_roq_forecast',
                'Weekly ROQ forecast run failed',
                str(exc),
            )
            raise

    def _send_cron_alert(self, module_name: str, subject: str, body: str) -> None:
        """Send an email alert when a scheduled action fails."""
        alert_email = self.env['ir.config_parameter'].sudo().get_param(
            'mml.cron_alert_email', False
        )
        if not alert_email:
            return
        try:
            self.env['mail.mail'].sudo().create({
                'subject': '[MML ALERT] %s: %s' % (module_name, subject),
                'body_html': '<pre>%s</pre>' % body,
                'email_to': alert_email,
            }).send()
        except Exception:
            _logger.exception('Failed to send cron alert email for %s', module_name)

    def unlink(self):
        """Delete runs along with their draft shipment groups.

        Shipment groups that have been confirmed, tendered, booked, or delivered,
        or that have a purchase order raised against any supplier line, are
        preserved — their run_id is set to null (orphaned) so they remain
        visible and manageable without the run record.

        Draft groups with no POs are safe to remove — they represent unrealised
        pipeline output that has no downstream commitments.
        """
        for run in self:
            safe_to_delete = run.shipment_group_ids.filtered(
                lambda g: g.state == 'draft'
                and not g.line_ids.filtered(lambda l: l.purchase_order_id)
            )
            preserved = run.shipment_group_ids - safe_to_delete
            if preserved:
                _logger.info(
                    'ROQ run %s deleted: preserving %d active/committed shipment group(s) '
                    '(%s) — run_id will be set to null.',
                    run.name,
                    len(preserved),
                    ', '.join(preserved.mapped('name')),
                )
            safe_to_delete.unlink()
        return super().unlink()

    def action_run(self):
        """User-triggered or cron-triggered ROQ run."""
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise exceptions.AccessError(_('Only system administrators can trigger ROQ runs manually.'))
        from ..services.roq_pipeline import RoqPipeline
        # Snapshot current settings on the run header
        get = self.env['ir.config_parameter'].sudo().get_param
        self.write({
            'lookback_weeks': _safe_int(get('roq.lookback_weeks'), 156),
            'sma_window_weeks': _safe_int(get('roq.sma_window_weeks'), 52),
            'default_lead_time_days': _safe_int(get('roq.default_lead_time_days'), 100),
            'default_review_interval_days': _safe_int(get('roq.default_review_interval_days'), 30),
            'default_service_level': _safe_float(get('roq.default_service_level'), 0.97),
            'enable_moq_enforcement': get('roq.enable_moq_enforcement', 'True') == 'True',
        })
        pipeline = RoqPipeline(self.env)
        pipeline.run(self)
        self.env['mml.event'].emit(
            'roq.forecast.run',
            quantity=1,
            billable_unit='roq_run',
            res_model=self._name,
            res_id=self.id,
            source_module='mml_roq_forecast',
            payload={'run_ref': self.name, 'sku_count': len(self.line_ids)},
        )
