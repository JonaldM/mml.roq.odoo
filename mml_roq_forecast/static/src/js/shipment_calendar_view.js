/** @odoo-module **/
/**
 * Shipment Calendar — Custom OWL View
 *
 * Replaces the standard FullCalendar-based diary view with a clean monthly
 * grid showing predicted container arrival dates. Supports:
 *
 *   - Daily granularity only (no hourly time slots)
 *   - Delivery date as primary anchor; freight_eta overrides when live data
 *     is available from carrier
 *   - HTML5 drag-and-drop to reschedule draft/confirmed shipments
 *   - Post-drag consolidation dialog — prompts for port-level recalculation
 *   - Maritime "Navigation Chart" design language
 */

import { Component, useState, useRef, onMounted, onWillUnmount, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/** Parse "YYYY-MM-DD" (or ISO datetime string) to a local Date. */
function parseDate(val) {
    if (!val) return null;
    const s = typeof val === "string" ? val.slice(0, 10) : val;
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
}

/** Format a Date as "YYYY-MM-DD". */
function formatDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

/** Human-readable short date "15 Apr". */
function shortDate(d) {
    return d.toLocaleDateString("en-NZ", { day: "numeric", month: "short" });
}

/** Delta in whole calendar days (positive = later). */
function daysDelta(from, to) {
    return Math.round((to.getTime() - from.getTime()) / 86400000);
}

/**
 * Return the Monday of the ISO week containing `d`, as "YYYY-MM-DD".
 * JS getDay() returns 0=Sun; ISO week starts Monday.
 */
function isoWeekMonday(d) {
    const dt = new Date(d);
    const day = dt.getDay();
    const diff = day === 0 ? -6 : 1 - day;
    dt.setDate(dt.getDate() + diff);
    return formatDate(dt);
}

/** Return the ISO week number (1–53) for a given Date. */
function isoWeekNumber(d) {
    const dt = new Date(d);
    dt.setHours(0, 0, 0, 0);
    dt.setDate(dt.getDate() + 3 - ((dt.getDay() + 6) % 7));
    const week1 = new Date(dt.getFullYear(), 0, 4);
    return (
        1 + Math.round(
            ((dt.getTime() - week1.getTime()) / 86400000 -
                3 + ((week1.getDay() + 6) % 7)) / 7
        )
    );
}

/**
 * Return 52 Monday Date objects for a rolling year.
 * quarterOffset shifts the window in 13-week (quarter) increments.
 * quarterOffset=0 → starts from today's ISO week Monday.
 */
function rollingYearWeeks(quarterOffset) {
    const todayMonday = parseDate(isoWeekMonday(new Date()));
    const start = new Date(todayMonday);
    start.setDate(start.getDate() + quarterOffset * 13 * 7);
    const weeks = [];
    for (let i = 0; i < 52; i++) {
        const d = new Date(start);
        d.setDate(start.getDate() + i * 7);
        weeks.push(d);
    }
    return weeks;
}

/** Build a 6-row × 7-col grid array for a given year/month (month is 0-based). */
function buildMonthGrid(year, month) {
    const firstDay = new Date(year, month, 1);
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    // Monday = 0 offset
    const startOffset = (firstDay.getDay() + 6) % 7;
    const gridStart = new Date(firstDay);
    gridStart.setDate(gridStart.getDate() - startOffset);

    const cells = [];
    for (let i = 0; i < 42; i++) {
        const d = new Date(gridStart);
        d.setDate(gridStart.getDate() + i);
        cells.push({
            date: d,
            dateStr: formatDate(d),
            isCurrentMonth: d.getMonth() === month,
            isToday: d.getTime() === today.getTime(),
        });
    }
    // Group into 6 weeks
    const weeks = [];
    for (let w = 0; w < 6; w++) {
        weeks.push(cells.slice(w * 7, w * 7 + 7));
    }
    return weeks;
}

// ─── Reschedule / Consolidation Dialog ───────────────────────────────────────

class RescheduleDialog extends Component {
    static template = "mml_roq_forecast.RescheduleDialog";
    static components = { Dialog };
    static props = {
        record: Object,
        oldDate: Object,
        newDate: Object,
        candidates: Array,
        close: Function,
    };

    get deltaText() {
        const delta = daysDelta(this.props.oldDate, this.props.newDate);
        const dir = delta > 0 ? "pushed out" : "pulled forward";
        return `${dir} by ${Math.abs(delta)} day${Math.abs(delta) !== 1 ? "s" : ""}`;
    }

    get fromText() { return shortDate(this.props.oldDate); }
    get toText()   { return shortDate(this.props.newDate); }

    onDone() { this.props.close(); }
}

// ─── Shipment Card ────────────────────────────────────────────────────────────

class ShipmentCard extends Component {
    static template = "mml_roq_forecast.ShipmentCard";
    static props = {
        record: Object,
        onDragStart: Function,
        onOpenRecord: Function,
    };

    setup() {
        this.dragging = useState({ value: false });
    }

    get stateLabel() {
        const labels = {
            draft: "Draft", confirmed: "Confirmed", tendered: "Tendered",
            booked: "Booked", delivered: "Delivered", cancelled: "Cancelled",
        };
        return labels[this.props.record.state] || this.props.record.state;
    }

    get isLiveEta() { return !!this.props.record.freight_eta; }

    get isDraggable() {
        return ["draft", "confirmed"].includes(this.props.record.state);
    }

    onDragStart(ev) {
        if (!this.isDraggable) { ev.preventDefault(); return; }
        this.dragging.value = true;
        this.props.onDragStart(ev, this.props.record);
    }

    onDragEnd() {
        this.dragging.value = false;
    }

    onClick() {
        this.props.onOpenRecord(this.props.record.id);
    }
}

// ─── Calendar Day Cell ────────────────────────────────────────────────────────

class CalendarDay extends Component {
    static template = "mml_roq_forecast.CalendarDay";
    static components = { ShipmentCard };
    static props = {
        day: Object,
        records: Array,
        isDropTarget: Boolean,
        draggingRecord: { optional: true },
        onDragStart: Function,
        onDragEnter: Function,
        onDragLeave: Function,
        onDrop: Function,
        onOpenRecord: Function,
    };

    onDragOver(ev) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
    }

    onDragEnter(ev) {
        ev.preventDefault();
        this.props.onDragEnter(this.props.day);
    }

    onDragLeave(ev) {
        // Only fire when leaving the day cell itself, not a child
        if (!ev.currentTarget.contains(ev.relatedTarget)) {
            this.props.onDragLeave();
        }
    }

    onDrop(ev) {
        ev.preventDefault();
        this.props.onDrop(ev, this.props.day);
    }
}

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

