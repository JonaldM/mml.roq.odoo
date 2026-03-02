def post_init_hook(env):
    """Register mml_roq_forecast capabilities on install."""
    from odoo.addons.mml_roq_forecast.services.roq_service import ROQService
    env['mml.capability'].register(
        [
            'roq.forecast.run',
            'roq.shipment.group.confirm',
            'roq.lead_time_stats.update',
        ],
        module='mml_roq_forecast',
    )
    env['mml.registry'].register('roq', ROQService)


def uninstall_hook(env):
    """Deregister all mml_roq_forecast entries on uninstall."""
    env['mml.capability'].deregister_module('mml_roq_forecast')
    # Placeholders for ROQService (wired in Task 14) and event subscriptions (Task 15).
    # Both deregister calls are safe no-ops until those tasks populate the registries.
    env['mml.registry'].deregister('roq')
    env['mml.event.subscription'].deregister_module('mml_roq_forecast')
