from odoo import models, fields


class PurchaseOrderRoqExt(models.Model):
    _inherit = 'purchase.order'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group', string='Shipment Group',
        ondelete='set null',
        help='Consolidation group this PO has been assigned to.',
    )
