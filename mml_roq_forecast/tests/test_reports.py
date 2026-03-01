from datetime import date
from odoo.tests.common import TransactionCase


class TestSupplierOrderScheduleReport(TransactionCase):

    def test_report_generates_without_error(self):
        supplier = self.env['res.partner'].create({
            'name': 'Report Supplier', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        run = self.env['roq.forecast.run'].create({'status': 'complete'})
        plan = self.env['roq.forward.plan'].create({
            'supplier_id': supplier.id,
            'generated_date': date.today(),
            'run_id': run.id,
            'horizon_months': 3,
        })
        # Render the report — should not raise
        report = self.env.ref('mml_roq_forecast.action_report_supplier_order_schedule')
        html, _ = report._render_qweb_html([plan.id])
        self.assertTrue(html)
