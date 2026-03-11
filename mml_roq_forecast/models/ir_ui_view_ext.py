from odoo import fields, models


class IrUiView(models.Model):
    """Extend ir.ui.view type selection to allow the custom OWL shipment
    calendar view type registered in web.assets_backend via JS viewRegistry."""
    _inherit = 'ir.ui.view'

    type = fields.Selection(
        selection_add=[('shipment_calendar', 'Shipment Planning Calendar')],
    )
