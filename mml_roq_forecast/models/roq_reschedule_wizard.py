from odoo import exceptions, fields, models


class RoqRescheduleWizard(models.TransientModel):
    """Consolidation suggestion wizard.

    Shown when a significant date-shift detects nearby same-origin groups.
    Offers the planner the option to consolidate or keep separate.
    """
    _name = 'roq.reschedule.wizard'
    _description = 'Shipment Consolidation Suggestion'

    source_group_id = fields.Many2one(
        'roq.shipment.group',
        string='Moved Shipment',
        readonly=True,
        required=True,
    )
    candidate_group_ids = fields.Many2many(
        'roq.shipment.group',
        string='Nearby Shipments',
        readonly=True,
    )
    summary = fields.Char(
        string='Summary',
        compute='_compute_summary',
    )

    def _compute_summary(self):
        """Build a human-readable consolidation suggestion summary."""
        for rec in self:
            names = ', '.join(rec.candidate_group_ids.mapped('name'))
            rec.summary = (
                f'{names} could be consolidated with '
                f'{rec.source_group_id.name} (same origin port, nearby dates).'
            )

    def action_consolidate(self):
        """Run consolidation engine on source + candidates.
        Requires the roq_consolidation service to be registered (provided by a
        future bridge module). Until then, instructs the planner to consolidate
        manually from the Shipment Groups list view."""
        svc = self.env['mml.registry'].service('roq_consolidation')
        if not svc.available():
            raise exceptions.UserError(
                'Automatic consolidation is not yet available. '
                'To consolidate, open the Shipment Groups list, select the '
                'groups shown below, and merge them manually.'
            )
        all_groups = self.source_group_id | self.candidate_group_ids
        svc.consolidate(all_groups.ids)
        return {'type': 'ir.actions.act_window_close'}

    def action_dismiss(self):
        """Keep groups separate — dismiss without consolidating."""
        return {'type': 'ir.actions.act_window_close'}
