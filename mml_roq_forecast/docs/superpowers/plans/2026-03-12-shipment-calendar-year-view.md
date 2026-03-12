# Shipment Calendar Year View Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default monthly calendar view with a rolling 12-month summary grid, where each cell shows per-state shipment counts and clicking a month drills into the existing day-level view.

**Architecture:** Add `zoomLevel: 'year' | 'month'` state to the existing `ShipmentCalendarController`. A new `ShipmentYearRenderer` component (with child `YearMonthCell`) renders the 4×3 grid when zoom is year. The existing `ShipmentCalendarRenderer` handles month zoom unchanged, gaining only a "← Year" back button. `_loadRecords()` branches on zoom level to fetch either the rolling 12-month window or the existing 3-month window.

**Tech Stack:** OWL (Odoo 19), JavaScript ES modules (`@odoo-module`), QWeb XML templates, SCSS (Odoo bundler).

**Spec:** `docs/superpowers/specs/2026-03-12-shipment-calendar-year-view-design.md`

---

## File Map

| File | Change |
|------|--------|
| `static/src/scss/shipment_calendar.scss` | Add year grid styles at end of OWL section |
| `static/src/js/shipment_calendar_view.js` | Add `YearMonthCell`, `ShipmentYearRenderer` components; update controller |
| `static/src/xml/shipment_calendar.xml` | Add two new templates; update controller + renderer templates |

No Python, no manifest, no model changes.

---

## Chunk 1: SCSS styles + structural test

### Task 1: Add year grid SCSS

**Files:**
- Modify: `static/src/scss/shipment_calendar.scss` (append after line 1031)
- Create: `tests/test_year_view_structure.py`

- [ ] **Step 1: Write a failing structural test**

Create `mml_roq_forecast/tests/test_year_view_structure.py`:

```python
"""Structural tests — verify year view components exist in JS/SCSS/XML."""
import pathlib

ROOT = pathlib.Path(__file__).parent.parent

def test_scss_has_year_grid():
    src = (ROOT / 'static/src/scss/shipment_calendar.scss').read_text()
    assert '.mml-sg-year-grid' in src, "SCSS must define .mml-sg-year-grid"
    assert '.mml-sg-year-cell' in src, "SCSS must define .mml-sg-year-cell"
    assert '.mml-sg-year-state-badge' in src, "SCSS must define .mml-sg-year-state-badge"

def test_js_has_year_components():
    src = (ROOT / 'static/src/js/shipment_calendar_view.js').read_text()
    assert 'YearMonthCell' in src, "JS must define YearMonthCell component"
    assert 'ShipmentYearRenderer' in src, "JS must define ShipmentYearRenderer component"
    assert 'zoomLevel' in src, "JS must include zoomLevel state"
    assert 'onDrillDown' in src, "JS must include onDrillDown method"
    assert 'onBackToYear' in src, "JS must include onBackToYear method"
    assert 'yearOffset' in src, "JS must include yearOffset state"

def test_xml_has_year_templates():
    src = (ROOT / 'static/src/xml/shipment_calendar.xml').read_text()
    assert 'ShipmentYearRenderer' in src, "XML must have ShipmentYearRenderer template"
    assert 'YearMonthCell' in src, "XML must have YearMonthCell template"
    assert 'onBackToYear' in src, "XML must wire onBackToYear"
```

- [ ] **Step 2: Run and confirm all three tests fail**

```bash
cd E:\ClaudeCode\projects\mml.odoo\mml.odoo.apps\mml.roq.model
pytest mml_roq_forecast/tests/test_year_view_structure.py -v
```

Expected: 3 FAILs — `AssertionError` on `.mml-sg-year-grid`, `YearMonthCell`, `ShipmentYearRenderer`.

- [ ] **Step 3: Add SCSS styles**

Append to the end of `static/src/scss/shipment_calendar.scss` (after line 1031, inside the existing file — do NOT replace the file):

