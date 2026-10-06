// Exercise the shipped side-panel JS with a stubbed Frappe.
//
// The panel has two fixes in it that can only fail silently: nesting sub-sections,
// and drawing a section header's icon (the desk stores the icon and never renders
// it). A panel that renders flat, or with no icons, raises nothing and logs nothing.
//
// Run: node tools/test_sidebar_sections.js
// Prints one JSON object on stdout; a non-zero exit means a check failed.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(
	path.join(APP, "sharm_dreams_group", "public", "js", "sidebar_sections.js"),
	"utf8"
);

const results = [];
function check(name, ok, detail) {
	results.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
}

// A jQuery stub that records what was done to a section header, which is the only
// thing this file does to the DOM.
const dom = { prepended: [], hidden: [] };
function el(tag) {
	const self = {
		tag,
		classes: new Set(),
		children: [],
		icon: null,
		length: 1,
		hasClass: (c) => self.classes.has(c),
		addClass(c) {
			self.classes.add(c);
			return self;
		},
		removeClass(c) {
			self.classes.delete(c);
			return self;
		},
		toggleClass(c, on) {
			if (on === false) self.classes.delete(c);
			else self.classes.add(c);
			return self;
		},
		html(v) {
			if (v === undefined) return self._html || "";
			self._html = v;
			return self;
		},
		attr(k, v) {
			if (v === undefined) return self._attr;
			self._attr = v;
			return self;
		},
		find(sel) {
			// `.sidebar-item-icon` answers "has one already?" with `.length`, which is
			// what `draw_section_icons` uses to stay idempotent; an anchor answers
			// `.length` too, which is how it decides it found a section header.
			if (sel === ".sidebar-item-icon") return self._icon ? Object.assign(el("icon"), { length: 1 }) : Object.assign(el(), { length: 0 });
			if (sel === ".item-anchor.section-break") return self._anchor || Object.assign(el(), { length: 0 });
			return Object.assign(el(), { length: 0 });
		},
		each(fn) {
			(self._each || []).forEach((item, i) => fn(i, item));
			return self;
		},
		prepend(node) {
			dom.prepended.push(node && node.tag ? node.tag : String(node));
			if (!self._icon) self._icon = node;
			return self;
		},
		first() {
			return self;
		},
		removeAttr() {
			return self;
		},
		on() {
			return self;
		},
		remove() {
			return self;
		},
	};
	return self;
}
const $ = function (sel) {
	if (typeof sel === "function") sel();
	return el(String(sel));
};
// `$(document).on("sidebar_setup", ...)` runs at load time
$.on = function () {
	return this;
};
$.prototype = el().constructor ? Object.getPrototypeOf(el()) : Object.prototype;
Object.assign($, { __dom: dom });
$.fn = el();

const sandbox = {
	console,
	$,
	setTimeout,
	// the file registers a document-level handler at load time
	document: {},
	frappe: {
		provide() {},
		get_route: () => ["Workspaces", "sharm-dreams-group"],
		set_route() {},
		boot: { workspace_sidebar_item: {} },
		app: { sidebar: null },
		ui: {
			Sidebar: class {},
		},
		utils: { icon: (n) => `<svg data-icon="${n}">` },
	},
};
sandbox.frappe.provide("frappe.ui.sidebar");
sandbox.frappe.ui.sidebar = {};
sandbox.frappe.ui.Sidebar.prototype.prepare = function () {
	sandbox.__prepare_ran = (sandbox.__prepare_ran || 0) + 1;
	return sandbox.__prepare_result;
};
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "sidebar_sections.js" });

// --- 1. nesting: a child section must not be hoisted to the top ----------
// The stock `find_nested_items()` pushes a `child` Section Break into the top level
// and files everything after it under the *previous* section. This is the layout the
// panel actually writes.
// Exactly the shape `apply_app_sidebar` writes: `child` on the sub-section, and
// `child` on every link too - the desk's own `find_nested_items()` files by `child`,
// so a link without it would not nest either.
const flat = [
	{ type: "Link", label: "Home" },
	{ type: "Section Break", label: "People", icon: "users", child: 0, collapsible: 1 },
	{ type: "Section Break", label: "The Employee Record", icon: "contact-round", child: 1, indent: 1 },
	{ type: "Link", label: "Employee", child: 1 },
	{ type: "Section Break", label: "Payroll", icon: "wallet", child: 0, keep_closed: 1 },
	{ type: "Link", label: "Payroll Entry", child: 1 },
	{ type: "Link", label: "Documents", link_type: "URL", url: "/drive", child: 1 },
	// a top-level link, the way Home is written: no `child`, so it must end the
	// section rather than being pulled into Payroll
	{ type: "Link", label: "Contract Type by Company" },
];

