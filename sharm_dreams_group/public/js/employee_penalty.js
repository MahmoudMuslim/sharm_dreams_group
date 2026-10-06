// `$(fn)` is jQuery's ready.  It replaces `frappe.dom_ready(...)`, which no longer
// exists: both calls below threw at load time, so no penalty column was ever
// filtered out of the Employee list.
// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, please see license.txt

// Colours the Employee list by penalty level.
//
// The level itself is computed server-side (sharm_dreams_group/hr/penalty.py) and
// stored on the employee as `sdg_penalty_level` / `sdg_penalty_indicator`, so this
// file only has to read it. That is deliberate: the level is a queryable Link, so
// "show me everyone at Critical" and a per-level headcount work without anything
// here, and the colour can never disagree with the number.
//
// Loaded through the app's `app_include_js`, so there is no Client Script record
// to drift out of step with this file.

// ---------------------------------------------------------------------------
// who sees this at all
// ---------------------------------------------------------------------------
// The permitted roles live in `SDG Penalty Settings`; the boot note carries the
// answer the server already worked out, so the desk never has to reimplement the
// rule and cannot disagree with it. Everything below is hidden entirely for a
// user without the permission - no tab, no columns, no tile colour.
//
// This is a courtesy, not the enforcement: the Number Cards, the colour lookup
// and the report are all refused server-side (hr/penalty.py), so clearing the
// browser cache does not expose the rows.
const PENALTY_FIELDS = [
	"sdg_penalty_level",
	"sdg_penalty_points",
	"sdg_penalty_count",
	"sdg_penalty_indicator",
	"sdg_penalty_colour",
	"sdg_penalty_summary",
	"sdg_penalty_tab",
	"sdg_penalty",
];

function can_view_penalties() {
	return !!(frappe.boot && frappe.boot.sdg_can_view_penalties);
}

frappe.ui.form.on("Employee", {
	onload(frm) {
		if (can_view_penalties()) {
			return;
		}
		// Every penalty field, plus the tab itself, is dropped from the layout.
		frm.layout.fields.forEach((field) => {
			if (PENALTY_FIELDS.includes(field.dfname)) {
				frm.layout.remove_field(field.dfname);
			}
		});
	},
});

// The Employee list reads its columns from a shared `List View Settings` record,
// so there is no per-user column setting to lean on and the fields cannot simply
// be turned off. The penalty columns are filtered out of the built list instead.
$(function () {
	const view = frappe.views && frappe.views.ListView;
	if (!view || !view.prototype || view.prototype.setup_columns._sdg) {
		return;
	}
	const original = view.prototype.setup_columns;

	const wrapped = function () {
		original.call(this);
		if (can_view_penalties()) {
			return;
		}
		const before = (this.columns || []).length;
		this.columns = (this.columns || []).filter(
			(c) => !PENALTY_FIELDS.includes(c.fieldname)
		);
		if (before !== this.columns.length) {
			this.setup_columns && this.refresh_columns && this.refresh_columns();
		}
	};
	wrapped._sdg = true;
	view.prototype.setup_columns = wrapped;
});

frappe.ui.form.on("Employee", {
	refresh(frm) {
		if (can_view_penalties()) {
			decorate_penalty_section(frm);
		}
	},
});

// get_indicator is the standard Frappe hook for the coloured strip down the left
// of a list row. It works on the plain list, on group-by, and wherever an
// Employee list is embedded, which a custom column or a field colour would not.
frappe.ui.form.on("Employee", {
	get_indicator(doc) {
		if (!can_view_penalties()) {
			return null;
		}
		const indicator = doc.sdg_penalty_indicator;
		if (!indicator) {
			// No level at all. An employee with penalties but no bands configured
			// is deliberately left unmarked rather than shown as clear, because
			// "clear" is a claim and no rule has been applied to make it.
			return null;
		}
		return indicator;
	},
});

