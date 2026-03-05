# ROQ & Freight Module — Clean Slate Model Mapping

**Generated:** 01 March 2026
**Approach:** Build from scratch. Existing custom models (`roq.forecast.job`, `purchase.order.container.line`, `freight.tender.wizard`) will be decommissioned. New modules inherit only from standard Odoo 19 models.

---

## Modules to Decommission

| Module | Model | Action |
|---|---|---|
| `roq_forecast` | `roq.forecast.job` | Uninstall. Empty shell, no data to migrate. |
| `custom_purchase_containers` | `purchase.order.container.line` | Uninstall. Replaced by container logic in new module. |
| `freight_tender_email` | `freight.tender.wizard` | Uninstall. Replaced by API-based freight tender model. |

**Pre-build step:** Uninstall these three modules and confirm no other modules depend on them.

---

## Module 1: `mml_roq_forecast`

### Standard Odoo Models — Fields to Add

Extend via `_inherit`. No schema replacement, just additional fields.

#### product.template

| Field | Type | Default | Purpose |
|---|---|---|---|
| `abc_tier` | Selection (A/B/C/D) | — | Current confirmed tier |
| `abc_tier_pending` | Selection (A/B/C/D) | — | Pending reclassification (dampener) |
| `abc_weeks_in_pending` | Integer | 0 | Consecutive weeks in pending tier |
| `abc_tier_override` | Selection (A/B/C/D) | — | Manual floor override |
| `abc_trailing_revenue` | Float | 0 | Trailing 12M revenue (computed, stored) |
| `abc_cumulative_pct` | Float | 0 | Cumulative revenue % |
| `cbm_per_unit` | Float | 0 | CBM per sellable unit |
| `pack_size` | Integer | 1 | Units per carton/pack |
| `is_roq_managed` | Boolean | True | Include in ROQ calculations |

> **Verify:** Do `cbm_per_unit` and `pack_size` already exist under different field names? Also check if `product.packaging` is being used for pack sizes — if so, we read from that instead of adding a new field.

#### res.partner (supplier)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `fob_port` | Char | — | FOB port for consolidation grouping |
| `supplier_lead_time_days` | Integer | — | Override (null = use system default) |
| `supplier_review_interval_days` | Integer | — | Override (null = use system default) |
| `supplier_service_level` | Float | — | Override (null = use system default / ABC tier) |
| `override_expiry_date` | Date | — | Auto-revert overrides after this date |
| `supplier_holiday_periods` | Text | — | JSON or One2many for shutdown periods (e.g., CNY) |
| `avg_lead_time_actual` | Float | — | Rolling avg of actual lead times (computed) |
| `lead_time_std_dev` | Float | — | Std dev of actual lead times (computed) |
| `lead_time_on_time_pct` | Float | — | % deliveries within tolerance (computed) |

> **Verify:** Does `fob_port` already exist from prior DSV work?

#### stock.warehouse

| Field | Type | Default | Purpose |
|---|---|---|---|
| `is_active_for_roq` | Boolean | True | Include this warehouse in ROQ forecast |

> **Note:** Country data available via `warehouse.partner_id.country_id` — no new field needed.

#### purchase.order

| Field | Type | Default | Purpose |
|---|---|---|---|
| `shipment_group_id` | Many2one → `roq.shipment.group` | — | Link to consolidation group |

---

### New Models to Create

#### roq.forecast.run

Weekly (or on-demand) ROQ execution header.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto-generated ref (e.g., `ROQ-2026-W09`) |
| `run_date` | Datetime | When the run was executed |
| `status` | Selection | `draft` / `running` / `complete` / `error` |
| `lookback_weeks` | Integer | Parameter snapshot: lookback used |
| `sma_window_weeks` | Integer | Parameter snapshot: SMA window used |
| `default_lead_time_days` | Integer | Parameter snapshot |
| `default_review_interval_days` | Integer | Parameter snapshot |
| `default_service_level` | Float | Parameter snapshot |
| `enable_moq_enforcement` | Boolean | Parameter snapshot: was MOQ enforcement active for this run |
| `total_skus_processed` | Integer | Computed |
| `total_skus_reorder` | Integer | Computed — SKUs with ROQ > 0 |
| `total_skus_oos_risk` | Integer | Computed — projected inv < 0 |
| `line_ids` | One2many → `roq.forecast.line` | Result lines |
| `notes` | Text | Run log / errors |

> **Rationale for parameter snapshots:** When reviewing a historical run, you need to know what parameters were in effect. Storing them on the run header makes each run self-documenting.

