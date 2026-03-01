from odoo import models, fields, api


class RoqForwardPlan(models.Model):
    _name = 'roq.forward.plan'
    _description = '12-Month Forward Procurement Plan'
    _order = 'generated_date desc, supplier_id'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('roq.forward.plan'),
    )
    supplier_id = fields.Many2one('res.partner', required=True, string='Supplier')
    fob_port = fields.Char(string='FOB Port', related='supplier_id.fob_port', store=True)
    generated_date = fields.Date(string='Generated Date', required=True)
    run_id = fields.Many2one('roq.forecast.run', string='Source ROQ Run', ondelete='set null')
    horizon_months = fields.Integer(string='Horizon (Months)', default=12)
    total_units = fields.Float(
        string='Total Units', compute='_compute_totals', store=True, digits=(10, 0),
    )
    total_cbm = fields.Float(
        string='Total CBM', compute='_compute_totals', store=True, digits=(10, 3),
    )
    total_fob_cost = fields.Float(
        string='Total FOB Cost', compute='_compute_totals', store=True, digits=(10, 2),
    )
    line_ids = fields.One2many('roq.forward.plan.line', 'plan_id', string='Plan Lines')

    @api.depends('line_ids.planned_order_qty', 'line_ids.cbm', 'line_ids.fob_line_cost')
    def _compute_totals(self):
        for rec in self:
            rec.total_units = sum(rec.line_ids.mapped('planned_order_qty'))
            rec.total_cbm = sum(rec.line_ids.mapped('cbm'))
            rec.total_fob_cost = sum(rec.line_ids.mapped('fob_line_cost'))


class RoqForwardPlanLine(models.Model):
    _name = 'roq.forward.plan.line'
    _description = 'Forward Plan Line'
    _order = 'plan_id, month, product_id'

    plan_id = fields.Many2one('roq.forward.plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse')
    month = fields.Date(string='Month (1st of Month)', required=True)
    forecasted_monthly_demand = fields.Float(string='Forecast Monthly Demand', digits=(10, 3))
    planned_order_qty = fields.Float(string='Planned Order Qty', digits=(10, 0))
    planned_order_date = fields.Date(string='Order Placement Date')
    planned_ship_date = fields.Date(string='Planned Ship Date')
    cbm = fields.Float(string='CBM', digits=(10, 3))
    fob_unit_cost = fields.Float(string='FOB Unit Cost', digits=(10, 4))
    fob_line_cost = fields.Float(string='FOB Line Cost', digits=(10, 2))
    consolidation_note = fields.Char(string='Consolidation Note')