// ─── Week Row ──────────────────────────────────────────────────────────────────

class WeekRow extends Component {
    static template = "mml_roq_forecast.WeekRow";
    static components = { ShipmentCard };
    static props = {
        weekMonday: Object,         // JS Date — Monday of this ISO week
        records: Array,
        isCurrentWeek: Boolean,
        loadStatus: String,         // 'green' | 'amber' | 'red' | 'none'
        loadPct: Number,
        draggingRecord: { optional: true },
        onDragStart: Function,
        onOpenRecord: Function,
        onDropRow: Function,        // (weekMonday: Date) => void
    };

    setup() {
        this.dropActive = useState({ value: false });
    }

    get weekNumber() { return isoWeekNumber(this.props.weekMonday); }

    get weekRange() {
        const end = new Date(this.props.weekMonday);
        end.setDate(end.getDate() + 6);
        return `${shortDate(this.props.weekMonday)} – ${shortDate(end)}`;
    }

    get loadBarStyle() {
        const pct = Math.min(this.props.loadPct || 0, 100);
        return pct ? `width: ${pct}%` : '';
    }

    onDragOver(ev) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
    }

    onDragEnter(ev) {
        ev.preventDefault();
        this.dropActive.value = true;
    }

    onDragLeave(ev) {
        if (!ev.currentTarget.contains(ev.relatedTarget)) {
            this.dropActive.value = false;
        }
    }

    onDrop(ev) {
        ev.preventDefault();
        this.dropActive.value = false;
        // All logic (no-op check, ORM write, dialog) lives in the renderer.
        this.props.onDropRow(this.props.weekMonday);
    }
}

// ─── Week Renderer ─────────────────────────────────────────────────────────────

