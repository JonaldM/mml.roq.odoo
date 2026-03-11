from datetime import timedelta

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
    ('confirmed', 'Confirmed'),   # action_confirm() — plan locked, no tender yet
    ('tendered', 'Tendered'),     # action_create_tender() → emits event → freight module creates tender
    ('booked', 'Booked'),         # freight module transitions this
    ('delivered', 'Delivered'),   # freight module writes back actual_delivery_date
    ('cancelled', 'Cancelled'),
]

SHIPMENT_MODE = [
    ('reactive', 'Reactive'),    # Created from weekly ROQ run
    ('proactive', 'Proactive'),  # Created from 12-month forward plan
]

# States in which a shipment group can be dragged on the planning calendar.
# Locked states (tendered, booked, delivered, cancelled) are not affected by
# the write() date-shift logic.
DRAGGABLE_STATES = {'draft', 'confirmed'}


class RoqShipmentGroup(models.Model):
    _name = 'roq.shipment.group'
    _description = 'Shipment Consolidation Group'
    _order = 'target_ship_date desc'
    _rec_name = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].sudo().next_by_code('roq.shipment.group'),
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

    # Freight status (populated via mml.registry.service('freight') — empty if mml_freight not installed)
    freight_eta = fields.Datetime(
        string='Freight ETA',
        compute='_compute_freight_status',
        store=False,
    )
    freight_status = fields.Char(
        string='Freight Status',
        compute='_compute_freight_status',
        store=False,
    )
    freight_last_update = fields.Datetime(
        string='Last Freight Update',
        compute='_compute_freight_status',
        store=False,
    )
    consolidation_suggestion = fields.Char(
        string='Consolidation Suggestion',
        help='Set when a nearby same-origin group is detected after rescheduling. '
             'Clear manually once actioned.',
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

    # fob_port is a stored alias for origin_port (for backwards compatibility / readability).
    # ALWAYS write to origin_port, not fob_port (related fields are read-only).
    # Reading either field returns the same value.
    fob_port = fields.Char(
        string='FOB Port (Key)', related='origin_port', store=True,
        help='Alias for origin_port used by consolidation grouping logic.',
    )

    @api.depends()
    def _compute_freight_status(self):
        """Fetch live freight booking status via service locator.
        Returns empty fields if mml_freight is not installed (NullService pattern).
        Empty @api.depends() ensures the result is never served from cache — each
        read hits the service locator so the calendar always shows live freight data."""
        svc = self.env['mml.registry'].service('freight')
        get_status = getattr(svc, 'get_booking_status', None)
        for rec in self:
            result = get_status(rec.id) if get_status else None
            if result:
                rec.freight_eta = result.get('eta')
                rec.freight_status = result.get('status')
                rec.freight_last_update = result.get('last_update')
            else:
                rec.freight_eta = False
                rec.freight_status = False
                rec.freight_last_update = False

    def _find_consolidation_candidates(self):
        """Return nearby draft/confirmed groups from same FOB port within config window.

        Queries for groups with the same origin_port in DRAGGABLE_STATES whose
        target_delivery_date falls within ±N days of this record's delivery date.
        N is configurable via ir.config_parameter 'roq.calendar.consolidation_window_days'.
        Returns an empty recordset if this group is not in a draggable state.
        """
        self.ensure_one()
        if self.state not in DRAGGABLE_STATES:
            return self.env['roq.shipment.group']

        window = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'roq.calendar.consolidation_window_days', default=21
            )
        )
        date_from = self.target_delivery_date - timedelta(days=window)
        date_to = self.target_delivery_date + timedelta(days=window)

        return self.search([
            ('id', '!=', self.id),
            ('origin_port', '=', self.origin_port),
            ('state', 'in', list(DRAGGABLE_STATES)),
            ('target_delivery_date', '>=', date_from),
            ('target_delivery_date', '<=', date_to),
        ])

    def write(self, vals):
        """Override to detect delivery date changes on draggable groups.

        When target_delivery_date changes:
        - Shifts target_ship_date by the same delta (preserving transit duration)
        - Re-evaluates oos_risk_flag on line records
        - Posts a chatter message recording the change
        Only applies to groups in DRAGGABLE_STATES (draft, confirmed).
        Locked states (tendered, booked, delivered) are not affected.
        """
        date_changing = 'target_delivery_date' in vals
        old_dates = {}
        if date_changing:
            for rec in self:
                if rec.state in DRAGGABLE_STATES:
                    old_dates[rec.id] = rec.target_delivery_date

        result = super().write(vals)

        if date_changing and old_dates:
            new_delivery = vals['target_delivery_date']
            if isinstance(new_delivery, str):
                new_delivery = fields.Date.from_string(new_delivery)

            for rec in self:
                if rec.id not in old_dates:
                    continue
                old_delivery = old_dates[rec.id]
                if old_delivery == new_delivery:
                    continue

                delta = new_delivery - old_delivery

                # Shift target_ship_date by same delta to preserve transit window
                if rec.target_ship_date:
                    rec.target_ship_date = rec.target_ship_date + delta

                # Re-evaluate OOS risk flags on line records
                for line in rec.line_ids:
                    line.oos_risk_flag = line.projected_inventory_at_delivery < 0

                # Chatter audit trail
                delta_days = delta.days
                direction = 'pushed out' if delta_days > 0 else 'pulled forward'
                rec.message_post(
                    body=(
                        f'Shipment rescheduled: delivery {direction} by '
                        f'{abs(delta_days)} day(s) '
                        f'({old_delivery} → {new_delivery}).'
                    ),
                    message_type='notification',
                )

                # Consolidation proximity check for significant shifts.
                # NOTE: Assigning rec.consolidation_suggestion here triggers a second
                # ORM write() call. It is safe because the nested write() has
                # vals = {'consolidation_suggestion': ...} which does NOT contain
                # 'target_delivery_date', so date_changing=False and this block
                # is not re-entered. Do NOT add a second date-watching key to vals
                # without auditing this path to avoid a recursive loop.
                threshold = int(
                    self.env['ir.config_parameter'].sudo().get_param(
                        'roq.calendar.reschedule_threshold_days', default=5
                    )
                )
                if abs(delta.days) > threshold:
                    candidates = rec._find_consolidation_candidates()
                    if candidates:
                        rec.consolidation_suggestion = ', '.join(
                            candidates.mapped('name')
                        )
                    else:
                        rec.consolidation_suggestion = False

        return result

    def action_confirm(self):
        """
        Confirm this shipment group — locks the plan without creating a freight tender.

        Confirmation can be done months in advance. Freight tendering is a separate step
        (action_create_tender) which enforces the horizon window (roq.tender.horizon_days).
        """
        self.ensure_one()
        if self.state != 'draft':
            raise exceptions.UserError('Only draft shipment groups can be confirmed.')

        self.write({'state': 'confirmed'})
        self.message_post(body='Shipment group confirmed. Freight tender can be submitted closer to the ship date.')

    def action_create_tender(self):
        """
        Request freight tender for a confirmed shipment group.

        Enforces the horizon window: target_ship_date must be within
        roq.tender.horizon_days (default 45) days. Raises UserError if too early
        so planners cannot accidentally tender 6 months out.

        Emits roq.shipment_group.confirmed — the mml_roq_freight bridge handles
        this event and calls FreightService.create_tender(). If mml_freight is not
        installed the event fires but is not handled, which is safe.
        """
        self.ensure_one()
        if self.state != 'confirmed':
            raise exceptions.UserError('Only confirmed shipment groups can be tendered.')

        if self.target_ship_date:
            horizon = int(
                self.env['ir.config_parameter'].sudo().get_param(
                    'roq.tender.horizon_days', default=45
                )
            )
            days_until_ship = (self.target_ship_date - fields.Date.today()).days
            if days_until_ship > horizon:
                earliest = self.target_ship_date - timedelta(days=horizon)
                raise exceptions.UserError(
                    f'This shipment is not due to ship for {days_until_ship} day(s). '
                    f'Freight tenders should be submitted within {horizon} days of the ship date. '
                    f'Earliest tender date: {earliest}.'
                )

        self.env['mml.event'].emit(
            'roq.shipment_group.confirmed',
            quantity=1,
            billable_unit='roq_po_line',
            res_model=self._name,
            res_id=self.id,
            source_module='mml_roq_forecast',
            payload={'group_ref': self.name},
        )
        self.message_post(body='Freight tender requested.')

    def action_cancel(self):
        self.ensure_one()
        if self.state in ('delivered', 'cancelled'):
            raise exceptions.UserError('Cannot cancel a delivered or already cancelled group.')
        self.write({'state': 'cancelled'})
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
    free_days_at_origin = fields.Integer(
        string='Free Days at Origin',
        readonly=True,
        help='Snapshot of supplier free days at time of shipment group creation.',
    )
    oos_risk_flag = fields.Boolean(
        string='OOS Risk',
        help='True if any SKU in this supplier order has projected inventory at delivery < 0.',
    )
    original_ship_date = fields.Date(string='Original Ship Date')
    product_count = fields.Integer(string='SKU Count')

    # Stored related — enables One2many from roq.forecast.run directly to all supplier lines
    run_id = fields.Many2one(
        'roq.forecast.run', string='ROQ Run',
        related='group_id.run_id', store=True, index=True, readonly=True,
    )
    # Non-stored related — for display in dashboard Order by Supplier tab
    container_type = fields.Selection(
        CONTAINER_TYPES, string='Container',
        related='group_id.container_type', readonly=True,
    )

    def action_view_forecast_lines(self):
        """Open filtered ROQ forecast lines for this supplier in the parent run."""
        self.ensure_one()
        run = self.group_id.run_id
        if not run:
            raise exceptions.UserError('This shipment group has no linked ROQ run.')
        return {
            'type': 'ir.actions.act_window',
            'name': f'SKUs — {self.supplier_id.name}',
            'res_model': 'roq.forecast.line',
            'view_mode': 'list',
            'domain': [
                ('run_id', '=', run.id),
                ('supplier_id', '=', self.supplier_id.id),
                ('roq_containerized', '>', 0),
                ('abc_tier', '!=', 'D'),
            ],
            'context': {'create': False, 'delete': False},
        }

    def action_raise_po_wizard(self):
        """Open the Raise Draft PO wizard pre-populated for this supplier."""
        self.ensure_one()
        if self.group_id.state != 'draft':
            raise exceptions.UserError(
                'Purchase orders can only be raised from draft shipment groups. '
                f'This group is currently {dict(SHIPMENT_STATE).get(self.group_id.state, self.group_id.state)}.'
            )
        run = self.group_id.run_id
        if not run:
            raise exceptions.UserError('This shipment group has no linked ROQ run.')
        if self.purchase_order_id:
            raise exceptions.UserError(
                f'A purchase order ({self.purchase_order_id.name}) is already linked to '
                f'{self.supplier_id.name}. Open it directly or unlink it first.'
            )
        forecast_lines = self.env['roq.forecast.line'].search([
            ('run_id', '=', run.id),
            ('supplier_id', '=', self.supplier_id.id),
            ('roq_containerized', '>', 0),
            ('abc_tier', '!=', 'D'),
        ]).sorted(key=lambda l: l.product_id.name or '')
        if not forecast_lines:
            raise exceptions.UserError(
                f'No active order lines found for {self.supplier_id.name} in run {run.name}.'
            )
        wizard = self.env['roq.raise.po.wizard'].create({
            'run_id': run.id,
            'supplier_id': self.supplier_id.id,
            'shipment_group_line_id': self.id,
            'line_ids': [(0, 0, {
                'forecast_line_id': fl.id,
                'product_id': fl.product_id.id,
                'warehouse_id': fl.warehouse_id.id,
                'qty_containerized': fl.roq_containerized,
                'qty_pack_rounded': fl.roq_pack_rounded,
                'qty_to_order': fl.roq_containerized,
                'notes': fl.notes or '',
            }) for fl in forecast_lines],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'roq.raise.po.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'name': f'Raise PO — {self.supplier_id.name}',
        }
