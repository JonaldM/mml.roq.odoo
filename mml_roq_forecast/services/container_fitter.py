"""
Container Fitting Algorithm.

For a given supplier's aggregated ROQ lines:
1. Calculate total CBM.
2. Assign smallest feasible container at >= lcl_threshold_pct utilisation.
3. If below threshold → LCL.
4. If FCL → pad remaining capacity, prioritising A-tier and lowest weeks cover.
5. Exclude SKUs already over max_padding_weeks_cover from padding.

Container capacities (usable CBM):
  20GP:  25.0 CBM
  40GP:  55.0 CBM
  40HQ:  67.5 CBM

Per spec: a single container ships to port; domestic split handled separately.
"""

CONTAINER_SPECS = {
    '20GP': 25.0,
    '40GP': 55.0,
    '40HQ': 67.5,
}

CONTAINER_ORDER = ['20GP', '40GP', '40HQ']  # Smallest first


class ContainerFitter:

    def __init__(self, lcl_threshold_pct=50, max_padding_weeks_cover=26):
        self.lcl_threshold_pct = lcl_threshold_pct / 100.0
        self.max_padding_weeks_cover = max_padding_weeks_cover

    def fit(self, lines):
        """
        lines: list of dicts, each with:
          - product_id: int
          - cbm: float (total CBM for this SKU's ROQ)
          - roq: float (pack-size-rounded ROQ)
          - cbm_per_unit: float
          - tier: str ('A','B','C','D')
          - weeks_cover: float (projected weeks of cover at delivery)

        Returns dict:
          - container_type: str
          - container_cbm: float
          - fill_pct: float
          - total_padding_units: int
          - line_results: list of {product_id, roq_containerized, padding_units}
        """
        # Check for missing CBM data
        if any(line['cbm_per_unit'] <= 0 for line in lines):
            return {
                'container_type': 'unassigned',
                'container_cbm': 0.0,
                'fill_pct': 0.0,
                'total_padding_units': 0,
                'line_results': [
                    {'product_id': l['product_id'], 'roq_containerized': l['roq'], 'padding_units': 0}
                    for l in lines
                ],
            }

        total_cbm = sum(line['cbm'] for line in lines)

        if total_cbm <= 0:
            return self._lcl_result(lines, total_cbm)

        # Find smallest feasible container
        chosen_type = None
        chosen_cbm = None
        overflow_cbm = 0.0
        for ctype in CONTAINER_ORDER:
            cap = CONTAINER_SPECS[ctype]
            if total_cbm <= cap:
                if total_cbm / cap >= self.lcl_threshold_pct:
                    chosen_type = ctype
                    chosen_cbm = cap
                break
        else:
            # Exceeds 40HQ — use largest container (multiple containers not yet supported)
            chosen_type = '40HQ'
            chosen_cbm = CONTAINER_SPECS['40HQ']
            overflow_cbm = total_cbm - chosen_cbm

        if chosen_type is None:
            return self._lcl_result(lines, total_cbm)

        # Calculate remaining capacity for padding
        remaining_cbm = chosen_cbm - total_cbm
        fill_pct = total_cbm / chosen_cbm

        # Allocate padding
        padding_eligible = [
            l for l in lines
            if l['weeks_cover'] < self.max_padding_weeks_cover and l['tier'] != 'D'
        ]
        # Sort: A-tier first, then lowest weeks cover
        tier_rank = {'A': 4, 'B': 3, 'C': 2, 'D': 1}
        padding_eligible.sort(
            key=lambda l: (-tier_rank.get(l['tier'], 0), l['weeks_cover'])
        )

        padding_by_product = {l['product_id']: 0 for l in lines}

        for line in padding_eligible:
            if remaining_cbm <= 0:
                break
            if line['cbm_per_unit'] <= 0:
                continue
            # Pack-size-aligned padding
            pack_size = max(1, int(line.get('pack_size', 1)))
            max_padding_units = int(remaining_cbm / line['cbm_per_unit'])
            max_padding_units = (max_padding_units // pack_size) * pack_size
            if max_padding_units > 0:
                padding_by_product[line['product_id']] = max_padding_units
                remaining_cbm -= max_padding_units * line['cbm_per_unit']

        line_results = []
        total_padding = 0
        for line in lines:
            pad = padding_by_product.get(line['product_id'], 0)
            total_padding += pad
            line_results.append({
                'product_id': line['product_id'],
                'roq_containerized': line['roq'] + pad,
                'padding_units': pad,
            })

        return {
            'container_type': chosen_type,
            'container_cbm': chosen_cbm,
            'fill_pct': fill_pct,
            'overflow_cbm': overflow_cbm,
            'total_padding_units': total_padding,
            'line_results': line_results,
        }

    def _lcl_result(self, lines, total_cbm):
        return {
            'container_type': 'LCL',
            'container_cbm': 0.0,
            'fill_pct': 0.0,
            'total_padding_units': 0,
            'line_results': [
                {'product_id': l['product_id'], 'roq_containerized': l['roq'], 'padding_units': 0}
                for l in lines
            ],
        }
