"""
OOS (Out-of-Stock) detection and imputation for demand history preprocessing.

Pure functions — no Odoo ORM dependency. Testable without any Odoo runtime.

detect_oos_weeks: flags zero-demand weeks that are likely stockouts based on
  nearby incoming receipt activity.

impute_oos_demand: replaces OOS zeros with the mean of nearby in-stock weeks.
"""
from datetime import timedelta

_OOS_WINDOW_DAYS = 28  # ±28 days — inclusive boundary


def detect_oos_weeks(weekly_pairs, receipt_dates):
    """
    Identify which zero-demand weeks are likely stockouts.

    weekly_pairs:  list of (week_start: date, qty: float) — oldest first
    receipt_dates: list of date — incoming stock.move receipt dates

    Returns: list[bool] — True = OOS week, same length as weekly_pairs.

    OOS rule: week is True if:
      1. qty == 0.0, AND
      2. any receipt_date satisfies abs((receipt_date - week_start).days) <= 28

    If receipt_dates is empty: all flags False (no replenishment signal,
    assume genuine zero demand — manufactured items, no purchase history).
    """
    if not receipt_dates:
        return [False] * len(weekly_pairs)

    flags = []
    for week_start, qty in weekly_pairs:
        if qty != 0.0:
            flags.append(False)
            continue
        oos = any(
            abs((r - week_start).days) <= _OOS_WINDOW_DAYS
            for r in receipt_dates
        )
        flags.append(oos)

    return flags


def impute_oos_demand(sales, oos_flags):
    """
    Replace OOS zero weeks with estimated demand.

    sales:     list[float] — weekly demand, oldest first
    oos_flags: list[bool]  — True = OOS week (same length as sales)

    Returns: new list[float] with OOS zeros replaced.

    Imputation rule (per OOS week at index i):
      - Collect neighbours at indices max(0, i-4) through min(n-1, i+4),
        excluding i, where oos_flags[j] == False.
      - If >= 2 in-stock neighbours: replace with their mean.
      - If < 2 in-stock neighbours in window: fall back to mean of non-zero
        in-stock values across the full series (oos_flags[j]==False AND sales[j]>0).
      - If no non-zero in-stock values exist: imputed value is 0.0.
      - If entire series is OOS: return unchanged copy.
    """
    n = len(sales)

    # If entire series is OOS, nothing to impute
    if all(oos_flags):
        return list(sales)

    # Precompute global non-zero in-stock mean (fallback for long OOS runs).
    # Uses non-zero in-stock weeks — this represents "demand when product was
    # actually being sold", not weeks where the product had zero genuine demand.
    nonzero_in_stock = [sales[j] for j in range(n) if not oos_flags[j] and sales[j] > 0]
    global_in_stock_mean = sum(nonzero_in_stock) / len(nonzero_in_stock) if nonzero_in_stock else 0.0

    result = list(sales)

    for i in range(n):
        if not oos_flags[i]:
            continue

        # Collect in-stock neighbours within ±4 weeks
        neighbours = [
            sales[j]
            for j in range(max(0, i - 4), min(n, i + 5))
            if j != i and not oos_flags[j]
        ]

        if len(neighbours) >= 2:
            result[i] = sum(neighbours) / len(neighbours)
        else:
            result[i] = global_in_stock_mean

    return result
