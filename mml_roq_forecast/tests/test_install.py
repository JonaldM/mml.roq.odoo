from odoo.tests.common import TransactionCase


class TestRoqInstall(TransactionCase):
    def test_roq_forecast_run_model_exists(self):
        self.assertIn('roq.forecast.run', self.env)

    def test_roq_forecast_line_model_exists(self):
        self.assertIn('roq.forecast.line', self.env)

    def test_product_template_has_abc_tier(self):
        self.assertIn('abc_tier', self.env['product.template']._fields)

    def test_res_partner_has_fob_port(self):
        self.assertIn('fob_port', self.env['res.partner']._fields)

    def test_freight_tender_has_shipment_group_id(self):
        self.assertIn('shipment_group_id', self.env['freight.tender']._fields)
