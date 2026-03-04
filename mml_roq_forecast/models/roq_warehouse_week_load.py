from datetime import date, timedelta

from odoo import api, models


# TEU equivalents per container type.
# LCL = 0: excluded from TEU load (weight-based billing; CBM still counted).
CONTAINER_TEU = {
    '20GP': 1.0,
    '40GP': 2.0,
    '40HQ': 2.0,
    'LCL': 0.0,
}


class RoqWarehouseWeekLoad(models.AbstractModel):
    """Utility service for computing warehouse inbound receiving load per week.

    Not a stored model. Call get_load() or get_rolling_load() via the ORM
    environment: self.env['roq.warehouse.week.load'].get_load(warehouse_id, week_date)

    Used by the shipment calendar coverage map to show saturation per warehouse.
    """

    _name = 'roq.warehouse.week.load'
    _description = 'Warehouse Weekly Receiving Load (computed service)'

    @api.model
    def get_load(self, warehouse_id, week_date):
        """Return the inbound load for a warehouse in the ISO week containing week_date.

        Args:
            warehouse_id (int): ID of the stock.warehouse record.
            week_date (date): Any date within the target week.

        Returns:
            dict with keys:
                cbm (float): Total CBM of shipments arriving that week.
                teu (float): Total TEU equivalent arriving that week.
                pct (float): Load as % of configured capacity (0.0 if no capacity set).
                status (str): 'green' (<70%), 'amber' (70-90%), 'red' (>=90%), 'none' (no capacity configured).
        """
        warehouse = self.env['stock.warehouse'].browse(warehouse_id)
        week_start = week_date - timedelta(days=week_date.weekday())
        week_end = week_start + timedelta(days=6)

        groups = self.env['roq.shipment.group'].search([
            ('destination_warehouse_ids', 'in', [warehouse_id]),
            ('target_delivery_date', '>=', week_start),
            ('target_delivery_date', '<=', week_end),
            ('state', 'not in', ['cancelled']),
        ])

        total_cbm = sum(g.total_cbm for g in groups)
        total_teu = sum(CONTAINER_TEU.get(g.container_type, 0.0) for g in groups)

        unit = warehouse.roq_capacity_unit
        if unit == 'cbm':
            capacity = warehouse.roq_weekly_capacity_cbm
            load_value = total_cbm
        else:
            capacity = warehouse.roq_weekly_capacity_teu
            load_value = total_teu

        if not capacity:
            pct = 0.0
            status = 'none'
        else:
            pct = (load_value / capacity) * 100
            if pct < 70:
                status = 'green'
            elif pct < 90:
                status = 'amber'
            else:
                status = 'red'

        return {
            'cbm': total_cbm,
            'teu': total_teu,
            'pct': pct,
            'status': status,
        }

    @api.model
    def get_rolling_load(self, warehouse_id, weeks=8):
        """Return load data for a rolling N-week window starting from today.

        Args:
            warehouse_id (int): ID of the stock.warehouse record.
            weeks (int): Number of weeks to include (default 8).

        Returns:
            list of dicts, each containing 'week' (date of Monday) plus
            the keys from get_load(): cbm, teu, pct, status.
        """
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        return [
            {
                'week': week_start + timedelta(weeks=i),
                **self.get_load(warehouse_id, week_start + timedelta(weeks=i)),
            }
            for i in range(weeks)
        ]