class ShipmentWeekRenderer extends Component {
    static template = "mml_roq_forecast.ShipmentWeekRenderer";
    static components = { WeekRow };
    static props = {
        records: Array,
        quarterLabel: String,
        quarterOffset: Number,
        onPrevQuarter: Function,
        onNextQuarter: Function,
        onToday: Function,
        onOpenRecord: Function,
        onDropRecord: Function,     // controller callback: (id, dateStr) => Promise<candidates[]>
        onBackToYear: Function,
        onSwitchToMonth: Function,
    };

    setup() {
        this.dialogs = useService("dialog");
        this.orm = useService("orm");
        this.drag = useState({ recordId: null, originalAnchorDateStr: null });
        this.weekLoadData = useState({ value: {} });
        this.weekList = useRef("weekList");

        onWillStart(() => this._loadWeekLoads(this.props.records));

        onWillUpdateProps((next) => {
            if (next.records !== this.props.records || next.quarterOffset !== this.props.quarterOffset) {
                this._loadWeekLoads(next.records);
            }
        });

        onMounted(() => {
            // Scroll current week into the visible area on first render
            if (this.weekList.el) {
                const currentRow = this.weekList.el.querySelector('.mml-sg-week-row--current');
                if (currentRow) {
                    currentRow.scrollIntoView({ block: 'center', behavior: 'instant' });
                }
            }
        });
    }

    // ── Helpers ──

    /** Anchor chain used for bucketing: freight_eta → delivery → ship */
    _anchor(rec) {
        return rec.freight_eta
            ? rec.freight_eta.slice(0, 10)
            : (rec.target_delivery_date || rec.target_ship_date);
    }

    // ── Computed ──

    /** 52 Monday Dates for the rolling year window (all preloaded, no lazy loading). */
    get weeks() {
        return rollingYearWeeks(this.props.quarterOffset);
    }

    get draggingRecord() {
        if (!this.drag.recordId) return null;
        return this.props.records.find(r => r.id === this.drag.recordId) || null;
    }

    recordsForWeek(monday) {
        const lo = formatDate(monday);
        const hi = formatDate(new Date(monday.getTime() + 6 * 86400000));
        return this.props.records.filter(r => {
            const a = this._anchor(r);
            return a && a >= lo && a <= hi;
        });
    }

    isCurrentWeek(monday) {
        const today = formatDate(new Date());
        const lo = formatDate(monday);
        const hi = formatDate(new Date(monday.getTime() + 6 * 86400000));
        return today >= lo && today <= hi;
    }

    loadStatusForWeek(monday) {
        return (this.weekLoadData.value[formatDate(monday)] || {}).status || 'none';
    }

    loadPctForWeek(monday) {
        return (this.weekLoadData.value[formatDate(monday)] || {}).pct || 0;
    }

    // ── Drag ──

    onDragStart(ev, record) {
        if (!["draft", "confirmed"].includes(record.state)) {
            ev.preventDefault();
            return;
        }
        this.drag.recordId = record.id;
        this.drag.originalAnchorDateStr = this._anchor(record);
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(record.id));
    }

    async onDropRow(targetMonday) {
        const { recordId, originalAnchorDateStr } = this.drag;
        this.drag.recordId = null;
        this.drag.originalAnchorDateStr = null;

        if (!recordId || !originalAnchorDateStr) return;

        const targetMondayStr = formatDate(targetMonday);

        // Same-week no-op: both anchors resolve to the same Monday → nothing to do.
        if (isoWeekMonday(parseDate(originalAnchorDateStr)) === targetMondayStr) return;

        const record = this.props.records.find(r => r.id === recordId);
        if (!record) return;

        const candidates = await this.props.onDropRecord(recordId, targetMondayStr);

        this.dialogs.add(RescheduleDialog, {
            record,
            oldDate: parseDate(originalAnchorDateStr),
            newDate: parseDate(targetMondayStr),
            candidates: candidates || [],
        });
    }

    // ── Load bar ──

    async _loadWeekLoads(records) {
        const warehouseIds = [...new Set(
            records.flatMap(r => r.destination_warehouse_ids || [])
        )];
        if (!warehouseIds.length) { this.weekLoadData.value = {}; return; }

        const mondayStrs = [...new Set(
            this.weeks.map(w => formatDate(w))
        )];

        const STATUS_ORDER = { none: -1, green: 0, amber: 1, red: 2 };
        const merged = {};

        for (const warehouseId of warehouseIds) {
            const data = await this.orm.call(
                'roq.warehouse.week.load',
                'get_loads_for_weeks',
                [warehouseId, mondayStrs],
            );
            for (const [wk, load] of Object.entries(data)) {
                if (!merged[wk] ||
                    STATUS_ORDER[load.status] > STATUS_ORDER[merged[wk].status]) {
                    merged[wk] = load;
                }
            }
        }

        this.weekLoadData.value = merged;
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
        onSwitchToWeek: Function,
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
        return this.props.records.filter(r => {
            const anchor = r.target_delivery_date || r.target_ship_date;
            return anchor && anchor.startsWith(prefix);
        });
    }
}

