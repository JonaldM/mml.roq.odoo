"""
(s,S) Periodic Review Inventory Policy — ROQ Calculation.

Reorder Point (s) = Out Level = demand × LT_weeks + safety_stock
Order-Up-To (S)   = demand × (LT_weeks + review_weeks) + safety_stock
ROQ (Raw)          = max(0, S − inventory_position)
"""
import math


def calculate_out_level(weekly_demand, lt_weeks, safety_stock):
    """s = demand × LT_weeks + safety_stock"""
    return weekly_demand * lt_weeks + safety_stock


def calculate_order_up_to(weekly_demand, lt_weeks, review_weeks, safety_stock):
    """S = demand × (LT_weeks + review_weeks) + safety_stock"""
    return weekly_demand * (lt_weeks + review_weeks) + safety_stock


def calculate_roq_raw(order_up_to, inventory_position):
    """ROQ = max(0, S − inventory_position)"""
    return max(0.0, order_up_to - inventory_position)


def round_to_pack_size(roq, pack_size):
    """Round ROQ up to nearest multiple of pack_size. Zero stays zero."""
    if roq <= 0:
        return 0
    pack_size = max(1, int(pack_size))
    return math.ceil(roq / pack_size) * pack_size


def calculate_projected_inventory(inventory_position, weekly_demand, lt_weeks):
    """
    Projected inventory at time of delivery.
    Negative = real OOS risk (not just safety stock breach).
    """
    return inventory_position - (weekly_demand * lt_weeks)


def calculate_weeks_of_cover(projected_inventory, weekly_demand):
    """
    Weeks of cover at time of delivery.
    Returns 999.0 sentinel if weekly_demand is 0 (prevent division by zero).
    """
    if weekly_demand <= 0:
        return 999.0
    return projected_inventory / weekly_demand
