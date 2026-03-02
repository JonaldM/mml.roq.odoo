from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from ..services.settings_helper import SettingsHelper


class TestSettingsHelper(TransactionCase):

    def setUp(self):
        super().setUp()
        self.helper = SettingsHelper(self.env)
        self.supplier = self.env['res.partner'].create({
            'name': 'Test Supplier SH',
            'supplier_rank': 1,
        })

    def test_returns_system_default_when_no_override(self):
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 100)

    def test_supplier_override_replaces_default(self):
        self.supplier.supplier_lead_time_days = 60
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 60)

    def test_expired_override_reverts_to_default(self):
        self.supplier.supplier_lead_time_days = 60
        self.supplier.override_expiry_date = date.today() - timedelta(days=1)
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 100)

    def test_future_expiry_override_is_still_active(self):
        self.supplier.supplier_lead_time_days = 60
        self.supplier.override_expiry_date = date.today() + timedelta(days=30)
        lt = self.helper.get_lead_time_days(self.supplier)
        self.assertEqual(lt, 60)

    def test_free_days_returns_zero_when_not_set(self):
        # Default field value = 0, SettingsHelper returns 0
        result = self.helper.get_free_days_at_origin(self.supplier)
        self.assertEqual(result, 0)

    def test_free_days_returns_supplier_value(self):
        self.supplier.free_days_at_origin = 14
        result = self.helper.get_free_days_at_origin(self.supplier)
        self.assertEqual(result, 14)

    def test_free_days_returns_zero_when_supplier_is_none(self):
        result = self.helper.get_free_days_at_origin(None)
        self.assertEqual(result, 0)
