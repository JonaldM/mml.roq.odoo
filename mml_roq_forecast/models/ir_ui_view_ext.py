from odoo import fields, models


class IrUiView(models.Model):
    """Extend ir.ui.view type selection to allow the custom OWL shipment
    calendar view type registered in web.assets_backend via JS viewRegistry.

    Also extends _get_view_info() so session.view_info includes shipment_calendar.
    Without this, the JS viewRegistry addValidation() rejects the view type entry
    and viewRegistry.get('shipment_calendar') returns undefined at runtime."""
    _inherit = 'ir.ui.view'

    type = fields.Selection(
        selection_add=[('shipment_calendar', 'Shipment Planning Calendar')],
    )

    def _get_view_info(self):
        info = super()._get_view_info()
        info['shipment_calendar'] = {
            'icon': 'fa fa-anchor',
            'multi_record': True,
        }
        return info
