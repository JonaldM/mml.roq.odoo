import logging

_logger = logging.getLogger(__name__)


class ROQService:
    """Public API for mml_roq_forecast. Retrieved via mml.registry.service('roq')."""

    def __init__(self, env):
        self.env = env

    def on_freight_booking_confirmed(self, event) -> None:
        """
        Called by mml_roq_freight bridge when a freight booking is confirmed.
        Triggers a full lead-time stats recompute for the booking's supplier partner.

        event: mml.event record with res_model='freight.booking', res_id=booking_id

        Uses get_booking_lead_time as an existence + data-readiness guard:
        - Returns None when mml_freight is not installed (NullService) → skip
        - Returns None when booking doesn't exist or transit_days_actual is unset → skip
        Once the guard passes, browse the booking once for the partner reference and
        delegate to action_update_lead_time_stats (batch recompute for that partner).
        A dedicated incremental _update_supplier_lead_time_feedback path is deferred
        to a future sprint; the full recompute is correct and sufficiently lightweight
        for the expected event frequency.
        """
        booking_id = event.res_id
        if not booking_id:
            return

        freight_svc = self.env['mml.registry'].service('freight')
        # get_booking_lead_time confirms booking exists and transit_days_actual is set.
        if freight_svc.get_booking_lead_time(booking_id) is None:
            return

        partner_id = freight_svc.get_booking_supplier_partner_id(booking_id)
        if not partner_id:
            return

        partner = self.env['res.partner'].browse(partner_id)
        if partner.exists():
            partner.action_update_lead_time_stats()