```scss

// ═══════════════════════════════════════════════════════════════════════════
// YEAR OVERVIEW GRID
// Rolling 12-month summary — click a cell to drill into day-level view.
// ═══════════════════════════════════════════════════════════════════════════

.mml-sg-year {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--nav-canvas);
}

.mml-sg-year-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  padding: 16px;
  overflow-y: auto;
}

.mml-sg-year-cell {
  background: var(--nav-surface);
  border: 1.5px solid var(--nav-border);
  border-radius: var(--nav-radius-lg);
  padding: 14px 16px;
  cursor: pointer;
  transition: box-shadow 0.12s, border-color 0.12s, transform 0.1s;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 140px;
  box-shadow: var(--nav-shadow);

  &:hover {
    border-color: var(--nav-confirmed);
    box-shadow: var(--nav-shadow-lg);
    transform: translateY(-1px);
  }

  &.mml-sg-year-cell--empty {
    opacity: 0.55;
    background: var(--nav-canvas);
  }
}

.mml-sg-year-cell-header {
  font-family: var(--nav-font-body);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--nav-text-muted);
  border-bottom: 1.5px solid var(--nav-grid);
  padding-bottom: 8px;
}

.mml-sg-year-cell-empty {
  font-family: var(--nav-font-body);
  font-size: 0.8125rem;
  color: var(--nav-text-muted);
  font-style: italic;
  margin-top: 4px;
}

.mml-sg-year-state-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.mml-sg-year-state-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mml-sg-year-state-badge {
  font-family: var(--nav-font-body);
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  padding: 1px 6px;
  border-radius: 2px;
  color: #fff;
  min-width: 72px;
  text-align: center;

  &.mml-sg-state-badge--draft     { background: var(--nav-draft); color: var(--nav-text); }
  &.mml-sg-state-badge--confirmed { background: var(--nav-confirmed); }
  &.mml-sg-state-badge--tendered  { background: var(--nav-tendered); }
  &.mml-sg-state-badge--booked    { background: var(--nav-booked); }
  &.mml-sg-state-badge--delivered { background: var(--nav-delivered); opacity: 0.8; }
  &.mml-sg-state-badge--cancelled { background: #9ca3af; opacity: 0.6; }
}

.mml-sg-year-state-count {
  font-family: var(--nav-font-mono);
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--nav-text);
}

.mml-sg-year-cell-footer {
  font-family: var(--nav-font-body);
  font-size: 0.6875rem;
  color: var(--nav-text-muted);
  border-top: 1px solid var(--nav-grid);
  padding-top: 6px;
  margin-top: auto;
}
```

- [ ] **Step 4: Run the SCSS structural test — expect 1 pass, 2 still fail**

```bash
pytest mml_roq_forecast/tests/test_year_view_structure.py::test_scss_has_year_grid -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd E:\ClaudeCode\projects\mml.odoo\mml.odoo.apps\mml.roq.model
git add mml_roq_forecast/static/src/scss/shipment_calendar.scss \
        mml_roq_forecast/tests/test_year_view_structure.py
git commit -m "feat: add year grid SCSS styles + structural tests"
```

---

## Chunk 2: YearMonthCell and ShipmentYearRenderer components

### Task 2: Add JS components

**Files:**
- Modify: `static/src/js/shipment_calendar_view.js` (insert new components before `ShipmentCalendarRenderer`, around line 187)

- [ ] **Step 1: Add `YearMonthCell` component**

In `shipment_calendar_view.js`, insert the following block immediately before the `// ─── Shipment Calendar Renderer ───` comment (line 187):

