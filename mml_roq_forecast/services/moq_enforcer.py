"""
MOQ Enforcement Service.

Applies after supplier aggregation (Step 6 of ROQ pipeline), before container fitting.
Per-supplier, per-SKU: reads supplier_moq (pre-populated from product.supplierinfo.min_qty).
If the total ROQ across all warehouses for one SKU is below the supplier MOQ, distributes
the uplift to the warehouse with the lowest weeks_of_cover_at_delivery, skipping any already
at or above the cover cap.

enforce=True  — raises quantities, sets moq_uplift_qty and moq_flag on all lines for the SKU
enforce=False — sets moq_flag only; quantities unchanged (scenario / data-loading mode)

Per spec §2.2a:
  - supplier_moq <= 0  → no enforcement (treated as no minimum)
  - moq_flag is set on ALL lines for a SKU when that SKU's total is below MOQ
  - If all warehouses are over the cover cap, uplift still goes to tightest (safety valve)
"""


class MoqEnforcer:

    @staticmethod
    def enforce(lines, enforce=True, max_padding_weeks_cover=26):
        """
        Enforce MOQ for one SKU across multiple warehouse lines.

        lines: list of dicts, each representing one warehouse line for the same SKU+supplier.
            Required keys: warehouse_id, roq_pack_rounded, weeks_of_cover_at_delivery,
                           supplier_moq
        enforce: if False, flag moq_flag but do not change quantities.
        max_padding_weeks_cover: uplift skips warehouses already at or above this threshold.

        Returns: same list (mutated in-place) with moq_uplift_qty and moq_flag populated.
        """
        if not lines:
            return lines

        moq = lines[0].get('supplier_moq', 0.0) or 0.0
        total_roq = sum(line['roq_pack_rounded'] for line in lines)

        # Initialise output fields
        for line in lines:
            line.setdefault('moq_uplift_qty', 0.0)
            line['moq_flag'] = False

        if moq <= 0 or total_roq >= moq:
            return lines  # No enforcement needed

        # Entire SKU is below MOQ — flag all lines regardless of enforce toggle
        for line in lines:
            line['moq_flag'] = True

        if not enforce:
            return lines  # Flag only; quantities unchanged

        uplift_remaining = moq - total_roq

        # Eligible = warehouses below cover cap, sorted by ascending weeks of cover
        eligible = [
            line for line in lines
            if line.get('weeks_of_cover_at_delivery', 0) < max_padding_weeks_cover
        ]
        eligible.sort(key=lambda line: line.get('weeks_of_cover_at_delivery', 0))

        for line in eligible:
            if uplift_remaining <= 0:
                break
            line['moq_uplift_qty'] = line.get('moq_uplift_qty', 0.0) + uplift_remaining
            line['roq_pack_rounded'] += uplift_remaining
            uplift_remaining = 0

        # Safety valve: all warehouses over cap → put uplift on the tightest anyway
        if uplift_remaining > 0 and lines:
            tightest = min(lines, key=lambda line: line.get('weeks_of_cover_at_delivery', 0))
            tightest['moq_uplift_qty'] = tightest.get('moq_uplift_qty', 0.0) + uplift_remaining
            tightest['roq_pack_rounded'] += uplift_remaining

        return lines