// ─── Shipment Calendar Renderer ───────────────────────────────────────────────

class ShipmentCalendarRenderer extends Component {
    static template = "mml_roq_forecast.ShipmentCalendarRenderer";
    static components = { CalendarDay };

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
        onSwitchToWeek: Function,
    };

    setup() {
        this.dialogs = useService("dialog");
        this.drag = useState({
            recordId: null,
            originalDateStr: null,
        });
        this.dropTargetDateStr = useState({ value: null });
    }

    get weeks() { return buildMonthGrid(this.props.year, this.props.month); }

    get draggingRecord() {
        if (!this.drag.recordId) return null;
        return this.props.records.find((r) => r.id === this.drag.recordId) || null;
    }

    get monthLabel() {
        return new Date(this.props.year, this.props.month, 1)
            .toLocaleDateString("en-NZ", { month: "long", year: "numeric" });
    }

    get DAY_HEADERS() {
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    }

    recordsForDay(dateStr) {
        return this.props.records.filter((r) => {
            const anchor = r.freight_eta
                ? r.freight_eta.slice(0, 10)
                : (r.target_delivery_date || r.target_ship_date);
            return anchor === dateStr;
        });
    }

    onDragStart(ev, record) {
        this.drag.recordId = record.id;
        this.drag.originalDateStr = record.freight_eta
            ? record.freight_eta.slice(0, 10)
            : (record.target_delivery_date || record.target_ship_date);
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(record.id));
    }

    onDragEnter(day) {
        this.dropTargetDateStr.value = day.dateStr;
    }

    onDragLeave() {
        this.dropTargetDateStr.value = null;
    }

    async onDrop(ev, day) {
        this.dropTargetDateStr.value = null;
        const { recordId, originalDateStr } = this.drag;
        this.drag.recordId = null;
        this.drag.originalDateStr = null;

        if (!recordId || !originalDateStr) return;
        if (day.dateStr === originalDateStr) return;

        const record = this.props.records.find((r) => r.id === recordId);
        if (!record) return;

        const oldDate = parseDate(originalDateStr);
        const newDate = parseDate(day.dateStr);

        // Delegate write to controller; get back consolidation candidates
        const candidates = await this.props.onDropRecord(recordId, day.dateStr);

        // Show consolidation dialog via dialog service (mounts in portal)
        this.dialogs.add(RescheduleDialog, {
            record,
            oldDate,
            newDate,
            candidates: candidates || [],
        });
    }
}

// ─── Controller (View Root Component) ────────────────────────────────────────

class ShipmentCalendarController extends Component {
    static template = "mml_roq_forecast.ShipmentCalendarController";
    static components = { ShipmentCalendarRenderer, ShipmentYearRenderer, ShipmentWeekRenderer };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const now = new Date();
        this.state = useState({
            year: now.getFullYear(),
            month: now.getMonth(),
            records: [],
            loading: true,
            zoomLevel: 'year',
            yearOffset: 0,
            quarterOffset: 0,
        });

