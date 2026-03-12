from odoo import models, fields


class PurchaseOrderRoqExt(models.Model):
    _inherit = 'purchase.order'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group', string='Shipment Group',
        ondelete='set null',
        help='Consolidation group this PO has been assigned to.',
    )
    # Compatibility stub: custom_purchase_containers module may not be installed.
    # This field definition prevents OWL from crashing when the inherited form view
    # references container_line_ids. The view was injected by that module and may
    # remain in ir_ui_view even after uninstall.
    container_line_ids = fields.One2many(
        'purchase.container.line', 'order_id', string='Containers',
    )


class PurchaseContainerLine(models.Model):
    _name = 'purchase.container.line'
    _description = 'Purchase Order Container Line (compatibility stub)'

    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade')
    container_type = fields.Char(string='Container Type')
    quantity = fields.Float(string='Quantity', digits=(10, 0))
