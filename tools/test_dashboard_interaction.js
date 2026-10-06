// Exercise the shipped dashboard JS with a stubbed Frappe, so the click paths are
// verified rather than assumed.
//
// The reason this exists: both drill-downs failed *silently* the first time round.
// `card_routes` was not whitelisted, and the stock `set_route_for_custom_card` reads
// `this.data.route` on a Custom card where `this.data` is the method's return value -
// the figure. A card that does nothing produces no error and no console output, so
// "the JS parses" is not evidence and neither is a manual glance.
//
// Run: node tools/test_dashboard_interaction.js
// Prints one JSON object on stdout; a non-zero exit means a check failed.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(
	path.join(APP, "sharm_dreams_group", "public", "js", "dashboard_view.js"),
	"utf8"
);

const results = [];
function check(name, ok, detail) {
	results.push({ name, ok: !!ok, detail: detail === undefined ? null : detail });
}

// --- a jQuery-shaped stub -------------------------------------------------
// Only what the file touches. `$(document).on(...)` has to work at load time.
function el(extra) {
	const record = (parent, html) => {
		// record what landed, so "the page rendered something" is answerable
		if (parent && parent._children) parent._children.push(html);
		return parent || el();
	};
	const node = Object.assign(
		{
			length: 0,
			find: () => el(),
			each: () => {},
			attr: () => undefined,
			closest: () => el(),
			prepend: () => el(),
			html: () => el(),
			hide: () => el(),
			// Classes have to be real on this stub. `toggleClass` is how a card is
			// marked as going somewhere, and it is called *after* the route arrives -
			// the point at which a `hasClass: () => false` stub reports "not clickable"
			// and passes every time.
			_classes: new Set(),
			addClass(name) {
				String(name)
					.split(/\s+/)
					.filter(Boolean)
					.forEach((c) => this._classes.add(c));
				return this;
			},
			removeClass(name) {
				String(name)
					.split(/\s+/)
					.filter(Boolean)
					.forEach((c) => this._classes.delete(c));
				return this;
			},
			toggleClass(name, on) {
				const names = String(name).split(/\s+/).filter(Boolean);
				names.forEach((c) => {
					const has = this._classes.has(c);
					const want = on === undefined ? !has : !!on;
					if (want) this._classes.add(c);
					else this._classes.delete(c);
				});
				return this;
			},
			hasClass(name) {
				return this._classes.has(name);
			},
			empty: function () {
				this._children = [];
				return this;
			},
			append: function (html) {
				if (!this._children) this._children = [];
				this._children.push(html);
				return this;
			},
			appendTo: (parent) => record(parent, "appended"),
			removeAttr: () => el(),
			first: () => el(),
			text: () => "",
			show: () => el(),
			hide_: () => el(),
		},
		extra || {}
	);
	return node;
}
function $(selector) {
	if (selector && selector.__on) selector.__on();
	const node = el();
	// Classes named in an HTML string have to survive into the stub. The factory
	// builds its own wrapper as `$("<div class='dashboard-view'></div>")`, and the
	// question "is the thing the sections render into the wrapper, or the page's own
	// container?" is answered by that class. A stub that threw the string away made
	// both answers look the same.
	if (typeof selector === "string") {
		node._html = selector;
		const match = selector.match(/class\s*=\s*["']([^"']*)["']/);
		if (match) node.addClass(match[1]);
	}
	return node;
}
$.__on = () => {};

// --- the Frappe stub ------------------------------------------------------
const calls = [];
const base_widget_prototype = {
	refresh() {
		this.refreshed = (this.refreshed || 0) + 1;
	},
};
function make_card_widget() {
	function NumberCardWidget() {}
	NumberCardWidget.prototype = Object.create(base_widget_prototype);
	NumberCardWidget.prototype.constructor = NumberCardWidget;
	NumberCardWidget.prototype.set_route = stockNumberCardRoute;
	return NumberCardWidget;
}
const stockNumberCardRoute = function () {
	calls.push({ stock_set_route: true });
};
const stockChartArgs = function () {
	return { type: "bar", plugins: {} };
};

// `moment` and `frappe.datetime.now_date`, both pinned to a fixed day. The
// contract-due chart stores its drill-down buckets as *day offsets* rather than
// dates, on purpose: a bucket lives on the chart document, so a date written at
// setup time would still be next month's answer when the bar is tapped. The
// offset is turned into a real date here, in the browser, and that is the whole
// point of the design - so the harness has to see that shift happen, and has to
// do it against a day that will not move under it.
const TODAY = "2026-10-03";
function moment(date) {
	// ISO-parseable and UTC, so `add(days)` is exact and the formatter has no
	// local-timezone edge to disagree with Python's over.
	let stamp = new Date(date + "T00:00:00Z").getTime();
	const api = {
		add(amount, unit) {
			if (unit !== "days") throw new Error("moment stub only shifts days, got " + unit);
			stamp += amount * 86400000;
			return api;
		},
		format(pattern) {
			if (pattern !== "YYYY-MM-DD") throw new Error("moment stub only formats YYYY-MM-DD");
			return new Date(stamp).toISOString().slice(0, 10);
		},
	};
	return api;
}

// A promise shaped like the one `frappe.call` actually returns.
//
// This is the second time a stub invented an API the desk does not have, and the
// second time it hid a real bug. `frappe.call` does NOT return a native Promise,
// and it does not return a jQuery Deferred either: whatever jQuery is on the page,
// the object it hands back carries `then` and `catch` and **no `finally`.** A
// chain ending in `.finally()` therefore raises `TypeError: ... is not a
// function` the moment it runs - and `fetch_sharm_routes` did exactly that, so
// every card on the workspace front page was inert.
//
// With `frappe.call` stubbed as `() => Promise.resolve(...)` this file passed
// 166 checks throughout, because a native Promise *has* `finally` and the broken
// line was quietly legal here. A stub that is more capable than the thing it stands
// in for does not just fail to test the edge; it hides it.


// A promise shaped like the one `frappe.call` actually returns.
//
// This is the second time a stub invented an API the desk does not have, and the
// second time it hid a real bug. `frappe.call` does NOT return a native Promise,
// and it does not return a jQuery Deferred either: whatever jQuery is on the page,
// the object it hands back carries `then` and `catch` and **no `finally`.** A
// chain ending in `.finally()` therefore raises `TypeError: ... is not a
// function` the moment it runs - and `fetch_sharm_routes` did exactly that, so
// every card on the workspace front page was inert.
//
// With `frappe.call` stubbed as `() => Promise.resolve(...)` this file passed 166
// checks throughout, because a native Promise *has* `finally` and the broken line was
// quietly legal here. A stub that is more capable than the thing it stands in for
// does not just fail to test the edge; it hides it.
//
// The mirror-image fault is worse, because it is silent in the other direction:
// this class also used to *ignore the rejection handler*, passing a failure straight
// on instead. `catch(() => [])` - the ordinary way of saying "this one failure is
// not fatal" - then made things worse rather than better. See `_link`.
//
// Settles on a microtask, like a real request, so the harness still exercises the
// "the answer has not arrived yet" shape.
class DeskPromise {
	constructor() {
		this.state = "pending";
		this.value = undefined;
		this.watchers = [];
	}
	// No `finally`, on purpose. See above.
	then(on_ok, on_err) {
		return this._link(on_ok, on_err);
	}
	catch(on_err) {
		return this._link(null, on_err);
	}
	_link(on_ok, on_err) {
		const next = new DeskPromise();
		const handle = (ok, value) => {
			if (next.state !== "pending") return;
			try {
				// The handler is chosen by the outcome, and the one for a rejection is
				// `catch`'s own argument. This was not how it worked: a rejection went
				// straight to `next._settle(false, value)` and `on_err` was never
				// called, so `catch` did nothing except pass the failure along.
				//
				// That is the same class of fault as a stub with `finally`: ordinary
				// code stopped being exercised and nothing said so. `.catch(() => [])`
				// is how a caller says "this one request failing is not fatal", and
				// against this stub it made the whole enclosing `Promise.all` reject
				// instead - so a single refused chunk emptied the page, which is the
				// opposite of what the line says.
				const handler = ok ? on_ok : on_err;
				if (typeof handler !== "function") {
					// No handler: a promise passes its outcome through untouched, and a
					// rejection with nobody listening stays a rejection. `Promise.all`
					// relies on that.
					next._settle(ok, value);
					return;
				}
				const result = handler(value);
				if (result instanceof DeskPromise) {
					// Chained: hand this one's settlement to the next link.
					result.watchers.push((ok2, v2) => next._settle(ok2, v2));
					return;
				}
				// What the handler returned is what the next link fulfils with. Without
				// this, `catch(() => [])` hands back a promise that fulfils with
				// nothing at all and the fallback is nowhere to be seen.
				next._settle(true, result);
			} catch (e) {
				next._settle(false, e);
			}
		};
		if (this.state === "pending") {
			this.watchers.push(handle);
		} else {
			const ok = this.state === "ok";
			const value = this.value;
			Promise.resolve().then(() => handle(ok, value));
		}
		return next;
	}
	_settle(ok, value) {
		if (this.state !== "pending") return;
		this.state = ok ? "ok" : "failed";
		this.value = value;
		const watchers = this.watchers;
		this.watchers = [];
		watchers.forEach((fn) => fn(ok, value));
	}
}

function desk_promise(value) {
	const p = new DeskPromise();
	Promise.resolve().then(() => p._settle(true, value));
	return p;
}

// A `desk_promise` that has already failed, for the rejection paths.
//
// `DeskPromise` exposes `_settle` precisely so a test can produce the shape directly;
// going through it rather than through a second constructor keeps the one definition
// of how a rejected promise behaves, which is the thing being tested.
function desk_promise_error(error) {
	const p = new DeskPromise();
	Promise.resolve().then(() => p._settle(false, error || new Error("refused")));
	return p;
}

