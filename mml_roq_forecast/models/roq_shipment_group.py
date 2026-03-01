from odoo import models, fields, api, exceptions

# Contract-specified container type values — do NOT change these keys
CONTAINER_TYPES = [
    ('20GP', "20' Standard (20GP)"),
    ('40GP', "40' Standard (40GP)"),
    ('40HQ', "40' High Cube (40HQ)"),
    ('LCL', 'LCL'),
]

SHIPMENT_STATE = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),   # action_confirm() → creates freight.tender
    ('tendered', 'Tendered'),     # freight module transitions this
    ('booked', 'Booked'),         # freight module transitions this
    ('delivered', 'Delivered'),   # freight module writes back actual_delivery_date
    ('cancelled', 'Cancelled'),
]

SHIPMENT_MODE = [
    ('reactive', 'Reactive'),    # Created from weekly ROQ run
    ('proactive', 'Proactive'),  # Created from 12-month forward plan
]


class RoqShipmentGroup(models.Model):
    _name = 'roq.shipment.group'
    _description = 'Shipment Consolidation Group'
    _order = 'target_ship_date desc'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.shipment.group'),
    )

    # Interface contract required fields
    state = fields.Selection(SHIPMENT_STATE, default='draft', required=True, tracking=True)
    origin_port = fields.Char(string='Origin Port (FOB)')
    destination_port = fields.Char(string='Destination Port')
    container_type = fields.Selection(CONTAINER_TYPES, string='Container Type')
    total_cbm = fields.Float(string='Total CBM', digits=(10, 3))
    total_weight_kg = fields.Float(string='Total Weight (kg)', digits=(10, 2))
    target_ship_date = fields.Date(string='Target Ship Date (ETD)')
    target_delivery_date = fields.Date(string='Target Delivery Date')
    po_ids = fields.Many2many(
        'purchase.order', string='Purchase Orders',
        help='POs consolidated in this shipment. Passed to freight.tender on confirm.',
    )
    freight_tender_id = fields.Many2one(
        'freight.tender', string='Freight Tender',
        ondelete='set null', readonly=True,
        help='Created by ROQ on action_confirm. Managed by mml_freight thereafter.',
    )

    # Delivery feedback — written by freight module (Option A per contract §4)
    actual_delivery_date = fields.Date(
        string='Actual Delivery Date', readonly=True,
        help='Written by mml_freight when freight.booking reaches delivered state.',
    )
    actual_lead_time_days = fields.Integer(
        string='Actual Lead Time (Days)', readonly=True,
    )
    lead_time_variance_days = fields.Integer(
        string='Lead Time Variance (Days)', readonly=True,
        help='Positive = late. Negative = early.',
    )

    # ROQ internal fields
    fill_percentage = fields.Float(string='Fill %', digits=(5, 1))
    mode = fields.Selection(SHIPMENT_MODE, default='reactive', required=True)
    destination_warehouse_ids = fields.Many2many(
        'stock.warehouse', string='Destination Warehouses',
    )
    run_id = fields.Many2one(
        'roq.forecast.run', string='Source ROQ Run', ondelete='set null',
    )
    line_ids = fields.One2many('roq.shipment.group.line', 'group_id', string='Supplier Lines')
    notes = fields.Text(string='Notes')

    # Alias for origin_port used by consolidation grouping logic
    fob_port = fields.Char(
        string='FOB Port (Key)', related='origin_port', store=True,
        help='Alias for origin_port used by consolidation grouping logic.',
    )

    def action_confirm(self):
        """
        Confirm this shipment group and create a freight.tender.
        Per interface contract §3: ROQ creates the tender; freight manages it thereafter.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise exceptions.UserError('Only draft shipment groups can be confirmed.')

        tender = self.env['freight.tender'].create({
            'shipment_group_ref': self.name,        # lightweight ref (always present)
            'shipment_group_id': self.id,           # relational link (our extension field)
            'origin_port': self.origin_port or '',
            'dest_port': self.destination_port or '',
            'requested_pickup_date': self.target_ship_date,
            'requested_delivery_date': self.target_delivery_date,
            'po_ids': [(4, po.id) for po in self.po_ids],
        })

        self.write({
            'freight_tender_id': tender.id,
            'state': 'confirmed',
        })
        self.message_post(
            body=f'Shipment group confirmed. Freight tender created: {tender.name}',
        )

    def action_cancel(self):
        self.ensure_one()
        if self.state in ('delivered', 'cancelled'):
            raise exceptions.UserError('Cannot cancel a delivered or already cancelled group.')
        self.state = 'cancelled'
        self.message_post(body='Shipment group cancelled.')


class RoqShipmentGroupLine(models.Model):
    _name = 'roq.shipment.group.line'
    _description = 'Shipment Group Supplier Line'

    group_id = fields.Many2one('roq.shipment.group', required=True, ondelete='cascade')
    supplier_id = fields.Many2one('res.partner', required=True, string='Supplier')
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Purchase Order', ondelete='set null',
        help='Nullable — PO may not exist yet in proactive mode.',
    )
    cbm = fields.Float(string='CBM Contribution', digits=(10, 3))
    weight_kg = fields.Float(string='Weight (kg)', digits=(10, 2))
    push_pull_days = fields.Integer(
        string='Push/Pull Days',
        help='Positive = pushed (delayed). Negative = pulled (brought forward). 0 = as planned.',
    )
    push_pull_reason = fields.Char(string='Push/Pull Reason')
    oos_risk_flag = fields.Boolean(
        string='OOS Risk',
        help='True if any SKU in this supplier order has projected inventory at delivery < 0.',
    )
    original_ship_date = fields.Date(string='Original Ship Date')
    product_count = fields.Integer(string='SKU Count')
