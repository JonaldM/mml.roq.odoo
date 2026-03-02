"""
Push/Pull Tolerance Calculator.

Push (delaying an order) is constrained by OOS risk:
- ANY item at real OOS (projected_inventory < 0) → push = 0 (hard block)
- Minimum weeks cover across all SKUs/warehouses in the order determines push headroom

Pull (bringing an order forward) is constrained by cash flow and supplier readiness:
- Default max pull = review interval (30 days)
- Configurable per-supplier

These rules are intentionally conservative:
- Safety stock breach is acceptable for consolidation timing
- Running to zero is not

Per spec §3.2 table:
  > 12 weeks cover → up to 6 weeks push
  8-12 weeks cover → up to 4 weeks push
  4-8 weeks cover  → up to 2 weeks push
  < 4 weeks cover  → 0 push
"""


def calculate_max_push_days(lines, free_days_at_origin=0):
    """
    lines: list of dicts with 'projected_inventory_at_delivery' and 'weeks_of_cover_at_delivery'
    free_days_at_origin: int — negotiated free storage days at supplier origin. Added to base
        push after tier calc. OOS hard block (projected_inventory < 0) still returns 0 regardless.
    Returns: int — maximum days the order can be delayed for consolidation
    """
    if not lines:
        return 0

    # Hard block: any item at real OOS — free days cannot override this
    if any(line['projected_inventory_at_delivery'] < 0 for line in lines):
        return 0

    # Find tightest item (minimum weeks cover, ignoring the 999 sentinel for zero demand)
    cover_values = [
        line['weeks_of_cover_at_delivery']
        for line in lines
        if line['weeks_of_cover_at_delivery'] < 999.0
    ]
    min_weeks = min(cover_values) if cover_values else 999.0

    if min_weeks > 12:
        base_push = 42   # 6 weeks
    elif min_weeks >= 8:
        base_push = 28   # 4 weeks
    elif min_weeks >= 4:
        base_push = 14   # 2 weeks
    else:
        return 0  # < 4 weeks cover — no push regardless of free days

    return base_push + free_days_at_origin


def calculate_max_pull_days(review_interval_days=30, override=None):
    """
    Returns maximum days an order can be brought forward.

    Default: review_interval_days (system setting or supplier-resolved value).

    Override semantics: REPLACE, not cap. A supplier override completely replaces
    the review interval default — it is not capped to the review interval. If the
    supplier has a longer pull window (e.g. their factory has long confirmed-order
    lead times), the override value is returned as-is.
    """
    if override is not None and override > 0:
        return override
    return review_interval_days


def has_oos_risk(lines):
    """True if any SKU in the supplier's order has projected inventory at delivery < 0."""
    return any(line['projected_inventory_at_delivery'] < 0 for line in lines)
