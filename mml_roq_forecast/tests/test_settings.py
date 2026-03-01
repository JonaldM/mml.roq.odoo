from odoo.tests.common import TransactionCase


class TestRoqSettings(TransactionCase):

    def test_roq_run_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.forecast.run')
        self.assertTrue(ref.startswith('ROQ-'))

    def test_shipment_group_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.shipment.group')
        self.assertTrue(ref.startswith('SG-'))

    def test_forward_plan_sequence_generates_ref(self):
        ref = self.env['ir.sequence'].next_by_code('roq.forward.plan')
        self.assertTrue(ref.startswith('FP-'))

    def test_all_roq_models_have_access_rules(self):
        models_to_check = [
            'roq.forecast.run', 'roq.forecast.line', 'roq.abc.history',
            'roq.shipment.group', 'roq.shipment.group.line',
            'roq.forward.plan', 'roq.forward.plan.line',
        ]
        for model_name in models_to_check:
            access = self.env['ir.model.access'].search([
                ('model_id.model', '=', model_name),
            ])
            self.assertTrue(access, f"No access rules for {model_name}")
