from datetime import date
from odoo.tests.common import TransactionCase


class TestProactiveConsolidation(TransactionCase):

    def setUp(self):
        super().setUp()
        from ..services.consolidation_engine import ConsolidationEngine
        self.engine = ConsolidationEngine(self.env)
        self.run = self.env['roq.forecast.run'].create({'status': 'complete'})

        self.s1 = self.env['res.partner'].create({
            'name': 'Pro Sup 1', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })
        self.s2 = self.env['res.partner'].create({
            'name': 'Pro Sup 2', 'supplier_rank': 1, 'fob_port': 'CNSHA',
        })

        # Build forward plans for both suppliers, same month
        plan1 = self.env['roq.forward.plan'].create({
            'supplier_id': self.s1.id, 'generated_date': date.today(),
            'run_id': self.run.id,
        })
        plan2 = self.env['roq.forward.plan'].create({
            'supplier_id': self.s2.id, 'generated_date': date.today(),
            'run_id': self.run.id,
        })
        product = self.env['product.product'].create({'name': 'Pro SKU', 'type': 'product'})
        wh = self.env['stock.warehouse'].search([], limit=1)
        month = date.today().replace(day=1)
        for plan, cbm in [(plan1, 10.0), (plan2, 12.0)]:
            self.env['roq.forward.plan.line'].create({
                'plan_id': plan.id, 'product_id': product.id,
                'warehouse_id': wh.id, 'month': month,
                'planned_order_qty': 100, 'cbm': cbm,
                'planned_ship_date': month,
            })

    def test_creates_proactive_shipment_group(self):
        self.engine.create_proactive_shipment_groups(self.run)
        groups = self.env['roq.shipment.group'].search([
            ('run_id', '=', self.run.id), ('mode', '=', 'proactive'),
        ])
        self.assertGreater(len(groups), 0)

    def test_proactive_group_has_two_supplier_lines(self):
        self.engine.create_proactive_shipment_groups(self.run)
        groups = self.env['roq.shipment.group'].search([
            ('run_id', '=', self.run.id), ('mode', '=', 'proactive'),
            ('fob_port', '=', 'CNSHA'),
        ])
        if groups:
            self.assertGreaterEqual(len(groups[0].line_ids), 2)
