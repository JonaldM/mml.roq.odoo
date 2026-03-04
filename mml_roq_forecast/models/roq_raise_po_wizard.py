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
                product_uom = wl.product_id.uom_po_id

                order_lines.append((0, 0, {
                    'product_id': wl.product_id.id,
                    'name': wl.product_id.display_name,
                    'product_qty': wl.qty_to_order,
                    'price_unit': price_unit,
                    # TODO(pre-deploy): verify field names against installed Odoo 19 purchase.order.line
                    # 'product_uom' is the UoM M2O (no _id suffix — Odoo historical naming)
                    # 'date_planned' is the scheduled date field name in Odoo 16–18
                    'product_uom': product_uom.id,
                    'date_planned': fields.Date.today(),
                }))

            po_vals = {
                'partner_id': self.supplier_id.id,
                'picking_type_id': warehouse.in_type_id.id,
                'order_line': order_lines,
            }
            if sg_line and sg_line.group_id:
                po_vals['shipment_group_id'] = sg_line.group_id.id

            po = self.env['purchase.order'].sudo().create(po_vals)
            po_ids.append(po.id)
            _logger.info(
                'ROQ: raised draft PO %s for supplier=%s warehouse=%s (%d lines)',
                po.name, self.supplier_id.name, warehouse.name, len(order_lines),
            )

        # Write back the first PO to the shipment group supplier line
        if sg_line and po_ids:
            sg_line.sudo().write({'purchase_order_id': po_ids[0]})
            # Link all POs to the shipment group if it has a po_ids field
            if sg_line.group_id and hasattr(sg_line.group_id, 'po_ids'):
                sg_line.group_id.sudo().write({'po_ids': [(4, pid) for pid in po_ids]})

        # Return action opening the raised PO(s)
        if len(po_ids) == 1:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'purchase.order',
                'res_id': po_ids[0],
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', po_ids)],
            'name': f'Draft POs — {self.supplier_id.name}',
            'target': 'current',
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
