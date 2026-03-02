def post_init_hook(env):
    """Register mml_roq_forecast capabilities on install."""
    env['mml.capability'].register(
        [
            'roq.forecast.run',
            'roq.shipment.group.confirm',
            'roq.lead_time_stats.update',
        ],
        module='mml_roq_forecast',
    )


def uninstall_hook(env):
    """Deregister all mml_roq_forecast entries on uninstall."""
    env['mml.capability'].deregister_module('mml_roq_forecast')
    env['mml.event.subscription'].deregister_module('mml_roq_forecast')