```javascript
// ─── Year Month Cell ──────────────────────────────────────────────────────

const STATE_ORDER = ['draft', 'confirmed', 'tendered', 'booked', 'delivered', 'cancelled'];
const STATE_LABELS = {
    draft: 'Draft', confirmed: 'Confirmed', tendered: 'Tendered',
    booked: 'Booked', delivered: 'Delivered', cancelled: 'Cancelled',
};

class YearMonthCell extends Component {
    static template = "mml_roq_forecast.YearMonthCell";
    static props = {
        year: Number,
        month: Number,      // 0-based
        records: Array,
        onDrillDown: Function,
    };

    get label() {
        return new Date(this.props.year, this.props.month, 1)
            .toLocaleDateString("en-NZ", { month: "long", year: "numeric" });
    }

    get stateRows() {
        const counts = {};
        for (const r of this.props.records) {
            counts[r.state] = (counts[r.state] || 0) + 1;
        }
        return STATE_ORDER
            .filter(s => counts[s] > 0)
            .map(s => ({ state: s, count: counts[s], label: STATE_LABELS[s] }));
    }

    get totalCbm() {
        const sum = this.props.records.reduce((s, r) => s + (r.total_cbm || 0), 0);
        return sum > 0 ? sum.toFixed(1) : null;
    }

    get isEmpty() {
        return this.props.records.length === 0;
    }

    onClick() {
        this.props.onDrillDown(this.props.year, this.props.month);
    }
}

// ─── Year Renderer ────────────────────────────────────────────────────────

class ShipmentYearRenderer extends Component {
    static template = "mml_roq_forecast.ShipmentYearRenderer";
    static components = { YearMonthCell };
    static props = {
        records: Array,
        yearOffset: Number,
        onDrillDown: Function,
        onPrevYear: Function,
        onNextYear: Function,
        onToday: Function,
    };

    get months() {
        const now = new Date();
        const base = new Date(now.getFullYear(), now.getMonth() + this.props.yearOffset * 12, 1);
        const result = [];
        for (let i = 0; i < 12; i++) {
            const d = new Date(base.getFullYear(), base.getMonth() + i, 1);
            result.push({ year: d.getFullYear(), month: d.getMonth() });
        }
        return result;
    }

    get rangeLabel() {
        const ms = this.months;
        const fmt = m => new Date(m.year, m.month, 1)
            .toLocaleDateString("en-NZ", { month: "short", year: "numeric" });
        return `${fmt(ms[0])} – ${fmt(ms[11])}`;
    }

    recordsForMonth(year, month) {
        const prefix = `${year}-${String(month + 1).padStart(2, "0")}`;
        return this.props.records.filter(
            r => r.target_delivery_date && r.target_delivery_date.startsWith(prefix)
        );
    }
}
```

- [ ] **Step 2: Run the JS structural test — expect 1 more pass**

```bash
pytest mml_roq_forecast/tests/test_year_view_structure.py::test_js_has_year_components -v
```

Expected: PASS (zoomLevel and onDrillDown/onBackToYear not yet added — this test should now FAIL because those are still missing). If it fails, that's expected — we'll fix it in Task 3.

> Note: The full `test_js_has_year_components` test will pass only after Task 3 updates the controller. Running it here just confirms `YearMonthCell` and `ShipmentYearRenderer` are present.

- [ ] **Step 3: Commit**

```bash
git add mml_roq_forecast/static/src/js/shipment_calendar_view.js
git commit -m "feat: add YearMonthCell and ShipmentYearRenderer OWL components"
```

### Task 3: Add XML templates for year components

**Files:**
- Modify: `static/src/xml/shipment_calendar.xml`

- [ ] **Step 1: Add `ShipmentYearRenderer` and `YearMonthCell` templates**

In `shipment_calendar.xml`, insert the following two templates immediately before the closing `</templates>` tag (line 215):

```xml
  <!-- ─── Year Overview Renderer ───────────────────────────────────────── -->
  <t t-name="mml_roq_forecast.ShipmentYearRenderer">
    <div class="mml-sg-year">
      <div class="mml-sg-cal-header">
        <div class="mml-sg-cal-nav">
          <button class="mml-sg-cal-btn" t-on-click="props.onPrevYear" title="Previous 12 months">
            <span class="fa fa-chevron-left"/>
          </button>
          <button class="mml-sg-cal-btn mml-sg-cal-btn-today" t-on-click="props.onToday">
            Today
          </button>
          <button class="mml-sg-cal-btn" t-on-click="props.onNextYear" title="Next 12 months">
            <span class="fa fa-chevron-right"/>
          </button>
        </div>
        <h2 class="mml-sg-cal-month-label" t-esc="rangeLabel"/>
      </div>
      <div class="mml-sg-year-grid">
        <t t-foreach="months" t-as="m" t-key="m.year + '-' + m.month">
          <YearMonthCell
            year="m.year"
            month="m.month"
            records="recordsForMonth(m.year, m.month)"
            onDrillDown="props.onDrillDown"
          />
        </t>
      </div>
    </div>
  </t>

  <!-- ─── Year Month Cell ───────────────────────────────────────────────── -->
  <t t-name="mml_roq_forecast.YearMonthCell">
    <div
      class="mml-sg-year-cell"
      t-att-class="{ 'mml-sg-year-cell--empty': isEmpty }"
      t-on-click="onClick"
      title="Click to view day-level calendar"
    >
      <div class="mml-sg-year-cell-header" t-esc="label"/>
      <t t-if="isEmpty">
        <span class="mml-sg-year-cell-empty">No shipments</span>
      </t>
      <t t-else="">
        <div class="mml-sg-year-state-rows">
          <t t-foreach="stateRows" t-as="row" t-key="row.state">
            <div class="mml-sg-year-state-row">
              <span
                class="mml-sg-year-state-badge"
                t-att-class="'mml-sg-state-badge--' + row.state"
                t-esc="row.label"
              />
              <span class="mml-sg-year-state-count" t-esc="row.count"/>
            </div>
          </t>
        </div>
        <div class="mml-sg-year-cell-footer">
          <t t-esc="props.records.length"/> shipments
          <t t-if="totalCbm"> · <t t-esc="totalCbm"/> CBM</t>
        </div>
      </t>
    </div>
  </t>
```

