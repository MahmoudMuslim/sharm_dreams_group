// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// Lets a side-panel section contain sub-sections.
//
// `sidebar.js` -> `find_nested_items()` only understands one level: it walks the
// flat boot list and pushes every `child` row into the last Section Break it saw.
// A Section Break marked `child` therefore gets hoisted to the top level as its own
// section, and the rows that followed it attach to the *previous* section instead of
// to it - so a nested section silently misfiles its contents.
//
// This re-walks the same flat list and nests properly: a child Section Break becomes
// a section inside the one above it, and the rows after it belong to that inner
// section.  Nothing is re-fetched, and no stock file is edited.
//
// The template already renders a section header as a child when `indent == 1`
// (sidebar_item.html), so a nested section needs `indent` as well as `child`.

frappe.provide("frappe.ui.sidebar");

const NESTING = "sharm_dreams_group_nested_sections";

/**
 * Nest a flat sidebar item list, allowing sections inside sections.
 *
 * The parent and the owner are kept apart, and keeping them apart is the whole
 * function.
 *
 * `owner` is the section whose `nested_items` the rows that follow are filed under.
 * `parent` is the top-level section a sub-section belongs to. They are the same
 * object until the first sub-section, and after that they are not: a sub-section
 * owns its own rows, but the *next* sub-section is a sibling of it, not a child of
 * it.
 *
 * With one variable they collapse, and the panel comes out as a staircase rather
 * than a section with four groups in it - which is exactly what it did:
 *
 *     Reports
 *       People & Headcount
 *         Employee Analytics
 *         Payroll & Tax            <- inside People & Headcount
 *           Salary Register
 *           Attendance & Leave      <- inside Payroll & Tax
 *           Money & Loans           <- inside Attendance & Leave
 *
 * Every group but the first ended up inside the group above it, and the panel read
 * as a single 32-deep list because that is what it was. Nothing raised: the rows are
 * all still present and still nested under *something*.
 */
function nest(items) {
	const rows = (items || []).map((item) => Object.assign({}, item, { nested_items: [] }));
	const top = [];
	let owner = null;
	let parent = null;

	for (const item of rows) {
		const is_section = item.type === "Section Break";

		if (is_section && item.child && parent) {
			// A sub-section: a child of the top-level section above it, and the owner
			// of whatever follows. `parent` is deliberately left alone.
			item.indent = 1;
			parent.nested_items.push(item);
			owner = item;
		} else if (is_section) {
			item.indent = item.indent || 0;
			top.push(item);
			owner = item;
			parent = item;
		} else if (item.child && owner) {
			owner.nested_items.push(item);
		} else {
			// a top-level link: it ends the section, so anything after it must not be
			// filed under it
			top.push(item);
			owner = null;
			parent = null;
		}
	}
	return top;
}

/**
 * Re-assert `keep_closed` after the desk has finished building the panel.
 *
 * `keep_closed` is read exactly once, in `enable_collapsible()`, and it is the
 * *initial* state rather than a policy: two stock paths overwrite it afterwards,
 * and either one is enough to leave a section standing open.
 *
 *   - `apply_section_break_state()` (sidebar_item.js) restores whatever the user
 *     last clicked from the `section-breaks-state` localStorage key, so a section
 *     expanded once stays open on every later page load.
 *   - `expand_parent_section()` (sidebar.js) opens the section holding the active
 *     route, which is reasonable - but it fires on every render, so it also
 *     re-opens a section whose parent never got opened.
 *
 * Neither is a bug in the desk, and neither can be reached from the sidebar
 * document, which is why this has to happen here.  `create_sidebar` is the seam:
 * it builds every item, so `enable_collapsible()` has already applied both
 * overrides by the time we get control, and `set_active_workspace_item()` has
 * not yet run - which is the order we want, because a user who followed a link
 * into a section should still see where they landed.
 *
 * `sidebar.items` is never cleared, so only the entries added by this render are
 * touched.  The walk is iterative rather than `items[].items[]` because this app's
 * sub-sections are themselves collapsible, and a recursive close would be simpler
 * only until someone nests a third level.
 */
frappe.ui.Sidebar.prototype.sharm_close_kept_closed_sections = function (first) {
	const sections = this.items
		.slice(first)
		.filter((entry) => entry && entry.item && entry.item.type === "Section Break");

	for (let i = 0; i < sections.length; i++) {
		const section = sections[i];
		if (section.item.keep_closed && !section.collapsed) {
			section.close();
		}
		// a sub-section renders inside its parent's `.nested-container`, which
		// `close()` has just hidden.  Closing the child as well keeps the two in
		// step, so expanding the parent does not reveal an already-open sub-tree.
		for (const nested of section.items || []) {
			sharm_close_kept_closed_one(nested);
		}
	}
};

function sharm_close_kept_closed_one(section) {
	if (!section || !section.item || section.item.type !== "Section Break") return;
	if (section.item.keep_closed && !section.collapsed) {
		section.close();
	}
	for (const nested of section.items || []) {
		sharm_close_kept_closed_one(nested);
	}
}

const stock_create_sidebar = frappe.ui.Sidebar.prototype.create_sidebar;

