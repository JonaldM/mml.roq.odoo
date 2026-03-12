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

import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
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
    static components = { ShipmentCalendarRenderer, ShipmentYearRenderer };

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
        });

        onWillStart(() => this._loadRecords());
        onWillUpdateProps(() => this._loadRecords());
    }

    get domain() { return this.props.domain || []; }
    get context() { return this.props.context || {}; }

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