- [ ] **Step 2: Commit**

```bash
git add mml_roq_forecast/static/src/xml/shipment_calendar.xml
git commit -m "feat: add ShipmentYearRenderer and YearMonthCell XML templates"
```

---

## Chunk 3: Controller wiring + final template update

### Task 4: Update ShipmentCalendarController

**Files:**
- Modify: `static/src/js/shipment_calendar_view.js` (controller section, lines 284–410)

- [ ] **Step 1: Update `static components` in `ShipmentCalendarController`**

Find (line ~287):
```javascript
    static components = { ShipmentCalendarRenderer };
```

Replace with:
```javascript
    static components = { ShipmentCalendarRenderer, ShipmentYearRenderer };
```

- [ ] **Step 2: Update `setup()` to add `zoomLevel` and `yearOffset`**

Find in `setup()`:
```javascript
        this.state = useState({
            year: now.getFullYear(),
            month: now.getMonth(),
            records: [],
            loading: true,
        });
```

Replace with:
```javascript
        this.state = useState({
            year: now.getFullYear(),
            month: now.getMonth(),
            records: [],
            loading: true,
            zoomLevel: 'year',
            yearOffset: 0,
        });
```

- [ ] **Step 3: Update `_loadRecords()` to branch on zoom level**

Replace the entire `_loadRecords` method (lines ~309–337) with:

```javascript
    async _loadRecords() {
        this.state.loading = true;
        let domain, fields;

        if (this.state.zoomLevel === 'year') {
            const now = new Date();
            const base = new Date(
                now.getFullYear(),
                now.getMonth() + this.state.yearOffset * 12,
                1
            );
            const end = new Date(base.getFullYear(), base.getMonth() + 12, 0);
            domain = [
                ...this.domain,
                ["target_delivery_date", ">=", formatDate(base)],
                ["target_delivery_date", "<=", formatDate(end)],
            ];
            fields = ["name", "state", "target_delivery_date", "total_cbm"];
        } else {
            const viewStart = new Date(this.state.year, this.state.month - 1, 1);
            const viewEnd = new Date(this.state.year, this.state.month + 2, 0);
            domain = [
                ...this.domain,
                ["target_delivery_date", ">=", formatDate(viewStart)],
                ["target_delivery_date", "<=", formatDate(viewEnd)],
            ];
            fields = [
                "name", "state", "origin_port", "container_type",
                "fill_percentage", "destination_warehouse_ids",
                "target_ship_date", "target_delivery_date",
                "freight_eta", "freight_status", "consolidation_suggestion",
            ];
        }

        try {
            const records = await this.orm.searchRead(
                "roq.shipment.group",
                domain,
                fields,
                { context: this.context }
            );
            this.state.records = records;
        } finally {
            this.state.loading = false;
        }
    }
```

- [ ] **Step 4: Add `onDrillDown`, `onBackToYear`, `onPrevYear`, `onNextYear` methods**

Insert the following methods after `onDropRecord` (after line ~362) and before `onOpenRecord`:

```javascript
    onDrillDown(year, month) {
        this.state.zoomLevel = 'month';
        this.state.year = year;
        this.state.month = month;
        this._loadRecords();
    }

    onBackToYear() {
        this.state.zoomLevel = 'year';
        this._loadRecords();
    }

    onPrevYear() {
        this.state.yearOffset -= 1;
        this._loadRecords();
    }

    onNextYear() {
        this.state.yearOffset += 1;
        this._loadRecords();
    }
```

- [ ] **Step 5: Update `onToday()` to reset `yearOffset`**

Find `onToday()`:
```javascript
    onToday() {
        const now = new Date();
        this.state.year = now.getFullYear();
        this.state.month = now.getMonth();
        this._loadRecords();
    }
```

Replace with:
```javascript
    onToday() {
        const now = new Date();
        this.state.year = now.getFullYear();
        this.state.month = now.getMonth();
        this.state.yearOffset = 0;
        this._loadRecords();
    }
```

- [ ] **Step 6: Add `onBackToYear` prop to `ShipmentCalendarRenderer`**

Find in `ShipmentCalendarRenderer`:
```javascript
    static props = {
        records: Array,
        year: Number,
        month: Number,
        onPrevMonth: Function,
        onNextMonth: Function,
        onToday: Function,
        onOpenRecord: Function,
        onDropRecord: Function,
    };
```

Replace with:
```javascript
    static props = {
        records: Array,
        year: Number,
        month: Number,
        onPrevMonth: Function,
        onNextMonth: Function,
        onToday: Function,
        onOpenRecord: Function,
        onDropRecord: Function,
        onBackToYear: Function,
    };
```

- [ ] **Step 7: Run the full JS structural test — expect PASS**

```bash
pytest mml_roq_forecast/tests/test_year_view_structure.py::test_js_has_year_components -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add mml_roq_forecast/static/src/js/shipment_calendar_view.js
git commit -m "feat: update ShipmentCalendarController with year zoom state and methods"
```

### Task 5: Update XML templates to wire everything together

**Files:**
- Modify: `static/src/xml/shipment_calendar.xml`

- [ ] **Step 1: Update `ShipmentCalendarController` template**

Replace the entire `mml_roq_forecast.ShipmentCalendarController` template (lines 5–26):

```xml
  <!-- ─── Controller Root ──────────────────────────────────────────────── -->
  <t t-name="mml_roq_forecast.ShipmentCalendarController">
    <div class="o_action o_view_controller mml-sg-cal-root">
      <t t-if="state.loading">
        <div class="mml-sg-cal-loading">
          <span class="fa fa-anchor fa-spin"/>
          <span>Loading shipments…</span>
        </div>
      </t>
      <t t-elif="state.zoomLevel === 'year'">
        <ShipmentYearRenderer
          records="state.records"
          yearOffset="state.yearOffset"
          onDrillDown="(y, m) => this.onDrillDown(y, m)"
          onPrevYear="() => this.onPrevYear()"
          onNextYear="() => this.onNextYear()"
          onToday="() => this.onToday()"
        />
      </t>
      <t t-else="">
        <ShipmentCalendarRenderer
          records="state.records"
          year="state.year"
          month="state.month"
          onPrevMonth="() => this.onPrevMonth()"
          onNextMonth="() => this.onNextMonth()"
          onToday="() => this.onToday()"
          onOpenRecord="(id) => this.onOpenRecord(id)"
          onDropRecord="(id, dateStr) => this.onDropRecord(id, dateStr)"
          onBackToYear="() => this.onBackToYear()"
        />
      </t>
    </div>
  </t>
```

- [ ] **Step 2: Add "← Year" back button to `ShipmentCalendarRenderer` template**

In the `mml_roq_forecast.ShipmentCalendarRenderer` template, find the nav div (lines 34–44):
```xml
        <div class="mml-sg-cal-nav">
          <button class="mml-sg-cal-btn" t-on-click="props.onPrevMonth" title="Previous month">
            <span class="fa fa-chevron-left"/>
          </button>
          <button class="mml-sg-cal-btn mml-sg-cal-btn-today" t-on-click="props.onToday">
            Today
          </button>
          <button class="mml-sg-cal-btn" t-on-click="props.onNextMonth" title="Next month">
            <span class="fa fa-chevron-right"/>
          </button>
        </div>
```