#### roq.forecast.line

Per-SKU per-warehouse result. The core output table.

| Field | Type | Notes |
|---|---|---|
| `run_id` | Many2one → `roq.forecast.run` | Parent run |
| `product_id` | Many2one → `product.product` | SKU |
| `warehouse_id` | Many2one → `stock.warehouse` | Destination warehouse |
| `supplier_id` | Many2one → `res.partner` | Supplier |
| `fob_port` | Char (related) | From supplier |
| `abc_tier` | Selection (A/B/C/D) | Tier at time of run |
| `trailing_12m_revenue` | Float | Revenue at time of run |
| `cumulative_revenue_pct` | Float | |
| `tier_override` | Char | Active override description |
| `soh` | Float | Stock on hand at warehouse |
| `confirmed_po_qty` | Float | Inbound PO qty for this warehouse |
| `inventory_position` | Float | SOH + confirmed PO |
| `avg_weekly_demand` | Float | Historical average |
| `forecasted_weekly_demand` | Float | Model output |
| `forecast_method` | Selection | `sma` / `ewma` / `holt_winters` |
| `forecast_confidence` | Selection | `high` / `medium` / `low` |
| `demand_std_dev` | Float | σ of weekly demand |
| `safety_stock` | Float | Z × σ × √LT |
| `z_score` | Float | From tier / override |
| `lead_time_days` | Integer | Effective LT used |
| `review_interval_days` | Integer | Effective review used |
| `out_level` | Float | Reorder point (s) |
| `order_up_to` | Float | Target level (S) |
| `roq_raw` | Float | max(0, S − inventory position) |
| `roq_pack_rounded` | Float | Rounded to pack size |
| `roq_containerized` | Float | After container fill padding |
| `cbm_per_unit` | Float | From product |
| `cbm_total` | Float | Computed: roq × cbm |
| `pack_size` | Integer | From product |
| `projected_inventory_at_delivery` | Float | Inv position − demand × LT |
| `weeks_of_cover_at_delivery` | Float | Projected inv / weekly demand |
| `container_type` | Selection | Assigned container or LCL |
| `container_fill_pct` | Float | |
| `padding_units` | Float | Excess from container fill |
| `supplier_moq` | Float | Snapshot of `product.supplierinfo.min_qty` at run time (0 if not set) |
| `moq_uplift_qty` | Float | Units added to this warehouse line due to MOQ uplift (0 if none or enforcement disabled) |
| `moq_flag` | Boolean | True if this SKU's supplier aggregate was below MOQ (set regardless of enforcement toggle) |
| `notes` | Char | Flags (missing data, warnings) |

#### roq.abc.history

Audit trail for tier changes (for dampener logic and reporting).

| Field | Type | Notes |
|---|---|---|
| `product_id` | Many2one → `product.template` | |
| `run_id` | Many2one → `roq.forecast.run` | Which run triggered the classification |
| `date` | Date | |
| `tier_calculated` | Selection (A/B/C/D) | Raw calculation before dampener |
| `tier_applied` | Selection (A/B/C/D) | After dampener + overrides |
| `trailing_revenue` | Float | |
| `cumulative_pct` | Float | |
| `override_active` | Char | Description of any active override |

#### roq.shipment.group

Consolidation group — multiple suppliers sharing a shipment.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto ref (e.g., `SG-2026-0042`) |
| `fob_port` | Char | Consolidation key |
| `planned_ship_date` | Date | Target ETD |
| `container_type` | Selection | `20ft` / `40ft` / `40hq` / `lcl` |
| `total_cbm` | Float | Combined CBM |
| `fill_percentage` | Float | |
| `status` | Selection | `draft` / `confirmed` / `tendered` / `booked` / `delivered` / `cancelled` |
| `mode` | Selection | `reactive` / `proactive` | How this group was created |
| `freight_tender_id` | Many2one → `freight.tender` | Nullable — link to freight module |
| `destination_warehouse_ids` | Many2many → `stock.warehouse` | |
| `line_ids` | One2many → `roq.shipment.group.line` | |
| `run_id` | Many2one → `roq.forecast.run` | Which ROQ run generated this |
| `notes` | Text | |

#### roq.shipment.group.line

Per-supplier within a consolidation group.