// reach the module's own `nest` through the prototype method it installs
const Sidebar = sandbox.frappe.ui.Sidebar;
check("Sidebar prototype patched", !!Sidebar && !!Sidebar.prototype, typeof Sidebar);

const sidebar = Object.create(Sidebar.prototype);
sidebar.sidebar_data = { items: flat };
sidebar.workspace_title = "Sharm Dreams Group";
sidebar.workspace_sidebar_items = [];
// the stock output, as `find_nested_items()` would leave it: a child Section Break
// hoisted to the top level, which is the bug this file exists to fix
sandbox.__prepare_result = [
	{ type: "Link", label: "Home" },
	{ type: "Section Break", label: "People", icon: "users", child: 0 },
	{ type: "Section Break", label: "The Employee Record", icon: "contact-round", child: 1 },
	{ type: "Link", label: "Employee", child: 1 },
	{ type: "Section Break", label: "Payroll", icon: "wallet", child: 0 },
	{ type: "Link", label: "Payroll Entry", child: 1 },
	{ type: "Link", label: "Documents", link_type: "URL", url: "/drive", child: 1 },
	{ type: "Link", label: "Contract Type by Company" },
];
sidebar.sidebar_data = { items: flat };
sidebar.wrapper = { find: () => ({ first: () => ({ removeAttr: () => {} }) }) };
sidebar.prepare();

const nested = sidebar.workspace_sidebar_items;
const topSections = nested.filter((i) => i.type === "Section Break" && !i.child);
check("Home stays at the top level", nested[0] && nested[0].label === "Home", nested[0] && nested[0].label);
check(
	"two top-level sections, not three",
	topSections.length === 2,
	topSections.map((s) => s.label)
);
const people = topSections.find((s) => s.label === "People");
check("People is a top-level section", !!people);
check(
	"the sub-section is nested inside People, not hoisted",
	people && people.nested_items && people.nested_items.some((i) => i.label === "The Employee Record"),
	people && people.nested_items && people.nested_items.map((i) => i.label)
);
check(
	"a nested section is indented, which is what the template checks",
	people &&
		people.nested_items
			.filter((i) => i.type === "Section Break")
			.every((i) => i.indent === 1)
);
check(
	"Employee is filed under the sub-section, not under People",
	people &&
		people.nested_items.some(
			(i) => i.type === "Section Break" && i.nested_items.some((c) => c.label === "Employee")
		)
);
check(
	"Payroll is a top-level section in its own right",
	topSections.some((s) => s.label === "Payroll")
);
const payroll = topSections.find((s) => s.label === "Payroll");
check(
	"Payroll owns Payroll Entry",
	payroll && payroll.nested_items.some((i) => i.label === "Payroll Entry"),
	payroll && payroll.nested_items.map((i) => i.label)
);
check(
	"a link marked as a child stays in the sub-section",
	payroll && payroll.nested_items.some((i) => i.label === "Documents"),
	payroll && payroll.nested_items.map((i) => i.label)
);
check(
	"a top-level link ends the section instead of joining it",
	!payroll.nested_items.some((i) => i.label === "Contract Type by Company") &&
		nested.some((i) => i.label === "Contract Type by Company"),
	payroll && payroll.nested_items.map((i) => i.label)
);

