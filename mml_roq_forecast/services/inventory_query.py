"""
Queries current stock positions (SOH + confirmed inbound POs) per SKU per warehouse.

SOH source: stock.quant filtered to internal locations of the target warehouse.
Confirmed PO qty: purchase.order.line in 'purchase' state, destination = warehouse.
"""


class InventoryQueryService:

    def __init__(self, env):
        self.env = env

    def _get_internal_locations(self, warehouse):
        """All internal stock locations belonging to this warehouse."""
        return self.env['stock.location'].search([
            ('warehouse_id', '=', warehouse.id),
            ('usage', '=', 'internal'),
        ])

    def get_soh(self, product, warehouse):
        """
        Stock on hand for product at warehouse (internal locations only).
        Returns float.
        """
        locations = self._get_internal_locations(warehouse)
        if not locations:
            return 0.0
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'in', locations.ids),
        ])
        return sum(quants.mapped('quantity'))

    def get_confirmed_po_qty(self, product, warehouse):
        """
        Quantity on confirmed (not yet received) purchase orders destined for this warehouse.
        Only counts PO lines in 'purchase' or 'done' state where qty remaining > 0.
        """
        dest_locations = self._get_internal_locations(warehouse)
        if not dest_locations:
            return 0.0

        po_lines = self.env['purchase.order.line'].search([
            ('product_id', '=', product.id),
            ('order_id.state', 'in', ['purchase', 'done']),
            ('order_id.dest_address_id', '=', False),  # Standard warehouse delivery
        ])

        # Filter to lines delivering to this warehouse via picking destination
        total = 0.0
        for line in po_lines:
            order_warehouse = line.order_id.picking_type_id.warehouse_id
            if order_warehouse.id == warehouse.id:
                # qty remaining to receive
                received = line.qty_received or 0.0
                ordered = line.product_qty or 0.0
                total += max(0.0, ordered - received)

        return total

    def get_inventory_position(self, product, warehouse):
        """SOH + confirmed inbound PO qty."""
        return self.get_soh(product, warehouse) + self.get_confirmed_po_qty(product, warehouse)