/**
 * Everything that has to happen once the panel is in the DOM.
 *
 * `create_sidebar` is the seam, not `sidebar_setup`: `setup()` triggers
 * `sidebar_setup` on its first line, before it assigns `workspace_title`, so a
 * handler bound to that event reads `undefined` and gives up.  Every fix below
 * needs the resolved workspace title to find the boot data, which is why they
 * were silently doing nothing on the first load of the desk and appearing to work
 * only after a route change.  By the time `create_sidebar` runs, `workspace_title`
 * is set and `prepare()` has resolved this panel's items.
 */
frappe.ui.Sidebar.prototype.create_sidebar = function () {
	const first = this.items.length;
	const made = stock_create_sidebar.apply(this, arguments);
	this.sharm_close_kept_closed_sections(first);
	this.draw_section_icons();
	this.open_internal_urls_in_this_tab();
	return made;
};

// Give a section header an icon.
//
// A `Workspace Sidebar Item` stores an icon on a Section Break, and the desk stores
// it faithfully - and then never draws it. `sidebar_item.html` renders the icon span
// only in the `else` branch, for links:
//
//     {% if (item.type == "Section Break" && item.indent != 1) { %}
//         <div class="item-anchor section-break">
//             <span class="sidebar-item-label">{{ item.label }}</span>
//     ...
//     {% } else { %}
//             {% let icon = item.icon %}<span class="sidebar-item-icon" ...>
//
// So every section in this panel has had a stored icon that rendered as nothing, and
// no DocType setting can change that. Injecting the span here keeps the fix in our own
// file rather than a patched stock template, and it means one row of markup instead of
// an icon per link.
frappe.ui.Sidebar.prototype.draw_section_icons = function () {
	// Read the icons off the rows this panel actually rendered, not off boot: boot
	// is keyed by workspace title and names whichever panel the route resolves to,
	// which is not necessarily the one on screen.
	// `this.items` is every row this render added, each an instance carrying its
	// own `item` - so the icon comes from the same object the anchor was built
	// from, and a row with no icon is skipped rather than guessed at.
	for (const entry of this.items) {
		if (!entry || !entry.item || entry.item.type !== "Section Break") continue;
		const icon = entry.item.icon;
		if (!icon) continue;

		const $anchor = entry.wrapper && entry.wrapper.find(".item-anchor.section-break").first();
		if (!$anchor || !$anchor.length) continue;
		// idempotent: the panel is rebuilt on every route
		if ($anchor.find(".sidebar-item-icon").length) continue;

		const $icon = $('<span class="sidebar-item-icon text-ink-gray-7"></span>');
		$icon.attr("item-icon", icon);
		$icon.html(frappe.utils.icon(icon, "sm", "", "", "text-ink-gray-7 current-color", true));
		$anchor.prepend($icon);
	}
};

/**
 * Re-nest the flat boot list into sections-inside-sections.
 *
 * Wrapped onto `prepare` rather than run from the `sidebar_setup` event, for the
 * ordering reason given on `create_sidebar` above.  It also has to run *before*
 * `make_sidebar`, which is what renders the list - stock `find_nested_items()`
 * would otherwise have flattened a child Section Break into its own top-level
 * section and filed the rows after it under the wrong parent.
 *
 * `sidebar_data.items` is the boot list, still flat and in document order;
 * `workspace_sidebar_items` has by now been rewritten by `find_nested_items()`,
 * so it is `sidebar_data` that gets re-read rather than the stock output.
 */
const stock_prepare = frappe.ui.Sidebar.prototype.prepare;

frappe.ui.Sidebar.prototype.prepare = function () {
	const done = stock_prepare.apply(this, arguments);

	// editor mode edits a copy, and `create_sidebar` renders that copy instead of
	// the boot list - re-nesting it would discard the edit in progress
	if (!this.editor || !this.editor.edit_mode) {
		const flat = this.sidebar_data && this.sidebar_data.items;
		// only re-nest when there is actually a sub-section, so the common case
		// stays a no-op and the stock one-level behaviour is untouched
		if (flat && flat.some((item) => item.type === "Section Break" && item.child)) {
			this.workspace_sidebar_items = nest(flat);
		}
	}
	return done;
};

// The desk hardcodes target="_blank" on every URL sidebar link (sidebar_item.html).
// That is right for an external site and wrong for one of our own: clicking Documents
// should move you into Drive, not leave a second copy of the desk behind.
//
// A URL that is a path on this site is internal, so it loses the target. There is no
// flag to opt out with: `boot.get_sidebar_items` copies a fixed field list, so anything
// stored on the sidebar row is dropped before the browser sees it.
frappe.ui.Sidebar.prototype.open_internal_urls_in_this_tab = function () {
	const data = (frappe.boot.workspace_sidebar_item || {})[this.workspace_title.toLowerCase()];
	if (!data) return;
	(data.items || []).forEach((item) => {
		if (item.type !== "Link" || item.link_type !== "URL") return;
		const url = item.url || "";
		// "/drive" and "/desk/x" are ours; "https://..." and "//host" are not
		if (!url.startsWith("/") || url.startsWith("//")) return;
		this.wrapper.find(`a[href="${url}"]`).first().removeAttr("target");
	});
};
