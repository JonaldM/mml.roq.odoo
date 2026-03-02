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

        svc = self.env['mml.registry'].service('freight')
        # get_booking_lead_time confirms booking exists and transit_days_actual is set.
        if svc.get_booking_lead_time(booking_id) is None:
            return

        booking = self.env['freight.booking'].browse(booking_id)
        if not booking.purchase_order_id:
            return

        partner = booking.purchase_order_id.partner_id
        if partner:
            partner.action_update_lead_time_stats()
