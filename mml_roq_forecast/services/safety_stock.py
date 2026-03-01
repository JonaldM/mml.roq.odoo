import math


def calculate_safety_stock(z_score, sigma, lt_weeks):
    """
    Safety Stock = Z × σ × √(LT_weeks)

    z_score: float — from ABCD tier (A=1.881, B=1.645, C=1.282, D=0)
    sigma: float — std dev of weekly demand (use fallback if low confidence)
    lt_weeks: float — total lead time in weeks (days / 7)

    Returns: float — safety stock units (never negative)
    """
    if z_score <= 0 or sigma <= 0 or lt_weeks <= 0:
        return 0.0
    return z_score * sigma * math.sqrt(lt_weeks)


Z_SCORES = {
    'A': 1.881,
    'B': 1.645,
    'C': 1.282,
    'D': 0.0,
}


def get_z_score(tier):
    """Returns Z-score for given ABC tier. Tier D = 0 (no safety stock)."""
    return Z_SCORES.get(tier, 0.0)