| Field | Type | Notes |
|---|---|---|
| `group_id` | Many2one → `roq.shipment.group` | Parent |
| `supplier_id` | Many2one → `res.partner` | |
| `purchase_order_id` | Many2one → `purchase.order` | Nullable (proactive mode: PO not yet created) |
| `cbm` | Float | This supplier's CBM contribution |
| `weight_kg` | Float | |
| `push_pull_days` | Integer | +pushed / −pulled / 0 as planned |
| `push_pull_reason` | Char | |
| `oos_risk_flag` | Boolean | Any item at real OOS risk |
| `original_ship_date` | Date | Before push/pull adjustment |
| `product_count` | Integer | Number of SKUs in this supplier's portion |

#### roq.forward.plan

12-month rolling procurement plan per supplier.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto ref |
| `supplier_id` | Many2one → `res.partner` | |
| `fob_port` | Char (related) | |
| `generated_date` | Date | When this plan was generated |
| `run_id` | Many2one → `roq.forecast.run` | Source run |
| `horizon_months` | Integer | Default 12 |
| `total_units` | Float | Computed |
| `total_cbm` | Float | Computed |
| `total_fob_cost` | Float | Computed from supplier pricelist |
| `line_ids` | One2many → `roq.forward.plan.line` | |

#### roq.forward.plan.line

Monthly per-SKU line within forward plan.

| Field | Type | Notes |
|---|---|---|
| `plan_id` | Many2one → `roq.forward.plan` | Parent |
| `product_id` | Many2one → `product.product` | |
| `warehouse_id` | Many2one → `stock.warehouse` | |
| `month` | Date | First of month (e.g., 2026-03-01) |
| `forecasted_monthly_demand` | Float | Per warehouse |
| `planned_order_qty` | Float | Accounting for review cycle |
| `planned_order_date` | Date | When to place PO (ship date − LT) |
| `planned_ship_date` | Date | |
| `cbm` | Float | |
| `fob_unit_cost` | Float | From supplier pricelist |
| `fob_line_cost` | Float | qty × unit cost |
| `consolidation_note` | Char | e.g., "Consolidate with Binzhou" |

---

### Settings: res.config.settings Extension

Use standard Odoo pattern — fields stored in `ir.config_parameter`.

| Config Key | Type | Default |
|---|---|---|
| `roq.default_lead_time_days` | Integer | 100 |
| `roq.default_review_interval_days` | Integer | 30 |
| `roq.default_service_level` | Float | 0.97 |
| `roq.lookback_weeks` | Integer | 156 |
| `roq.sma_window_weeks` | Integer | 52 |
| `roq.min_n_value` | Integer | 8 |
| `roq.abc_band_a_pct` | Integer | 70 |
| `roq.abc_band_b_pct` | Integer | 20 |
| `roq.abc_dampener_weeks` | Integer | 4 |
| `roq.container_lcl_threshold_pct` | Integer | 50 |
| `roq.max_padding_weeks_cover` | Integer | 26 |
| `roq.max_pull_days` | Integer | 30 |
| `roq.enable_moq_enforcement` | Boolean | True |

---

## Module 2: `mml_freight_forwarding`

### New Models to Create

#### freight.tender

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto ref (e.g., `FT-2026-0042`) |
| `shipment_group_id` | Many2one → `roq.shipment.group` | Nullable (manual tenders) |
| `origin_port` | Char | FOB port |
| `destination_port` | Char | NZ port |
| `destination_warehouse_ids` | Many2many → `stock.warehouse` | |
| `container_type` | Selection | `20ft` / `40ft` / `40hq` / `lcl` |
| `total_cbm` | Float | |
| `total_weight_kg` | Float | |
| `target_ship_date` | Date | Requested ETD |
| `target_delivery_date` | Date | Requested ETA |
| `supplier_count` | Integer | |
| `po_ids` | Many2many → `purchase.order` | Linked POs |
| `cargo_description` | Text | |
| `special_requirements` | Text | |
| `status` | Selection | `draft` / `submitted` / `quoted` / `accepted` / `booked` / `cancelled` |
| `dsv_reference` | Char | DSV reference |
| `submitted_at` | Datetime | |
| `quote_ids` | One2many → `freight.quote` | |
| `notes` | Text | |

#### freight.quote

| Field | Type | Notes |
|---|---|---|
| `tender_id` | Many2one → `freight.tender` | |
| `dsv_quote_reference` | Char | |
| `carrier` | Char | Shipping line |
| `vessel_name` | Char | |
| `etd` | Date | |
| `eta` | Date | |
| `transit_days` | Integer | |
| `ocean_freight_cost` | Float | |
| `local_charges_origin` | Float | |
| `local_charges_dest` | Float | |
| `total_cost` | Float | |
| `currency_id` | Many2one → `res.currency` | |
| `cost_per_cbm` | Float | Computed |
| `valid_until` | Date | |
| `is_accepted` | Boolean | |
| `notes` | Text | |