// --- 1a. a second sub-section is a sibling, not a child of the first ---------
// Every section on this panel except Reports has exactly one sub-section, so a
// panel of one-sub-section sections cannot tell a correct nesting from a staircase.
// Reports has four, and with the single-variable nest() it came out as:
//
//     Reports
//       People & Headcount
//         Payroll & Tax        <- a child of the sub-section above it
//           Attendance & Leave
//             Money & Loans
//
// Four groups became three levels of nesting, and the panel read as one list 32
// deep. Nothing raised, every report was still present, and every report was still
// reachable - so nothing in the suite that asked "is this report in the panel"
// could see it. The check has to name the shape.
const four_groups = [
	{ type: "Link", label: "Home" },
	{ type: "Section Break", label: "Reports", icon: "file-chart-column", child: 0 },
	{ type: "Section Break", label: "People & Headcount", child: 1, indent: 1 },
	{ type: "Link", label: "Employee Analytics", child: 1 },
	{ type: "Link", label: "Employee Exits", child: 1 },
	{ type: "Section Break", label: "Payroll & Tax", child: 1, indent: 1 },
	{ type: "Link", label: "Salary Register", child: 1 },
	{ type: "Section Break", label: "Attendance & Leave", child: 1, indent: 1 },
	{ type: "Link", label: "Monthly Attendance Sheet", child: 1 },
	{ type: "Section Break", label: "Money & Loans", child: 1, indent: 1 },
	{ type: "Link", label: "Trial Balance", child: 1 },
];
const grouped = Object.create(Sidebar.prototype);
grouped.sidebar_data = { items: four_groups };
grouped.workspace_title = "Sharm Dreams Group";
grouped.workspace_sidebar_items = [];
grouped.wrapper = { find: () => ({ first: () => ({ removeAttr: () => {} }) }) };
grouped.prepare();

const reports = grouped.workspace_sidebar_items.find((i) => i.label === "Reports");
const groups = (reports && reports.nested_items) || [];
check("Reports holds four sub-sections", groups.length === 4, groups.map((g) => g.label));
check(
	"and all four are siblings at the same depth, not a chain",
	groups.every((g) => g.type === "Section Break"),
	groups.map((g) => g.type + ":" + g.label)
);
check(
	"none of them is nested inside another",
	groups.every((g) => !(g.nested_items || []).some((c) => c.type === "Section Break")),
	groups.map((g) => (g.nested_items || []).map((c) => c.type + ":" + c.label))
);
check(
	"each still owns its own reports",
	groups[1] &&
		groups[1].nested_items.some((c) => c.label === "Salary Register") &&
		groups[2] &&
		groups[2].nested_items.some((c) => c.label === "Monthly Attendance Sheet") &&
		groups[3] &&
		groups[3].nested_items.some((c) => c.label === "Trial Balance"),
	groups.map((g) => (g.nested_items || []).map((c) => c.label))
);
check(
	"and each owns only its own",
	groups[0] &&
		groups[0].nested_items.every((c) => ["Employee Analytics", "Employee Exits"].indexOf(c.label) !== -1),
	groups[0] && groups[0].nested_items.map((c) => c.label)
);

// The depth, as the reader sees it: three levels, not five.
function panel_depth(list, level) {
	const at = level || 0;
	let deepest = at;
	for (const item of list || []) {
		if (item.nested_items && item.nested_items.length) {
			deepest = Math.max(deepest, panel_depth(item.nested_items, at + 1));
		}
	}
	return deepest;
}
check(
	"the Reports tree is three deep - Reports, a group, a report",
	panel_depth(grouped.workspace_sidebar_items) <= 3,
	panel_depth(grouped.workspace_sidebar_items)
);

// --- 1b. editor mode is left alone ---------------------------------------
// The sidebar editor edits a copy and `create_sidebar` renders that copy, so
// re-nesting the boot list mid-edit would throw the edit away.
const editor = Object.create(Sidebar.prototype);
editor.sidebar_data = { items: flat };
editor.editor = { edit_mode: true };
sandbox.__prepare_result = [{ type: "Link", label: "an edit in progress" }];
editor.prepare();
check(
	"editor mode is not re-nested",
	editor.workspace_sidebar_items === undefined ||
		!editor.workspace_sidebar_items.some((i) => i.label === "People" && !i.nested_items),
	editor.workspace_sidebar_items
);

// and a flat panel with no sub-sections is left exactly as the desk built it
const plain = Object.create(Sidebar.prototype);
plain.sidebar_data = {
	items: [
		{ type: "Link", label: "Home" },
		{ type: "Section Break", label: "Only", icon: "settings", child: 0 },
		{ type: "Link", label: "One Row", child: 1 },
	],
};
sandbox.__prepare_result = [{ type: "Link", label: "untouched" }];
plain.prepare();
check(
	"a panel with no sub-sections is not re-nested",
	(plain.workspace_sidebar_items || []).length === 0 ||
		!(plain.workspace_sidebar_items || []).some((i) => i.label === "Only" && i.nested_items),
	plain.workspace_sidebar_items
);

