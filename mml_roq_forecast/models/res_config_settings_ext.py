from odoo import models, fields


class ResConfigSettingsRoqExt(models.TransientModel):
    _inherit = 'res.config.settings'

    roq_default_lead_time_days = fields.Integer(
        string='Default Lead Time (Days)', default=100,
        config_parameter='roq.default_lead_time_days',
    )
    roq_default_review_interval_days = fields.Integer(
        string='Default Review Interval (Days)', default=30,
        config_parameter='roq.default_review_interval_days',
    )
    roq_lookback_weeks = fields.Integer(
        string='Lookback Weeks', default=156,
        config_parameter='roq.lookback_weeks',
    )
    roq_sma_window_weeks = fields.Integer(
        string='SMA Window (Weeks)', default=52,
        config_parameter='roq.sma_window_weeks',
    )
    roq_min_n_value = fields.Integer(
        string='Min N Value', default=8,
        config_parameter='roq.min_n_value',
    )
    roq_abc_dampener_weeks = fields.Integer(
        string='ABC Dampener (Weeks)', default=4,
        config_parameter='roq.abc_dampener_weeks',
    )
    roq_container_lcl_threshold_pct = fields.Integer(
        string='Container LCL Threshold (%)', default=50,
        config_parameter='roq.container_lcl_threshold_pct',
    )
    roq_max_pull_days = fields.Integer(
        string='Max Pull Days', default=30,
        config_parameter='roq.max_pull_days',
    )
    roq_enable_moq_enforcement = fields.Boolean(
        string='Enforce Supplier MOQs',
        config_parameter='roq.enable_moq_enforcement',
        default=True,
        help='When enabled, orders below supplier MOQ are raised to the minimum and '
             'extra units allocated to the warehouse with lowest cover. '
             'When disabled, the MOQ flag is still shown but quantities are unchanged.',
    )