function decorate_penalty_section(frm) {
	const colour = frm.doc.sdg_penalty_colour;
	const tab = "sdg_penalty_tab";
	if (!frm.doc.sdg_penalty_level) {
		return;
	}
	// A coloured edge on the tab's own label, so the level is legible from the tab
	// strip itself without opening the tab. Frappe has no field-level background,
	// so this sets the tab header's style directly.
	const field = frm.layout.fields.find((f) => f.df.fieldname === tab);
	if (field && colour && !field.$wrapper.attr("data-sdg-coloured")) {
		field.$wrapper.attr("data-sdg-coloured", "1");
		const heading = field.$wrapper.find(".form-section-heading, .section-title, .col-sm-12 .clearfix").first();
		if (heading && heading.length) {
			heading.css({ "border-left": `3px solid ${colour}`, "padding-left": "8px" });
		}
	}
}

// ---------------------------------------------------------------------------
// the image view
// ---------------------------------------------------------------------------
// /desk/<doctype>/view/image renders one tile per record and fills each tile's
// bullets from the *list view* columns, so the level already appears there as
// text once the column is on the list.  What it has no way to do is colour a
// tile, so `item_html` is wrapped to put the level's colour on it.
//
// Wrapping rather than replacing: the original builds the tile, and all this adds
// is a border and a badge.  Wrapping is safe here for the same reason it is for
// `get_report_filters` - the hook is opt-in for this one view, and the level's
// own Link field remains the single source of the value.

let LEVEL_COLOURS = {};

function penalty_line(level) {
	const colour = LEVEL_COLOURS[level];
	// The level is written out as well as coloured: a colour alone is not
	// readable by everyone, and a tile is glanceable rather than studied.
	const style = colour ? ` style="color:${colour};font-weight:600"` : "";
	return `<li class="sdg-penalty"${style}>Penalty: ${frappe.utils.escape_html(__(level))}</li>`;
}

// Frappe's item_details_html renders only the FIRST truthy list-view field - the
// loop latches after one - so a tile carries a single line. The penalty is
// therefore inserted as a second line directly under the status, rather than
// appended where it would never be reached.
function wrap_details_html() {
	const view = frappe.views && frappe.views.ImageView;
	// Existence is checked before the `_sdg` marker is read: dereferencing first
	// throws on a view that does not have the method, which is the opposite of
	// what a guard is for.
	if (!view || !view.prototype || !view.prototype.item_details_html) {
		return;
	}
	if (view.prototype.item_details_html._sdg) {
		return;
	}
	const original = view.prototype.item_details_html;

	const wrapped = function (item) {
		const html = original.call(this, item);
		const level = item.sdg_penalty_level;
		if (!level) {
			return html;
		}
		// straight after the first <li>, i.e. under the status
		return html.replace("</li>", `</li>${penalty_line(level)}`);
	};
	wrapped._sdg = true;
	view.prototype.item_details_html = wrapped;
}

// A coloured edge on the tile itself, so the level is visible without reading
// any of the text. Frappe has no per-record hook for this, hence the wrap.
function wrap_item_html() {
	const view = frappe.views && frappe.views.ImageView;
	if (!view || !view.prototype || !view.prototype.item_html) {
		return;
	}
	if (view.prototype.item_html._sdg) {
		return;
	}
	const original = view.prototype.item_html;

	const wrapped = function (item) {
		const html = original.call(this, item);
		const colour = LEVEL_COLOURS[item.sdg_penalty_level];
		if (!colour) {
			return html;
		}
		return html.replace(
			'class="image-view-item ellipsis"',
			`class="image-view-item ellipsis" style="border-left:4px solid ${colour}"`
		);
	};
	wrapped._sdg = true;
	view.prototype.item_html = wrapped;
}

function install_image_view_patches() {
	if (!can_view_penalties()) {
		return;
	}
	wrap_details_html();
	wrap_item_html();
}

$(function () {
	// Installed before the colours arrive so a tile rendered immediately is not
	// left unpatched; LEVEL_COLOURS is read at call time, so the wrappers pick the
	// colours up as soon as they land.
	install_image_view_patches();
	frappe.xcall("sharm_dreams_group.hr.penalty.level_colours")
		.then((colours) => {
			LEVEL_COLOURS = colours || {};
		})
		.catch(() => {
			LEVEL_COLOURS = {};
		});
});