Replace with:
```xml
        <div class="mml-sg-cal-nav">
          <button class="mml-sg-cal-btn" t-on-click="props.onBackToYear" title="Back to year overview">
            <span class="fa fa-th-large me-1"/>Year
          </button>
          <button class="mml-sg-cal-btn" t-on-click="props.onPrevMonth" title="Previous month">
            <span class="fa fa-chevron-left"/>
          </button>
          <button class="mml-sg-cal-btn mml-sg-cal-btn-today" t-on-click="props.onToday">
            Today
          </button>
          <button class="mml-sg-cal-btn" t-on-click="props.onNextMonth" title="Next month">
            <span class="fa fa-chevron-right"/>
          </button>
        </div>
```

- [ ] **Step 3: Run all structural tests — expect all 3 to pass**

```bash
cd E:\ClaudeCode\projects\mml.odoo\mml.odoo.apps\mml.roq.model
pytest mml_roq_forecast/tests/test_year_view_structure.py -v
```

Expected output:
```
PASSED tests/test_year_view_structure.py::test_scss_has_year_grid
PASSED tests/test_year_view_structure.py::test_js_has_year_components
PASSED tests/test_year_view_structure.py::test_xml_has_year_templates
3 passed
```

- [ ] **Step 4: Run the full module test suite to ensure no regressions**

```bash
pytest mml_roq_forecast/tests/ -m "not odoo_integration" -q
```

Expected: all tests pass, 0 failures.

- [ ] **Step 5: Commit**

```bash
git add mml_roq_forecast/static/src/xml/shipment_calendar.xml
git commit -m "feat: wire year/month zoom in controller and renderer templates"
```

---

## Chunk 4: Browser verification

### Task 6: Verify in Odoo UI

**No code changes — this task is manual browser verification on the Hetzner dev instance (port 8090).**

- [ ] **Step 1: Sync and update module on Hetzner**

```bash
# From Windows — sync mml.roq.model to Hetzner
cd E:\ClaudeCode\projects\mml.odoo\mml.odoo.apps
tar czf /tmp/mml_roq_sync.tar.gz mml.roq.model/
scp /tmp/mml_roq_sync.tar.gz root@100.94.135.90:/tmp/
ssh root@100.94.135.90 "chmod -R 777 /home/deploy/odoo-dev/addons/mml.roq.model 2>/dev/null; rm -rf /home/deploy/odoo-dev/addons/mml.roq.model && tar xzf /tmp/mml_roq_sync.tar.gz -C /home/deploy/odoo-dev/addons/ && echo done"
```

Then restart the dev container to pick up the new static assets:

```bash
ssh root@100.94.135.90 "docker restart mml-dev-odoo && echo restarted"
```

- [ ] **Step 2: Open the shipment calendar**

Navigate to: `http://100.94.135.90:8090/odoo/shipment-calendar` (or via MML Operations menu → Shipment Calendar).

Verify:
- [ ] Landing view is the 12-month rolling grid (not a single month)
- [ ] Range label shows current month → 11 months ahead (e.g. "Mar 2026 – Feb 2027")
- [ ] Month cells with data show state badge rows (coloured label + count)
- [ ] Month cells with no shipments show "No shipments" in muted italic text, still have a light border

- [ ] **Step 3: Verify navigation**

- [ ] Prev/Next arrows shift the 12-month window by 12 months
- [ ] "Today" resets to the current rolling window
- [ ] Clicking a month cell enters the day-level calendar view for that month
- [ ] Month label in day view shows the correct month (e.g. "March 2026")
- [ ] "Year" button in day view header returns to the year grid
- [ ] Existing drag-and-drop still works in day view

- [ ] **Step 4: Edge cases**

- [ ] Empty month cells are clickable (drill in shows empty day calendar — no error)
- [ ] Delivered / cancelled shipments appear in year cell with muted badge (if any exist)

- [ ] **Step 5: Final commit**

If any tweaks were needed during browser verification, commit them:
```bash
git add -p
git commit -m "fix: adjust year view after browser verification"
```

If no changes needed:
```bash
git log --oneline -5  # confirm 4-5 clean commits
```