#### freight.booking

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto ref |
| `quote_id` | Many2one → `freight.quote` | |
| `tender_id` | Many2one → `freight.tender` | Denormalised |
| `shipment_group_id` | Many2one → `roq.shipment.group` | Denormalised |
| `dsv_booking_reference` | Char | |
| `carrier` | Char | |
| `vessel_name` | Char | |
| `voyage_number` | Char | |
| `container_number` | Char | |
| `bl_number` | Char | Bill of lading |
| `etd` | Date | Confirmed |
| `eta` | Date | Confirmed |
| `atd` | Date | Actual departure |
| `ata` | Date | Actual arrival |
| `delivered_date` | Date | Actual delivery to warehouse |
| `status` | Selection | `confirmed` / `departed` / `in_transit` / `arrived` / `delivered` / `cancelled` |
| `customs_status` | Selection | `pending` / `cleared` / `held` |
| `total_cost_actual` | Float | Final invoiced |
| `actual_lead_time_days` | Integer | Computed on delivery |
| `lead_time_variance_days` | Integer | Actual − assumed |
| `po_ids` | Many2many → `purchase.order` | |
| `tracking_event_ids` | One2many → `freight.tracking.event` | |
| `notes` | Text | |

#### freight.tracking.event

| Field | Type | Notes |
|---|---|---|
| `booking_id` | Many2one → `freight.booking` | |
| `event_date` | Datetime | |
| `event_type` | Selection | `gate_in` / `loaded` / `departed` / `transhipment` / `arrived` / `customs_cleared` / `delivered` / `other` |
| `location` | Char | Port or location |
| `description` | Text | |
| `dsv_event_code` | Char | Raw code |
| `source` | Selection | `api` / `manual` |

---

## Standard Odoo Models — Read Only (No Schema Changes)

These models are data sources. We query them but don't add fields.

| Model | We Read | For |
|---|---|---|
| `sale.order.line` | `product_id`, `product_uom_qty`, `order_id.date_order`, `order_id.warehouse_id` | Demand history |
| `sale.order` | `date_order`, `warehouse_id`, `state` | Order filtering |
| `stock.quant` | `product_id`, `location_id`, `quantity` | Current SOH per warehouse |
| `stock.location` | `warehouse_id`, location hierarchy | Map quants to warehouses |
| `stock.move` | Alternative demand source (delivery-confirmed) | Validation / comparison |
| `stock.picking` | `picking_type_id`, source warehouse | Warehouse attribution |
| `product.supplierinfo` | `partner_id`, `price`, `currency_id`, `min_qty` | FOB pricing for cash flow |
| `stock.landed.cost` | Historical landed costs | Freight cost estimation |
| `stock.landed.cost.lines` | Per-line cost breakdown | Duty/freight/insurance split |
| `stock.valuation.layer` | Product valuation | Working capital calculations |
| `res.currency` | Exchange rates | Multi-currency FOB pricing |
| `res.country` | Country codes | Warehouse geography |
| `product.category` | Category hierarchy | Potential ABC override grouping |
| `procurement.group` | Native procurement | Coexistence awareness |
| `stock.warehouse.orderpoint` | Native reorder rules | Coexistence — disable for ROQ-managed products |

---

## Dependency Chain

```
mml_freight_forwarding
    depends: [base, purchase, stock]

mml_roq_forecast
    depends: [base, sale, purchase, stock, stock_landed_costs, mml_freight_forwarding]
```

> `mml_freight_forwarding` has no dependency on `mml_roq_forecast`. The ROQ module depends on the freight module (to link shipment groups to tenders). This means the freight module can be built and deployed first / independently.

---

## Pre-Build Checklist

| # | Action | Status |
|---|---|---|
| 1 | Uninstall `roq_forecast` module | ☐ |
| 2 | Uninstall `custom_purchase_containers` module | ☐ |
| 3 | Uninstall `freight_tender_email` module | ☐ |
| 4 | Confirm no other modules depend on the above three | ☐ |
| 5 | Confirm `cbm_per_unit` / `pack_size` don't already exist on `product.template` | ☐ |
| 6 | Confirm `fob_port` doesn't already exist on `res.partner` | ☐ |
| 7 | Identify `tt.model.config` / `tt.predicted.results` — keep or remove? | ☐ |
| 8 | Backup database before uninstalls | ☐ |
