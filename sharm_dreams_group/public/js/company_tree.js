// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, please see license.txt

// A Company filter means "this company and everything under it".
//
// `Sharm Dreams Group` is the group parent and holds no data of its own: zero
// employees, zero salary slips, zero invoices. All 13 operating companies hang
// off it. So the stock behaviour - a Company filter is an equality on `company`
// - returns nothing at all for the parent, which makes the one value a manager
// actually wants (the whole group) the one value that shows an empty screen.
//
// This widens a Company filter to the selected company plus its descendants.
// A leaf company has no descendants, so single-company filtering is left
// exactly as it was; only the parent changes, and only from empty to correct.
//
// `frappe.dashboard_utils.get_all_filters` is the one place the desk builds a
// widget's filters - chart_widget.set_chart_filters and
// number_card_widget.get_filters both go through it - so patching it covers
// Dashboard Charts and Number Cards together, in both filter shapes.
//
// The tree is fetched once from company_tree.py. Nothing here changes behaviour
// for a company with no descendants, and no widget currently carries a company
// filter, so the only path that widens is a company a person actually chose.

let COMPANY_TREE = {};

function widen(company) {
	if (typeof company !== "string" || !company) {
		return null;
	}
	const members = COMPANY_TREE[company];
	// Absent from the tree means it is not a company, and a single-member list
	// means a leaf: leave both alone.
	if (!members || members.length < 2) {
		return null;
	}
	return members;
}

function expand_filters(filters) {
	if (!filters) {
		return filters;
	}

	// Report-shaped: {"company": "Sharm Dreams Group"}
	if (!Array.isArray(filters) && typeof filters === "object") {
		const members = widen(filters.company);
		if (!members) {
			return filters;
		}
		// A bare list would be read by the server as the (operator, value) pair's
		// first element, so the operator has to travel with it.
		return { ...filters, company: ["in", members] };
	}

	// Doctype-shaped: [["Employee", "company", "=", "Sharm Dreams Group"], ...]
	if (Array.isArray(filters)) {
		let changed = false;
		const rows = filters.map((row) => {
			if (!Array.isArray(row) || row.length !== 4 || row[1] !== "company") {
				return row;
			}
			const members = widen(row[3]);
			if (!members) {
				return row;
			}
			changed = true;
			return [row[0], row[1], "in", members];
		});
		return changed ? rows : filters;
	}

	return filters;
}

function patch_get_all_filters() {
	const utils = frappe.dashboard_utils;
	if (!utils || typeof utils.get_all_filters !== "function" || utils.get_all_filters._sdg_tree) {
		return;
	}
	const original = utils.get_all_filters;
	const wrapped = function (doc) {
		return expand_filters(original.call(this, doc));
	};
	wrapped._sdg_tree = true;
	utils.get_all_filters = wrapped;
}

function init() {
	patch_get_all_filters();
	frappe.xcall("sharm_dreams_group.company_tree.get_company_tree")
		.then((tree) => {
			COMPANY_TREE = tree || {};
		})
		.catch(() => {
			// No tree means no widening, which is the stock behaviour. Not worth
			// interrupting the desk over.
			COMPANY_TREE = {};
		});
}

// `$(fn)` is jQuery's ready.  It replaces `frappe.dom_ready(...)`, which no longer
// exists: this call threw at load time, so nothing in this file ever ran.
$(init);