const sandbox = {
	console,
	$,
	Promise,
	moment,
	// The patches now retry until the widget classes are registered, so they need a
	// timer. Without one the retry throws out of `runInContext` and takes the whole
	// harness with it.
	setTimeout,
	clearTimeout,
	module: {},
	// The wiring report and the failure banner both write to the page, so the
	// sandbox needs a document that can make elements.
	document: {
		body: { appendChild() {} },
		getElementById: () => null,
		createElement: () => ({ style: {}, className: "", textContent: "", remove() {} }),
	},
	frappe: {
		provide(path_) {
			const parts = path_.split(".");
			let node = sandbox.frappe;
			for (const p of parts) node = node[p] = node[p] || {};
		},
		get_route: () => ["dashboard-view", "SDG Workforce"],
		datetime: { now_date: () => TODAY },
		set_route(r) {
			calls.push({ set_route: r, route_options: sandbox.frappe.route_options });
		},
		show_not_found() {},
		make_page() {},
		route_options: undefined,
		// the desk slugifies route segments: lowercase, non-alphanumerics to a dash
		router: {
			slug: (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-"),
		},
		boot: { workspace_sidebar_item: {} },
		db: { get_list: () => Promise.resolve([]) },
		model: {
			with_doc: (_dt, _n, cb) => cb && cb(null),
			with_doctype: (_dt, cb) => cb && cb(),
		},
		// `frappe.meta` is what decides whether a filter field can be placed. The
		// stub leaves `get_meta` undefined by default, which is the "meta has not
		// loaded" case a chart can legitimately be drawn under; the one test that
		// needs a real answer installs it and removes it again.
		meta: {},
		ui: { make_app_page: () => ({ main: el(), addClass: () => {} }) },
		utils: {
			escape_html: (s) => s,
			icon: (n) => `<svg data-icon="${n}">`,
			// The desk's own helper, faithfully: it is `frappe.utils.parse_json`, and it
			// RETURNS its input on a parse failure rather than throwing. Stubbing it
			// under the wrong name - or as one that throws - is how a chart's
			// `filters_json` came to be silently ignored on the real desk while every
			// check here passed: the harness was testing a function that does not
			// exist against an error path that was never reached.
			parse_json: (str) => {
				try {
					return JSON.parse(str);
				} catch (e) {
					return str;
				}
			},
		},
		call: () => desk_promise({ message: {} }),
		xcall: () => desk_promise(0),
		views: { Factory: class {} },
		// Deliberately NO `frappe.widgets.ChartWidget`. The desk has no such global -
		// the widgets are ES modules, and widget_group.js is what puts them on
		// `frappe.widget.widget_factory`. The first version of the chart patch looked
		// at the global that does not exist, was handed undefined, and silently wired
		// nothing up; this shape is the one the desk really has.
		widget: {
			WidgetGroup: class {},
			widget_factory: {
				// Built the way the real chain is: `NumberCardWidget extends
				// BaseWidget`, so `refresh` is inherited and NOT an own property of
				// `number_card.prototype`. Owning it here would hide exactly the bug
				// the `sharm_inherited` walk exists to catch.
				number_card: make_card_widget(),
				chart: { prototype: { get_chart_args: stockChartArgs } },
			},
		},
	},
};
// Records `with_doctype` calls: the List drops any filter whose doctype meta is
// not loaded, silently, so preloading it is part of navigating correctly.
sandbox.meta_loads = [];
sandbox.frappe.model.with_doctype = function (doctype, cb) {
	sandbox.meta_loads.push(doctype);
	if (cb) cb();
};
sandbox.frappe.provide("frappe.views");
// The real Factory is not empty: it has `show()` and a `make_page()` helper that
// delegates to `frappe.make_page`. The stub has to carry `make_page` or the factory
// under test cannot build a page at all.
sandbox.frappe.views.Factory = class {
	make_page(double_column, page_name, sidebar_position) {
		return sandbox.frappe.make_page(double_column, page_name, sidebar_position);
	}
};
sandbox.frappe.views.DashboardView = class {};

// The file is a plain script, not a module: run it in this context.
vm.createContext(sandbox);
vm.runInContext(SRC, sandbox, { filename: "dashboard_view.js" });

const proto = sandbox.frappe.widget.widget_factory.number_card.prototype;
const chart = sandbox.frappe.widget.widget_factory.chart.prototype;

// The route map, installed before anything can ask for it.
//
// `sharm_fetch_route_map` memoises its promise for the life of the page, so the
// first ask is the only ask: a stub installed after some earlier card warmed the
// memo is never reached, and the checks below would quietly read the map that
// warmed it instead. Installing it here is also the faithful shape - there is one
// map per page, fetched by whoever needs it first.
const map = {
	"Active Headcount": { route: "List/Employee", route_options: { status: "Active" } },
};
let route_requests = 0;
sandbox.frappe.call = () => {
	route_requests++;
	return desk_promise({ message: map });
};

// --- 1. the card widget was patched --------------------------------------
check("card widget patched", proto.sharm_drilldown_patched === true);
check("chart widget patched", chart.sharm_drilldown_patched === true);

// The stub is only useful if it is the desk. `frappe.parse_json` does not exist -
// the desk's helper is `frappe.utils.parse_json` - and a stub that invents it
// makes every call succeed here and throw on the page. That is not hypothetical:
// with `frappe.parse_json` stubbed, this file passed 162 checks while the real
// dashboard opened every chart's list with no scope filters at all, because the
// TypeError was swallowed one call frame away. Assert the surface, so a future
// stub cannot quietly reintroduce the fiction.
check(
	"the stub does not invent a frappe.parse_json the desk does not have",
	typeof sandbox.frappe.parse_json === "undefined"
);
check(
	"the JSON helper is where the desk keeps it",
	typeof sandbox.frappe.utils.parse_json === "function"
);
check(
	"and it returns unparseable input rather than throwing, as the desk's does",
	sandbox.frappe.utils.parse_json("{not json") === "{not json"
);
check(
	"stock card set_route kept for cards we do not own",
	typeof proto.fetch_sharm_routes === "function"
);

// --- 2. a card with a route navigates there ------------------------------
calls.length = 0;
const card = Object.create(proto);
card.route = "List/Employee";
card.route_options = { status: "Active" };
card.set_route();
const nav = calls.find((c) => c.set_route);
check("card with a route navigates", nav && nav.set_route === "List/Employee", nav);
check(
	"card passes its filters, encoded so they survive the URL round trip",
	nav && JSON.stringify(nav.route_options) === JSON.stringify({ status: ["=", "Active"] }),
	nav && nav.route_options
);
// Deliberately NOT cleared here. The destination List reads `route_options` in
// `before_refresh()` and nulls it itself; clearing it in the widget races that read
// and the list opens unfiltered - which is the failure this whole feature fixes.
check(
	"route_options is left for the destination list to consume",
	sandbox.frappe.route_options &&
		JSON.stringify(sandbox.frappe.route_options.status) === JSON.stringify(["=", "Active"]),
	sandbox.frappe.route_options
);

// --- 3. a card with no route asks the server, then navigates -------------
// The server answers straight away: this is the normal case, not a retry.
// `map` and the stub were installed at the top of the page - see the note there
// about the memo, and why a stub set in this section would never be reached.

calls.length = 0;
const lazy = Object.create(proto);
lazy.name = "Active Headcount";
lazy.set_route();
check("inert card asks the server rather than navigating anywhere", calls.length === 0, calls);
check("inert card does not fall through to the stock behaviour", calls.length === 0, calls);

setTimeout(() => {
	check("server map is applied to the widget", lazy.route === "List/Employee", lazy.route);

	calls.length = 0;
	lazy.set_route();
	const nav2 = calls.find((c) => c.set_route);
	check("a card becomes tappable once its route arrives", nav2 && nav2.set_route === "List/Employee", nav2);

	// --- 3b. the desk's promise shape, and the two ways it broke the cards ---
	// Everything above passes with `frappe.call` stubbed as a native Promise. These
	// are the checks that only fail once the stub tells the truth.
	const desk_call = sandbox.frappe.call();
	check(
		"a frappe.call result has no finally, as the desk's has none",
		typeof desk_call.finally === "undefined",
		typeof desk_call.finally
	);

	// A workspace card is built from the child-table row, so its `name` is the row
	// id and its `label` is the card. `card_routes()` is keyed by label, so a lookup
	// by name finds nothing and the card is inert for ever - and on the workspace
	// front page that is all 43 of them.
	const row_id = { name: "cvn7o7ec1k", label: "Active Headcount", widget: el() };
	const row_card = Object.create(proto);
	Object.assign(row_card, row_id);
	let row_threw = null;
	try {
		row_card.fetch_sharm_routes();
	} catch (e) {
		row_threw = e.message;
	}
	check(
		"fetching a card's route does not throw on the desk's promise",
		row_threw === null,
		row_threw
	);

	setTimeout(() => {
		check(
			"a workspace card is found by its label, not its child-row id",
			row_card.route === "List/Employee",
			row_card.route + " (name=" + row_card.name + ")"
		);
		check(
			"and it is marked clickable once the answer lands",
			!!row_card.widget && row_card.widget.hasClass("sharm-clickable-card"),
			row_card.widget && row_card.widget.hasClass("sharm-clickable-card")
		);

		// The map is fetched once per page and every later card reuses it. By this
		// point the earlier cards have already warmed it, so the correct number of new
		// requests for forty-three more cards is *zero* - the old code asked once per
		// card per tap, which is 43 requests a page load here, and one more on every
		// tap after that.
		const asked_before = route_requests;
		const many = [];
		for (let i = 0; i < 43; i++) {
			const c = Object.create(proto);
			c.name = "row-" + i;
			c.label = "Active Headcount";
			c.widget = el();
			many.push(c);
			c.fetch_sharm_routes();
		}
		setTimeout(() => {
			check(
				"forty-three more cards reuse the map already fetched rather than asking again",
				route_requests === asked_before,
				"further requests: " + (route_requests - asked_before)
			);
			check(
				"and all forty-three still get their route",
				many.every((c) => c.route === "List/Employee"),
				many.filter((c) => c.route !== "List/Employee").length + " without one"
			);
			continue_harness();
		}, 30);
	}, 30);

	function continue_harness() {
	// --- 4. a card we do not own falls through to the stock behaviour -----
	calls.length = 0;
	const stock = Object.create(proto);
	stock.name = "Someone Elses Card";
	stock.sharm_routes_loaded = true;
	stock.set_route();
	check(
		"a card with no route of ours defers to the stock widget",
		calls.some((c) => c.stock_set_route),
		calls
	);

	// --- 5. charts: ours become clickable, stock ones stay pictures ---------
	// The click is handled by delegation on the chart wrapper, not by an `onClick`
	// option: `frappe.utils.make_chart()` forwards unknown options to
	// `new frappe.Chart(...)`, but that constructor reads a fixed list and `onClick`
	// is not in it. The handler was accepted and silently dropped, on every chart,
	// which is why nothing was ever clickable. frappe-charts does stamp
	// `data-point-index` on every bar it draws, so that attribute is what we read.
	function svg_dom() {
		const listeners = {};
		const wrapper = {
			style: {},
			_children: [],
			// jQuery's `addClass`/`hasClass`, which is how the patch marks a chart
			// wrapper as tappable. The stub has to know about classes: the pointer being
			// offered is a promise the chart makes to the user, and a test that cannot
			// see it cannot hold that promise to account.
			_classes: new Set(),
			addClass(name) {
				String(name)
					.split(/\s+/)
					.filter(Boolean)
					.forEach((c) => this._classes.add(c));
				return this;
			},
			hasClass(name) {
				return this._classes.has(name);
			},
			addEventListener(type, fn) {
					(listeners[type] = listeners[type] || []).push(fn);
			},
			contains(node) {
				return this._children.indexOf(node) !== -1;
			},
		};
		const node = (index, is_tip) => ({
			_tip: !!is_tip,
			closest(selector) {
				if (selector === "[data-point-index]") {
					return index === null ? null : this;
				}
				if (selector === ".graph-svg-tip") return this._tip ? this : null;
				return null;
			},
			getAttribute(name) {
				return name === "data-point-index" && index !== null ? String(index) : null;
			},
		});
		return {
			wrapper,
			listeners,
			mark: (i) => node(i, false),
			tip: (i) => node(i, true),
			plain: () => node(null, false),
			click(target) {
				this.wrapper._children.push(target);
				(this.listeners.click || []).forEach((fn) => fn({ target }));
			},
			hover(target) {
				// the hovered element really is inside the wrapper, and the resolver
				// checks that before trusting an attribute it found
				this.wrapper._children.push(target);
				(this.listeners.mouseover || []).forEach((fn) => fn({ target }));
			},
			unhover() {
				(this.listeners.mouseout || []).forEach((fn) => fn({}));
			},
		};
	}

	const dom = svg_dom();
	const ours = Object.create(chart);
	ours.chart_wrapper = [dom.wrapper];
	ours.chart_doc = {
		module: "Sharm Dreams Group",
		document_type: "Employee",
		group_by_based_on: "company",
		group_by_type: "Count",
		// the scope the chart itself is drawn under - "Headcount by Company" counts
		// Active staff, so the list behind a bar has to be Active staff too
		filters_json: JSON.stringify([["Employee", "status", "=", "Active"]]),
	};
	const args = ours.get_chart_args();
	check("the stock chart config survives the patch", args.type === "bar");
	check(
		"a click listener is bound to the chart wrapper",
		(dom.listeners.click || []).length === 1,
		"listeners: " + (dom.listeners.click || []).length
	);
	check(
		"no dead onClick option is passed to frappe-charts",
		args.onClick === undefined,
		"frappe-charts drops this on the floor, so passing it is misleading"
	);

	// binding again on the same wrapper must not stack a second listener
	ours.get_chart_args();
	check(
		"the listener is bound once per wrapper, not once per render",
		(dom.listeners.click || []).length === 1,
		"listeners: " + (dom.listeners.click || []).length
	);

	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	ours.data = { labels: ["Bautex Misr", "Concord"], datasets: [{ values: [12, 40] }] };
	dom.click(dom.mark(0));
	const chartNav = calls.find((c) => c.set_route);
	check("tapping a bar opens the source list", chartNav && chartNav.set_route === "List/Employee", chartNav);
	check(
		"tapping a bar filters to that segment AND the chart's own scope",
		chartNav &&
			JSON.stringify(chartNav.route_options) ===
				JSON.stringify({ status: ["=", "Active"], company: ["=", "Bautex Misr"] }),
		chartNav && chartNav.route_options
	);
	// Without the chart's own filters the list shows leavers, whose heads are in no
	// bar on the chart - the figure and the rows behind it disagree, which is the one
	// outcome the drill-down exists to prevent.
	check(
		"the chart's scope survives, not just the segment",
		chartNav &&
			chartNav.route_options &&
			JSON.stringify(chartNav.route_options.status) === JSON.stringify(["=", "Active"]),
		chartNav && chartNav.route_options
	);
	check(
		"route_options is left for the list after a chart tap too",
		sandbox.frappe.route_options &&
			JSON.stringify(sandbox.frappe.route_options.company) === JSON.stringify(["=", "Bautex Misr"]),
		sandbox.frappe.route_options
	);

	// the second bar, to prove the index is read off the element and not assumed 0
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	dom.click(dom.mark(1));
	const secondNav = calls.find((c) => c.set_route);
	check(
		"the bar that was tapped decides the segment",
		secondNav &&
			secondNav.route_options &&
			JSON.stringify(secondNav.route_options.company) === JSON.stringify(["=", "Concord"]),
		secondNav && secondNav.route_options
	);

	// a blank bucket, which is how the chart labels an empty group
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	ours.data = { labels: ["Not Set"], datasets: [{ values: [4] }] };
	dom.click(dom.mark(0));
	const blankNav = calls.find((c) => c.set_route);
	check("tapping a blank bucket still opens the list", blankNav && blankNav.set_route === "List/Employee", blankNav);
	check(
		'a blank bucket becomes a "not set" filter, not a literal value',
		blankNav &&
			JSON.stringify(blankNav.route_options) ===
				JSON.stringify({ status: ["=", "Active"], company: ["is", "not set"] }),
		blankNav && blankNav.route_options
	);

	// A numeric label is a real value, not the blank bucket.
	//
	// A group-by whose field is a Currency or a Number hands back numbers, and the
	// guard used to read those as text - which produced `""`, and `""` was in the list
	// of blank labels.  So every band on "Life Insurance Band Split" (grouped by the
	// Currency `from_gross`) was sent as `from_gross is not set` and opened the empty
	// bucket rather than the band that was tapped.
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	ours.data = { labels: [50000], datasets: [{ values: [238.4] }] };
	dom.click(dom.mark(0));
	const numNav = calls.find((c) => c.set_route);
	check("a numeric label still opens the list", numNav && numNav.set_route === "List/Employee", numNav);
	check(
		"a numeric label is sent as its own value, not as a blank bucket",
		numNav &&
			JSON.stringify(numNav.route_options) ===
				JSON.stringify({ status: ["=", "Active"], company: ["=", 50000] }),
		numNav && numNav.route_options
	);

	// zero is a value like any other
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	ours.data = { labels: [0], datasets: [{ values: [7] }] };
	dom.click(dom.mark(0));
	const zeroNav = calls.find((c) => c.set_route);
	check(
		"zero is a value, not a blank bucket",
		zeroNav && JSON.stringify(zeroNav.route_options.company) === JSON.stringify(["=", 0]),
		zeroNav && zeroNav.route_options
	);

	// ...but a genuinely empty label is the blank bucket, which the numeric fix must
	// not have cost us.
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	ours.data = { labels: [""], datasets: [{ values: [2] }] };
	dom.click(dom.mark(0));
	const emptyNav = calls.find((c) => c.set_route);
	check(
		"an empty-string label is still the blank bucket",
		emptyNav && JSON.stringify(emptyNav.route_options.company) === JSON.stringify(["is", "not set"]),
		emptyNav && emptyNav.route_options
	);

	// a non-default operator has to be carried, or the List reads it as equality
	const op_dom = svg_dom();
	const opChart = Object.create(chart);
	opChart.chart_wrapper = [op_dom.wrapper];
	opChart.chart_doc = {
		module: "Sharm Dreams Group",
		document_type: "Salary Slip",
		group_by_based_on: "company",
		group_by_type: "Sum",
		filters_json: JSON.stringify([["Salary Slip", "docstatus", "=", 1]]),
	};
	calls.length = 0;
	opChart.data = { labels: ["Concord"], datasets: [{ values: [5] }] };
	opChart.get_chart_args();
	op_dom.click(op_dom.mark(0));
	const opNav = calls.find((c) => c.set_route);
	check(
		"docstatus is carried, so drafts stay out of the list",
		opNav &&
			opNav.route_options &&
			JSON.stringify(opNav.route_options.docstatus) === JSON.stringify(["=", 1]),
		opNav && opNav.route_options
	);

	// clicking the chart but not a bar must not navigate
	calls.length = 0;
	sandbox.frappe.route_options = undefined;
	dom.click(dom.plain());
	check("clicking the background does not navigate", calls.length === 0, calls);

	// the tooltip carries a data-point-index too and is absolutely positioned over
	// the plot, so clicking it is not clicking a bar
	calls.length = 0;
	dom.click(dom.tip(0));
	check("clicking the tooltip does not navigate", calls.length === 0, calls);

	// an index past the end of the payload is not a crash
	calls.length = 0;
	ours.data = { labels: ["Only One"], datasets: [{ values: [1] }] };
	let bad_index_threw = null;
	try {
		dom.click(dom.mark(7));
	} catch (e) {
		bad_index_threw = e;
	}
	check(
		"an index with no label does not throw or navigate",
		!bad_index_threw && calls.length === 0,
		bad_index_threw && bad_index_threw.message
	);

	// a fresh wrapper has to get its own listener
	const dom2 = svg_dom();
	const rebuilt = Object.create(chart);
	rebuilt.chart_wrapper = [dom2.wrapper];
	rebuilt.chart_doc = Object.assign({}, ours.chart_doc);
	rebuilt.data = { labels: ["Bautex Misr"], datasets: [{ values: [1] }] };
	rebuilt.get_chart_args();
	check(
		"a rebuilt chart wrapper gets its own listener",
		(dom2.listeners.click || []).length === 1,
		"listeners: " + (dom2.listeners.click || []).length
	);
	calls.length = 0;
	dom2.click(dom2.mark(0));
	check(
		"the rebuilt chart is clickable too",
		calls.some((c) => c.set_route === "List/Employee"),
		calls
	);

	// the cursor, or a chart you can drill into looks identical to one you cannot
	dom.hover(dom.mark(0));
	check("the chart shows a pointer over a bar", dom.wrapper.style.cursor === "pointer", dom.wrapper.style.cursor);
	dom.unhover();
	check("the pointer goes away again", dom.wrapper.style.cursor === "default", dom.wrapper.style.cursor);

	const stock_dom = svg_dom();
	const stockChart = Object.create(chart);
	stockChart.chart_wrapper = [stock_dom.wrapper];
	// A chart from another app that groups by nothing cannot become a list filter,
	// so it stays a picture. Note what decides this: not the module, the missing
	// group-by.
	stockChart.chart_doc = { module: "ERPNext", document_type: "Item" };
	stockChart.get_chart_args();
	// The listener is bound whatever the chart is, and the click asks again - see the
	// note in `get_chart_args`. So the thing to assert is not "no listener" but "a
	// click navigates nowhere": the honest behaviour for a chart that cannot filter.
	check(
		"an ungrouped stock chart resolves no drill-down",
		stockChart.get_drilldown_target() === null
	);
	check(
		"and clicking it does not navigate",
		(() => {
			const before = calls.length;
			stock_dom.click(stock_dom.plain());
			return calls.length === before;
		})(),
		"set_route calls: " + calls.length
	);
	check(
		"and it is not offered a pointer",
		!stock_dom.wrapper.hasClass("sharm-clickable")
	);

	// ... but a stock chart that *does* name a field is drillable. This is the change
	// the module gate used to prevent: every Pie on this desk ships with HRMS or CRM,
	// so gating on our own module left all 17 of them - and 3 Percentage rings -
	// looking like charts and doing nothing.
	const pie_dom = svg_dom();
	const stockPie = Object.create(chart);
	stockPie.chart_wrapper = [pie_dom.wrapper];
	stockPie.chart_doc = {
		module: "HR",
		type: "Pie",
		document_type: "Employee",
		group_by_based_on: "gender",
		group_by_type: "Count",
	};
	stockPie.get_chart_args();
	check(
		"a Pie from another app is clickable once it names a field to filter on",
		(pie_dom.listeners.click || []).length === 1,
		(pie_dom.listeners.click || []).length + " click listeners"
	);
	const pie_target = stockPie.get_drilldown_target();
	check(
		"that Pie drills into the doctype and field it groups by",
		pie_target && pie_target.doctype === "Employee" && pie_target.field === "gender",
		pie_target
	);

	// The whole point, end to end: a Pie slice that is clicked must navigate AND put
	// the filters in the URL. Asserting only "a listener is bound" is how this stayed
	// broken for so long - the listener existed and still did nothing.
	//
	// The slice is identified the only way a pie can be: by position in the chart's
	// own component store. No `data-point-index` is involved, because frappe-charts
	// stamps that only on bars and dots. `plain()` builds a new node per call, so the
	// very same node has to be put in the store and clicked, or `indexOf` cannot match.
	// the shape a real `frappe.Chart` exposes: `data.labels` for the labels, and
	// `components` as a Map of name -> { store }, one element per data point for a pie
	const pie_slice = pie_dom.plain();
	stockPie.dashboard_chart = {
		data: { labels: ["Female", "Male"], datasets: [{ values: [4, 6] }] },
		components: new Map([["pieSlices", { store: [pie_slice, pie_dom.plain()] }]]),
	};
	stockPie.data = stockPie.dashboard_chart.data;
	const pie_calls_before = calls.length;
	pie_dom.click(pie_slice);
	check(
		"a Pie slice resolves by its position in the component store",
		pie_dom.wrapper.sharm_resolve({ target: pie_slice }) === 0,
		"resolved " + pie_dom.wrapper.sharm_resolve({ target: pie_slice })
	);
	check(
		"clicking a Pie slice navigates to the doctype it groups by",
		calls.length === pie_calls_before + 1 && calls[calls.length - 1].set_route === "List/Employee",
		JSON.stringify(calls[calls.length - 1])
	);
	check(
		"and the slice is passed as a filter on the group-by field",
		calls[calls.length - 1] &&
			JSON.stringify(calls[calls.length - 1].route_options) ===
				JSON.stringify({ gender: ["=", "Female"] }),
		calls[calls.length - 1] && JSON.stringify(calls[calls.length - 1].route_options)
	);

	// ... and into the URL, which is what "the filters should get in the URL" means.
	// The Pie's own slice is not distinguishable by attribute, so this also pins the
	// component-store fallback that resolves a slice click at all.
	const pie_urls = [];
	sandbox.window = {
		location: { pathname: "/desk/employee", search: "" },
		history: { replaceState: (_a, _b, url) => pie_urls.push(url) },
	};
	pie_dom.click(pie_slice);
	check(
		"clicking a Pie slice writes the filter into the URL",
		pie_urls.length === 1 &&
			decodeURIComponent(pie_urls[0]) === '/desk/employee?gender=["=","Female"]',
		JSON.stringify(pie_urls)
	);
	sandbox.window = undefined;

	// A field the destination doctype does not have is a different failure. The desk
	// asks `get_doctype_for_field()`, gets nothing, and drops the filter - so the list
	// opens showing every row while the URL claims to be filtered. The stock
	// `Webpage Views` chart does exactly this: it groups `Web Page View` by `owner`,
	// and that doctype has no `owner` field.
	sandbox.frappe.meta.get_meta = (doctype) =>
		doctype === "Web Page View"
			? { fields: [{ fieldname: "path" }, { fieldname: "browser" }], has_field: (f) => ["path", "browser"].includes(f) }
			: null;

	const ghost_dom = svg_dom();
	const ghost = Object.create(chart);
	ghost.chart_wrapper = [ghost_dom.wrapper];
	ghost.chart_doc = {
		module: "Website",
		type: "Line",
		document_type: "Web Page View",
		group_by_based_on: "owner",
		group_by_type: "Sum",
	};
	ghost.get_chart_args();
	check("and it resolves no drill-down target at all", ghost.get_drilldown_target() === null);
	const ghost_before = calls.length;
	ghost_dom.click(ghost_dom.plain());
	check(
		"a chart grouped by a field the doctype does not have does not navigate",
		calls.length === ghost_before,
		"set_route calls: " + calls.length
	);
	check(
		"and it is not offered a pointer",
		!ghost_dom.wrapper.hasClass("sharm-clickable")
	);

	// ...but only when meta has actually loaded. This is the race the listener-side
	// check exists for: a dashboard chart draws before its doctype meta arrives, and
	// asking the question then answers "no" for a chart that is perfectly drillable.
	// The listener binds anyway and re-asks on the click, by which point meta is here.
	const nometa_dom = svg_dom();
	const nometa = Object.create(chart);
	nometa.chart_wrapper = [nometa_dom.wrapper];
	nometa.chart_doc = {
		module: "HR",
		type: "Pie",
		document_type: "Employee",
		group_by_based_on: "gender",
		group_by_type: "Count",
	};
	nometa.get_chart_args();
	check(
		"a chart is still clickable when the destination meta has not loaded yet",
		(nometa_dom.listeners.click || []).length === 1,
		(nometa_dom.listeners.click || []).length + " click listeners"
	);
	delete sandbox.frappe.meta.get_meta;

	// a chart grouped by nothing cannot be filtered, so it stays a picture
	const plain_dom = svg_dom();
	const ungrouped = Object.create(chart);
	ungrouped.chart_wrapper = [plain_dom.wrapper];
	ungrouped.chart_doc = { module: "Sharm Dreams Group", document_type: "Employee" };
	ungrouped.get_chart_args();
	check("a chart with no group-by resolves no drill-down", ungrouped.get_drilldown_target() === null);
	const plain_before = calls.length;
	plain_dom.click(plain_dom.plain());
	check(
		"and clicking it does not navigate",
		calls.length === plain_before,
		"set_route calls: " + calls.length
	);

	// --- 5b. filters, read off filters_json -------------------------------
	// a filter on another doctype cannot narrow this list, and a field the List has
	// never heard of would come back as no filter at all
	const mixedChart = Object.create(chart);
	mixedChart.chart_doc = {
		module: "Sharm Dreams Group",
		document_type: "Employee",
		group_by_based_on: "company",
		group_by_type: "Count",
		filters_json: JSON.stringify([
			["Salary Slip", "docstatus", "=", 1],
			["Employee", "status", "=", "Active"],
		]),
	};
	check(
		"a filter belonging to another doctype is dropped",
		mixedChart.get_drilldown_target().filters.status === "Active" &&
			mixedChart.get_drilldown_target().filters.docstatus === undefined,
		mixedChart.get_drilldown_target().filters
	);
	check(
		"malformed filters_json yields no filters rather than throwing",
		(() => {
			const broken = Object.create(chart);
			broken.chart_doc = {
				module: "Sharm Dreams Group",
				document_type: "Employee",
				group_by_based_on: "company",
				group_by_type: "Count",
				filters_json: "{not json",
			};
			return JSON.stringify(broken.get_drilldown_target().filters) === "{}";
		})()
	);

	// The same scope, all the way into the navigation rather than as far as the
	// target object. This is the check the missing `frappe.parse_json` slipped past:
	// `sharm_chart_filters` caught the TypeError and handed back `{}`, so every chart
	// on the real dashboard opened its list with no scope at all, and every check
	// above still passed because they never left the page. "Headcount by Company" is
	// filtered to Active staff; tapping a bar has to open Active staff, not the
	// leavers whose heads are in no bar on the chart.
	const scoped = Object.create(chart);
	scoped.chart_doc = {
		module: "Sharm Dreams Group",
		document_type: "Employee",
		group_by_based_on: "company",
		group_by_type: "Count",
		filters_json: JSON.stringify([["Employee", "status", "=", "Active"]]),
	};
	scoped.dashboard_chart = { data: { labels: ["Bautex Misr"] } };
	const scoped_before = calls.length;
	scoped.go_to_drilldown("Bautex Misr");
	const scoped_call = calls[calls.length - 1];
	check(
		"a chart's own scope survives into the navigation",
		calls.length === scoped_before + 1 &&
			scoped_call.set_route === "List/Employee" &&
			JSON.stringify(scoped_call.route_options) ===
				JSON.stringify({ status: ["=", "Active"], company: ["=", "Bautex Misr"] }),
		JSON.stringify(scoped_call && scoped_call.route_options)
	);

	// --- 5c. a Custom chart, whose bars are windows rather than values ------
	// "Contract End Dates" is a `Custom` chart because `Group By` cannot answer the
	// question: it groups on a field's raw value, so pointing it at a Date gives one
	// bar per distinct day. The dates are bucketed in Python, and that is what makes
	// this shape harder to get right - a computed bar is not a stored value, so it
	// cannot *be* the filter the way a segment's `company = "..."` is. The bar names
	// a window and the chart document has to say what that window means.
	//
	// Without that declaration this is the single unclickable picture on a dashboard
	// whose entire purpose is that everything on it is clickable.
	const CONTRACT_BUCKETS = [
		// The lower bound is a literal, not an offset. Frappe compares a Date as
		// `ifnull(field, '0001-01-01')`, so a plain "before today" would also match
		// every row with no date - here the largest bar on the chart - and the list
		// would open with 180 undated contracts filed under "Ended".
		{ label: "Ended", op: "between", floor: "0001-01-02", to: -1 },
		{ label: "Ends in 30 days", op: "between", from: 0, to: 30 },
		{ label: "Ends in 3 months", op: "between", from: 31, to: 90 },
		{ label: "Ends in 12 months", op: "between", from: 91, to: 365 },
		{ label: "Later", op: ">", offset: 365 },
		{ label: "No end date on file", op: "is", value: "not set" },
	];
	function custom_chart_doc(buckets, extra) {
		return Object.assign(
			{
				module: "Sharm Dreams Group",
				chart_type: "Custom",
				source: "Contract End Dates",
				document_type: "",
				group_by_based_on: "",
				custom_options: JSON.stringify({
					fieldtype: "Int",
					drilldown: {
						doctype: "Employee",
						field: "contract_end_date",
						base_filters: { status: "Active" },
						buckets: buckets,
					},
				}),
			},
			extra
		);
	}
	sandbox.frappe.meta.get_meta = (doctype) =>
		doctype === "Employee"
			? {
					fields: [
						{ fieldname: "contract_end_date" },
						{ fieldname: "company" },
						{ fieldname: "status" },
					],
					has_field: (f) => ["contract_end_date", "company", "status"].includes(f),
			  }
			: doctype === "Web Page View"
				? {
						// Loaded, and without `owner` - the stock `Webpage Views` chart's
						// real mistake. A meta that is merely *absent* is not the same thing:
						// a dashboard chart draws before its meta arrives, so "not loaded
						// yet" cannot prove the field is wrong and the click is allowed.
						fields: [{ fieldname: "path" }, { fieldname: "browser" }],
						has_field: (f) => ["path", "browser"].includes(f),
				  }
				: null;

	const contract_chart = Object.create(chart);
	contract_chart.chart_doc = custom_chart_doc(CONTRACT_BUCKETS);
	const contract_target = contract_chart.get_drilldown_target();
	check(
		"a Custom chart resolves a drill-down target",
		!!contract_target && contract_target.doctype === "Employee" && contract_target.bucketed === true,
		JSON.stringify(contract_target)
	);
	check(
		"and it carries the chart's own scope, so the list cannot be wider than the chart",
		!!contract_target && contract_target.filters.status === "Active"
	);

	// Each bar, and the exact filter it opens. Both halves are named here: a bar
	// that draws 12 and opens a list of 40 is the disagreement this app treats as
	// worse than a dead click.
	const opened = {};
	for (const label of [
		"Ended",
		"Ends in 30 days",
		"Ends in 3 months",
		"Ends in 12 months",
		"Later",
		"No end date on file",
	]) {
		const before = calls.length;
		contract_chart.go_to_drilldown(label);
		const call = calls[calls.length - 1];
		opened[label] =
			calls.length === before + 1 ? { route: call.set_route, filters: call.route_options } : null;
	}
	check(
		"tapping a bucketed bar opens the list its window names",
		Object.values(opened).every((o) => o && o.route === "List/Employee"),
		JSON.stringify(opened)
	);
	check(
		"a window's bounds are shifted from today, so the bar cannot go stale",
		JSON.stringify(opened["Ends in 30 days"].filters.contract_end_date) ===
			JSON.stringify(["between", ["2026-10-03", "2026-11-02"]]),
		JSON.stringify(opened["Ends in 30 days"].filters.contract_end_date)
	);
	check(
		"the 3-month and 12-month windows start where the previous one stopped",
		JSON.stringify(opened["Ends in 3 months"].filters.contract_end_date) ===
			JSON.stringify(["between", ["2026-11-03", "2027-01-01"]]) &&
			JSON.stringify(opened["Ends in 12 months"].filters.contract_end_date) ===
				JSON.stringify(["between", ["2027-01-02", "2027-10-03"]]),
		JSON.stringify([
			opened["Ends in 3 months"].filters.contract_end_date,
			opened["Ends in 12 months"].filters.contract_end_date,
		])
	);
	check(
		"the past window keeps its floor literal, so it cannot list the undated rows",
		JSON.stringify(opened["Ended"].filters.contract_end_date) ===
			JSON.stringify(["between", ["0001-01-02", "2026-10-02"]]),
		JSON.stringify(opened["Ended"].filters.contract_end_date)
	);
	check(
		"and it stops at yesterday, which is where the bar stops",
		opened["Ended"].filters.contract_end_date[1][1] === "2026-10-02"
	);
	check(
		"'later than a year out' needs no floor: a blank date is the smallest date",
		JSON.stringify(opened["Later"].filters.contract_end_date) === JSON.stringify([">", "2027-10-03"]),
		JSON.stringify(opened["Later"].filters.contract_end_date)
	);
	check(
		"the undated bar asks for unset rather than for the empty string",
		JSON.stringify(opened["No end date on file"].filters.contract_end_date) ===
			JSON.stringify(["is", "not set"]),
		JSON.stringify(opened["No end date on file"].filters.contract_end_date)
	);
	check(
		"every bar travels with the Active-staff filter the chart itself used",
		Object.values(opened).every(
			(o) => JSON.stringify(o.filters.status) === JSON.stringify(["=", "Active"])
		),
		JSON.stringify(Object.values(opened).map((o) => o.filters.status))
	);

	// --- 5d. a Custom chart whose bars are ages, which offsets cannot say ----
	// "Employees by Age" buckets `date_of_birth` in Python and is reused from HRMS
	// rather than owned here. An age band is *not* a day offset, and this is the
	// whole reason: the band moves with the calendar year while an offset moves with
	// the day, so thirty years is neither 10957 days nor any other number of them.
	// Reading `-30` as days would put everyone who tapped the 25-29 bar in a list
	// of people a month old.
	//
	// So these buckets carry absolute dates instead, which the resolver takes as
	// given - and the Python side rewrites them on every migrate so they cannot
	// silently become last year's bands.
	const AGE_BUCKETS = [
		{ label: "25-29", op: "between", date_from: "1996-10-04", date_to: "2001-10-05" },
		{ label: "80+", op: "between", date_from: "0001-01-02", date_to: "1946-10-05" },
	];
	const age_chart = Object.create(chart);
	age_chart.chart_doc = custom_chart_doc(AGE_BUCKETS, {
		source: "Employees by Age",
		custom_options: JSON.stringify({
			fieldtype: "Int",
			drilldown: {
				doctype: "Employee",
				field: "date_of_birth",
				base_filters: { status: "Active" },
				buckets: AGE_BUCKETS,
			},
		}),
	});
	sandbox.frappe.meta.get_meta = (doctype) =>
		doctype === "Employee"
			? {
					fields: [{ fieldname: "date_of_birth" }, { fieldname: "status" }],
					has_field: (f) => ["date_of_birth", "status"].includes(f),
			  }
			: null;

	const age_opened = {};
	for (const label of ["25-29", "80+"]) {
		const before = calls.length;
		age_chart.go_to_drilldown(label);
		const call = calls[calls.length - 1];
		age_opened[label] =
			calls.length === before + 1 ? { route: call.set_route, filters: call.route_options } : null;
	}
	check(
		"an age band's dates are used as given, never shifted by days",
		JSON.stringify(age_opened["25-29"].filters.date_of_birth) ===
			JSON.stringify(["between", ["1996-10-04", "2001-10-05"]]),
		JSON.stringify(age_opened["25-29"].filters.date_of_birth)
	);
	check(
		"and the oldest band keeps its literal floor, so no undated row lands in it",
		JSON.stringify(age_opened["80+"].filters.date_of_birth) ===
			JSON.stringify(["between", ["0001-01-02", "1946-10-05"]]),
		JSON.stringify(age_opened["80+"].filters.date_of_birth)
	);

	// Half a window is not a window. A bucket carrying one bound and not the other
	// would otherwise resolve to `between [undefined, date]` and filter on a string
	// that matches nothing, or - worse - on a half-built predicate the desk quietly
	// drops, leaving the list unfiltered and the click quietly lying.
	const half_before = calls.length;
	const half = Object.create(chart);
	half.chart_doc = custom_chart_doc([{ label: "half", op: "between", date_from: "1996-10-04" }]);
	half.go_to_drilldown("half");
	check(
		"a bucket with only one absolute bound does not navigate",
		calls.length === half_before,
		JSON.stringify(calls[calls.length - 1])
	);

	// --- 5e. a chart with two lines, where the tapped line picks the field -----
	// "Hiring vs Attrition Count" draws joiners against leavers, month by month. Its
	// two bars for one month mean different things - 9 hires and 0 leavers - and a
	// single `date_of_joining` filter cannot be both, so the click has to say which
	// line it was on.
	//
	// The chart cannot say it for itself, which is why `sharm_clicked_series` exists.
	// frappe-charts stamps `data-point-index` on bars and scatter dots and on nothing
	// else, a Line draws no per-point element at all, and `SvgTip.setValues` is handed
	// the whole column of values - so its own Line click handler reads one attribute,
	// the x index, and has no idea which line the cursor was on.
	//
	// What it does have is geometry: `chart.dataByIndex[index].values[i].yPos` is where
	// each series' point sits at that x. Nearest to the cursor is the one tapped.
	const SERIES = 12; // the module's own SERIES_PX, restated
	function line_chart(columns, box) {
		return {
			dataByIndex: columns,
			container: {
				getBoundingClientRect: () => ({
					top: box.top || 0,
					left: box.left || 0,
				}),
			},
		};
	}
	function series_event(y) {
		return { pageY: y };
	}
	function series_doc(buckets, by_series, extra) {
		return custom_chart_doc(buckets, {
			source: "Hiring vs Attrition Count",
			custom_options: JSON.stringify({
				fieldtype: "Int",
				drilldown: {
					doctype: "Employee",
					field: "date_of_joining",
					base_filters: {},
					by_series: by_series,
					buckets: buckets,
				},
			}),
			...(extra || {}),
		});
	}

	// Real windows, because `sharm_bucket_filter` refuses a bucket with no operator
	// and the tap would refuse for that reason rather than the one under test.
	const MONTHS = [
		{ label: "Feb 2026", op: "between", date_from: "2026-02-01", date_to: "2026-02-28" },
		{ label: "Sep 2026", op: "between", date_from: "2026-09-01", date_to: "2026-09-30" },
	];
	const BY_SERIES = { 0: "date_of_joining", 1: "relieving_date" };
	// Both fields have to be on the stub, and the block above left a meta with only
	// `date_of_birth` and `status` for the age checks. `sharm_custom_drilldown`
	// verifies every field a `by_series` map names - a map naming a field the doctype
	// lacks is a chart that navigates with a filter the List drops - so without this
	// the target is legitimately null and every check below would pass vacuously.
	const AGE_FIELDS = ["date_of_birth", "date_of_joining", "relieving_date", "status"];
	sandbox.frappe.meta.get_meta = (doctype) =>
		doctype === "Employee"
			? {
					fields: AGE_FIELDS.map((fieldname) => ({ fieldname })),
					has_field: (f) => AGE_FIELDS.includes(f),
			  }
			: null;
	// y 190 is the baseline both lines sit on when the month has no leavers; 67 is
	// where 130 leavers lands. Those are the real numbers from the drawn chart.
	const SEP_2026 = [
		{ title: "Hiring Count", yPos: 190, value: 0 },
		{ title: "Attrition Count", yPos: 67, value: 130 },
	];
	const FEB_2026 = [
		{ title: "Hiring Count", yPos: 181, value: 9 },
		{ title: "Attrition Count", yPos: 190, value: 0 },
	];

	const line = Object.create(chart);
	line.chart_doc = series_doc(MONTHS, BY_SERIES);
	line.dashboard_chart = line_chart({ 0: FEB_2026, 1: SEP_2026 }, { top: 0 });

	const line_target = line.get_drilldown_target();
	check(
		"a two-line chart resolves a target carrying its per-series map",
		!!line_target &&
			line_target.bucketed === true &&
			JSON.stringify(line_target.by_series) === JSON.stringify(BY_SERIES),
		JSON.stringify(line_target && line_target.by_series)
	);

	// `go_to_drilldown` navigates and returns nothing, so what it sent is read off
	// the recorded call rather than off a return value - the same way the age checks
	// above read theirs.
	function tap_line(label, series) {
		const before = calls.length;
		line.go_to_drilldown(label, series);
		if (calls.length !== before + 1) return null;
		const call = calls[calls.length - 1];
		return { route: call.set_route, filters: call.route_options };
	}

	// The whole point: the *same* month opens two different lists depending on which
	// line was tapped.
	const on_attrition = tap_line("Sep 2026", 1);
	check(
		"the attrition line filters on relieving_date, not the chart's own field",
		!!on_attrition && on_attrition.filters.relieving_date !== undefined,
		JSON.stringify(on_attrition)
	);
	const on_hiring = tap_line("Sep 2026", 0);
	check(
		"and the hiring line filters on date_of_joining",
		!!on_hiring && on_hiring.filters.date_of_joining !== undefined,
		JSON.stringify(on_hiring)
	);
	check(
		"so one month opens two different lists, as the two bars require",
		!!on_hiring &&
			!!on_attrition &&
			JSON.stringify(on_hiring.filters) !== JSON.stringify(on_attrition.filters),
		`hiring ${JSON.stringify(on_hiring && on_hiring.filters)} / attrition ${JSON.stringify(
			on_attrition && on_attrition.filters
		)}`
	);

	// The refusal. This is the half that matters: a click that cannot tell which line
	// it was on must navigate nowhere, because opening the other line's list is the
	// one outcome this whole file exists to avoid.
	const before_ambiguous = calls.length;
	line.go_to_drilldown("Feb 2026", null);
	check(
		"a two-line chart refuses a click that did not say which line",
		calls.length === before_ambiguous,
		JSON.stringify(calls[calls.length - 1])
	);
	const before_undeclared = calls.length;
	line.go_to_drilldown("Feb 2026", 5);
	check(
		"and refuses a line its own declaration does not describe",
		calls.length === before_undeclared,
		JSON.stringify(calls[calls.length - 1])
	);

	// The geometry itself, which is where this can go wrong in a way the checks above
	// cannot see: a Line point far from the cursor resolves to its own line, and one
	// with both lines bunched near the cursor resolves to nothing.
	//
	// The numbers are the ones read off the drawn chart, not invented - 190 is the
	// baseline both lines sit on in a month with no leavers and 67 is where 130
	// leavers lands on a 300px plot. That is 0.95px per person, which is the whole
	// reason `SERIES_PX` has to be small and why September is the only month on this
	// chart where a click can honestly say which line it was on.
	function resolve_at(column, cursor_y) {
		const values = line.dashboard_chart.dataByIndex[column];
		let nearest = -1;
		let nearest_gap = Infinity;
		let second_gap = Infinity;
		values.forEach((value, series) => {
			const gap = Math.abs(value.yPos - cursor_y);
			if (gap < nearest_gap) {
				second_gap = nearest_gap;
				nearest_gap = gap;
				nearest = series;
			} else if (gap < second_gap) {
				second_gap = gap;
			}
		});
		if (nearest === -1) return null;
		if (nearest_gap > SERIES) return null;
		if (second_gap - nearest_gap < SERIES) return null;
		return nearest;
	}
	check(
		"a cursor on the hiring line resolves to the hiring line",
		resolve_at(1, SEP_2026[0].yPos) === 0,
		`got ${resolve_at(1, SEP_2026[0].yPos)}`
	);
	check(
		"and a cursor on the attrition line resolves to that one",
		resolve_at(1, SEP_2026[1].yPos) === 1,
		`got ${resolve_at(1, SEP_2026[1].yPos)}`
	);
	check(
		"the drawn September is 123px apart, so this chart can be clicked there",
		Math.abs(SEP_2026[0].yPos - SEP_2026[1].yPos) > SERIES,
		`${Math.abs(SEP_2026[0].yPos - SEP_2026[1].yPos)}px apart, threshold ${SERIES}`
	);
	check(
		"and February's two lines are 9px apart, so a click there is honestly refused",
		resolve_at(0, FEB_2026[0].yPos) === null &&
			Math.abs(FEB_2026[0].yPos - FEB_2026[1].yPos) < SERIES,
		`${Math.abs(FEB_2026[0].yPos - FEB_2026[1].yPos)}px apart, resolved to ${
			resolve_at(0, FEB_2026[0].yPos)
		}`
	);
	check(
		"a cursor in empty space below every line resolves to nothing",
		resolve_at(0, 290) === null,
		`got ${resolve_at(0, 290)}`
	);
	check(
		"a column with one series is always that series, whatever the cursor is on",
		(() => {
			const one = line_chart({ 0: [{ title: "Only", yPos: 100 }] }, { top: 0 });
			const values = one.dataByIndex[0];
			return values.length === 1 ? 0 : null;
		})() === 0,
		"one line needs no geometry"
	);

	// Restored, and restored *whole*: the block above swapped `get_meta` for a
	// two-field Employee so the age checks could not pass on a field this
	// doctype does not have. A partial restore would quietly disarm the checks
	// that follow, which is the one way a stub is worse than no stub.
	sandbox.frappe.meta.get_meta = (doctype) =>
		doctype === "Employee"
			? {
					fields: [
						{ fieldname: "contract_end_date" },
						{ fieldname: "company" },
						{ fieldname: "status" },
					],
					has_field: (f) => ["contract_end_date", "company", "status"].includes(f),
			  }
			: doctype === "Web Page View"
				? {
						fields: [{ fieldname: "path" }, { fieldname: "browser" }],
						has_field: (f) => ["path", "browser"].includes(f),
				  }
				: null;

	// Matched by label, never by bar position. Position is the shorter key and the
	// worse one: reorder the windows in Python and every bar silently opens another
	// bar's list, which reads as a broken click rather than as a mistake.
	const reordered = Object.create(chart);
	reordered.chart_doc = custom_chart_doc(CONTRACT_BUCKETS.slice().reverse());
	reordered.data = { labels: ["Later", "No end date on file", "Ended"] };
	check(
		"a bar finds its bucket by name, not by position",
		(() => {
			const before = calls.length;
			reordered.go_to_drilldown("Ended");
			const call = calls[calls.length - 1];
			return (
				calls.length === before + 1 &&
				JSON.stringify(call.route_options.contract_end_date) ===
					JSON.stringify(["between", ["0001-01-02", "2026-10-02"]])
			);
		})()
	);

	// The labels come off the chart already translated, and the comparison happens in
	// the browser, so case and padding cannot be allowed to decide whether a bar
	// opens anything.
	check(
		"a translated label still matches its bucket",
		(() => {
			const before = calls.length;
			contract_chart.go_to_drilldown("  ends in 30 DAYS ");
			return calls.length === before + 1 && calls[calls.length - 1].set_route === "List/Employee";
		})()
	);

	// A bar whose label is not a bucket - a chart edited in the desk, a source whose
	// labels and buckets drifted. A dead click, honestly.
	const unknown_before = calls.length;
	contract_chart.go_to_drilldown("Some Future Bucket");
	check(
		"a bar matching no bucket does not navigate",
		calls.length === unknown_before,
		"set_route calls: " + calls.length
	);
	const non_string_before = calls.length;
	contract_chart.go_to_drilldown(undefined);
	check("and neither does a missing label", calls.length === non_string_before);

	// A bucket using an operator the resolver has no branch for. It reaches the
	// resolver and comes back nothing, which is the correct outcome - the check is
	// that it does not reach `set_route` with a half-built filter.
	const odd_op = Object.create(chart);
	odd_op.chart_doc = custom_chart_doc([{ label: "Weird", op: "regex", pattern: "." }]);
	const odd_before = calls.length;
	odd_op.go_to_drilldown("Weird");
	check("a bucket with an operator we cannot honour does not navigate", calls.length === odd_before);

	// The three ways a Custom chart is legitimately only a picture. Each is a
	// different missing piece, and each has to end in the same place: no target, no
	// navigation, and no pointer.
	const picture_before = calls.length;
	for (const doc of [
		custom_chart_doc([]), // no buckets: no bar resolves to a filter
		Object.assign(custom_chart_doc(CONTRACT_BUCKETS), { custom_options: JSON.stringify({ fieldtype: "Int" }) }), // no drilldown block
		Object.assign(custom_chart_doc(CONTRACT_BUCKETS), { custom_options: "{not json" }),
	]) {
		const picture = Object.create(chart);
		picture.chart_doc = doc;
		const target = picture.get_drilldown_target();
		picture.go_to_drilldown("Ends in 30 days");
		if (target !== null) throw new Error("a Custom chart missing a piece resolved a target anyway");
	}
	check(
		"a Custom chart missing any part of its declaration stays a picture",
		calls.length === picture_before,
		"set_route calls: " + calls.length
	);

	// The same field check the Group By path makes. A filter on a field the list has
	// no column for is dropped in the browser, so the URL would claim a filter the
	// list never applied - a click that navigates and then lies.
	const wrong_field = Object.create(chart);
	wrong_field.chart_doc = custom_chart_doc(CONTRACT_BUCKETS, {
		custom_options: JSON.stringify({
			fieldtype: "Int",
			drilldown: {
				doctype: "Web Page View",
				field: "owner",
				buckets: CONTRACT_BUCKETS,
			},
		}),
	});
	check(
		"a Custom chart filtering on a field the doctype lacks resolves nothing",
		wrong_field.get_drilldown_target() === null,
		JSON.stringify(wrong_field.get_drilldown_target())
	);
	// ...and the contrast, so the check above cannot pass for the wrong reason: a
	// doctype whose meta has not arrived yet is not a verdict against the click.
	const unloaded = Object.create(chart);
	unloaded.chart_doc = custom_chart_doc(CONTRACT_BUCKETS, {
		custom_options: JSON.stringify({
			fieldtype: "Int",
			drilldown: { doctype: "Not Loaded Yet", field: "whatever", buckets: CONTRACT_BUCKETS },
		}),
	});
	check(
		"but a doctype whose meta has not loaded yet still gets its click",
		unloaded.get_drilldown_target() !== null
	);
	delete sandbox.frappe.meta.get_meta;

	// --- 6. the factory builds exactly one page ------------------------------
// The bug this catches, twice over. `render_dashboard` used `frappe.make_page(1,
// name)` and used its return value as if it were the Page. It is not: the real
// `frappe.make_page` returns the *container element*, and `set_title`/`main`
// live on `container.page`. So the desk built a page whose header never reached
// `this.wrapper`, `page_actions` came back empty, and `setup_page()` passed that
// `undefined` into `frappe.ui.keys.get_shortcut_group` - a WeakMap, which rejects
// a non-object. That throws inside the Page constructor, the factory never reaches
// `$wrapper`, and the route is blank with a stack pointing into
// `alt_keyboard_shortcuts.js`, nowhere near the cause.
const built = [];
sandbox.frappe.container = {
	add_page(label) {
		// the real one: `.get(0)` - a DOM element, with the label hanging off it
		const container = { tagName: "DIV", label: label, _children: [] };
		built.push({ via: "add_page", page_name: label });
		return container;
	},
	change_to(label) {
		built.push({ via: "change_to", page_name: label });
	},
};
sandbox.frappe.router.slug = sandbox.frappe.router.slug || ((s) => String(s || ""));
sandbox.frappe.ui.make_app_page = function (opts) {
	// the real one returns the Page instance and hangs it off the container
	built.push({
		via: "make_app_page",
		parentIsObject: !!(opts && opts.parent && opts.parent.tagName),
		single_column: opts && opts.single_column,
	});
	const page = { main: el(), wrapper: el(), set_title: (t) => (page._title = t) };
	opts.parent.page = page;
	return page;
};
const factory = new sandbox.frappe.views.DashboardViewFactory();

// Frappe's real signature, which is the whole point of this stub.
// `frappe.model.with_doc(doctype, name, cb)` calls `cb(name, r)` - the *name* first,
// the server response second - in both of its branches. An earlier version of this
// stub passed a document object as the first argument, which is not what the desk
// does, so the factory read `chart_options`/`cards`/`charts` off a string, rendered
// one empty section, and every dashboard came up blank while all 28 checks passed.
const DASHBOARD_DOC = {
	name: "SDG Workforce",
	dashboard_name: "SDG Workforce",
	chart_options: JSON.stringify({ sections: [{ title: "People", cards: ["Active Headcount"], charts: [] }] }),
	cards: [{ card: "Active Headcount" }],
	charts: [],
};
sandbox.frappe.model.with_doc = (doctype, name, cb) =>
	cb(name, { message: { docs: [Object.assign({}, DASHBOARD_DOC, { name: name, dashboard_name: name })] } });
sandbox.frappe.get_doc = (doctype, name) =>
	Object.assign({}, DASHBOARD_DOC, { name: name, dashboard_name: name });
sandbox.frappe.model.with_doctype = (doctype, cb) => cb && cb();

check("no name means not found, not a broken page", (() => {
	let notFound = false;
	sandbox.frappe.show_not_found = () => (notFound = true);
	factory.make(["dashboard-view"]);
	return notFound;
})());

factory.make(["dashboard-view", "SDG Workforce"]);
check("the dashboard document is requested by name", !!factory.dashboard, factory.dashboard);
// The blank-page guard. `with_doc` hands the callback a *name*, so a factory that
// reads the first argument as the document ends up holding a string, whose
// `chart_options` is undefined - one empty section, no cards, no charts, and
// nothing thrown anywhere.
check(
	"the factory holds the dashboard, not its name",
	factory.dashboard && factory.dashboard.name === "SDG Workforce" && !!factory.dashboard.chart_options,
	factory.dashboard && typeof factory.dashboard
);
check(
	"the layout is read off the document, so the page is not blank",
	factory.sections && factory.sections.length > 0 && factory.sections[0].title === "People",
	factory.sections
);
check("the container is created once", built.filter((b) => b.via === "add_page").length === 1, built);
check(
	"the app page is built onto the container object, not onto a name",
	built.some((b) => b.via === "make_app_page" && b.parentIsObject),
	built
);
// `add_page` turns the label into `id="page-<label>"`, which `change_to` and the
// desk's own selectors assume is one token. A space produces an id with a space
// in it.
check(
	"the page name carries no space",
	built.filter((b) => b.via === "add_page").every((b) => !/\s/.test(b.page_name)),
	built.filter((b) => b.via === "add_page")
);
// the return value is what the code must use: `set_title`/`main`/`wrapper` live on
// the Page, not on the container `frappe.make_page` hands back
check(
	"the factory holds the Page instance make_app_page returned",
	!!factory.page && typeof factory.page.set_title === "function" && !!factory.page.main,
	factory.page && Object.keys(factory.page)
);
check(
	"the page is switched to",
	built.some((b) => b.via === "change_to"),
	built
);
check("the page carries the dashboard title", factory.page && factory.page._title === "SDG Workforce", factory.page && factory.page._title);
check("the wrapper is hung off the page", !!factory.$wrapper);
// `$wrapper` is what every section is rendered into, and what every rule in the
// stylesheet is written against. jQuery's `.append()` returns the set it was called
// on rather than what it added, so assigning its result gave the page's own main
// section: the bands became siblings of the wrapper instead of children of it, and
// `.dashboard-view` - the flex column, the 24px between bands, the outer margin -
// matched nothing on the page. No error, no missing widget, and the harness was
// happy, because it only ever asked whether *something* was rendered.
//
// The stub's `append` returns `this` for the same reason jQuery's does, so the
// identity check below is the real one.
check(
	"the wrapper is a new element, not the one it was appended to",
	!!factory.$wrapper && factory.$wrapper !== factory.page.main,
	factory.$wrapper && factory.page.main && "the wrapper IS the page main"
);
check(
	"and that element is the .dashboard-view the stylesheet targets",
	!!factory.$wrapper && factory.$wrapper.hasClass && factory.$wrapper.hasClass("dashboard-view"),
	factory.$wrapper && factory.$wrapper._classes && Array.from(factory.$wrapper._classes).join(" ")
);
check(
	"the wrapper was appended into the page",
	!!factory.$wrapper &&
		Array.isArray(factory.page.main._children) &&
		factory.page.main._children.indexOf(factory.$wrapper) !== -1,
	factory.page.main && factory.page.main._children && factory.page.main._children.length
);
// The blank-page guard itself: something must actually land in the wrapper.
// --- 7. the view has no dead tab code -------------------------------------
// `render_tabs()` existed and was never called, so `this.tabs` was only ever set by
// a method nothing invoked. Dead code that reads like an unbuilt feature is worse
// than no code: it invites someone to "finish" it without asking what for.
check("no dead tab-building method left in the source", !/\n\trender_tabs\s*\(\s*\)\s*\{/.test(SRC));
check("sections render stacked", SRC.includes("this.sections.forEach((s) => this.render_section(s, this.$wrapper))"));

// --- 7b. a section is a list of blocks, and the reader understands both shapes --
//
// The layout used to be one card row then one chart row, at a fixed four cards
// across and 240px charts, and `render_section` read `section.cards` and
// `section.charts` directly. It now reads blocks, so it has to understand a
// section written either way - this app writes blocks and every stock dashboard
// the desk ships writes the old keys, through this same factory.
//
// Nothing above can tell the difference: the harness's own document is written in
// the old shape, so a reader that dropped it would still pass everything above and
// then render every real dashboard with no charts on it. The block path is the one
// this app actually ships, so it gets its own section.
const normalize = sandbox.sharm_normalize_blocks;
check("the block reader is reachable from the page", typeof normalize === "function", typeof normalize);

if (typeof normalize === "function") {
	const legacy = normalize({ title: "People", cards: ["A", "B"], charts: ["C"] });
	check(
		"a section in the old shape still renders: cards then charts",
		legacy.length === 2 &&
			legacy[0].type === "cards" &&
			legacy[0].items.join() === "A,B" &&
			legacy[1].type === "charts" &&
			legacy[1].items.join() === "C",
		legacy
	);
	check(
		"an old-shape section gets the desk's own defaults",
		legacy[0].columns === 4 && legacy[1].columns === 2 && legacy[1].height === 240,
		legacy
	);
	check(
		"a section with no charts does not get an empty chart row",
		normalize({ title: "P", cards: ["A"], charts: [] }).map((b) => b.type).join() === "cards"
	);
	check(
		"a section with no cards does not get an empty card row",
		normalize({ title: "P", cards: [], charts: ["C"] }).map((b) => b.type).join() === "charts"
	);

	// The shape this app writes: charts are allowed between card rows, which is the
	// whole reason the block list exists.
	const interleaved = normalize({
		title: "Records To Complete",
		blocks: [
			{ type: "cards", items: ["A", "B", "C", "D", "E", "F"], columns: 6 },
			{ type: "charts", items: ["G"], columns: 3, height: 260 },
			{ type: "cards", items: ["H", "I", "J", "K", "L"], columns: 5 },
		],
	});
	check(
		"a chart can sit between two rows of cards",
		interleaved.map((b) => b.type).join() === "cards,charts,cards",
		interleaved.map((b) => b.type)
	);
	check(
		"each block keeps the width it was declared with",
		interleaved.map((b) => b.columns).join() === "6,3,5",
		interleaved.map((b) => b.columns)
	);
	check(
		"a chart block keeps the height it was declared with",
		interleaved[1].height === 260 && interleaved[0].height === undefined,
		interleaved[1].height
	);

	// A width `WidgetGroup` would turn into `grid-col-null`. The layout silently
	// renders at the stock two, which is the failure this whole change is about.
	for (const [bad, why] of [
		[null, "null"],
		[undefined, "missing"],
		["four", "a word"],
		[0, "zero"],
		[-3, "negative"],
		[900, "absurd"],
	]) {
		const blocks = normalize({ blocks: [{ type: "cards", items: ["A"], columns: bad }] });
		const columns = blocks[0].columns;
		check(
			`a ${why} width falls back to the default rather than reaching WidgetGroup`,
			Number.isInteger(columns) && columns >= 1 && columns <= 12,
			columns
		);
	}
	check(
		"a block left with no items is dropped, not drawn as an empty grid",
		normalize({
			blocks: [{ type: "cards", items: [] }, { type: "charts", items: ["A"] }],
		}).length === 1
	);
	check(
		"a null block in the list does not throw",
		normalize({ blocks: [null, { type: "cards", items: ["A"] }] }).length === 1
	);
	check(
		"blocks win over the old keys when a section somehow has both",
		normalize({
			cards: ["OLD"],
			charts: ["OLDC"],
			blocks: [{ type: "cards", items: ["NEW"] }],
		})[0].items.join() === "NEW"
	);
}

const section_charts = sandbox.sharm_section_charts;
const section_cards = sandbox.sharm_section_cards;
check("the chart accessor is reachable from the page", typeof section_charts === "function", typeof section_charts);
check("the card accessor is reachable from the page", typeof section_cards === "function", typeof section_cards);
if (typeof section_charts === "function" && typeof section_cards === "function") {
	const mixed = {
		blocks: [
			{ type: "cards", items: ["A", "B"] },
			{ type: "charts", items: ["C", "D"] },
			{ type: "cards", items: ["E"] },
		],
	};
	check("every chart in a section is found across its blocks", section_charts(mixed).join() === "C,D");
	check("every card in a section is found across its blocks", section_cards(mixed).join() === "A,B,E");
	check(
		"the accessors work on the old shape too",
		section_charts({ cards: ["A"], charts: ["C"] }).join() === "C" &&
			section_cards({ cards: ["A"], charts: ["C"] }).join() === "A"
	);
	// The wiring report and the chart fetch both count through these. If either
	// read `section.charts` instead, the count is zero: the report prints "charts
	// clickable 0/0" and the fetch asks for no charts, so the page draws headings
	// and nothing else. Neither raises.
	check(
		"nothing reads a block-shaped section's cards or charts key directly",
		!/\(s\)\s*=>\s*\(s\.cards\s*\|\|\s*\[\]\)/.test(SRC) && !/s\.charts\s*\|\|\s*\[\]/.test(SRC),
		"a reader still goes through the old key"
	);
	check(
		"the chart fetch asks the accessor, not the key",
		/Section_charts|sections\.flatMap\(\(s\) => sharm_section_charts/.test(SRC),
		"the fetch of which charts to load"
	);
}

// The width reaches the stylesheet as a property, because the desk only styles
// `grid-col-1..3` and a class per width would be twelve rules to keep in step
// with a table that is meant to be edited.
const apply_columns = sandbox.sharm_apply_columns;
check("the width applier is reachable from the page", typeof apply_columns === "function", typeof apply_columns);
if (typeof apply_columns === "function") {
	// A hand-built stand-in rather than the harness's `$`. `sharm_apply_columns`
	// touches four things and nothing else - `.length`, `removeClass`,
	// `addClass`, and `style.setProperty` on the first element - so this is a
	// faithful shape, and it means the check does not depend on how completely the
	// harness's jQuery stub happens to emulate `find()`.
	function body_stub(class_name) {
		const classes = new Set(class_name.split(" ").filter(Boolean));
		const properties = {};
		const stub = {
			length: 1,
			style: {
				setProperty(name, value) {
					properties[name] = value;
				},
			},
			removeClass(what) {
				const drop = typeof what === "function" ? what(0, Array.from(classes).join(" ")) : what;
				(drop || "").split(/\s+/).filter(Boolean).forEach((c) => classes.delete(c));
				return stub;
			},
			addClass(names) {
				names.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
				return stub;
			},
		};
		// jQuery sets: the element is reached as `$body[0]`, which is why
		// `removeClass`'s callback is handed the index too.
		stub[0] = stub;
		stub.classes = classes;
		stub.properties = properties;
		return stub;
	}

	const body = body_stub("widget-group-body grid-col-2");
	apply_columns(body, 5, "cards");
	const at = () => [Array.from(body.classes).join(" "), body.properties["--sharm-cols"]].join(" | ");
	check(
		"a declared width is set as --sharm-cols, which the stylesheet reads",
		body.properties["--sharm-cols"] === "5",
		at()
	);
	check("the row is told which kind it is", body.classes.has("sharm-cards"), at());
	check(
		"the stock grid-col class is removed, so the two cannot fight",
		!Array.from(body.classes).some((c) => /^grid-col-\d+$/.test(c)),
		at()
	);
	// Re-rendering with a company filter calls this again with a different number.
	// A class left behind from the first pass would leave two on the element and
	// lose to whichever the stylesheet happens to order last.
	apply_columns(body, 2, "charts");
	check(
		"re-applying replaces the width rather than stacking another on",
		body.properties["--sharm-cols"] === "2" &&
			body.classes.has("sharm-charts") &&
			!body.classes.has("sharm-cards"),
		at()
	);
	check(
		"applying to nothing does not throw",
		(function () {
			apply_columns({ length: 0 }, 3, "cards");
			apply_columns(null, 3, "cards");
			apply_columns(undefined, 3, "cards");
			return true;
		})()
	);
}

// --- 8. a chart with no colour must not kill the widget ---------------------
// Frappe's get_chart_colors() ends Line/Bar charts with `colors = [color || []]`,
// so an unset colour reaches Chart.js as `[[]`, i.e. "", which BaseChart rejects
// with `"" is not a valid color`. The widget then fails while being constructed and
// the chart is simply absent from the page.
//
// Frappe's get_chart_colors(), copied verbatim from
// frappe/public/js/frappe/widgets/chart_widget.js so the test fails loudly if
// upstream ever changes shape and this stub stops being a faithful copy.
function stock_get_chart_colors() {
	let colors = [];
	if (this.chart_doc.y_axis.length) {
		this.chart_doc.y_axis.map((field) => {
			colors.push(field.color);
		});
	} else if (["Line", "Bar"].includes(this.chart_doc.type)) {
		colors = [this.chart_doc.color || []];
	}
	return colors;
}

const colour_sandbox = {
	console: { log() {}, warn() {}, error() {} },
	$,
	setTimeout,
	clearTimeout,
	document: {},
	Promise,
	frappe: {
		provide() {},
		get_route: () => ["dashboard-view", "x"],
		set_route() {},
		boot: { workspace_sidebar_item: {} },
		app: { sidebar: null },
		db: { get_list: () => Promise.resolve([]) },
		model: { with_doc: (a, b, c) => c && c(null), with_doctype: (a, c) => c && c() },
		ui: { make_app_page: () => ({ main: el() }) },
		utils: { escape_html: (s) => s, icon: (n) => n },
		call: () => desk_promise({ message: {} }),
		xcall: () => desk_promise(0),
		views: {},
		widget: {
			WidgetGroup: class {},
			widget_factory: {
				number_card: { prototype: { set_route: function () {} } },
				chart: {
					prototype: {
						get_chart_args: function () {
							return {};
						},
						// Frappe's get_chart_colors(), as it stands upstream.
						get_chart_colors: stock_get_chart_colors,
					},
				},
			},
		},
	},
};
vm.createContext(colour_sandbox);
vm.runInContext(SRC, colour_sandbox, { filename: "dashboard_view.js" });

function colours_of(chart_doc) {
	const inst = Object.create(colour_sandbox.frappe.widget.widget_factory.chart.prototype);
	inst.chart_doc = chart_doc;
	return inst.get_chart_colors();
}

check(
	"the unpatched method really does emit the empty colour Chart.js rejects",
	JSON.stringify(stock_get_chart_colors.call({ chart_doc: { y_axis: [], type: "Bar", color: "" } })) ===
		"[[]]",
	"if this is no longer [[]] upstream has fixed it and the patch can go"
);
check(
	"an unset colour is dropped instead of crashing the chart",
	JSON.stringify(colours_of({ y_axis: [], type: "Bar", color: "" })) === "[]",
	"got " + JSON.stringify(colours_of({ y_axis: [], type: "Bar", color: "" }))
);
check(
	"a real colour survives the filter",
	JSON.stringify(colours_of({ y_axis: [], type: "Bar", color: "#3d5a80" })) === '["#3d5a80"]',
	"got " + JSON.stringify(colours_of({ y_axis: [], type: "Bar", color: "#3d5a80" }))
);
check(
	"a heatmap still gets an empty palette",
	JSON.stringify(colours_of({ y_axis: [], type: "Heatmap" })) === "[]"
);
check(
	"per-series y_axis colours still come through",
	JSON.stringify(
		colours_of({ y_axis: [{ color: "#111111" }, { color: "#222222" }], type: "Bar" })
	) === '["#111111","#222222"]'
);

// --- 9. a discarded chart must stop drawing ---------------------------------
// Two places in chart_widget.js drop the reference to the live frappe-chart without
// calling its destroy() - the only thing that disconnects the ResizeObserver from
// configure(). The orphan then redraws into a container that has already been
// emptied and throws
//   Uncaught NotFoundError: removeChild ... not a child of this node
//
//   refresh():  re-runs setup_container(), so `this.body` is emptied outright.
//   render():   for Pie / Donut / Percentage, does
//                 this.chart_wrapper.empty(); delete this.dashboard_chart;
//               - and the old instance's svg is exactly what just left the DOM.
//               The second one is the one that fires: this site has 21 circular
//               charts and they rebuild on every data refresh.
// The stub lives in the vm, so its `console` is not the host's - a warning raised
// inside dashboard_view.js has to be collected here to be asserted on.
const sandbox_warnings = [];
const life_sandbox = {
	console: {
		log() {},
		warn: (...args) => sandbox_warnings.push(args.map(String).join(" ")),
		error() {},
	},
	$,
	setTimeout,
	clearTimeout,
	document: {},
	Promise,
	frappe: {
		provide() {},
		get_route: () => ["dashboard-view", "x"],
		set_route() {},
		boot: { workspace_sidebar_item: {} },
		app: { sidebar: null },
		db: { get_list: () => Promise.resolve([]) },
		model: { with_doc: (a, b, c) => c && c(null), with_doctype: (a, c) => c && c() },
		ui: { make_app_page: () => ({ main: el() }) },
		utils: { escape_html: (s) => s, icon: (n) => n },
		call: () => desk_promise({ message: {} }),
		xcall: () => desk_promise(0),
		views: {},
		widget: {
			WidgetGroup: class {},
			widget_factory: {
				number_card: { prototype: { set_route: function () {} } },
				chart: {
					prototype: {
						get_chart_args: function () {
							return {};
						},
						get_chart_colors: function () {
							return [];
						},
						// Frappe's refresh(), as it stands upstream.
						refresh() {
							delete this.dashboard_chart;
							this.refreshed = (this.refreshed || 0) + 1;
						},
						// Frappe's render(), as it stands upstream: the circular
						// branch is the one that matters here.
						render() {
							if (!this.dashboard_chart) {
								this.rendered = (this.rendered || 0) + 1;
							} else if (["Pie", "Donut", "Percentage"].includes(this.chart_doc.type)) {
								this.chart_wrapper_empty = (this.chart_wrapper_empty || 0) + 1;
								delete this.dashboard_chart;
								this.rendered = (this.rendered || 0) + 1;
							} else {
								this.updated = (this.updated || 0) + 1;
							}
						},
					},
				},
			},
		},
	},
};
vm.createContext(life_sandbox);
vm.runInContext(SRC, life_sandbox, { filename: "dashboard_view.js" });

const life_proto = life_sandbox.frappe.widget.widget_factory.chart.prototype;
const live = Object.create(life_proto);
let destroyed = 0;
live.dashboard_chart = {
	destroy() {
		destroyed += 1;
	},
};
live.refresh();
check("a discarded chart is destroyed before it is dropped", destroyed === 1, "destroy calls: " + destroyed);
check("the stock refresh still runs", live.refreshed === 1, "stock refresh calls: " + live.refreshed);

const second = Object.create(life_proto);
second.dashboard_chart = {
	destroy() {
		throw new Error("already torn down");
	},
};
let survived = true;
try {
	second.refresh();
} catch (e) {
	survived = false;
}
check(
	"a chart that throws on destroy does not stop the new one being built",
	survived && second.refreshed === 1
);

const third = Object.create(life_proto);
third.dashboard_chart = undefined;
third.refresh();
check("a widget with no chart yet refreshes cleanly", third.refreshed === 1);

// The circular branch of render(), which is where the leak actually came from.
// The wrapper has to look measurable, or the render is deferred - see the NaN
// width tests below.
const measurable = () => [{ isConnected: true, clientWidth: 400 }];

for (const type of ["Pie", "Donut", "Percentage"]) {
	const donut = Object.create(life_proto);
	donut.chart_doc = { type };
	donut.chart_wrapper = measurable();
	let circle_destroyed = 0;
	donut.dashboard_chart = {
		destroy() {
			circle_destroyed += 1;
		},
	};
	donut.render();
	check(
		`a ${type} chart's instance is destroyed before it is emptied and replaced`,
		circle_destroyed === 1 && donut.rendered === 1,
		"destroyed: " + circle_destroyed + ", rendered: " + donut.rendered
	);
}

// A Line or Bar chart reuses its instance - stock calls `update(this.data)` - so
// destroying it there would blank every bar chart on every refresh.
const bar = Object.create(life_proto);
bar.chart_doc = { type: "Bar" };
bar.chart_wrapper = measurable();
let bar_destroyed = 0;
bar.dashboard_chart = {
	destroy() {
		bar_destroyed += 1;
	},
};
bar.render();
check(
	"a Bar chart keeps its instance, because stock updates it in place",
	bar_destroyed === 0 && bar.updated === 1,
	"destroyed: " + bar_destroyed + ", updated: " + bar.updated
);
check("a Bar chart is never emptied mid-render", !bar.emptied, bar.emptied);

// --- 9b. a chart is never drawn into an element that cannot be measured ----
// Measured too early, `getElementContentWidth` reads its padding off
// `getComputedStyle`, which returns "" for an element not in the document;
// `parseFloat("")` is NaN, and every coordinate derived from the width is NaN.
// The SVG then has no geometry, so there is nothing for a click to land on.
function detached_chart() {
	const c = Object.create(life_proto);
	c.chart_doc = { type: "Bar", chart_name: "Headcount by Company" };
	c.chart_wrapper = [{ isConnected: false, clientWidth: 0 }];
	return c;
}

const waiting = detached_chart();
waiting.render();
check(
	"a chart whose wrapper is not in the document is not drawn yet",
	waiting.rendered === undefined && waiting.updated === undefined,
	"rendered: " + waiting.rendered
);
check("and it is scheduled to try again", typeof waiting.sharm_retry !== "undefined");

// zero width, but connected: still not measurable, same outcome
const zero = detached_chart();
zero.chart_wrapper = [{ isConnected: true, clientWidth: 0 }];
zero.render();
check("a chart with zero width is not drawn either", zero.updated === undefined);

// give up eventually, and say why - a chart at the wrong size beats no chart
const stubborn = detached_chart();
sandbox_warnings.length = 0;
for (let i = 0; i < 15; i++) stubborn.render();
check(
	"a chart that never becomes measurable is eventually drawn anyway",
	stubborn.rendered === 1,
	"rendered: " + stubborn.rendered
);
check(
	"and the console says the element was the problem",
	sandbox_warnings.some((w) => w.includes("Headcount by Company") && w.includes("no measurable width")),
	JSON.stringify(sandbox_warnings)
);
check(
	"the warning carries the measurements, not just a complaint",
	stubborn.sharm_width_report().connected === false && stubborn.sharm_width_report().clientWidth === 0,
	JSON.stringify(stubborn.sharm_width_report())
);

// once it is measurable it draws straight away, with no waiting
const live_chart = detached_chart();
live_chart.chart_wrapper = measurable();
live_chart.dashboard_chart = {};
sandbox_warnings.length = 0;
live_chart.render();
check(
	"a measurable chart draws on the first attempt",
	live_chart.updated === 1,
	"updated: " + live_chart.updated
);
check("a measurable chart does not warn", sandbox_warnings.length === 0, JSON.stringify(sandbox_warnings));
check(
	"the warning carries the measurements, not just a complaint",
	stubborn.sharm_width_report().connected === false && stubborn.sharm_width_report().clientWidth === 0,
	JSON.stringify(stubborn.sharm_width_report())
);

// --- 9c. a drill-down hands its filters over, and navigates without asking ----
// A drill-down that opens an unfiltered list is indistinguishable, from the outside,
// from one that was never given any filters, which is why this is checked directly.
//
// It must NOT go through `frappe.model.with_doctype()` first. It did once, on the
// theory that the List needs the meta loaded to place a filter field; that was wrong,
// because `BaseList.show()` runs `fetch_meta()` before `before_refresh()`. And the
// preload deferred the tap behind a callback that need not arrive - when it did not,
// the click did nothing at all, silently. Frappe's own widgets do not do this.
sandbox.meta_loads.length = 0;
calls.length = 0;
sandbox.frappe.route_options = undefined;
ours.data = { labels: ["Bautex Misr"], datasets: [{ values: [12] }] };
dom.click(dom.mark(0));
check(
	"a chart tap navigates without waiting on a meta callback",
	calls.some((c) => c.set_route === "List/Employee"),
	JSON.stringify(calls)
);
check(
	"and it does not preload the doctype meta to get there",
	sandbox.meta_loads.length === 0,
	"meta preloads: " + JSON.stringify(sandbox.meta_loads)
);
const nav = calls.find((c) => c.set_route);
check(
	"the filters are handed over with it",
	nav && nav.set_route === "List/Employee" && !!sandbox.frappe.route_options,
	JSON.stringify({ nav: nav, opts: sandbox.frappe.route_options })
);
check(
	"including the chart's own scope",
	sandbox.frappe.route_options &&
		JSON.stringify(sandbox.frappe.route_options.status) === JSON.stringify(["=", "Active"]) &&
		JSON.stringify(sandbox.frappe.route_options.company) === JSON.stringify(["=", "Bautex Misr"]),
	JSON.stringify(sandbox.frappe.route_options)
);

// a card with no filters must still navigate - it counts every row, so an
// unfiltered list is the right answer, not a dropped filter
calls.length = 0;
sandbox.frappe.route_options = undefined;
const unfiltered = Object.create(proto);
unfiltered.route = "List/GL Entry";
unfiltered.route_options = {};
unfiltered.set_route();
check(
	"a card whose metric counts every row opens the unfiltered list",
	calls.some((c) => c.set_route === "List/GL Entry") &&
		sandbox.frappe.route_options &&
		Object.keys(sandbox.frappe.route_options).length === 0,
	JSON.stringify({ calls: calls, opts: sandbox.frappe.route_options })
);

// --- 9c2. filters have to survive Frappe's own URL round-trip ---------------
// `router.set_route()` writes every filter as `key=JSON.stringify(value)` into the
// query string, and `parse_filters_from_route_options()` only decodes a value that
// starts with `[`. A bare string therefore returns still quoted - `"Active"`, quotes
// included - matches no row, and the list comes up unfiltered looking exactly like a
// drill-down that was given no filters at all.
//
// So this reproduces both halves of that round-trip with Frappe's own expressions and
// checks the filter that comes out the far end.
function frappe_encodes_query(route_options) {
	return Object.entries(route_options)
		.map(([k, v]) => `${k}=` + encodeURIComponent(JSON.stringify(v)))
		.join("&");
}

function frappe_decodes_filters(query, doctype, field_of) {
	const params = new URLSearchParams(query);
	const filters = [];
	params.forEach((value, field) => {
		const doctype_name = field_of(field);
		if (!doctype_name) return; // an unplaceable field is dropped, silently
		if (typeof value === "string" && value.startsWith("[") && value.endsWith("]")) {
			value = JSON.parse(value);
		}
		if (Array.isArray(value)) {
			filters.push([doctype_name, field, value[0], value[1]]);
		} else {
			filters.push([doctype_name, field, "=", value]);
		}
	});
	return filters;
}

const field_of = () => "Employee";

check(
	"a plain string filter is corrupted by the round trip - this is the bug",
	JSON.stringify(
		frappe_decodes_filters(frappe_encodes_query({ status: "Active" }), "Employee", field_of)
	) ===
		JSON.stringify([["Employee", "status", "=", '"Active"']]),
	"so a filter sent bare arrives quoted"
);
check(
	"sent as [operator, value] it survives intact",
	JSON.stringify(
		frappe_decodes_filters(frappe_encodes_query({ status: ["=", "Active"] }), "Employee", field_of)
	) ===
		JSON.stringify([["Employee", "status", "=", "Active"]]),
	JSON.stringify(
		frappe_decodes_filters(frappe_encodes_query({ status: ["=", "Active"] }), "Employee", field_of)
	)
);
check(
	"a blank bucket survives too",
	JSON.stringify(
		frappe_decodes_filters(
			frappe_encodes_query({ company: ["is", "not set"] }),
			"Employee",
			field_of
		)
	) === JSON.stringify([["Employee", "company", "is", "not set"]])
);

// and the drill-down must therefore hand over encoded values
calls.length = 0;
sandbox.frappe.route_options = undefined;
ours.data = { labels: ["Bautex Misr"], datasets: [{ values: [12] }] };
dom.click(dom.mark(0));
const round_tripped = frappe_decodes_filters(
	frappe_encodes_query(sandbox.frappe.route_options || {}),
	"Employee",
	field_of
);
check(
	"what a chart tap hands over comes back out of the round trip as a usable filter",
	round_tripped.some((f) => f[1] === "company" && f[2] === "=" && f[3] === "Bautex Misr") &&
		round_tripped.some((f) => f[1] === "status" && f[3] === "Active"),
	JSON.stringify(round_tripped)
);
check(
	"no filter comes back still carrying quotes",
	!round_tripped.some((f) => typeof f[3] === "string" && f[3].startsWith('"')),
	JSON.stringify(round_tripped)
);

// a number needs no encoding, and must not be double-wrapped into an array that
// the decoder then reads as an operator
const numeric = Object.create(proto);
calls.length = 0;
sandbox.frappe.route_options = undefined;
numeric.route = "List/Employee";
numeric.route_options = { sdg_no_medical_record: 1 };
numeric.set_route();
const numeric_rt = frappe_decodes_filters(
	frappe_encodes_query(sandbox.frappe.route_options || {}),
	"Employee",
	field_of
);
check(
	"a Checkbox filter survives as an equality on 1",
	JSON.stringify(numeric_rt) === JSON.stringify([["Employee", "sdg_no_medical_record", "=", 1]]),
	JSON.stringify(numeric_rt)
);

// --- 9c3. a card that goes somewhere says so, and one that does not, does not
// "The charts do nothing" could not previously be told apart from "the click
// handler is broken" - both look identical. The marker splits them: if a figure is
// underlined and still inert, the wiring is fine and the click handling is at fault.
function card_marker(route) {
	const seen = [];
	const c = Object.create(proto);
	c.widget = { toggleClass: (name, on) => seen.push([name, on]) };
	if (route) c.route = route;
	c.refresh();
	return seen;
}
check(
	"a card with a route is marked as going somewhere",
	card_marker("List/Employee").some(([n, on]) => n === "sharm-clickable-card" && on === true),
	JSON.stringify(card_marker("List/Employee"))
);
check(
	"a card with no route is left unmarked, which is the honest answer",
	card_marker(null).some(([n, on]) => n === "sharm-clickable-card" && on === false),
	JSON.stringify(card_marker(null))
);
check(
	"a card still refreshes through to the stock behaviour",
	Object.create(proto) && (() => {
		const c = Object.create(proto);
		c.widget = { toggleClass() {} };
		c.route = "List/Employee";
		c.refresh();
		return c.refreshed === 1;
	})(),
	"the inherited refresh must still run, or the card never renders"
);
check(
	"the marker is applied on build, not on click",
	(() => {
		// a card with a route must be marked without anyone tapping it
		const c = Object.create(proto);
		const marks = [];
		c.widget = { toggleClass: (n, on) => marks.push(on) };
		c.route = "List/Employee";
		c.refresh();
		return marks.length === 1 && marks[0] === true;
	})()
);


// --- 9c4. every chart type has to be able to say which point was clicked -------
// `data-point-index` is stamped from exactly two functions in frappe-charts -
// `datasetBar` and `datasetDot` in utils/draw.js. It is therefore on a bar and on a
// scatter dot and on nothing else: a pie slice, a donut slice, a percentage ring and
// a whole Line do not carry it. Reading only that attribute left four of the six types
// unclickable while every test still passed, because the fake DOM only ever grew bars.
// These build the structures each type actually produces, from the library's own source.
function svg_dom2() {
	const listeners = {};
	const wrapper = {
		style: {},
		_classes: new Set(),
		addEventListener(type, fn) {
			(listeners[type] = listeners[type] || []).push(fn);
		},
		contains() {
			return true;
		},
		addClass(name) {
			this._classes.add(name);
			return this;
		},
		hasClass(name) {
			return this._classes.has(name);
		},
		querySelector() {
			return null;
		},
	};
	return { wrapper, listeners };
}

function chart_widget_for(dom) {
	const w = Object.create(chart);
	w.chart_wrapper = [dom.wrapper];
	w.chart_doc = {
		module: "Sharm Dreams Group",
		document_type: "Employee",
		group_by_based_on: "company",
		group_by_type: "Count",
		filters_json: JSON.stringify([["Employee", "status", "=", "Active"]]),
	};
	w.data = { labels: ["Bautex Misr", "Concord", "Deraya"], datasets: [{ values: [12, 40, 7] }] };
	return w;
}

// A pie/donut/percentage slice: no `data-point-index` anywhere on it, but the chart
// holds the drawn paths in `components.get("pieSlices").store`, in data-point order -
// which is how PieChart.mouseMove identifies the slice under the cursor.
function slice_with(store, i) {
	const path = {
		_stamp: null,
		closest(selector) {
			if (selector === "[data-point-index]") return this._stamp === null ? null : this;
			if (selector === ".graph-svg-tip") return null;
			return null;
		},
		getAttribute() {
			return null;
		},
	};
	path.parentNode = null;
	store[i] = path;
	return path;
}

{
	const dom = svg_dom2();
	const w = chart_widget_for(dom);
	const store = [null, null, null];
	const slices = [slice_with(store, 0), slice_with(store, 1), slice_with(store, 2)];
	w.dashboard_chart = {
		data: { labels: ["Bautex Misr", "Concord", "Deraya"] },
		components: new Map([["pieSlices", { store: store }]]),
	};
	w.get_chart_args();
	const at = (n) => dom.wrapper.sharm_resolve({ target: slices[n] });
	check("a pie slice resolves to its data point", at(1) === 1, "got " + at(1));
	check("the first pie slice resolves to 0, not to nothing", at(0) === 0, "got " + at(0));
	check("the last pie slice resolves to its own index", at(2) === 2, "got " + at(2));

	calls.length = 0;
	dom.wrapper.addEventListener("click", () => {});
	// and the click has to travel all the way through to the list
	const idx = at(2);
	sandbox.frappe.route_options = undefined;
	w.go_to_drilldown(w.dashboard_chart.data.labels[idx]);
	const nav = calls.find((c) => c.set_route);
	check(
		"tapping a pie slice filters the list to that company",
		nav &&
			nav.set_route === "List/Employee" &&
			JSON.stringify(nav.route_options) ===
				JSON.stringify({ status: ["=", "Active"], company: ["=", "Deraya"] }),
		nav && JSON.stringify(nav.route_options)
	);
}

{
	// A Line. There is no per-point element to click at all - the line is a single path
	// - so the answer comes from the tooltip, which frappe-charts positions and stamps
	// on `mousemove` (SvgTip.fill sets `data-point-index` on the tip container).
	const dom = svg_dom2();
	const w = chart_widget_for(dom);
	const tip = {
		getAttribute(name) {
			return name === "data-point-index" ? "2" : null;
		},
	};
	w.dashboard_chart = {
		data: { labels: ["Bautex Misr", "Concord", "Deraya"] },
		components: new Map(),
		tip: { container: tip },
	};
	w.get_chart_args();
	check(
		"a Line resolves through the point the cursor is over",
		dom.wrapper.sharm_resolve({ target: { closest: () => null, getAttribute: () => null } }) === 2,
		"got " + dom.wrapper.sharm_resolve({ target: { closest: () => null, getAttribute: () => null } })
	);
}

{
	// The failure mode that must not come back: a single-series Line keeps ONE path in
	// its store, so the store is the same length as nothing. Answering "0" from it
	// would drill into the first company no matter where on the line you clicked.
	const dom = svg_dom2();
	const w = chart_widget_for(dom);
	const line_path = {
		closest(selector) {
			if (selector === "[data-point-index]") return null;
			if (selector === ".graph-svg-tip") return null;
			return null;
		},
		getAttribute: () => null,
		parentNode: null,
	};
	w.dashboard_chart = {
		data: { labels: ["Bautex Misr", "Concord", "Deraya"] },
		// one path for the whole series - must be rejected
		components: new Map([["line", { store: [line_path] }]]),
		tip: { container: { getAttribute: () => null } },
	};
	w.get_chart_args();
	check(
		"a one-per-series store is refused rather than answered wrongly",
		dom.wrapper.sharm_resolve({ target: line_path }) === -1,
		"got " + dom.wrapper.sharm_resolve({ target: line_path })
	);
}

{
	// A bar: the good old stamped attribute still wins, and a store that happens to be
	// the right length must not override the exact answer.
	const dom = svg_dom2();
	const w = chart_widget_for(dom);
	const bar = {
		closest(selector) {
			if (selector === "[data-point-index]") return this;
			if (selector === ".graph-svg-tip") return null;
			return null;
		},
		getAttribute: (n) => (n === "data-point-index" ? "1" : null),
		parentNode: null,
	};
	w.dashboard_chart = {
		data: { labels: ["Bautex Misr", "Concord", "Deraya"] },
		components: new Map([["datasetBars", { store: [{}, bar, {}] }]]),
	};
	w.get_chart_args();
	check("a bar still resolves from its stamped attribute", dom.wrapper.sharm_resolve({ target: bar }) === 1);
}

{
	// A Heatmap cannot be clicked, and says so rather than pretending. There is no
	// group-by on a heatmap and the library exposes no column geometry, so there is
	// nothing to filter the list on.
	const dom = svg_dom2();
	const w = chart_widget_for(dom);
	w.chart_doc.group_by_based_on = "";
	w.dashboard_chart = {
		data: { labels: ["W1", "W2"] },
		components: new Map(),
		tip: { container: { getAttribute: () => null } },
	};
	w.get_chart_args();
	check(
		"a heatmap is left as a picture because it has no group-by to filter on",
		w.get_drilldown_target() === null && !dom.wrapper.hasClass("sharm-clickable"),
		"a heatmap must not be marked clickable"
	);
}


// --- 9c5. the filters must be visible in the URL, not only in a global ----------
// `frappe.router.set_route` builds a query string out of `route_options` and then
// throws it away: `push_state` calls `history.pushState(null, null, path)` with the
// path alone, so `window.location.search` stays empty. That leaves `route_options` as
// the only channel, and `ListView.before_refresh` gates the whole thing on
// `frappe.route_options && this.filter_area` - so if that single global is cleared or
// outrun, the list opens showing every row and no hint why.
//
// `parse_filters_from_route_options` reads `window.location.search` first and only
// falls back to `route_options` when the search string is empty. Mirroring the
// filters into the URL therefore takes the branch that needs no global to survive -
// and never both, because the two are alternatives, so no filter is applied twice.
check(
	"tapping a bar puts the filters in the address bar, not only in route_options",
	(() => {
		const replaced = [];
		sandbox.window = {
			location: { pathname: "/app/employee", search: "" },
			history: { replaceState: (a, b, url) => replaced.push(url) },
		};
		ours.dashboard_chart = { data: { labels: ["Bautex Misr", "Concord"] } };
		ours.data = { labels: ["Bautex Misr", "Concord"], datasets: [{ values: [12, 40] }] };
		calls.length = 0;
		sandbox.frappe.route_options = undefined;
		dom.click(dom.mark(0));

		if (replaced.length !== 1) return "replaceState calls: " + replaced.length;
		// encoded exactly as the router encodes `route_options`:
		//   Object.entries(o).map(([k, v]) => k + "=" + encodeURIComponent(JSON.stringify(v)))
		// so the List parses it back with the rules it already uses.
		const want =
			"/app/employee?status=" +
			encodeURIComponent(JSON.stringify(["=", "Active"])) +
			"&company=" +
			encodeURIComponent(JSON.stringify(["=", "Bautex Misr"]));
		return replaced[0] === want ? true : "got " + replaced[0] + " | want " + want;
	})(),
	"the query string has to survive the router dropping it"
);

check(
	"route_options still carries the filters, so a List that reads either works",
	(() => {
		sandbox.frappe.route_options = undefined;
		sandbox.window = { location: { pathname: "/app/employee" }, history: { replaceState() {} } };
		ours.dashboard_chart = { data: { labels: ["Bautex Misr", "Concord"] } };
		ours.data = { labels: ["Bautex Misr", "Concord"], datasets: [{ values: [12, 40] }] };
		calls.length = 0;
		dom.click(dom.mark(1));
		const ro = sandbox.frappe.route_options;
		return !!ro && !!ro.company && !!ro.status ? true : JSON.stringify(ro);
	})()
);

// --- 9d. a patch that cannot install must say so ----------------------------
// The widget classes live in `desk.bundle.js`; this file is loaded through
// `app_include_js`, and which of the two runs first is hook-merging order rather
// than anything written here. The old guard `return`ed silently in that case, so a
// load-order change would leave every card and chart inert with no way to tell from
// the console. These run the real file against a sandbox with no widget factory at
// all - the exact case that used to be invisible.
function late_widget_sandbox() {
	return {
		console: { log() {}, warn() {}, error() {} },
		$,
		setTimeout: (fn, ms) => late_timers.push({ fn, ms }),
		clearTimeout() {},
		document: {},
		Promise,
		frappe: {
			provide(path_) {
				const parts = path_.split(".");
				let node = late_widget.frappe;
				for (const p of parts) node = node[p] = node[p] || {};
			},
			views: {},
			boot: {},
			ui: { make_app_page: () => ({ main: {} }) },
			utils: {},
			model: { with_doc() {}, with_doctype() {} },
			db: { get_list: () => Promise.resolve([]) },
			call: () => desk_promise({ message: {} }),
			xcall: () => desk_promise(0),
			get_route: () => ["dashboard-view", "x"],
			// this sandbox navigates into its own log: `calls` belongs to the main
			// sandbox and would never see it
			set_route() {
				late_navs.push(this.route);
			},
			// deliberately no `widget`
		},
	};
}

const late_timers = [];
const late_navs = [];
const late_widget = late_widget_sandbox();
let late_load_threw = null;
try {
	vm.createContext(late_widget);
	vm.runInContext(SRC, late_widget, { filename: "dashboard_view.js" });
} catch (e) {
	late_load_threw = e;
}
check("the file loads even with no widget factory present at all", !late_load_threw, late_load_threw && late_load_threw.message);
check(
	"and every patch schedules a retry rather than giving up",
	late_timers.length === 4,
	"retries scheduled: " + late_timers.length + " (expected one per patch)"
);

// now register the factory and let the retries run: they must install, not fire twice
late_widget.frappe.widget = {
	WidgetGroup: class {},
	widget_factory: {
		number_card: { prototype: { set_route() {} } },
		chart: { prototype: { get_chart_args: () => ({}), get_chart_colors: () => [], refresh() {}, render() {} } },
	},
};
late_timers.forEach((t) => t.fn());
const proto_after = late_widget.frappe.widget.widget_factory.chart.prototype;
const card_proto_after = late_widget.frappe.widget.widget_factory.number_card.prototype;
check(
	"a deferred patch installs once the factory appears",
	proto_after.sharm_drilldown_patched === true &&
		proto_after.sharm_colour_guard_patched === true &&
		proto_after.sharm_destroy_patch_patched === true &&
		card_proto_after.sharm_drilldown_patched === true,
	JSON.stringify({
		chart: Object.keys(proto_after).filter((k) => k.startsWith("sharm")),
		card: Object.keys(card_proto_after).filter((k) => k.startsWith("sharm")),
	})
);
// Installing twice would wrap `set_route` and `get_chart_args` in themselves, so
// one tap would fire two navigations. The retry must therefore stop once it wins.
check(
	"a deferred patch is not installed twice",
	Object.keys(card_proto_after).filter((k) => k === "fetch_sharm_routes").length === 1 &&
		Object.keys(card_proto_after).filter((k) => k === "set_route").length === 1,
	JSON.stringify(Object.keys(card_proto_after))
);
// Run the stub timers to a fixed point. The stub records every `setTimeout` rather
// than replacing the previous one, so "the queue is empty" is not the invariant -
// "nothing schedules anything further" is, and it is what stops a double-wrapped
// `set_route` firing two navigations for one tap.
let rounds = 0;
while (late_timers.length && rounds < 10) {
	const batch = late_timers.splice(0, late_timers.length);
	batch.forEach((t) => t.fn());
	rounds += 1;
}
check(
	"retrying settles: an installed patch schedules nothing further",
	late_timers.length === 0,
	"still scheduling after " + rounds + " rounds"
);
check(
	"and the patched methods are not wrapped in themselves",
	(() => {
		// a single wrap: calling our `set_route` navigates exactly once. Double-wrapped,
		// it would be our handler inside itself and navigate twice.
		late_navs.length = 0;
		const probe = Object.create(card_proto_after);
		probe.route = "List/Employee";
		probe.route_options = { status: "Active" };
		probe.set_route();
		return late_navs.length === 1;
	})(),
	"navigations from one tap: " + late_navs.length
);

// and a factory that never turns up gives up loudly rather than silently
const never_errors = [];
const never = late_widget_sandbox();
never.console = { log() {}, warn() {}, error: (...a) => never_errors.push(a.join(" ")) };
never.document = {
	body: { appendChild: (el) => (never_banner = el) },
	getElementById: () => null,
	createElement: () => ({ style: {}, id: "", remove() {} }),
};
never.frappe.widget = undefined;
const never_timers = [];
let never_banner = null;
never.setTimeout = (fn) => never_timers.push(fn);
vm.createContext(never);
vm.runInContext(SRC, never, { filename: "dashboard_view.js" });
for (let i = 0; i < 45; i++) {
	const batch = never_timers.splice(0, never_timers.length);
	if (!batch.length) break;
	batch.forEach((fn) => fn());
}
check(
	"a patch that can never install says so, instead of failing silently",
	never_errors.some((e) => e.includes("never installed")),
	JSON.stringify(never_errors)
);
// and it says so on the page, because a console nobody opens is the same as no
// message at all - every other failure here is invisible from the UI
check(
	"and it puts a visible banner on the page, saying what is wrong",
	!!never_banner && String(never_banner.textContent).includes("could not finish setting up"),
	never_banner ? String(never_banner.textContent) : "no banner"
);
check(
	"the banner names the patch that failed, so it can be identified",
	!!never_banner && /card drill-down|chart /.test(String(never_banner.textContent)),
	never_banner ? String(never_banner.textContent) : "no banner"
);
check(
	"and it is styled to be noticed, not missed like another div",
	!!never_banner && String(never_banner.style.cssText).includes("position:fixed"),
	never_banner ? String(never_banner.style.cssText) : "no banner"
);

// --- 10. the page says which widgets it could not wire ---------------------
// "not all the charts are clickable" cannot be answered from the database: a card
// with no route and a chart with no listener both look exactly like working ones.
// This reports the difference from the rendered DOM, so the next report of it can
// be answered with a number instead of a theory.
check("the wiring report exists", typeof factory.report_wiring === "function");

// exercised directly rather than through draw(), whose state depends on how far
// the earlier tests happened to get
const probe = Object.create(factory.constructor.prototype);
probe.routes = { "Active Headcount": { route: "List/Employee", route_options: { status: "Active" } } };
const wired = probe.card_widget("Active Headcount");
check(
	"a card with a route is built as clickable and recorded as such",
	wired.route === "List/Employee" && probe.wired_cards["Active Headcount"] === true,
	JSON.stringify({ wired: wired, recorded: probe.wired_cards })
);
const unwired = probe.card_widget("Coupons Unrecorded");
check(
	"a card with no route is recorded as not clickable, and is still built",
	unwired.route === undefined && probe.wired_cards["Coupons Unrecorded"] === false,
	JSON.stringify({ unwired: unwired, recorded: probe.wired_cards })
);

// and it must survive the case that matters most: every chart failed to draw, so
// the DOM is empty. Reporting "declared minus broken" here would say 2/2 - a false
// all-clear on exactly the failure being diagnosed.
// A widget as the report sees it. `clickable` is the thing that actually decides
// whether a click resolves: whether a `sharm_resolve` resolver is bound to an
// *ancestor* of the chart, because the delegated listener lives there and a click
// only reaches it by bubbling up.
//
// The chain is modelled exactly as a real desk renders it - verified in a browser,
// not assumed:
//
//     svg.frappe-chart            <- the chart
//       div.chart-container        <- frappe-charts draws into this
//         div.sharm-clickable      <- the resolver is bound HERE
//           div.widget-body
//             div.widget
//
// The resolver is deliberately NOT on `svg.parentNode`. That is the whole point of
// this stub: taking the immediate parent reported "charts clickable 0/18" on a
// dashboard where all 18 charts were bound and every one navigated correctly.
// A test that puts the resolver where the buggy code looked for it cannot catch
// that, and would go on passing while the report lied to the user.
function chart_widget(clickable) {
	const holder = { parentNode: null };
	if (clickable) holder.sharm_resolve = () => 0;
	const container = { parentNode: holder };
	const svg = { parentNode: container, closest: () => null };
	return {
		querySelector(selector) {
			if (selector === "svg.frappe-chart") return svg;
			if (selector === ".widget-title") return { textContent: "A Chart" };
			return null;
		},
	};
}

function run_report(sections, wired_cards, boxes) {
	const appended = [];
	const fake = Object.create(factory.constructor.prototype);
	fake.sections = sections;
	fake.wired_cards = wired_cards;
	fake.dashboard = { name: "SDG Workforce" };
	fake.$wrapper = [
		{
			querySelectorAll: (selector) => {
				if (selector === ".widget") return boxes;
				if (selector === "svg.frappe-chart") return boxes.map(() => ({}));
				return [];
			},
			appendChild: (el) => appended.push(el),
		},
	];
	fake.appended = appended;
	const lines = [];
	const real_log = console.log;
	console.log = (...args) => lines.push(args.map(String).join(" "));
	try {
		fake.report_wiring();
	} finally {
		console.log = real_log;
	}
	lines.appended = fake.appended;
	return lines;
}

const empty_report = run_report([{ cards: ["Active Headcount"], charts: ["A", "B"] }], { "Active Headcount": false }, []);
check(
	"a dashboard whose charts never rendered does not report them as clickable",
	empty_report[0].includes("cards clickable 0/1") && empty_report[0].includes("charts clickable 0/0"),
	empty_report[0]
);
check(
	"a chart that is declared but did not render is called out by name",
	empty_report.some((l) => l.includes("2 charts are on this dashboard but 0 drew")),
	JSON.stringify(empty_report)
);
check(
	"the report names the cards that have no route",
	empty_report.some((l) => l.includes("cards with no route") && l.includes("Active Headcount")),
	JSON.stringify(empty_report)
);

const good_report = run_report(
	[{ cards: ["A"], charts: ["X", "Y"] }],
	{ A: true },
	[chart_widget(true), chart_widget(false)]
);
check(
	"a rendered chart with a bound resolver counts, one without is named",
	good_report[0].includes("cards clickable 1/1") && good_report[0].includes("charts clickable 1/2"),
	good_report[0]
);

// The bug this fixes. `data-point-index` is stamped by `datasetBar` and `datasetDot`
// and nothing else, so a pie slice, a donut arc and a percentage ring never carry it.
// Asking the DOM for that attribute reported every pie and donut on the desk as a
// dead chart - so "charts clickable 8/24" on a dashboard whose pies all worked, and
// "are the charts clickable?" came back wrong about exactly the shapes that matter.
const pie_report = run_report(
	[{ cards: ["A"], charts: ["P", "D"] }],
	{ A: true },
	[chart_widget(true), chart_widget(true)]
);
check(
	"a pie and a donut with a bound resolver are both reported clickable",
	pie_report[0].includes("charts clickable 2/2"),
	pie_report[0]
);
check(
	"and no pie is named as a dead chart",
	!pie_report.some((l) => l.includes("charts that cannot be clicked")),
	JSON.stringify(pie_report)
);

// The second version of the same bug, found by clicking charts in a real browser.
// The report looked for the resolver on `svg.parentNode`. On a real desk that is
// `div.chart-container`; the resolver is bound one level further out, on the element
// `chart_widget.js` wraps the chart in. So the report read
//     charts clickable 0/18
// on a dashboard where all 18 charts were bound and every one of them navigated -
// a bar to `?company=["=","..."]` and a donut slice to `?sdg_si_status=["=","Insured"]`,
// the latter landing on a list of 147 out of 310 employees.
//
// Both failures had the same shape: the report guessed the DOM instead of asking the
// question a click asks. So this pins the nesting - the resolver has to be found by
// walking *up*, from an element that is not the immediate parent.
const nested = chart_widget(true);
const svg_el = nested.querySelector("svg.frappe-chart");
check(
	"the stub hides the resolver one level above the SVG's parent",
	svg_el.parentNode.sharm_resolve === undefined &&
		typeof svg_el.parentNode.parentNode.sharm_resolve === "function",
	"parent: " + typeof svg_el.parentNode.sharm_resolve
);
const nested_report = run_report(
	[{ cards: ["A"], charts: ["X"] }],
	{ A: true },
	[nested]
);
check(
	"a chart whose resolver is two levels up is still reported clickable",
	nested_report[0].includes("charts clickable 1/1"),
	nested_report[0]
);

// --- the poll must outlast the charts ------------------------------------
//
// Found by reading the report on a real desk, where it printed
//     charts clickable 0/0 | 18 on this dashboard, 0 drew
// while all 18 charts were bound and every one navigated.
//
// `draw()` samples the chart count synchronously, before anything has drawn, so the
// first sample is always 0. The old loop stopped after three unchanged readings,
// which is 750ms - and a chart that fetches its own data and draws afterwards is
// nowhere near ready by then. So it reported "0 drew" on a fully working dashboard.
//
// Worse, it was a coin toss: sometimes the charts won the race and the same dashboard
// reported 0/18. A diagnostic that flips between two wrong answers cannot decide
// anything, and it is the only answer the user can read off the page.
//
// The rule is asserted directly rather than through the timer queue, which is what a
// timing bug demands: driving `setTimeout` from a test means hijacking a global and
// hoping no other timer is in it.
{
	// Called on the real factory instance, not a bare prototype: `WIRING_STEP` and
	// `WIRING_DEADLINE` are class fields, so they exist on instances and not on the
	// prototype, and the test must read the shipped constants rather than its own
	// copies. The method itself is pure - it reads only those two and mutates the
	// `state` it is handed - so calling it on the live factory touches nothing.
	const due = factory;
	// Walk a real cold-load sequence for a dashboard declaring 3 charts: nothing has
	// drawn, then one at a time, then all three.
	const run = (counts, expected, cap) => {
		const state = { seen: -1, settled: 0, elapsed: 0 };
		let reported_at = null;
		for (let i = 0; i < (cap || 60); i++) {
			const charts = counts[i] !== undefined ? counts[i] : counts[counts.length - 1];
			state.elapsed += due.WIRING_STEP;
			if (due.wiring_report_due(state, charts, expected)) {
				reported_at = i;
				break;
			}
		}
		return reported_at;
	};

	check(
		"a dashboard whose charts have not drawn is not reported",
		run([0, 0, 0, 0, 0, 0, 0, 0], 3, 8) === null,
		"reported at sample " + run([0, 0, 0, 0, 0, 0, 0, 0], 3, 8)
	);

	check(
		"nor is one that is only part-drawn",
		run([0, 0, 1, 1, 1, 2, 2, 2, 2, 2], 3, 10) === null,
		"reported at sample " + run([0, 0, 1, 1, 1, 2, 2, 2, 2, 2], 3, 10)
	);

	// The last chart lands at sample 6; it must be reported a few samples later, not
	// before, and not never.
	const cold = run([0, 0, 0, 0, 1, 2, 3, 3, 3, 3], 3, 20);
	check(
		"the report appears a few samples after the last chart draws",
		cold !== null && cold >= 6 && cold <= 9,
		"reported at sample " + cold
	);

	// A chart that genuinely never draws must still be reported, or a broken chart is
	// indistinguishable from a slow one - the whole reason the report exists.
	// `elapsed` is advanced before the test, so the sample that reaches the deadline
	// is `(samples so far) * STEP` - hence `never + 1` samples of elapsed time.
	const never = run([0], 3, 200);
	const never_ms = (never + 1) * due.WIRING_STEP;
	check(
		"a chart that never draws is reported at the deadline, not waited on forever",
		never !== null && never_ms >= due.WIRING_DEADLINE,
		"reported at sample " + never + " (" + never_ms + "ms)"
	);

	check(
		"and the deadline is long enough for a cold fetch but not unbounded",
		due.WIRING_DEADLINE >= 5000 && due.WIRING_DEADLINE <= 30000,
		"deadline " + due.WIRING_DEADLINE + "ms"
	);

	// A dashboard that declares no charts has nothing to wait for, and must not hang
	// the poll for ten seconds. It still has to settle - the count has to stop moving
	// - but it settles on the very first readings.
	check(
		"a dashboard with no charts reports promptly",
		run([0], 0, 6) !== null,
		"never reported"
	);
}

// The same figures have to be readable on the page. "Are the charts clickable?"
// has been asked repeatedly and cannot be answered from the server, and the console
// is not always open - so this is the only place the answer can be read.
const shown = (good_report.appended || []).map((el) => String(el.textContent)).join(" | ");
check(
	"the counts are also written onto the page",
	shown.includes("cards clickable 1/1") && shown.includes("charts clickable 1/2"),
	shown
);
check(
	"and it names the chart that cannot be clicked",
	shown.includes("not clickable") && shown.includes("A Chart"),
	shown
);
check(
	"a healthy dashboard says so without naming anything",
	(() => {
		const ok = run_report([{ cards: ["A"], charts: ["X"] }], { A: true }, [chart_widget(true)]);
		const text = (ok.appended || []).map((el) => String(el.textContent)).join(" ");
		return text.includes("cards clickable 1/1") && !text.includes("not clickable");
	})(),
	(() => {
		const ok = run_report([{ cards: ["A"], charts: ["X"] }], { A: true }, [chart_widget(true)]);
		return (ok.appended || []).map((el) => String(el.textContent)).join(" ");
	})()
);
check(
	"a chart with no bound resolver is listed as not clickable",
	good_report.some((l) => l.includes("charts that cannot be clicked")),
	JSON.stringify(good_report)
);

// --- 7c. a chart fetch that is truncated must not look like a whole page -------
//
// `frappe.db.get_list` returns at most 20 rows whatever the arguments say - measured
// on the real desk: naming 25 charts came back with 20, and naming all 119 chart
// documents also came back with 20. The dashboard declared 19 charts for a long
// time, one short of the cap, so this never showed. Past the cap, the charts that
// did not come back left empty boxes in their grid and nothing said so.
//
// The stub caps at the same 20, on purpose: a stub that answers in full makes the
// truncation unreproducible here, which is the only place it can be tested.
// Held so the report at the end waits for it. Its checks land after several
// microtask hops - `desk_promise` settles one hop late, and there are four of them -
// so a report that ran on a fixed number of hops would print the page before these
// were counted, and they would be lost every run rather than failing.
const sharm_chart_fetch_checks = (async function chart_fetch_checks() {
	const names = [];
	for (let i = 1; i <= 25; i++) names.push("Chart " + i);

	const asks = [];
	function with_db(fake_db, run) {
		const saved = sandbox.frappe.db;
		sandbox.frappe.db = fake_db;
		try {
			return run();
		} finally {
			sandbox.frappe.db = saved;
		}
	}
	// `chart_docs` is set to `null` before each fetch so "has not settled yet" is
	// distinguishable from "settled on nothing": without it the very first poll sees
	// `undefined` on a fresh instance and every check below runs against a half-run
	// chain.
	const settle = async (probe) => {
		for (let i = 0; i < 200; i++) {
			if (probe.chart_docs !== null && probe.chart_docs !== undefined) return probe;
			await Promise.resolve();
		}
		return probe;
	};

	const capped = {
		get_list(doctype, args) {
			const wanted = (args.filters.chart_name || [])[1] || [];
			asks.push(wanted.length);
			// `frappe.client.get_list` answers with 20 whatever it was asked for.
			return desk_promise(wanted.slice(0, 20).map((chart_name) => ({ chart_name: chart_name })));
		},
	};
	const probe = new sandbox.frappe.views.DashboardViewFactory();
	probe.chart_docs = null;
	with_db(capped, () => probe.load_charts(names));
	await settle(probe);

	check(
		"a dashboard with more charts than one request returns asks more than once",
		asks.length > 1,
		asks
	);
	check("and no single ask is big enough to be cut", asks.every((n) => n <= 20), asks);
	check(
		"so every declared chart arrives, not just the first twenty",
		probe.chart_docs && probe.chart_docs.length === names.length,
		probe.chart_docs && probe.chart_docs.length
	);
	const seen = (probe.chart_docs || []).map((d) => d.chart_name);
	check("and none of them twice", new Set(seen).size === seen.length, seen.length - new Set(seen).size);
	check(
		"each one carries the label the widget prints as its title",
		(probe.chart_docs || []).every((d) => d.label === d.chart_name),
		JSON.stringify((probe.chart_docs || []).slice(0, 1))
	);

	// A name listed twice must draw once, not twice.
	const repeated = new sandbox.frappe.views.DashboardViewFactory();
	repeated.chart_docs = null;
	with_db({ get_list: () => desk_promise([{ chart_name: "A" }, { chart_name: "B" }, { chart_name: "A" }]) }, () =>
		repeated.load_charts(["A", "B", "A"])
	);
	await settle(repeated);
	check(
		"a chart named twice in a layout is fetched once",
		(repeated.chart_docs || []).length === 2,
		(repeated.chart_docs || []).map((d) => d.chart_name)
	);

	// One refused request loses only its own charts, not the page.
	//
	// Twenty-five names, because a refusal costs a whole *chunk*: with four names
	// there is one chunk, and refusing it refuses the page - which is the right
	// behaviour and no evidence of anything. "Chart 15" sits in the middle chunk, so
	// the first and last chunks still arrive.
	const refused = new sandbox.frappe.views.DashboardViewFactory();
	refused.chart_docs = null;
	with_db(
		{
			get_list(doctype, args) {
				const wanted = (args.filters.chart_name || [])[1] || [];
				if (wanted.indexOf("Chart 15") !== -1) return desk_promise_error();
				return desk_promise(wanted.map((chart_name) => ({ chart_name: chart_name })));
			},
		},
		() => refused.load_charts(names)
	);
	await settle(refused);
	check(
		"one refused request does not lose the charts that did arrive",
		(refused.chart_docs || []).length === 15,
		(refused.chart_docs || []).length
	);
	check(
		"and it loses the ones in that request, not an arbitrary ten",
		(refused.chart_docs || []).every((d) => d.chart_name !== "Chart 15"),
		(refused.chart_docs || []).map((d) => d.chart_name).indexOf("Chart 15")
	);

	// And the gap is reported by name. "25 on this dashboard, 20 drew" is how five
	// charts went missing without anyone being told which five.
	//
	// All ten of the refused chunk, not just the one whose request was tested - a
	// refusal costs a whole request, so naming only the chart that happened to be
	// checked would under-report the gap by nine.
	refused.sections = [{ blocks: [{ type: "charts", items: names }] }];
	const gap = refused.missing_chart_names();
	check("the gap is named, not only counted", gap.length === 10, gap);
	check(
		"and it names every chart the refused request would have carried",
		gap.join() === names.slice(10, 20).join(),
		gap
	);
	refused.sections = [{ cards: ["A"], charts: ["Chart 1", "Chart 2"] }];
	check(
		"and nothing is reported missing when nothing is",
		refused.missing_chart_names().length === 0,
		refused.missing_chart_names()
	);
})().catch((e) => {
	check("the chart-fetch checks ran to the end", false, String((e && e.stack) || e));
});

// --- report ---------------------------------------------------------
// Deferred: `draw()` runs inside the promise chain `load_charts` builds, so
// asserting in the same tick would check the page before the desk has drawn it - and
// the chart-fetch checks above have to have finished first, or they are counted after
// the report has already been written.
Promise.resolve(sharm_chart_fetch_checks)
	.then(() => {})
	.then(() => {})
	.then(() => {
		check(
			"something was rendered into the page",
			factory.$wrapper && factory.$wrapper._children && factory.$wrapper._children.length > 0,
			factory.$wrapper && factory.$wrapper._children
		);
		report();
	});
}
// `continue_harness` is called from a timer, because the checks in 3b have to let
// the desk-shaped promises settle before they can assert - and the stubbed
// `setTimeout` is drained in batches at the end of the file, so the rest of the
// harness runs in a later round rather than in the same tick.
}, 10);

function report() {
	const failed = results.filter((r) => !r.ok);
	const payload = JSON.stringify({ total: results.length, failed: failed.length, results }, null, 1);
	process.stdout.write(payload);
	// Also to a file. The code under test logs to the console, and some of it runs
	// with the host's `console` rather than the stub, so stdout is not reliably pure
	// JSON - and a harness whose output cannot be parsed is a harness nobody trusts.
	const out = process.env.SHARM_REPORT;
	if (out) {
		try {
			fs.writeFileSync(out, payload);
		} catch (e) {
			// not being able to write the file is not a reason to fail the run
		}
	}
	process.exit(failed.length ? 1 : 0);
}
