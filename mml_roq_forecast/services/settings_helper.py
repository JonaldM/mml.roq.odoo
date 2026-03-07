"""
Resolves effective ROQ parameters for a given supplier and/or product.

Override semantics: REPLACE, never add. Supplier override completely replaces
system default. If override_expiry_date is set and passed, reverts to default.
"""
from datetime import date


SYSTEM_DEFAULTS = {
    'lead_time_days': 100,
    'review_interval_days': 30,
    'service_level': 0.97,
    'lookback_weeks': 156,
    'sma_window_weeks': 52,
    'min_n_value': 8,
}


class SettingsHelper:

    def __init__(self, env):
        self.env = env
        self._param_cache = {}

    def _get_param(self, key, default):
        # NOTE: SettingsHelper does not support boolean parameters via this method
        # for production use — use direct ir.config_parameter reads with string
        # comparison for booleans (e.g. get_param(...) == '1').
        val = self._param_cache.get(key)
        if val is None:
            raw = self.env['ir.config_parameter'].sudo().get_param(
                f'roq.{key}', None
            )
            val = raw
            self._param_cache[key] = raw
        if val is None:
            return default
        if isinstance(default, bool):
            return val.lower() in ('1', 'true', 'yes')  # safe string→bool conversion
        try:
            return type(default)(val)
        except (ValueError, TypeError):
            _logger.warning(
                "ROQ config param 'roq.%s' has invalid value %r — using default %r",
                key, val, default,
            )
            return default

    def _override_active(self, supplier):
        """Returns True if supplier override is active (not expired)."""
        expiry = supplier.override_expiry_date
        if not expiry:
            return True
        return expiry >= date.today()

    def get_lead_time_days(self, supplier):
        default = self._get_param('default_lead_time_days', 100)
        if supplier and supplier.supplier_lead_time_days and self._override_active(supplier):
            return supplier.supplier_lead_time_days
        return default

    def get_review_interval_days(self, supplier):
        default = self._get_param('default_review_interval_days', 30)
        if supplier and supplier.supplier_review_interval_days and self._override_active(supplier):
            return supplier.supplier_review_interval_days
        return default

    def get_service_level(self, supplier, tier):
        """
        Service level resolution order:
        1. Supplier override (if active) → replaces everything
        2. ABC tier mapping
        3. System default
        """
        TIER_SERVICE_LEVELS = {
            'A': 0.97, 'B': 0.95, 'C': 0.90, 'D': 0.0,
        }
        if supplier and supplier.supplier_service_level and self._override_active(supplier):
            return supplier.supplier_service_level
        return TIER_SERVICE_LEVELS.get(tier, self._get_param('default_service_level', 0.97))

    def get_free_days_at_origin(self, supplier):
        """
        Returns negotiated free storage days at origin for this supplier.
        No system-level default — 0 is the field default on res.partner.
        Not subject to override_expiry_date (commercial fact, not a temp override).
        """
        if supplier:
            return supplier.free_days_at_origin or 0
        return 0

    def get_lookback_weeks(self):
        return self._get_param('lookback_weeks', 156)

    def get_sma_window_weeks(self):
        return self._get_param('sma_window_weeks', 52)

    def get_min_n_value(self):
        return self._get_param('min_n_value', 8)
