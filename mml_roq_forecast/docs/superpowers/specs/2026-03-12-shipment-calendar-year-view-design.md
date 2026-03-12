# Shipment Calendar Year View — Design Spec

> **For agentic workers:** implement using superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Add a rolling 12-month summary view as the default entry point to the shipment calendar, with drill-down to the existing day-level month view on click.

**Architecture:** Extend the existing OWL `ShipmentCalendarController` with a `zoomLevel` state variable. No new view type, no Python changes, no model changes.

**Tech Stack:** OWL (Odoo 19), JavaScript ES modules, SCSS, QWeb XML templates.

---

## Context

The existing `shipment_calendar` custom OWL view (`static/src/js/shipment_calendar_view.js`) renders a standard monthly day-grid calendar. It currently defaults to the current month.

**Component hierarchy (existing):**
```
ShipmentCalendarController   ← root, manages state + data fetch
  └── ShipmentCalendarRenderer  ← renders 6-week × 7-day grid
        └── CalendarDay          ← one day cell, drag-drop target
              └── ShipmentCard   ← one shipment record card
```

**New component hierarchy (after this change):**
```
ShipmentCalendarController   ← adds zoomLevel state
  ├── ShipmentYearRenderer   ← NEW: 4×3 month summary grid (year zoom)
  │     └── YearMonthCell    ← NEW: one month cell with state badge rows
  └── ShipmentCalendarRenderer  ← existing day grid (month zoom, unchanged)
```

---

## Design Decisions

### Zoom level
- Default: `'year'`
- States: `'year'` | `'month'`
- Stored in `ShipmentCalendarController` state only — not in URL or server

### Year range
- Rolling 12 months from today: current month through 11 months ahead
- Example (viewed March 2026): March 2026 → February 2027
- Prev/next year arrows shift the window by 12 months
- "Today" resets to the rolling window anchored on the current month

### Data fetching
- **Year zoom**: single RPC fetching `target_delivery_date` >= today's month start, <= 12 months ahead. Fields: `name`, `state`, `target_delivery_date`, `total_cbm`.
- **Month zoom**: existing 3-month window logic, existing field list — unchanged.
- No read_group / server-side aggregation — client filters loaded records by month. At MML scale (~400 SKUs, O(100) shipment groups over 12 months) this is fine.

### Month cell content (Option A — state badge rows)
```
MARCH 2026
[draft]      3
[confirmed]  2
[tendered]   1
[booked]     2
──────────────
8 shipments · 142 CBM
```
- Only states with count > 0 are shown
- `delivered` and `cancelled` states shown if present but visually de-emphasised (muted)
- Empty months: show month label + "No shipments" in muted text, still clickable to drill in
- CBM footer: sum of `total_cbm` for all shipments in that month

### Navigation
**Year zoom header:**
```
← (prev 12m)    Mar 2026 – Feb 2027    (next 12m) →        [Today]
```

**Month zoom header (existing nav + new breadcrumb):**
```
[← Year]    < March 2026 >        [Today]
```

### Drill-down
- Clicking any month cell (including empty ones) sets `zoomLevel = 'month'`, updates `state.year` and `state.month`, calls `_loadRecords()`.
- "← Year" button resets `zoomLevel = 'year'`, calls `_loadRecords()` for the year range.

---

## Files Changed

| File | Type | Change |
|------|------|--------|
| `static/src/js/shipment_calendar_view.js` | Modify | Add `zoomLevel` to state; add `ShipmentYearRenderer` + `YearMonthCell` components; add `onDrillDown`, `onBackToYear`, `onPrevYear`, `onNextYear`; update `_loadRecords` to branch on zoom |
| `static/src/xml/shipment_calendar.xml` | Modify | Add `ShipmentYearRenderer` and `YearMonthCell` templates; update `ShipmentCalendarController` template to conditionally render year vs month view and the correct header |
| `static/src/scss/shipment_calendar.scss` | Modify | Add year grid styles: `.mml-sg-year-grid`, `.mml-sg-year-cell`, `.mml-sg-year-cell-header`, `.mml-sg-year-state-row`, state badge colours, hover/click states, empty cell style |

**No changes to:** Python models, XML view definitions, `__manifest__.py`, security files, or tests (pure-Python service tests are unaffected; OWL component tests follow existing patterns).

---

## State Badge Colours (matching existing ShipmentCard)

| State | Colour |
|-------|--------|
| draft | `#6c757d` (grey) |
| confirmed | `#0d6efd` (blue) |
| tendered | `#fd7e14` (orange) |
| booked | `#198754` (green) |
| delivered | `#adb5bd` (light grey, muted) |
| cancelled | `#adb5bd` (light grey, muted) |

---

## Behaviour Details

### `_loadRecords()` branching
```javascript
// Year zoom: rolling 12 months
const start = new Date(now.getFullYear(), now.getMonth() + yearOffset * 12, 1);
const end = new Date(start.getFullYear(), start.getMonth() + 12, 0);
domain = [...baseDomain,
    ["target_delivery_date", ">=", formatDate(start)],
    ["target_delivery_date", "<=", formatDate(end)],
];
fields = ["name", "state", "target_delivery_date", "total_cbm"];

// Month zoom: unchanged — 3-month window, full field list
```

### `yearOffset` state
`state.yearOffset` (integer, default `0`) tracks prev/next year navigation. `onPrevYear` decrements, `onNextYear` increments, `onToday` resets to `0`.

### `YearMonthCell` props
```javascript
{
    year: Number,
    month: Number,        // 0-based
    records: Array,       // all records for this month (pre-filtered by controller)
    onDrillDown: Function,
}
```

### CBM calculation in cell
```javascript
get totalCbm() {
    return this.props.records.reduce((sum, r) => sum + (r.total_cbm || 0), 0).toFixed(1);
}
```

### Counts by state
```javascript
get stateCounts() {
    const counts = {};
    for (const r of this.props.records) {
        counts[r.state] = (counts[r.state] || 0) + 1;
    }
    return counts;
}
```

---

## Success Criteria

1. Opening the shipment calendar lands on the rolling 12-month grid, not the current month
2. Each month cell shows correct per-state counts and total CBM
3. Clicking a month cell enters the existing day-level view for that month
4. "← Year" button returns to the year grid with state preserved
5. Prev/next year navigation shifts the 12-month window correctly
6. "Today" resets to the current rolling window in both zoom levels
7. Empty months display gracefully (no errors, "No shipments" label)
8. Existing month view drag-and-drop, consolidation dialog, and record open are fully unaffected