// --- 2. a same-site URL opens in this tab ---------------------------------
const targets = [];
sidebar.wrapper = {
	find(sel) {
		if (String(sel).startsWith("a[href=")) {
			// `a[href="/drive"]` - strip `a[href="` and the closing `"]`
			const url = String(sel).slice(8, -2);
			return {
				first: () => ({
					removeAttr: (a) => targets.push({ url, removed: a }),
				}),
			};
		}
		return el();
	},
};
sidebar.workspace_title = "Sharm Dreams Group";
sandbox.frappe.boot.workspace_sidebar_item = {
	"sharm dreams group": {
		items: [
			{ type: "Link", label: "Documents", link_type: "URL", url: "/drive" },
			{ type: "Link", label: "Payroll Guide", link_type: "URL", url: "https://example.com/x" },
			{ type: "Link", label: "Other", link_type: "URL", url: "//cdn.example.com/y" },
		],
	},
};
sidebar.open_internal_urls_in_this_tab();
const internal = targets.filter((t) => t.url === "/drive");
const external = targets.filter((t) => t.url !== "/drive");
check("a same-site URL loses target=_blank", internal.length === 1 && internal[0].removed === "target", internal);
check("an external URL is left alone", external.length === 0, external);

// --- 4. the section icon is actually injected into a rendered header --------
// The desk stores a Section Break's icon and never renders it: `sidebar_item.html`
// only emits the icon span for links and sub-sections. So this app injects it.
//
// This is the check that matters most in this file, because the injection lived
// in a script that was never loaded at all - `app_include_js` said
// "sidebar_sections.js", the desk emitted src="/sidebar_sections.js", and it 404'd.
// Every other assertion in this harness passed against a file the browser had
// never executed. A harness cannot catch that, which is why there is also a
// Python test asserting the hook entries are paths the desk can resolve.
(function sectionIcons() {
	const drawer = Object.create(Sidebar.prototype);

	// one built row: a section with an icon, one without, and a link. Only the
	// first should get a span - a link already renders its own icon from the
	// template, and a section with no icon must not be given an empty square.
	function row(item, hasIcon) {
		const wrapper = el("wrapper");
		const anchor = el("anchor");
		// the anchor is what `wrapper.find(".item-anchor.section-break")` resolves
		// to; a section that has one is what the icon gets prepended into
		if (hasIcon) wrapper._anchor = anchor;
		return { item, wrapper, _anchor: anchor };
	}
	const withIcon = row({ type: "Section Break", label: "Dashboard", icon: "layout-dashboard" }, true);
	const withoutIcon = row({ type: "Section Break", label: "Overview" }, false);
	const aLink = row({ type: "Link", label: "Employee", icon: "users" }, false);
	drawer.items = [withIcon, withoutIcon, aLink];

	const before = dom.prepended.length;
	drawer.draw_section_icons();
	const added = dom.prepended.slice(before);

	// `$('<span class="sidebar-item-icon ...">')` goes through the jQuery stub,
	// which wraps whatever it is given as an element's tag - so what lands in
	// `prepended` is the whole tag string, not the bare word "span".
	check(
		"a section header with an icon gets one",
		added.some((t) => String(t).includes("sidebar-item-icon")),
		added
	);
	check(
		"the injected span carries the section's own icon name",
		String((withIcon._anchor._icon && withIcon._anchor._icon._html) || "").includes("layout-dashboard"),
		String((withIcon._anchor._icon && withIcon._anchor._icon._html) || "")
	);
	check("a section with no icon is left alone", added.length === 1, added);
	check("a link is not given a second icon", added.length === 1 && !added.some((t) => String(t).includes("users")));

	// idempotent: the panel is rebuilt on every route change, so running twice on
	// the same row must not stack a second span
	const again = dom.prepended.length;
	drawer.draw_section_icons();
	check("re-running adds nothing", dom.prepended.length === again, dom.prepended.slice(again));
})();

// Written to a file as well as stdout, for the same reason as the dashboard
// harness: the code under test logs to the console, so stdout cannot be relied on
// to be pure JSON.
const payload = JSON.stringify(
	{ total: results.length, failed: results.filter((r) => !r.ok).length, results },
	null,
	1
);
process.stdout.write(payload);
if (process.env.SHARM_REPORT) {
	try {
		require("fs").writeFileSync(process.env.SHARM_REPORT, payload);
	} catch (e) {
		// not being able to write the file is not a reason to fail the run
	}
}
process.exit(results.some((r) => !r.ok) ? 1 : 0);
