from odoo import models, fields


class FreightTenderRoqExtension(models.Model):
    """
    Extends mml_freight's freight.tender with a relational link back to the
    ROQ shipment group that triggered it.

    This keeps mml_freight free of any dependency on mml_roq_forecast.
    mml_freight uses a lightweight shipment_group_ref Char field.
    This module adds the proper Many2one for navigation and ORM queries.

    See: roq-freight-interface-contract.md §2
    """
    _inherit = 'freight.tender'

    shipment_group_id = fields.Many2one(
        'roq.shipment.group',
        string='Shipment Group',
        ondelete='set null',
        index=True,
        help='ROQ consolidation group that triggered this freight tender.',
    )