        onWillStart(() => this._loadRecords());
        onWillUpdateProps(() => this._loadRecords());
    }

    get domain() { return this.props.domain || []; }
    get context() { return this.props.context || {}; }

    get weekViewQuarterLabel() {
        const weeks = rollingYearWeeks(this.state.quarterOffset);
        const first = weeks[0];
        const last = weeks[weeks.length - 1];
        const fmt = d => d.toLocaleDateString("en-NZ", { day: "numeric", month: "short", year: "numeric" });
        return `${fmt(first)} – ${fmt(last)}`;
    }

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
                ["target_ship_date", ">=", formatDate(base)],
                ["target_ship_date", "<=", formatDate(end)],
            ];
            fields = ["name", "state", "target_delivery_date", "target_ship_date", "total_cbm"];
        } else if (this.state.zoomLevel === 'week') {
            const weeks = rollingYearWeeks(this.state.quarterOffset);
            const bsStr = formatDate(weeks[0]);
            // Extend end to Sunday of the last week
            const lastWeek = weeks[weeks.length - 1];
            const beStr = formatDate(new Date(lastWeek.getTime() + 6 * 86400000));
            domain = [
                ...this.domain,
                "|",
                "&", ["target_delivery_date", ">=", bsStr], ["target_delivery_date", "<=", beStr],
                "&", ["target_delivery_date", "=", false],
                    "&", ["target_ship_date", ">=", bsStr], ["target_ship_date", "<=", beStr],
            ];
            fields = [
                "name", "state", "origin_port", "container_type",
                "fill_percentage", "destination_warehouse_ids",
                "target_ship_date", "target_delivery_date",
                "freight_eta", "freight_status", "consolidation_suggestion",
                "total_cbm",
            ];
        } else {
            const viewStart = new Date(this.state.year, this.state.month - 1, 1);
            const viewEnd = new Date(this.state.year, this.state.month + 2, 0);
            const vsStr = formatDate(viewStart);
            const veStr = formatDate(viewEnd);
            domain = [
                ...this.domain,
                "|",
                "&", ["target_delivery_date", ">=", vsStr], ["target_delivery_date", "<=", veStr],
                "&", ["target_delivery_date", "=", false], "&", ["target_ship_date", ">=", vsStr], ["target_ship_date", "<=", veStr],
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

    async onDropRecord(recordId, newDateStr) {
        try {
            await this.orm.write("roq.shipment.group", [recordId], {
                target_delivery_date: newDateStr,
            });
        } catch (e) {
            this.notification.add(
                e.data?.message || "Failed to reschedule shipment.",
                { type: "danger" }
            );
            await this._loadRecords();
            return [];
        }

        await this._loadRecords();

        // Resolve consolidation candidates from the updated record's suggestion
        const updated = this.state.records.find((r) => r.id === recordId);
        if (updated && updated.consolidation_suggestion) {
            const suggestedNames = updated.consolidation_suggestion.split(", ").map((s) => s.trim());
            return this.state.records.filter((r) => suggestedNames.includes(r.name));
        }
        return [];
    }

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

    onPrevQuarter() {
        this.state.quarterOffset -= 1;
        this._loadRecords();
    }

    onNextQuarter() {
        this.state.quarterOffset += 1;
        this._loadRecords();
    }

    onSwitchToWeek() {
        this.state.zoomLevel = 'week';
        this.state.quarterOffset = 0;
        this._loadRecords();
    }

    onSwitchToMonth() {
        this.state.zoomLevel = 'month';
        this._loadRecords();
    }

    onOpenRecord(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "roq.shipment.group",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    onPrevMonth() {
        let { year, month } = this.state;
        month -= 1;
        if (month < 0) { month = 11; year -= 1; }
        this.state.year = year;
        this.state.month = month;
        this._loadRecords();
    }

    onNextMonth() {
        let { year, month } = this.state;
        month += 1;
        if (month > 11) { month = 0; year += 1; }
        this.state.year = year;
        this.state.month = month;
        this._loadRecords();
    }

    onToday() {
        const now = new Date();
        this.state.year = now.getFullYear();
        this.state.month = now.getMonth();
        this.state.yearOffset = 0;
        this.state.quarterOffset = 0;
        this.state.zoomLevel = 'week';
        this._loadRecords();
    }
}

// ─── View Registration ────────────────────────────────────────────────────────

export const ShipmentCalendarView = {
    type: "shipment_calendar",
    display_name: "Shipment Calendar",
    icon: "fa fa-anchor",
    multiRecord: true,
    Controller: ShipmentCalendarController,
};

registry.category("views").add("shipment_calendar", ShipmentCalendarView);
