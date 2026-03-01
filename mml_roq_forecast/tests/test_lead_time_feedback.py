import datetime
from odoo.tests.common import TransactionCase


class TestLeadTimeFeedback(TransactionCase):

    def test_supplier_stats_update_from_delivered_bookings(self):
        supplier = self.env['res.partner'].create({
            'name': 'LT Feedback Supplier', 'supplier_rank': 1,
        })
        # freight.booking requires carrier_id (delivery.carrier) and currency_id.
        # transit_days_actual is computed from actual_pickup_date + actual_delivery_date.
        # freight.booking uses state='delivered' (not status='delivered').
        delivery_product = self.env['product.product'].create({
            'name': 'LT Test Delivery Product', 'type': 'service',
        })
        carrier = self.env['delivery.carrier'].create({
            'name': 'LT Test Carrier',
            'delivery_type': 'fixed',
            'product_id': delivery_product.id,
        })
        currency = self.env.company.currency_id
        pickup = datetime.datetime(2026, 1, 1)

        for days in [95, 105]:
            delivery_dt = pickup + datetime.timedelta(days=days)
            booking = self.env['freight.booking'].create({
                'carrier_id': carrier.id,
                'currency_id': currency.id,
                'state': 'delivered',
                'actual_pickup_date': pickup,
                'actual_delivery_date': delivery_dt,
            })
            po = self.env['purchase.order'].create({
                'partner_id': supplier.id,
                'date_order': '2026-01-01',
            })
            booking.po_ids = [(4, po.id)]

        supplier._compute_lead_time_stats()
        self.assertAlmostEqual(supplier.avg_lead_time_actual, 100.0, delta=5)
        self.assertGreater(supplier.lead_time_std_dev, 0)
