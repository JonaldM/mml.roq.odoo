from odoo.tests.common import TransactionCase


class TestModelExtensions(TransactionCase):

    def test_product_template_roq_fields(self):
        pt = self.env['product.template']
        for field in ['abc_tier', 'abc_tier_pending', 'abc_weeks_in_pending',
                      'abc_tier_override', 'abc_trailing_revenue',
                      'cbm_per_unit', 'pack_size', 'is_roq_managed']:
            self.assertIn(field, pt._fields, f"Missing field: {field}")

    def test_res_partner_roq_fields(self):
        rp = self.env['res.partner']
        for field in ['fob_port', 'supplier_lead_time_days',
                      'supplier_review_interval_days', 'override_expiry_date']:
            self.assertIn(field, rp._fields, f"Missing field: {field}")

    def test_stock_warehouse_roq_field(self):
        self.assertIn('is_active_for_roq', self.env['stock.warehouse']._fields)

    def test_purchase_order_shipment_group_field(self):
        self.assertIn('shipment_group_id', self.env['purchase.order']._fields)
