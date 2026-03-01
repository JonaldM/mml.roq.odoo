"""
FOB Port Consolidation Engine — Reactive Mode.

After a ROQ run completes, groups supplier orders by FOB port.
Creates roq.shipment.group records with push/pull analysis.

Reactive mode (Phase 2):
  - Runs post-ROQ, groups existing supplier lines
  - Links to existing POs where they exist

Proactive mode (Phase 3/4 — stub here, implemented in Sprint 4):
  - Driven by 12-month forward plan
  - Creates future shipment groups before POs exist
"""
from collections import defaultdict
from datetime import date, timedelta
from .push_pull import calculate_max_push_days, calculate_max_pull_days, has_oos_risk


class ConsolidationEngine:

    def __init__(self, env):
        self.env = env

    def group_by_fob_port(self, run):
        """
        Returns dict: {fob_port: [{supplier: record, lines: [...]}]}
        Groups lines from a completed ROQ run by FOB port.
        Only includes lines with roq_containerized > 0 and a supplier with fob_port set.
        """
        lines = self.env['roq.forecast.line'].search([
            ('run_id', '=', run.id),
            ('roq_containerized', '>', 0),
            ('supplier_id.fob_port', '!=', False),
        ])

        by_port = defaultdict(lambda: defaultdict(list))
        for line in lines:
            fob = line.supplier_id.fob_port
            sid = line.supplier_id.id
            by_port[fob][sid].append(line)

        # Convert to: {port: [{supplier: record, lines: [...]}]}
        result = {}
        for port, supplier_dict in by_port.items():
            result[port] = [
                {'supplier': self.env['res.partner'].browse(sid), 'lines': supplier_lines}
                for sid, supplier_lines in supplier_dict.items()
            ]
        return result

    def create_reactive_shipment_groups(self, run):
        """
        Creates roq.shipment.group records from a completed ROQ run.
        One group per FOB port (if multiple suppliers share a port).
        Single-supplier ports still get a group — useful for freight tender.
        """
        grouped = self.group_by_fob_port(run)
        warehouses = self.env['stock.warehouse'].search([('is_active_for_roq', '=', True)])

        created = self.env['roq.shipment.group']

        for fob_port, supplier_groups in grouped.items():
            total_cbm = sum(
                sum(line.cbm_total for line in sg['lines'])
                for sg in supplier_groups
            )

            # Determine container type from total CBM
            container_type = self._assign_container_type(total_cbm)

            # Calculate planned ship date (today + average lead time for this port)
            planned_ship_date = self._estimate_ship_date(supplier_groups)

            sg = self.env['roq.shipment.group'].create({
                'origin_port': fob_port,
                'target_ship_date': planned_ship_date,
                'container_type': container_type,
                'total_cbm': total_cbm,
                'fill_percentage': self._fill_pct(total_cbm, container_type),
                'state': 'draft',
                'mode': 'reactive',
                'run_id': run.id,
                'destination_warehouse_ids': [(6, 0, warehouses.ids)],
            })

            # Create per-supplier lines
            for supplier_group in supplier_groups:
                supplier = supplier_group['supplier']
                lines = supplier_group['lines']

                supplier_cbm = sum(line.cbm_total for line in lines)
                supplier_oos = has_oos_risk([{
                    'projected_inventory_at_delivery': l.projected_inventory_at_delivery,
                } for l in lines])

                # Push/pull calculation
                line_data = [{
                    'projected_inventory_at_delivery': l.projected_inventory_at_delivery,
                    'weeks_of_cover_at_delivery': l.weeks_of_cover_at_delivery,
                } for l in lines]
                max_push = calculate_max_push_days(line_data)
                max_pull = calculate_max_pull_days(
                    review_interval_days=int(
                        self.env['ir.config_parameter'].sudo()
                        .get_param('roq.max_pull_days', 30)
                    ),
                )

                self.env['roq.shipment.group.line'].create({
                    'group_id': sg.id,
                    'supplier_id': supplier.id,
                    'cbm': supplier_cbm,
                    'push_pull_days': 0,  # User sets actual push/pull days
                    'push_pull_reason': f'Max push: {max_push}d | Max pull: {max_pull}d',
                    'oos_risk_flag': supplier_oos,
                    'original_ship_date': planned_ship_date,
                    'product_count': len(set(l.product_id.id for l in lines)),
                })

            created |= sg

        return created

    def _assign_container_type(self, total_cbm):
        from .container_fitter import CONTAINER_SPECS, CONTAINER_ORDER
        for ctype in CONTAINER_ORDER:
            if total_cbm <= CONTAINER_SPECS[ctype]:
                return ctype
        return '40HQ'  # Largest available

    def _fill_pct(self, total_cbm, container_type):
        from .container_fitter import CONTAINER_SPECS
        cap = CONTAINER_SPECS.get(container_type, 0)
        return (total_cbm / cap * 100.0) if cap > 0 else 0.0

    def _estimate_ship_date(self, supplier_groups):
        """
        Estimate planned ship date as today + median supplier lead time.
        Defaults to 100 days if no lead time data.
        """
        lead_times = []
        for sg in supplier_groups:
            lt = sg['supplier'].supplier_lead_time_days
            if lt:
                lead_times.append(lt)
        avg_lt = sum(lead_times) / len(lead_times) if lead_times else 100
        return date.today() + timedelta(days=avg_lt)

    def create_proactive_shipment_groups(self, run):
        """
        Creates proactive shipment groups from roq.forward.plan records.
        Groups forward plan lines by FOB port and month.
        No POs exist yet — shipment_group_line.purchase_order_id = False.
        """
        plans = self.env['roq.forward.plan'].search([('run_id', '=', run.id)])
        if not plans:
            return self.env['roq.shipment.group']

        # Group plan lines by (fob_port, month)
        by_port_month = defaultdict(list)
        for plan in plans:
            fob = plan.fob_port or plan.supplier_id.fob_port
            if not fob:
                continue
            for line in plan.line_ids:
                key = (fob, line.month)
                by_port_month[key].append((plan.supplier_id, line))

        created = self.env['roq.shipment.group']
        warehouses = self.env['stock.warehouse'].search([('is_active_for_roq', '=', True)])

        for (fob_port, month), supplier_lines in by_port_month.items():
            # Group by supplier within this month/port
            by_supplier = defaultdict(list)
            for supplier, line in supplier_lines:
                by_supplier[supplier.id].append((supplier, line))

            if not by_supplier:
                continue

            total_cbm = sum(line.cbm for _, line in supplier_lines)
            container_type = self._assign_container_type(total_cbm)

            sg = self.env['roq.shipment.group'].create({
                'origin_port': fob_port,
                'target_ship_date': month,
                'container_type': container_type,
                'total_cbm': total_cbm,
                'fill_percentage': self._fill_pct(total_cbm, container_type),
                'state': 'draft',
                'mode': 'proactive',
                'run_id': run.id,
                'destination_warehouse_ids': [(6, 0, warehouses.ids)],
            })

            for sid, s_lines in by_supplier.items():
                supplier = s_lines[0][0]
                supplier_cbm = sum(line.cbm for _, line in s_lines)
                product_count = len(set(line.product_id.id for _, line in s_lines))

                self.env['roq.shipment.group.line'].create({
                    'group_id': sg.id,
                    'supplier_id': supplier.id,
                    'cbm': supplier_cbm,
                    'push_pull_days': 0,
                    'push_pull_reason': 'Proactive — no OOS data yet',
                    'oos_risk_flag': False,
                    'original_ship_date': month,
                    'product_count': product_count,
                })

            created |= sg

        return created
