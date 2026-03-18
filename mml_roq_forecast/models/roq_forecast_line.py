from odoo import models, fields

ABC_TIERS = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D (Dormant)')]
FORECAST_METHODS = [
    ('sma', 'SMA'),
    ('ewma', 'EWMA'),
    ('holt_winters', 'Holt-Winters'),
    ('croston', 'Croston/SBA'),
]
FORECAST_CONFIDENCE = [
    ('high', 'High'),
    ('medium', 'Medium'),
    ('low', 'Low (< MIN_N data points)'),
]
CONTAINER_TYPES = [
    ('20GP', "20' Standard"),
    ('40GP', "40' Standard"),
    ('40HQ', "40' High Cube"),
    ('LCL', 'LCL'),
    ('unassigned', 'Unassigned (missing CBM/pack size)'),
]


class RoqForecastLine(models.Model):
    _name = 'roq.forecast.line'
    _description = 'ROQ Forecast Line'
    _order = 'run_id desc, supplier_id, product_id'
    _rec_name = 'product_id'

    run_id = fields.Many2one('roq.forecast.run', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, string='Product')
    warehouse_id = fields.Many2one('stock.warehouse', required=True, string='Warehouse')
    supplier_id = fields.Many2one('res.partner', string='Supplier')
    fob_port = fields.Char(string='FOB Port', related='supplier_id.fob_port', store=True)

    # ABCD
    abc_tier = fields.Selection(ABC_TIERS, string='Tier')
    trailing_12m_revenue = fields.Float(string='Trailing 12M Revenue', digits=(10, 2))
    cumulative_revenue_pct = fields.Float(string='Cumulative %', digits=(6, 2))
    tier_override = fields.Char(string='Override Active')

    # Inventory position
    soh = fields.Float(string='SOH', digits=(10, 3))
    confirmed_po_qty = fields.Float(string='Confirmed PO Qty', digits=(10, 3))
    inventory_position = fields.Float(string='Inventory Position', digits=(10, 3))

    # Forecast
    avg_weekly_demand = fields.Float(string='Avg Weekly Demand', digits=(10, 3))
    forecasted_weekly_demand = fields.Float(string='Forecast Weekly Demand', digits=(10, 3))
    forecast_method = fields.Selection(FORECAST_METHODS, string='Forecast Method')
    forecast_confidence = fields.Selection(FORECAST_CONFIDENCE, string='Confidence')
    demand_std_dev = fields.Float(string='Demand Std Dev (σ)', digits=(10, 3))

    # Safety stock
    safety_stock = fields.Float(string='Safety Stock', digits=(10, 3))
    z_score = fields.Float(string='Z-Score', digits=(6, 3))
    lead_time_days = fields.Integer(string='Lead Time (Days)')
    review_interval_days = fields.Integer(string='Review Interval (Days)')

    # ROQ
    out_level = fields.Float(string='Out Level (s)', digits=(10, 3))
    order_up_to = fields.Float(string='Order-Up-To (S)', digits=(10, 3))
    roq_raw = fields.Float(string='ROQ (Raw)', digits=(10, 3))
    roq_pack_rounded = fields.Float(string='ROQ (Pack Rounded)', digits=(10, 3))
    roq_containerized = fields.Float(string='ROQ (Containerized)', digits=(10, 3))

    # Container
    cbm_per_unit = fields.Float(string='CBM/Unit', digits=(10, 4))
    cbm_total = fields.Float(string='CBM Total', digits=(10, 3))
    pack_size = fields.Integer(string='Pack Size')
    container_type = fields.Selection(CONTAINER_TYPES, string='Container')
    container_fill_pct = fields.Float(string='Fill %', digits=(5, 1))
    padding_units = fields.Float(string='Padding Units', digits=(10, 3))

    # Urgency
    projected_inventory_at_delivery = fields.Float(
        string='Projected Inv at Delivery', digits=(10, 3),
    )
    weeks_of_cover_at_delivery = fields.Float(
        string='Weeks Cover at Delivery', digits=(6, 1),
    )

    # MOQ
    supplier_moq = fields.Float(
        string='Supplier MOQ', default=0.0,
        help='Snapshot of product.supplierinfo.min_qty at run time. 0 = not set.',
    )
    moq_uplift_qty = fields.Float(
        string='MOQ Uplift Units', default=0.0,
        help='Units added to this warehouse line due to MOQ uplift.',
    )
    moq_flag = fields.Boolean(
        string='Below MOQ',
        help="True if this SKU's supplier aggregate was below MOQ before uplift. "
             "Set regardless of enforcement toggle.",
    )

    notes = fields.Char(string='Flags / Warnings')
