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
    roq_max_padding_weeks_cover = fields.Integer(
        string='Max Padding Weeks Cover',
        default=26,
        config_parameter='roq.max_padding_weeks_cover',
    )
    roq_default_service_level = fields.Float(
        string='Default Service Level',
        default=0.97,
        digits=(4, 3),
        config_parameter='roq.default_service_level',
    )
    roq_abc_trailing_revenue_weeks = fields.Integer(
        string='ABCD Trailing Revenue Weeks',
        default=52,
        config_parameter='roq.abc_trailing_revenue_weeks',
    )
    roq_enable_moq_enforcement = fields.Boolean(
        string='Enforce Supplier MOQs',
        config_parameter='roq.enable_moq_enforcement',
        default=True,
        help='When enabled, orders below supplier MOQ are raised to the minimum and '
             'extra units allocated to the warehouse with lowest cover. '
             'When disabled, the MOQ flag is still shown but quantities are unchanged.',
    )
    roq_tender_horizon_days = fields.Integer(
        string='Tender Horizon (Days)',
        default=45,
        config_parameter='roq.tender.horizon_days',
        help='Shipment groups can only be tendered this many days before the target ship date. '
             'Prevents tendering freight 6–12 months in advance when carrier rates are not yet firm.',
    )
