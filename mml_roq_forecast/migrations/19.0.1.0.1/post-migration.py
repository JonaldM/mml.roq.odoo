"""Backfill ROQ ir.config_parameter defaults for instances installed
before data/ir_config_parameter_data.xml was added.

The seed XML file declares ``noupdate="1"`` so regular module upgrades
(``-u``) skip these rows on already-installed instances. A QA run on the
dev DB (mml_test_sprint, 2026-03-11) confirmed that several ROQ system
defaults were missing as a result.

This post-migration script is idempotent: it only writes a key if the
parameter is absent (``get_param`` returns falsy / ``None``). It is safe
to run on every upgrade.

Source of truth: ``mml_roq_forecast/data/ir_config_parameter_data.xml``.
The dictionary below MUST mirror that file exactly. There is a structural
test at ``mml_roq_forecast/tests/test_config_param_backfill.py`` that
parses the XML and fails if the two diverge.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


# Mirrors mml_roq_forecast/data/ir_config_parameter_data.xml exactly.
# All values are strings — ir.config_parameter stores text.
ROQ_DEFAULTS = {
    'roq.default_lead_time_days': '100',
    'roq.default_review_interval_days': '30',
    'roq.default_service_level': '0.97',
    'roq.lookback_weeks': '156',
    'roq.sma_window_weeks': '52',
    'roq.min_n_value': '8',
    'roq.abc_dampener_weeks': '4',
    'roq.abc_trailing_revenue_weeks': '52',
    'roq.container_lcl_threshold_pct': '50',
    'roq.max_padding_weeks_cover': '26',
    'roq.max_pull_days': '30',
    'roq.enable_moq_enforcement': 'True',
    'roq.calendar.consolidation_window_days': '21',
    'roq.calendar.reschedule_threshold_days': '5',
    'roq.tender.horizon_days': '45',
}


def migrate(cr, version):
    """Backfill any missing ROQ config parameters.

    Odoo's migration framework calls this with a raw cursor (``cr``) and
    the previously-installed module version string. We construct an
    environment from the cursor using the standard Odoo 19 pattern.

    Args:
        cr: psycopg2 cursor for the current database.
        version: Previously-installed version string, or ``None`` if the
            module is being installed fresh (in which case the data file
            handles seeding and this script is effectively a no-op).
    """
    if not version:
        # Fresh install — data file handles seeding. Nothing to backfill.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Param = env['ir.config_parameter'].sudo()

    backfilled = []
    for key, value in ROQ_DEFAULTS.items():
        # get_param returns the string value or False when missing.
        if not Param.get_param(key):
            Param.set_param(key, value)
            backfilled.append(key)

    if backfilled:
        _logger.info(
            "mml_roq_forecast: backfilled %d missing ROQ defaults: %s",
            len(backfilled),
            sorted(backfilled),
        )
    else:
        _logger.info(
            "mml_roq_forecast: all %d ROQ defaults already present, no backfill needed.",
            len(ROQ_DEFAULTS),
        )
