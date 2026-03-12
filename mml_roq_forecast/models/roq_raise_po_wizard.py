import logging
from odoo import models, fields, api, exceptions, _

_logger = logging.getLogger(__name__)


class RoqRaisePoWizard(models.TransientModel):
    _name = 'roq.raise.po.wizard'
    _description = 'Raise Draft Purchase Orders from ROQ Results'

    run_id = fields.Many2one('roq.forecast.run', string='ROQ Run', readonly=True, required=True)
    supplier_id = fields.Many2one('res.partner', string='Supplier', readonly=True, required=True)
    shipment_group_line_id = fields.Many2one(
        'roq.shipment.group.line', string='Shipment Group Line',
        readonly=True, ondelete='set null',
    )
    use_containerized = fields.Boolean(
        string='Include container padding (A-tier)',
        default=True,
        help='ON = use ROQ (Containerized) qty including A-tier container padding. '
             'OFF = use ROQ (Pack Rounded) demand-only qty.',
    )
    line_ids = fields.One2many('roq.raise.po.wizard.line', 'wizard_id', string='Order Lines')

    @api.onchange('use_containerized')
    def _onchange_use_containerized(self):
        for line in self.line_ids:
            line.qty_to_order = (
                line.qty_containerized if self.use_containerized else line.qty_pack_rounded
            )

    def action_raise_pos(self):
        """Create one draft purchase.order per supplier × warehouse, then return to PO view."""
        self.ensure_one()
        if not self.line_ids:
            raise exceptions.UserError('No lines to raise POs for.')

        # Group wizard lines by warehouse, skip zero-qty lines
        lines_by_wh: dict = {}
        for line in self.line_ids:
            if line.qty_to_order > 0:
                lines_by_wh.setdefault(line.warehouse_id, []).append(line)

        if not lines_by_wh:
            raise exceptions.UserError(
                'All order quantities are zero — nothing to raise. '
                'Edit quantities or toggle the quantity mode.'
            )

        # Permission check — must precede all PO creation
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise exceptions.UserError(
                _("You need Purchase User access to raise purchase orders.")
            )

        sg_line = self.shipment_group_line_id
        po_ids = []

        for warehouse, wh_lines in lines_by_wh.items():
            order_lines = []
            for wl in wh_lines:
                # Resolve supplier price from product.supplierinfo if available
                supplierinfo = self.env['product.supplierinfo'].search([
                    ('partner_id', '=', self.supplier_id.id),
                    ('product_tmpl_id', '=', wl.product_id.product_tmpl_id.id),
                ], limit=1)
                if not supplierinfo:
                    raise exceptions.UserError(
                        _("No supplier pricelist entry found for '%s' from '%s'. "
                          "Add a vendor price before raising the PO.") % (
                            wl.product_id.display_name, self.supplier_id.name)
                    )
                price_unit = supplierinfo.price

                order_lines.append((0, 0, {
                    'product_id': wl.product_id.id,
                    'name': wl.product_id.display_name,
                    'product_qty': wl.qty_to_order,
                    'price_unit': price_unit,
                    # Odoo 19: product_uom_id (renamed from product_uom in prior versions).
                    # uom_po_id removed in Odoo 19 — use uom_id (standard UOM).
                    'product_uom_id': wl.product_id.uom_id.id,
                    'date_planned': fields.Date.today(),
                }))

            po_vals = {
                'partner_id': self.supplier_id.id,
                'picking_type_id': warehouse.in_type_id.id,
                'order_line': order_lines,
                'origin': self.run_id.name,
            }
            if sg_line and sg_line.group_id:
                po_vals['shipment_group_id'] = sg_line.group_id.id

            # Duplicate guard — reject if an existing draft PO exists for this run/supplier/warehouse
            existing = self.env['purchase.order'].search([
                ('state', '=', 'draft'),
                ('partner_id', '=', self.supplier_id.id),
                ('picking_type_id', '=', warehouse.in_type_id.id),
                ('origin', '=', self.run_id.name),
            ], limit=1)
            if existing:
                raise exceptions.UserError(
                    _("A draft PO already exists for supplier '%s' at warehouse '%s' "
                      "for this ROQ run (%s). Cancel or delete it before raising a new one.")
                    % (self.supplier_id.name, warehouse.name, self.run_id.name)
                )

            po = self.env['purchase.order'].create(po_vals)
            po_ids.append(po.id)
            _logger.info(
                'ROQ: raised draft PO %s for supplier=%s warehouse=%s (%d lines)',
                po.name, self.supplier_id.name, warehouse.name, len(order_lines),
            )

        # Write back the first PO to the shipment group supplier line
        if sg_line and po_ids:
            sg_line.sudo().write({'purchase_order_id': po_ids[0]})
            # Link all POs to the shipment group if it has a po_ids field
            if sg_line and sg_line.group_id and 'po_ids' in sg_line.group_id._fields:
                sg_line.group_id.sudo().write({'po_ids': [(4, pid) for pid in po_ids]})

        # Show notification and open POs in list view (avoids custom form view fields
        # from uninstalled modules such as custom_purchase_containers).
        po_names = ', '.join(
            self.env['purchase.order'].browse(po_ids).mapped('name')
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', po_ids)],
            'name': f'Draft POs — {self.supplier_id.name}',
            'target': 'current',
            'views': [(False, 'list'), (False, 'form')],
        }


class RoqRaisePoWizardLine(models.TransientModel):
    _name = 'roq.raise.po.wizard.line'
    _description = 'ROQ Raise PO Wizard Line'
    _order = 'warehouse_id, product_id'

    wizard_id = fields.Many2one('roq.raise.po.wizard', required=True, ondelete='cascade')
    forecast_line_id = fields.Many2one('roq.forecast.line', string='Forecast Line', readonly=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True, required=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', readonly=True, required=True)
    qty_containerized = fields.Float(
        string='ROQ Containerized', readonly=True, digits=(10, 3),
        help='Post-container-fitting quantity including A-tier padding.',
    )
    qty_pack_rounded = fields.Float(
        string='ROQ Pack Rounded', readonly=True, digits=(10, 3),
        help='Demand-driven quantity before container fitting.',
    )
    qty_to_order = fields.Float(
        string='Qty to Order', digits=(10, 3),
        help='Final quantity to put on the purchase order. Edit as needed.',
    )
    notes = fields.Char(string='Flags', readonly=True)
