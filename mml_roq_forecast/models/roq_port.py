from odoo import models, fields, api


class RoqPort(models.Model):
    _name = 'roq.port'
    _description = 'Freight Port (UN/LOCODE)'
    _order = 'code'

    code = fields.Char(
        string='UN/LOCODE', size=5, required=True, index=True,
        help='5-character UN/LOCODE, e.g. CNSHA for Shanghai. Case-insensitive on input, stored uppercase.',
    )
    name = fields.Char(string='Port Name', required=True)
    country_id = fields.Many2one('res.country', string='Country')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        'Port UN/LOCODE must be unique.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('code'):
                vals['code'] = vals['code'].upper()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('code'):
            vals['code'] = vals['code'].upper()
        return super().write(vals)

    @api.depends('code', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f'{rec.code} — {rec.name}'
