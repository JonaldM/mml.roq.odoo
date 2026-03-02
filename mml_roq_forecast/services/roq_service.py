import json
import logging

_logger = logging.getLogger(__name__)


class ROQService:
    """Public API for mml_roq_forecast. Retrieved via mml.registry.service('roq')."""

    def __init__(self, env):
        self.env = env

    def on_freight_booking_confirmed(self, event) -> None:
        """
        Called by mml_roq_freight bridge when a freight booking is confirmed.
        Updates lead-time feedback on the related supplier.

        event: mml.event record with res_model='freight.booking', res_id=booking_id
        """
        booking_id = event.res_id
        if not booking_id:
            return
        svc = self.env['mml.registry'].service('freight')
        transit_days = svc.get_booking_lead_time(booking_id)
        if transit_days is None:
            return
        # Delegate to res.partner lead-time stats update
        # The partner is identified via the booking's purchase order
        booking = self.env['freight.booking'].browse(booking_id)
        if not booking.exists():
            return
        if not booking.purchase_order_id:
            return
        partner = booking.purchase_order_id.partner_id
        if partner:
            partner.action_update_lead_time_stats()
