from odoo.tests.common import TransactionCase


class TestLeadTimeFeedback(TransactionCase):

    def test_supplier_stats_update_from_delivered_bookings(self):
        supplier = self.env['res.partner'].create({
            'name': 'LT Feedback Supplier', 'supplier_rank': 1,
        })
        # Create two completed bookings with known actual lead times
        for days in [95, 105]:
            booking = self.env['freight.booking'].create({
                'carrier': 'Test Carrier',
                'atd': '2026-01-01',
                'delivered_date': f'2026-{1 + (days // 30):02d}-{(days % 30) + 1:02d}',
                'status': 'delivered',
                'actual_lead_time_days': days,
            })
            po = self.env['purchase.order'].create({
                'partner_id': supplier.id,
                'date_order': '2026-01-01',
            })
            booking.po_ids = [(4, po.id)]

        supplier._compute_lead_time_stats()
        self.assertAlmostEqual(supplier.avg_lead_time_actual, 100.0, delta=5)
        self.assertGreater(supplier.lead_time_std_dev, 0)
