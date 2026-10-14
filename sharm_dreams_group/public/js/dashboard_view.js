// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, see license.txt

// Two things, both in one place because they are one feature: a `Dashboard` document
// rendered as *sections* or as *tabs*, with every number clickable through to the rows
// behind it.
//
// Why this file exists at all: `router.render_page()` dispatches on the first path
// segment, so `dashboard-view/<name>` becomes the view name "DashboardView" and looks
// for `frappe.views.DashboardViewFactory`. Frappe registers only Form, List, Report and
// Tree, so the route falls through to a Page lookup and 404s. `frappe.views
// .DashboardView` is real, working code that nothing routes to - it extends ListView
// and expects a `doctype`, so it was written for DocType dashboards and never wired to
// `Dashboard` documents. Registering the factory here fixes the route without editing
// any stock file.
//
// Why the cards are clickable here: a `Custom` Number Card driven by a Python method
// has no `route` of its own, and `number_card_widget.js` only navigates when one is
// set - so every card on this app looked like a number and did nothing. The metric
// definition carries the target and `card_routes()` hands the map over; see
// `sharm_dreams_group.hr.dashboard.METRICS`.

// Frappe re-encodes `frappe.route_options` into the query string on every
// navigation:
//
//     `${key}=` + encodeURIComponent(JSON.stringify(value))
//
// so a plain string comes back from the URL still quoted.  The decoder undoes that
// for arrays only:
//
//     } else if (typeof value === "string" && value.startsWith("[") && ...) {
//         value = JSON.parse(value);
//
// A bare `"Active"` therefore arrives as the *eight-character string* `"Active"`,
// quotes included, matches no row, and the list comes up unfiltered - which is
// indistinguishable, from the desk, from a drill-down that was never given any
// filters.  Encoding every value as `[operator, value]` goes through the branch that
// does get decoded, and lands as `[doctype, field, operator, value]`.
//
// Only string values are affected: `JSON.stringify(1)` is `1`, which needs no
// decoding.  Numbers, Checkbox flags and dates are therefore unaffected either way,
// and normalising them costs nothing.
function sharm_encodable_filters(route_options) {
	const out = {};
	for (const field in route_options || {}) {
		const value = route_options[field];
		out[field] = Array.isArray(value) ? value : ["=", value];
	}
	return out;
}

// One navigation helper for both drill-downs, so the two cannot drift apart.
//
// This deliberately does no meta preloading. It did once, on the theory that
// `parse_filters_from_route_options()` needs the doctype meta loaded to place a
// filter field. That theory was wrong - `BaseList.show()` runs `fetch_meta()`
// before `before_refresh()` - and the preload deferred the tap behind a callback
// that need not arrive, so a click could do nothing at all, silently.
function sharm_navigate(route, route_options) {
	const filters = sharm_encodable_filters(route_options);
	console.log("[sharm_dreams_group] navigate", route, JSON.stringify(filters));
	frappe.route_options = filters;
	frappe.set_route(route);

	// Put the filters in the address bar as well, not only in `route_options`.
	//
	// `frappe.router.set_route` reads `frappe.route_options` to build a query string
	// and then throws it away: `push_state` calls `history.pushState(null, null, path)`
	// with the path alone, so `window.location.search` stays empty. That leaves
	// `route_options` as the only channel, and `ListView.before_refresh` gates on
	// `frappe.route_options && this.filter_area` - so anything that clears or outruns
	// that one global, and the list opens showing every row with no sign of why.
	//
	// `parse_filters_from_route_options` reads `window.location.search` FIRST and
	// only falls back to `route_options` when the search string is empty. So writing
	// the filters here takes the branch that does not depend on a global surviving,
	// and never both - the two are alternatives, so no filter is applied twice.
	//
	// It also makes the result legible: the filters are in the URL, so they survive a
	// refresh, can be bookmarked or pasted to a colleague, and can be read without
	// opening devtools. Encoded exactly as the router encodes them.
	sharm_push_filters_to_url(filters);
}

/**
 * Is `field` a real field of `doctype`, so a filter on it can be placed?
 *
 * The desk asks the same question a moment later, from the other end, when
 * `parse_filters_from_route_options` runs:
 *
 *     doctype = frappe.meta.get_doctype_for_field(list_doctype, field);
 *     if (doctype) filters.push(...)      // else: no filter at all
 *
 * So the answer has to be yes for the navigation to mean anything. Answering it
 * here turns a silent mismatch into a chart that declines to be clickable.
 *
 * Meta may not be loaded yet when this is asked - `get_chart_args` runs while the
 * chart is being drawn, and a chart can be drawn before its doctype meta arrives.
 * When meta is absent we say yes, because saying no would make every chart a
 * picture during a race, and the desk itself would drop the filter rather than
 * apply the wrong one. It only ever says "no" for a field that is known to be
 * absent from meta that has loaded.
 */
function sharm_field_exists(doctype, field) {
	if (!doctype || !field) return false;
	try {
		const meta = frappe.meta && frappe.meta.get_meta && frappe.meta.get_meta(doctype);
		// no meta loaded yet: cannot prove it is wrong, so allow the click
		if (!meta || !meta.fields) return true;
		if (typeof meta.has_field === "function") return meta.has_field(field);
		return meta.fields.some((f) => f && (f.fieldname === field || f.name === field));
	} catch (e) {
		// a doctype with no meta at all is not a reason to lose the click
		return true;
	}
}


/** Mirror `route_options` into the query string, the way the router would have. */
function sharm_push_filters_to_url(filters) {
	// `typeof`, not a bare `!window`: reading an undeclared identifier throws, and a
	// click must never be lost to the reporting of it.
	if (typeof window === "undefined") return;
	if (!window.history || typeof window.history.replaceState !== "function") return;
	if (!filters || typeof filters !== "object") return;
	const query = Object.keys(filters)
		.map((key) => encodeURIComponent(key) + "=" + encodeURIComponent(JSON.stringify(filters[key])))
		.join("&");
	if (!query) return;
	try {
		window.history.replaceState(null, "", window.location.pathname + "?" + query);
	} catch (e) {
		// A URL Frappe will not let us rewrite is not a reason to lose the click.
	}
}


// Run a patch now, or as soon as the thing it patches exists.
//
// The widget prototypes live in `desk.bundle.js`, which assigns
// `frappe.widget.widget_factory` when it executes. This file is loaded through
// `app_include_js`, and whether that lands before or after the desk bundle is a
// property of hook-merging order rather than of anything written here. The old guard
// just `return`ed when the factory was not there yet - silently, with no message - so
// a load-order change would leave every card and every chart inert, and the only
// symptom would be a dashboard that looks correct and does nothing.
//
// So: try immediately, retry until it takes, and say so either way. A patch that
// announces itself can be diagnosed from one line of console; one that gives up
// quietly cannot be diagnosed at all.
// Say so on the page, not only in the console. Every failure mode of this file is
// invisible from the UI - an unrouted card, a chart with no listener and a patch that
// never installed all look like a dashboard that simply does nothing - so the one
// case that is unambiguously broken gets a banner. It is the only way to tell the
// three apart without opening devtools, and it disappears on its own once the patch
// installs.
function sharm_banner(label) {
	if (
		!document ||
		!document.body ||
		typeof document.getElementById !== "function" ||
		typeof document.createElement !== "function"
	) {
		return;
	}
	if (document.getElementById("sharm-install-error")) return;
	const box = document.createElement("div");
	box.id = "sharm-install-error";
	box.style.cssText = [
		"position:fixed",
		"top:0",
		"left:0",
		"right:0",
		"z-index:9999",
		"background:#d64545",
		"color:#fff",
		"font:600 13px/1.5 system-ui,sans-serif",
		"padding:10px 16px",
	].join(";");
	box.textContent =
		"Sharm Dreams Group: the dashboard could not finish setting up (" +
		label +
		"). Cards and charts will not respond to clicks until this is fixed.";
	document.body.appendChild(box);
}

/** Remove the banner, once a patch has installed after all. */
function sharm_clear_banner() {
	if (!document || typeof document.getElementById !== "function") return;
	const box = document.getElementById("sharm-install-error");
	if (box && box.remove) box.remove();
}

function sharm_install(label, attempt) {
	if (attempt()) {
		console.log("[sharm_dreams_group] installed: " + label);
		sharm_clear_banner();
		return true;
	}
	let tries = 0;
	const tick = function () {
		if (attempt()) {
			console.log("[sharm_dreams_group] installed (late): " + label);
			sharm_clear_banner();
			return;
		}
		if (++tries >= 40) {
			console.error(
				"[sharm_dreams_group] never installed: " + label,
				"frappe.widget is " + (typeof frappe.widget === "undefined" ? "undefined" : "present")
			);
			sharm_banner(label);
			return;
		}
		setTimeout(tick, 100);
	};
	setTimeout(tick, 100);
	return false;
}

/** The chart widget class, or null while it has not been registered yet. */
function sharm_chart_widget() {
	const factory = frappe.widget && frappe.widget.widget_factory;
	return (factory && factory.chart) || null;
}

/** The number card widget class, or null while it has not been registered yet. */
function sharm_card_widget() {
	const factory = frappe.widget && frappe.widget.widget_factory;
	return (factory && factory.number_card) || null;
}

// Find a method a subclass inherits rather than owns.
//
// `NumberCardWidget` does not define `refresh`; it inherits it from `BaseWidget`.
// Reading `number_card.prototype.refresh` therefore yields `undefined`, and wrapping
// that would make every card throw on its first render - a card widget broken by
// the very code meant to make it clickable. Walk the chain, and only up to the first
// prototype that actually owns the method.
function sharm_inherited(cls, name) {
	let proto = cls.prototype;
	while (proto && !Object.prototype.hasOwnProperty.call(proto, name)) {
		proto = Object.getPrototypeOf(proto);
	}
	return proto ? proto[name] : null;
}

// What a row of cards and a row of charts default to, matching the desk's own
// dashboard: four across, two across, 240px tall.
//
// Named here rather than inline so `normalize_blocks` is the single place that knows
// them, and so a stock dashboard - which writes `cards`/`charts` and never `blocks` -
// keeps rendering exactly as it did before this file grew a layout.
const SHARM_DEFAULT_CARD_COLUMNS = 4;
const SHARM_DEFAULT_CHART_COLUMNS = 2;
const SHARM_DEFAULT_CHART_HEIGHT = 240;

// How many chart names to ask for in one request.
//
// `frappe.db.get_list` does not return everything it was asked for. Its server side
// is `frappe.client.get_list`, whose `limit_page_length` defaults to 20, and a
// `limit_page_length: 0` in the arguments does not lift it: a request naming 25
// charts comes back with 20, and so does a request naming all 119 chart documents
// on this site.
//
// That was invisible while the dashboard declared 19 charts - one short of the cap.
// Adding six took it over, and the five that did not come back were not missing
// widgets: their grid was still drawn, so the page had five empty boxes where a
// chart belonged and nothing said which. Ten is comfortably under the cap, so no
// chunk can be cut.
const CHART_FETCH_CHUNK = 10;

// How close, in pixels, two lines on the same chart have to be before a click stops
// claiming to know which one was tapped. See `sharm_clicked_series`.
//
// Twelve is a number rather than a guess about this chart's scale, and the reason it
// is not smaller is that the two lines are *usually* far apart: `Hiring vs Attrition`
// is 9 hires against 0 leavers for most of a year, and on a 300px chart with a range
// of 130 that is about twenty pixels. It is not smaller because at a month where the
// two agree - which is exactly the month somebody wants to look at - the honest
// answer is that the click says nothing, and a threshold tight enough to "resolve"
// those would be resolving the ones the eye cannot tell too.
const SERIES_PX = 12;

/**
 * A section as a list of `{type, items, columns, height}` blocks.
 *
 * `chart_options` written by `section_layout` already has this shape. A section
 * written the older way - `cards` and `charts` keys - is two blocks, which is what it
 * always rendered as. Both shapes have to work: this factory renders every dashboard
 * on the site, and the stock ones do not pass through this app's Python.
 *
 * A `columns` or `height` that is not a usable number is dropped rather than passed
 * on. `WidgetGroup` turns `columns` straight into a class name, so a `null` or a
 * string would produce `grid-col-null` and silently fall back to the stock
 * `grid-col-2` for a row that asked for three - a layout silently not applied, which
 * is the failure this whole change exists to remove.
 */
function sharm_normalize_blocks(section) {
	const known = {
		cards: { columns: SHARM_DEFAULT_CARD_COLUMNS },
		charts: { columns: SHARM_DEFAULT_CHART_COLUMNS, height: SHARM_DEFAULT_CHART_HEIGHT },
	};
	const out = [];

	(section.blocks || []).forEach(function (block) {
		if (!block) return;
		const kind = block.type === "charts" ? "charts" : "cards";
		const items = (block.items || []).filter(Boolean);
		if (!items.length) return;
		out.push(sharm_block(kind, items, block));
	});

	// Only when there were no blocks at all. A section that declared blocks and had
	// every one of them empty falls through to `cards`/`charts`, which for this app's
	// own sections are the same items - so it renders rather than vanishing.
	if (!out.length) {
		if (section.cards && section.cards.length) {
			out.push(sharm_block("cards", section.cards, {}));
		}
		if (section.charts && section.charts.length) {
			out.push(sharm_block("charts", section.charts, {}));
		}
	}
	return out;

	function sharm_block(kind, items, block) {
		const fallback = known[kind];
		const columns = Math.min(12, Math.max(1, parseInt(block.columns, 10) || fallback.columns));
		const out = { type: kind, items: items, columns: columns };
		if (kind === "charts") {
			const height = parseInt(block.height, 10) || fallback.height;
			out.height = Math.min(900, Math.max(120, height));
		}
		return out;
	}
}

/** Every chart a section draws, in block order and without repeats. */
function sharm_section_charts(section) {
	const names = [];
	sharm_normalize_blocks(section).forEach(function (block) {
		if (block.type === "charts") block.items.forEach((n) => names.push(n));
	});
	return [...new Set(names)];
}

/** Every number card a section draws, in block order and without repeats. */
function sharm_section_cards(section) {
	const names = [];
	sharm_normalize_blocks(section).forEach(function (block) {
		if (block.type === "cards") block.items.forEach((n) => names.push(n));
	});
	return [...new Set(names)];
}

/**
 * Put the row width and the kind on the element the stylesheet actually keys off.
 *
 * `WidgetGroup` writes `grid-col-{columns}` onto the group body, and the desk's
 * stylesheet only defines that class up to `grid-col-3` - which is why this app's own
 * CSS overrides `.grid-col-4`. So a layout asking for five or six produced a class
 * nothing styled, and the row silently rendered at the stock two or three.
 *
 * The width therefore goes on as a custom property, not as a class: the stylesheet is
 * then one rule for any count rather than one per count, so the six bands on a
 * dashboard can ask for five, six, two and three cards across without this file
 * carrying a class for each. The stock classes are removed as the property is set,
 * because a re-render - a company filter changing which charts are on the page -
 * would otherwise leave both and lose to whichever the stylesheet orders last.
 */
function sharm_apply_columns($body, columns, kind) {
	if (!$body || !$body.length) return;
	// The kind classes go too, not just the stock ones. `render_section` is called
	// again whenever the page re-renders - a company filter changes which charts are
	// on it - and jQuery hands the *same* group body back, so a class from the first
	// pass is still there. Two rules of equal specificity both matching means the one
	// the stylesheet happens to order later wins: a row that had been cards would
	// quietly get the chart floor of 340px and show fewer columns than it declared,
	// with nothing in the console.
	$body.removeClass(function (index, cls) {
		// jQuery hands `removeClass` a function whose return value is treated as
		// space-separated class names, so this has to be the whole list to drop.
		return (cls.match(/grid-col-\d+|sharm-cols-\d+|sharm-cards|sharm-charts/g) || []).join(" ");
	});
	$body.addClass(kind === "charts" ? "sharm-charts" : "sharm-cards");
	$body[0].style.setProperty("--sharm-cols", String(columns));
}

frappe.provide("frappe.views");

// The factory is registered in a try/catch on purpose.  It is the first thing in
// this file, so anything wrong with it - `frappe.views.Factory` not being defined
// yet, most likely - threw at load time and took the two drill-down patches below
// with it, because they never ran.  That turned one broken route into a desk where
// no card and no chart anywhere did anything, with the real error only in a console
// nobody was watching.  Now a failure here costs the dashboard page and says so,
// and the patches still install.
try {
frappe.views.DashboardViewFactory = class DashboardViewFactory extends frappe.views.Factory {
	make(route) {
		const name = route[1];
		if (!name) return frappe.show_not_found(route);

		frappe.model.with_doc("Dashboard", name, (_name, r) => {
			// `with_doc` does NOT hand over the document. Its callback receives the
			// *name* first and the server response second - `callback(name, r)` in
			// frappe/model/model.js, in both of its branches. Treating the first
			// argument as the dashboard makes `this.dashboard` the string
			// "SDG Workforce", so `chart_options`, `cards` and `charts` are all
			// undefined, `read_layout()` falls back to one empty implicit section and
			// the page renders blank. There is no error and nothing in the console:
			// a blank page, on all three dashboards, for as long as this stood.
			//
			// The response's `docs[0]` is the document when the server was called;
			// `frappe.get_doc` covers the other branch, where it was already cached.
			const dashboard =
				(r && r.message && r.message.docs && r.message.docs[0]) ||
				frappe.get_doc("Dashboard", name);
			if (!dashboard || dashboard.name !== name) return frappe.show_not_found(route);
			this.dashboard = dashboard;
			this.render_dashboard();
		});
	}

	render_dashboard() {
		frappe.model.with_doctype("Dashboard", () => {
			// Built the way `treeview.js` does it: `frappe.container.add_page` for the
			// container, then `frappe.ui.make_app_page` for the page itself, keeping the
			// Page instance that `make_app_page` *returns*.
			//
			// `frappe.make_page()` cannot be used here. It returns the container
			// element, not the Page - `set_title`, `main` and `wrapper` all live on
			// `container.page` - so `this.page.set_title(...)` was a call on a DOM
			// node. Worse, the desk builds no usable page at all through it: the page
			// header never reaches `this.wrapper`, so `page_actions` comes back empty
			// and `setup_page()` hands that `undefined` to
			// `frappe.ui.keys.get_shortcut_group`, whose WeakMap rejects a
			// non-object. That throws inside the Page constructor, `render_dashboard`
			// never gets as far as `$wrapper`, and the route comes up blank - with the
			// stack pointing into `alt_keyboard_shortcuts.js`, nowhere near the cause.
			//
			// The name must not contain a space: it becomes `id="page-<name>"`, which
			// `container.change_to` and the CSS selectors downstream both assume is a
			// single token.
			const page_name = "dashboard-view-" + frappe.router.slug(this.dashboard.name);
			const container = frappe.container.add_page(page_name);
			this.page = frappe.ui.make_app_page({ parent: container, single_column: true });
			frappe.container.change_to(page_name);

			this.page.set_title(this.dashboard.dashboard_name || this.dashboard.name);
			this.page.main.addClass("dashboard-page");
			// Built as an element and *then* appended, rather than appended from an HTML
		// string and the result assigned.
		//
		// jQuery's `.append()` returns the set it was called on, not what it added, so
		// `this.page.main.append('<div class="dashboard-view"></div>')` hands back the
		// page's own main section. `$wrapper` was therefore that section, every heading
		// and row was appended as a sibling of the wrapper rather than inside it, and
		// every rule this app writes against `.dashboard-view` - the flex column, the
		// 24px between bands, the outer margin - matched nothing on the page.
		//
		// Nothing noticed, because the symptom is absence: no error, no missing widget,
		// and the bands still stacked correctly on their own, just with none of the
		// spacing this file's stylesheet exists to give them.
		this.$wrapper = $("<div class='dashboard-view'></div>");
		this.page.main.append(this.$wrapper);

			this.sections = this.read_layout();
			const wanted = [...new Set(this.sections.flatMap((s) => sharm_section_charts(s)))];
			this.load_charts(wanted);
		});
	}

	/** Section layout from `chart_options`, or a single implicit section. */
	read_layout() {
		let layout = null;
		try {
			layout = JSON.parse(this.dashboard.chart_options || "null");
		} catch (e) {
			layout = null;
		}
		if (layout && Array.isArray(layout.sections) && layout.sections.length) {
			return layout.sections;
		}
		// no layout: show everything this dashboard has, under no heading
		return [
			{
				title: null,
				cards: (this.dashboard.cards || []).map((d) => d.card).filter(Boolean),
				charts: (this.dashboard.charts || []).map((d) => d.chart).filter(Boolean),
			},
		];
	}

	// Fetched in chunks of `CHART_FETCH_CHUNK`, and why that is not optional.
	load_charts(names) {
		const done = () => {
			// routes are fetched alongside: one round trip each, and the page does not
			// draw until both are in, so no card is briefly unclickable
			frappe
				.call("sharm_dreams_group.hr.dashboard.card_routes")
				.then((r) => {
					this.routes = (r && r.message) || {};
					this.draw();
				})
				.catch(() => {
					this.routes = {};
					this.draw();
				});
		};

		if (!names.length) {
			this.chart_docs = [];
			return done();
		}

		// Chunked rather than one request, because one request is truncated at 20 -
		// see `CHART_FETCH_CHUNK`.
		const chunks = [];
		for (let i = 0; i < names.length; i += CHART_FETCH_CHUNK) {
			chunks.push(names.slice(i, i + CHART_FETCH_CHUNK));
		}

		Promise.all(
			chunks.map((chunk) =>
				frappe.db
					.get_list("Dashboard Chart", {
						filters: { chart_name: ["in", chunk] },
						fields: ["*"],
						limit_page_length: 0,
					})
					.catch(() => [])
			)
		)
			.then((batches) => {
				// One document per name. Two chunks cannot overlap, but a name listed
				// twice in a layout would arrive twice and draw twice, so the list is
				// flattened in one pass rather than trusted to.
				//
				// One pass, and the same object in it: a `filter` that reads a `seen`
				// map and a `map` that fills it is two passes over the same array, and
				// the filter runs first against an empty map - which deduplicates
				// nothing at all, silently.
				const seen = {};
				this.chart_docs = [];
				[].concat.apply([], batches).forEach((d) => {
					if (!d || !d.chart_name || seen[d.chart_name]) return;
					seen[d.chart_name] = true;
					// `label` is the title the stock widget prints. Without it a chart
					// draws under a blank heading.
					this.chart_docs.push(Object.assign(d, { label: d.chart_name }));
				});
				done();
			})
			.catch(() => {
				this.chart_docs = [];
				done();
			});
	}

	draw() {
		this.$wrapper.empty();
		this.active = this.sections[0].title;
		this.sections.forEach((s) => this.render_section(s, this.$wrapper));
		// What actually ended up on the page, as opposed to what is in the database.
		// These two have never agreed, and the difference is invisible from the server:
		// a card with no `route` and a chart with no click listener both look identical
		// to a working one, so this is the only place the gap can be seen.
		//
		// Polled rather than reported straight away. The widgets are built here but
		// fetch their own data and draw afterwards, so a report taken now would count
		// an empty page and blame the charts.
		//
		// And it has to be polled for long enough, which is the second version of a
		// bug that made this report lie. The first sample is taken synchronously,
		// before a single widget has drawn, so it is always 0. Stopping after three
		// unchanged readings therefore fired at 750ms - while every chart was still
		// fetching - and printed "18 on this dashboard, 0 drew". Sometimes the charts
		// won the race and it printed 0/18 for the same dashboard. Neither number was
		// ever a fact about the charts; both were a fact about the poll.
		//
		// So: stop as soon as every declared chart has drawn, which is the normal case
		// and is quick; and otherwise keep waiting until the deadline, so a chart that
		// genuinely fails to draw is reported as a failure rather than as "still
		// loading". Silence about a broken chart is the one thing worse than being slow.
		clearTimeout(this.wiring_poll);
		const state = { seen: -1, settled: 0, elapsed: 0 };
		const expected = (this.sections || []).reduce((n, s) => n + sharm_section_charts(s).length, 0);
		const tick = () => {
			const charts = this.$wrapper[0].querySelectorAll("svg.frappe-chart").length;
			state.elapsed += this.WIRING_STEP;
			if (this.wiring_report_due(state, charts, expected)) {
				this.report_wiring();
				return;
			}
			this.wiring_poll = setTimeout(tick, this.WIRING_STEP);
		};
		tick();
	}

	// How often the wiring poll samples, and how long it is prepared to wait for a
	// chart that may never arrive.
	WIRING_STEP = 250;
	WIRING_DEADLINE = 10000;

	/**
	 * Is it time to write the wiring report out?
	 *
	 * Pulled out of the poll and made a plain function of its arguments, because the
	 * bug it exists to prevent is a *timing* bug and timing is exactly what a test
	 * cannot reach through the global timer queue. Asserting it directly is the
	 * difference between pinning the rule and pinning the accident.
	 *
	 * @param {{seen:number, settled:number, elapsed:number}} state mutated in place
	 * @param {number} charts   charts drawn so far
	 * @param {number} expected charts this dashboard declares
	 */
	wiring_report_due(state, charts, expected) {
		if (charts !== state.seen) {
			state.seen = charts;
			state.settled = 0;
			return false;
		}
		// Require the count to have stopped moving, so a page that draws its charts
		// one at a time is not reported half-built.
		if (++state.settled < 3) return false;
		// Then either everything asked for is there - the normal case, reported as
		// soon as the last chart lands - or we have waited long enough that a missing
		// chart is a failure rather than a slow fetch. There is no third option:
		// stopping early prints "0 drew" on a working dashboard, and never stopping
		// makes a broken chart look like a slow one forever.
		return charts >= expected || state.elapsed >= this.WIRING_DEADLINE;
	}

	/** One line per dashboard saying how many widgets can be clicked, and which cannot. */
	report_wiring() {
		const wanted = {};
		(this.sections || []).forEach((s) => sharm_section_cards(s).forEach((n) => (wanted[n] = true)));
		const names = Object.keys(wanted);
		const dead_cards = names.filter((n) => !(this.wired_cards || {})[n]);

		// Charts are checked against the DOM rather than against the document, because
		// the document only says what was *asked* for. A rendered bar carrying
		// `data-point-index` is the thing a click actually lands on.
		//
		// Counted from what rendered, never as "declared minus broken": a chart that
		// failed to draw is absent from the DOM altogether, so subtracting it from
		// the declared total reports it as fine. That is the one thing this report
		// must never do.
		//
		// A chart is identified by `svg.frappe-chart`, which is what
		// `makeSVGContainer()` puts on the element. Not `.chart_wrapper` - that
		// element carries no class at all (`chart_widget.js` builds a bare `<div>`),
		// so selecting it matches nothing and reports every dashboard as having no
		// charts on it, which is what it did until a real dashboard said so.
		const declared = (this.sections || []).reduce((n, s) => n + sharm_section_charts(s).length, 0);
		const drawn = Array.prototype.slice
			.call(this.$wrapper[0].querySelectorAll(".widget"))
			.filter((w) => w.querySelector("svg.frappe-chart"));
		// A chart counts as clickable if a click on it can actually be resolved -
		// which is what the wrapper's own bound listener asks, not whether some
		// element inside it happens to carry `data-point-index`.
		//
		// That attribute is stamped by `datasetBar` and `datasetDot` and by nothing
		// else in frappe-charts. A Pie slice, a donut arc and a percentage ring never
		// get one, so asking the DOM reported every pie on the desk as a dead chart -
		// and "charts clickable 8/24" on a dashboard whose pies all work. The report
		// was wrong about precisely the shapes this file is built to support, which
		// makes it worse than no report: it says the feature is broken when it is not.
		//
		// The resolver is the ground truth, and it is found by walking *up* from the
		// SVG to wherever it was actually attached.
		//
		// Both earlier attempts guessed the element. The first asked the DOM for
		// `[data-point-index]`, which frappe-charts stamps from `datasetBar` and
		// `datasetDot` and nowhere else - so every Pie, donut and percentage ring was
		// called a dead chart. The second took `svg.parentNode`, which on a real desk
		// is `div.chart-container`, while the resolver sits one level further out on
		// the element `chart_widget.js` wraps the chart in. That reported
		// "charts clickable 0/18" on a dashboard where all 18 charts were bound and
		// every one navigated correctly - verified by clicking them.
		//
		// A report that says the feature is broken when it works is worse than no
		// report, so stop guessing at the DOM shape. Ask the question a click asks:
		// is there an ancestor of this chart that can resolve the click? Walking up
		// and testing for `sharm_resolve` is exactly the set of elements a bubbling
		// click would reach, so it cannot disagree with what a click does.
		let clickable = 0;
		const dead_charts = [];
		drawn.forEach((widget) => {
			const wrap = widget.querySelector("svg.frappe-chart");
			let node = wrap;
			let resolvable = false;
			while (node) {
				if (typeof node.sharm_resolve === "function") {
					resolvable = true;
					break;
				}
				node = node.parentNode;
			}
			if (resolvable) {
				clickable += 1;
				return;
			}
			const title = widget.querySelector(".widget-title");
			dead_charts.push(title ? title.textContent.trim() : "(untitled chart)");
		});

		console.log(
			"%c[sharm_dreams_group] " + this.dashboard.name,
			"font-weight:bold",
			"cards clickable " + (names.length - dead_cards.length) + "/" + names.length,
			"charts clickable " + clickable + "/" + drawn.length
		);
		if (dead_cards.length) console.log("  cards with no route:", dead_cards);
		if (dead_charts.length) console.log("  charts that cannot be clicked:", dead_charts);
		if (drawn.length !== declared) {
			// Which ones. A count on its own was the report that let five charts go
			// missing for a whole release: it said "25 on this dashboard, 20 drew" and
			// nothing about which five, so the gap had to be found by comparing the
			// layout with the page by hand.
			//
			// Asked the other way round - declared minus what came back - rather than
			// by reading titles off the DOM, because a chart that failed to *fetch* has
			// no DOM to read and is exactly the case worth naming.
			const named = this.missing_chart_names();
			console.log(
				"  " +
					declared +
					" charts are on this dashboard but " +
					drawn.length +
					" drew" +
					(named.length ? ":" : " - nothing on the page matches them either"),
				named.length ? named : "(none: the layout names charts with no document)"
			);
		}

		// The same figures on the page. "Are the charts clickable?" has been asked
		// repeatedly and cannot be answered from the server, because a chart that is
		// drawn but not clickable looks identical to one that is neither - and the
		// console is not always open. This is the one place the answer can be read
		// without devtools.
		this.render_wiring_report(names, dead_cards, drawn, dead_charts, clickable, declared);
	}

	/**
	 * Chart names the layout declares that no widget on the page is showing.
	 *
	 * The difference is taken from the documents rather than the page, so a chart
	 * that never fetched is named rather than merely counted.
	 */
	missing_chart_names() {
		const loaded = {};
		(this.chart_docs || []).forEach((d) => {
			loaded[d.chart_name] = true;
		});
		const missing = [];
		(this.sections || []).forEach((s) => {
			sharm_section_charts(s).forEach((name) => {
				if (!loaded[name] && missing.indexOf(name) === -1) missing.push(name);
			});
		});
		return missing;
	}

	/** A small line at the foot of the dashboard: what is clickable, and what is not. */
	render_wiring_report(wanted_cards, dead_cards, drawn, dead_charts, clickable, declared) {
		const line = document.createElement("div");
		line.className = "sharm-wiring-report";
		line.style.cssText = [
			"margin:24px 0 8px",
			"padding:10px 14px",
			"border-radius:6px",
			"font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace",
			"background:#f1f3f5",
			"color:#495057",
			"border-left:3px solid " +
				(dead_cards.length || dead_charts.length || drawn.length !== declared
					? "#d64545"
					: "#2f6f4e"),
		].join(";");

		const parts = [];
		parts.push("cards clickable " + (wanted_cards.length - dead_cards.length) + "/" + wanted_cards.length);
		parts.push("charts clickable " + clickable + "/" + drawn.length);
		if (declared !== drawn.length) parts.push(declared + " on this dashboard, " + drawn.length + " drew");

		line.textContent =
			"Sharm Dreams Group - " +
			parts.join("  |  ") +
			(dead_cards.length ? "  |  no route: " + dead_cards.join(", ") : "") +
			(dead_charts.length ? "  |  not clickable: " + dead_charts.join(", ") : "");

		if (this.$wrapper && this.$wrapper[0]) this.$wrapper[0].appendChild(line);
	}

	// Sections render stacked, one under another, in the order `chart_options` lists
	// them. That is what a `Dashboard` is for, and stacking is what the four
	// dashboards here want: a section is a heading followed by its figures, and a
	// reader scans down the page.
	//
	// There is no tab strip, deliberately. This file used to carry a method that built
	// one, and nothing ever called it - `this.tabs` was only ever set in there - so it
	// sat unread while looking like a feature somebody had forgotten to switch on. It
	// has been removed rather than wired up. If tabs are ever wanted, the section
	// titles are already in `chart_options`, so the work is small - but it should be
	// a decision, not an accident left in a file.

	// A section is a heading followed by *blocks*, each a row of cards or a row of
	// charts with its own width and, for charts, its own height.
	//
	// The indirection is worth one paragraph because the thing it replaced was worse
	// than it looks. A section used to render all of its cards and then all of its
	// charts, at four cards across and two charts at a fixed 240px, with no way for
	// the page to say otherwise. "Records To Complete" drew eleven cards and then one
	// chart - so the only figure on that band showing *which company* each gap sits in
	// was a full screen below the numbers. "Payroll" put ten cards in front of six
	// charts. And because the row width was a constant, a one-word label was given
	// the same card as "Average Gross Salary" and clipped.
	//
	// `section_layout` in `hr/dashboard.py` writes this shape and reads
	// `SECTION_LAYOUT` for the per-band arrangement. A section written the old way -
	// `cards` and `charts` keys, no `blocks` - still renders, at the desk's old
	// defaults, because stock Frappe and HRMS write sections in that shape and this
	// factory is the only thing that renders them. That path is the reason
	// `normalize_blocks` exists at all rather than the shape being assumed.

	render_section(section, $into) {
		if (!section) return;
		if (section.title) {
			$into.append(`<h5 class="dashboard-section-head">${frappe.utils.escape_html(section.title)}</h5>`);
		}
		sharm_normalize_blocks(section).forEach((block) => {
			if (block.type === "charts") this.render_chart_block(block, $into);
			else this.render_card_block(block, $into);
		});
	}

	/** One row of number cards. */
	render_card_block(block, $into) {
		const $c = $("<div></div>").appendTo($into);
		new frappe.widget.WidgetGroup({
			container: $c,
			type: "number_card",
			columns: block.columns,
			options: { allow_sorting: false, allow_create: false, allow_delete: false, allow_hiding: false },
			widgets: block.items.map((name) => this.card_widget(name)),
			in_customize_mode: false,
		});
		$c.find(".widget-group-head").hide();
		// `columns` reaches `WidgetGroup` as a class the stock stylesheet owns
		// (`grid-col-4` and only up to `grid-col-3` in the desk's own CSS), so the
		// layout this app asks for would silently not happen. The class below is the
		// one this app's stylesheet keys off, and it is set after the group has
		// written its own classes onto the body.
		sharm_apply_columns($c.find(".widget-group-body"), block.columns, "cards");
	}

	/** One row of charts. */
	render_chart_block(block, $into) {
		const $c = $("<div></div>").appendTo($into);
		// Widget data is the chart *document*: ChartWidget reads
		// this.chart_doc.document_type to offer the "open the list" action, so
		// handing it a bare name would render a chart with no drill-down. There is
		// no frappe.boot.charts; the stock view fetches them the same way.
		const wanted = block.items;
		const docs = (this.chart_docs || []).filter((c) => wanted.includes(c.chart_name));
		// A block whose charts were all renamed away must not draw an empty grid, or
		// the row reads as a rendering fault rather than as nothing to show.
		if (!docs.length) return;
		const group = new frappe.widget.WidgetGroup({
			container: $c,
			type: "chart",
			columns: block.columns,
			height: block.height,
			options: { allow_sorting: false, allow_create: false, allow_delete: false, allow_hiding: false },
			widgets: docs,
			in_customize_mode: false,
		});
		group.container.find(".widget-group-head").hide();
		sharm_apply_columns($c.find(".widget-group-body"), block.columns, "charts");
	}

	/** A number card that goes somewhere when clicked. */
	card_widget(name) {
		const target = (this.routes || {})[name];
		// Recorded as the widget is built, because "can this card be clicked" is
		// otherwise unanswerable: an unrouted card looks exactly like a routed one.
		this.wired_cards = this.wired_cards || {};
		this.wired_cards[name] = !!target;
		return target
			? { type: "number_card", name: name, route: target.route, route_options: target.route_options }
			: { type: "number_card", name: name };
	}
};
} catch (e) {
	console.error(
		"[sharm_dreams_group] /desk/dashboard-view will not open: " + (e && e.message),
		"The cards and charts on the workspace page are unaffected."
	);
}


// Card drill-down, for cards rendered by the stock workspace block rather than by
// this factory.
//
// A `Custom` Number Card cannot use the stock path. `set_route_for_custom_card()`
// reads `this.data.route`, but for a Custom card `this.data` is whatever the card's
// *method returned* - for us, the figure (`set_body()` does
// `this.data = await frappe.xcall(this.settings.method, ...)`). So `this.data.route`
// is always undefined and that method can never fire, whichever DocType you point it
// at. The stock hook is therefore the wrong place to intervene; the route has to be
// read from the widget, and `set_route` is the one method every click goes through.
//
// `route` arrives on the widget because `base_widget.js` does `Object.assign(this,
// opts)`, and this file's `card_widget()` puts it there. On a *workspace* block it
// does not, which is why the fetch below exists at all - see `sharm_fetch_route_map`.
// `card_routes()` once per page, shared by every card that asks.
//
// It was one `frappe.call` per card per tap. That was invisible while the app's
// own dashboards were the only place cards rendered - there the route is handed to
// the widget up front by `card_widget()`, so nothing ever fetched - and it became
// forty-three calls on the workspace front page, which builds its widgets from the
// child table with no route of ours attached. Memoised on the promise, so a hundred
// cards asking in the same tick produce one request and one resolution.
let sharm_route_map_promise = null;

function sharm_fetch_route_map() {
	if (!sharm_route_map_promise) {
		sharm_route_map_promise = frappe
			.call("sharm_dreams_group.hr.dashboard.card_routes")
			.then((r) => (r && r.message) || {})
			.catch((e) => {
				// Dropped, not kept: a failed call must not leave a rejected promise
				// cached for the rest of the page's life, or every card would refuse
				// to retry and the panel would stay dead until a reload.
				sharm_route_map_promise = null;
				throw e;
			});
	}
	return sharm_route_map_promise;
}

sharm_install("card drill-down", function () {
	const proto = sharm_card_widget();
	if (!proto || !proto.prototype) return false;
	if (proto.prototype.sharm_drilldown_patched) return true;
	proto.prototype.sharm_drilldown_patched = true;

	const stock_refresh_card = sharm_inherited(proto, "refresh");

	/**
	 * Say whether this card goes somewhere, and mark it accordingly.
	 *
	 * Split out of `refresh` because on a workspace block the route is not known when
	 * the card first draws - it arrives from the server afterwards - and `refresh`
	 * does not run again. So the mark has to be reapplied when the answer lands, or
	 * the card navigates correctly while showing none of the affordance, which reads
	 * as "the cards are broken" rather than as a cosmetic miss.
	 */
	function sharm_mark_card_clickable(card) {
		if (card.widget && card.widget.toggleClass) {
			card.widget.toggleClass("sharm-clickable-card", !!card.route);
		}
	}

	proto.prototype.sharm_mark_clickable = function () {
		sharm_mark_card_clickable(this);
	};

	proto.prototype.refresh = function () {
		const out = stock_refresh_card ? stock_refresh_card.apply(this, arguments) : undefined;
		// Marked here rather than in the click handler: by the time a click happens
		// the user has already decided the figure is not a link.  A card with no
		// route is left unmarked, which is the honest answer - it goes nowhere.
		sharm_mark_card_clickable(this);
		// A workspace block has no route yet, and waiting for the first tap to fetch
		// one spends that tap invisibly: the reader clicks a card that looks live and
		// nothing happens, which is the report this was fixed for. So ask as soon as
		// the card draws. `sharm_fetch_route_map` makes it one call per page however
		// many cards ask - forty-three of them, on the front page.
		if (!this.route && this.label) {
			this.fetch_sharm_routes();
		}
		return out;
	};

	const stock_set_route = proto.prototype.set_route;

	proto.prototype.set_route = function () {
		if (this.route) {
			// Not cleared afterwards, and that is deliberate. `route_options` is a
			// global the destination List reads in `before_refresh()`, which consumes
			// it and sets it to null itself. Clearing it here raced that read: the list
			// came up with no filter at all, which is the one outcome this feature
			// exists to prevent, and it looked like the drill-down had "not worked"
			// rather than like a race. The stock widget sets it the same way.
			sharm_navigate(this.route, this.route_options);
			return;
		}
		// no route of ours: a card the desk navigates itself, or one whose target
		// has not been fetched yet. `fetch_sharm_routes` retries once it arrives.
		if (!this.sharm_routes_loaded) {
			this.fetch_sharm_routes();
			return;
		}
		return stock_set_route.apply(this, arguments);
	};

	/** Ask the server where the cards go, then adopt the answer. */
	proto.prototype.fetch_sharm_routes = function () {
		const self = this;
		// Two handlers on one `then`, never a trailing `.finally`.
		//
		// `frappe.call` does not return a native Promise and it does not return a
		// jQuery Deferred either, whatever jQuery is on the page: the object it hands
		// back has `then` and `catch` and **no** `finally`, so a chain ending in one
		// raises `TypeError: ... .finally is not a function` at the moment it runs.
		// That was already here - the previous version of this method ended in
		// `.finally` too - and it was the second reason the workspace cards never
		// worked: the lookup missed (see below) and then the chain that would have
		// noticed threw on its way out.
		//
		// `refresh()` now calls this while the page is drawing, so the failure is no
		// longer a dead click but forty-three `Block «number_card» skipped because of
		// plugins error` messages and an empty band of cards. The desk catches per
		// block and logs an opaque `%o Object`, which is how a one-line TypeError
		// presents as "the cards have gone".
		return sharm_fetch_route_map().then(
			(map) => {
				// By `label`, not by `name`. The two happen to be the same on this
				// app's own dashboards - `card_widget()` passes the label in as the
				// name - but a workspace block builds its widget straight from the
				// child-table row, where `name` is the row id ("cvn7o7ec1k") and
				// `label` is "Active Headcount". Looking the row id up in a
				// label-keyed map found nothing, so the route never arrived.
				const target = map[self.label] || map[self.name];
				if (target) {
					self.route = target.route;
					self.route_options = target.route_options;
				}
				sharm_mark_card_clickable(self);
				self.sharm_routes_loaded = !!self.route || self.sharm_routes_loaded === true;
			},
			() => {
				// Not marked loaded. A failed call - a dropped connection, a session
				// that had just expired - would otherwise mark the card inert for the
				// rest of the page's life, and the next tap would fall through to the
				// stock behaviour and do nothing. Retrying is safe because the retry
				// is a tap, not a loop. Swallowed rather than rethrown: the caller is
				// `refresh()`, which has no handler and would only produce an unhandled
				// rejection - and the flag is the recovery.
				self.sharm_routes_loaded = false;
			}
		);
	};
});


// Chart drill-down: tap a bar or a slice to see the rows behind it.
//
// A Dashboard Chart is a picture in stock Frappe - there is no click handler
// anywhere in chart_widget.js, and no configuration that adds one. `get_chart_args()`
// is the seam, because it runs on every render and is what `setup_dashboard_chart()`
// hands to `frappe.utils.make_chart()`.
//
// It is worth being precise about what that seam can and cannot do, because getting
// it wrong cost every chart on this site its click. `make_chart()` copies whatever
// it is given onto the config, but the frappe-charts constructor reads a *fixed list*
// of options and `onClick` is not on it, so an `onClick` set here is discarded
// without a word. The click therefore has to come from the DOM - see the note on
// `data-point-index` further down, which is the attribute that makes it possible.
//
// The handler has to work out *which* segment was tapped, because the DOM reports an
// index rather than a record. `this.data` is the fetched payload -
// `{labels: [...], datasets: [{values: [...]}]}` - so the label at that index is the
// group, and for a Group By chart the group is a value of the group-by field.
//
// Every chart that groups by a real field is wired, whichever app it came from.
//
// This used to be gated on `doc.module === "Sharm Dreams Group"`, on the argument
// that a stock chart grouped by something we cannot filter on would send the user
// somewhere meaningless. But the module was standing in for a test the code
// already makes, three lines further down: a chart can only become a list filter
// if it names a `document_type`, a `group_by_based_on` and a `group_by_type`.
// On this site that left 32 charts - 17 of them Pies, 3 Percentage rings - as
// pictures, and every Pie on the desk is one of them, because the Pies all ship
// with HRMS and CRM. "Employees by Gender", "Job Applicant Pipeline",
// "Lead Source" and the rest looked like charts and did nothing.
//
// So the test is the one that means something: can this chart name a field to
// filter on. The 56 charts that cannot - the ones driven by a Python
// `dashboard_chart_source`, with no doctype and no group-by - stay pictures,
// because there is genuinely no list behind them. That is a fact about the
// chart rather than about which app it was filed under.
//
// Every chart has to have a colour, or it does not draw at all.
//
// `get_chart_colors()` in Frappe ends with
//
//     colors = [this.chart_doc.color || []];   // Line and Bar charts
//
// so a Line or Bar chart with no colour set yields `[[]]`. Chart.js coerces that
// to `""`, `BaseChart` throws `"" is not a valid color`, and the widget dies while
// it is being constructed - which also breaks the tooltip binding and leaves the
// `removeChild` error behind in the console. Sixty five charts in this site were in
// that state, including every bar chart on the group dashboards, so the charts were
// not merely wrong, they were absent.
//
// The setup module now gives this app's charts a real colour, but that cannot cover
// the stock ERPNext and HRMS charts or a chart someone builds by hand, so the
// palette is filtered here: anything that is not a non-empty string is dropped, and
// an empty list lets Chart.js fall back to its own defaults.
sharm_install("chart colour guard", function () {
	const widget = sharm_chart_widget();
	if (!widget || !widget.prototype) return false;
	if (widget.prototype.sharm_colour_guard_patched) return true;
	widget.prototype.sharm_colour_guard_patched = true;

	const stock_get_chart_colors = widget.prototype.get_chart_colors;

	widget.prototype.get_chart_colors = function () {
		if (typeof stock_get_chart_colors !== "function") return [];
		const colors = stock_get_chart_colors.apply(this, arguments);
		if (!Array.isArray(colors)) return colors;
		return colors.filter((color) => typeof color === "string" && color.trim() !== "");
	};
});
	
// Patch frappe.Chart.prototype.makeChartArea to guard against the race where
// the ResizeObserver fires after the SVG was already removed.
// This runs IMMEDIATELY (not via sharm_install) because frappe.Chart is loaded
// in desk.bundle.js before this bundle, and the HR Setup workspace renders
// its chart synchronously on page load - the sharm_install timeout would be too late.
(function () {
	if (!frappe.Chart || frappe.Chart.prototype.sharm_makeChartArea_patched) return;
	frappe.Chart.prototype.sharm_makeChartArea_patched = true;
	const stock_makeChartArea = frappe.Chart.prototype.makeChartArea;
	if (typeof stock_makeChartArea === "function") {
		frappe.Chart.prototype.makeChartArea = function () {
			// The original makeChartArea does: this.svg && this.container.removeChild(this.svg)
			// which throws if the SVG was already removed (e.g., by chart_wrapper.empty()
			// before a queued ResizeObserver callback fires). We guard by clearing this.svg
			// after a safe removal so the original's check becomes falsy.
			if (this.svg && this.container && this.container.contains(this.svg)) {
				this.container.removeChild(this.svg);
				// Prevent the original's unconditional removeChild from throwing
				this.svg = null;
			} else if (this.svg && (!this.container || !this.container.contains(this.svg))) {
				// SVG already gone (detached or removed) - null it so original skips removal
				this.svg = null;
			}
			// Delegate to the original for the rest of the setup (creating new SVG,
			// drawArea, legendArea, etc.).
			return stock_makeChartArea.apply(this, arguments);
		};
	}
})();

// A discarded chart keeps drawing.
//
// `ChartWidget.render()` rebuilds the chart instance for every Pie, Donut and
// Percentage chart, on every render:
//
//     this.chart_wrapper.empty();       // the old <svg> leaves the DOM
//     delete this.dashboard_chart;     // ... and the instance is forgotten
//     this.dashboard_chart = make_chart(...)
//
// frappe-charts' `destroy()` is the only thing that disconnects the ResizeObserver
// it installs in `configure()`, and nothing calls it. So each rebuild leaves an
// observer watching a wrapper whose svg has just been detached; when it fires it
// runs `draw()` -> `makeChartArea()` -> `this.container.removeChild(this.svg)` on a
// node that is no longer there:
//
//     Uncaught NotFoundError: Failed to execute 'removeChild' on 'Node':
//     the node to be removed is not a child of this node.
//
// The redraw aborts part-way, so a chart can be left showing its previous frame.
// This site has 21 circular charts, so it fired constantly. Destroying the stale
// instance first costs one call and ends the leak.
sharm_install("chart teardown", function () {
	const widget = sharm_chart_widget();
	if (!widget || !widget.prototype) return false;
	if (widget.prototype.sharm_destroy_patch_patched) return true;
	widget.prototype.sharm_destroy_patch_patched = true;

	/** Disconnect the stale instance's observer, if there is one. */
	function discard(widget_instance) {
		const stale = widget_instance.dashboard_chart;
		if (!stale || typeof stale.destroy !== "function") return;
		try {
			stale.destroy();
		} catch (e) {
			// A chart already torn down must not stop the new one being built.
		}
	}

	

	widget.prototype.sharm_measurable = function () {
		return measurable(this);
	};

	/** What was actually wrong with the element, for the console when it stays wrong. */
	widget.prototype.sharm_width_report = function () {
		const el = this.chart_wrapper && this.chart_wrapper[0];
		if (!el) return { wrapper: null };
		let padding = {};
		try {
			const styles = window.getComputedStyle(el);
			padding = { left: styles.paddingLeft, right: styles.paddingRight };
		} catch (e) {
			padding = "getComputedStyle threw";
		}
		return {
			connected: el.isConnected,
			clientWidth: el.clientWidth,
			parentTag: el.parentElement && el.parentElement.className,
			padding: padding,
		};
	};

	// Same leak by another route: `refresh()` re-empties `this.body`, detaching the
	// wrapper the old instance still points at.
	const stock_refresh = widget.prototype.refresh;
	widget.prototype.refresh = function () {
		discard(this);
		return stock_refresh.apply(this, arguments);
	};

	// And by the route that actually mattered. Only the circular types rebuild here -
	// for a Line or Bar, stock calls `this.dashboard_chart.update(this.data)` and the
	// instance has to survive, so this must not touch it.
	const stock_render = widget.prototype.render;
	widget.prototype.render = function () {
		const type = this.chart_doc && this.chart_doc.type;
		if (type === "Pie" || type === "Donut" || type === "Percentage") discard(this);

		// A chart measured before it has a width is drawn with NaN geometry.
		//
		// `BaseChart.updateWidth()` is `element.clientWidth - padding`, and
		// `getElementContentWidth()` reads the padding off `getComputedStyle`, which
		// returns an empty declaration for an element that is not in the document.
		// `parseFloat("")` is NaN, so the width - and every x position, bar width and
		// arc path derived from it - is NaN:
		//
		//     <svg> attribute width: Expected length, "NaN"
		//     <g> attribute transform: Expected number, "translate(NaN, 0)"
		//
		// The SVG that results has no geometry, so there is nothing for a click to
		// land on. `BaseWidget.make()` is what makes this reachable - it calls
		// `make_widget()`, which calls `refresh()`, and only appends the widget to the
		// container on the line after that. The stock timing usually hides it, because
		// `make_chart()` reaches the renderer through a promise; when it does not, the
		// chart is built while detached.
		//
		// So: do not render into an element that cannot be measured. Deferred to the
		// next tick rather than dropped, because `render()` only re-draws from the
		// `this.data` already in hand and does not re-fetch.
		if (!this.sharm_measurable()) {
			this.sharm_attempts = (this.sharm_attempts || 0) + 1;
			if (this.sharm_attempts <= 12) {
				clearTimeout(this.sharm_retry);
				this.sharm_retry = setTimeout(() => this.render(), 120);
				return;
			}
			// Out of retries. Draw it anyway - a chart at the wrong size is worth more
			// than no chart - but say what was wrong with the element, because the
			// alternative is a page of NaN warnings and no explanation.
			console.warn(
				"[sharm_dreams_group] " +
					(this.chart_doc && this.chart_doc.chart_name) +
					" has no measurable width after " +
					this.sharm_attempts +
					" tries; drawn at the wrong size",
				this.sharm_width_report()
			);
		}
		this.sharm_attempts = 0;
		return stock_render.apply(this, arguments);
	};

	/** True once this chart's wrapper is in the document and actually has a width. */
	function measurable(instance) {
		const el = instance.chart_wrapper && instance.chart_wrapper[0];
		if (!el || !el.isConnected) return false;
		return el.clientWidth > 0;
	}
});

sharm_install("chart drill-down", function () {
	// `frappe.widget.widget_factory.chart` is the handle, not `frappe.widgets`.
	// The widgets are ES modules and widget_group.js is what puts them on the
	// factory object - there is no `frappe.widgets.ChartWidget` global at all, so
	// the old reference was always undefined, the guard below returned early, and
	// no chart was ever clickable.
	const widget = sharm_chart_widget();
	if (!widget || !widget.prototype) return false;
	if (widget.prototype.sharm_drilldown_patched) return true;
	widget.prototype.sharm_drilldown_patched = true;

	const stock_get_chart_args = widget.prototype.get_chart_args;

	widget.prototype.get_chart_args = function () {
		const args = stock_get_chart_args.apply(this, arguments);
		// `this.dashboard_chart` is the Chart.js instance, not the document - the
		// document is `chart_doc`, and mixing the two is why the first version of
		// this found no module and wired nothing up.
		//
		// Deliberately NOT `if (!this.get_drilldown_target()) return args`. That was
		// the next bug: the check is asked at draw time, and the field-existence half
		// of it needs `frappe.meta`, which on a cold page load is usually not loaded
		// yet. So a perfectly good chart asked the question too early, was told "no",
		// and was left permanently inert with no listener - the one failure that
		// looks identical to a chart that was never meant to be clickable.
		//
		// So bind the listener unconditionally and let the click itself decide. The
		// listener re-checks on every tap, by which time meta has loaded, and the
		// only charts it refuses are the ones with no doctype, no group-by field or a
		// group-by the destination doctype does not have. A click on one of those does
		// nothing, which is the honest outcome for a chart that cannot be filtered.
		//
		// The styling below still needs the answer up front, so it is applied only
		// where one is already known - and a chart that becomes known later picks the
		// pointer up on its first hover, from the same check the click uses.

		// Say so, on this chart only, and only where the answer is already known.
		// frappe-charts draws to an SVG, and an SVG carries no `:hover` of its own, so
		// the `cursor: pointer` has to be hung on an element. A stock chart stays a
		// picture and gets no pointer: a click that does nothing is worse than one
		// that is not offered. Where the answer is not known yet - meta still loading -
		// the `mouseover` handler below answers it live and sets the pointer then.
		if (this.get_drilldown_target() && this.chart_wrapper && this.chart_wrapper.addClass) {
			this.chart_wrapper.addClass("sharm-clickable");
		}
		if (this.widget && this.widget.attr) {
			this.widget.attr("title", __("Click a bar to see the rows behind it"));
		}

		const self = this;

		// Why a delegated DOM listener and not `args.onClick`:
		//
		// `frappe.utils.make_chart()` copies whatever it is given into `chart_args`
		// and hands them to `new frappe.Chart(...)`, but that constructor reads a
		// fixed list of options - title, type, data, colors, height, isNavigable,
		// animate, ... - and `onClick` is not among them. It was therefore accepted,
		// passed along, and silently dropped on the floor, on every single chart.
		// That is the whole reason no chart ever responded to a click, and it is
		// invisible from the outside: no error, no warning, just a dead chart.
		//
		// What frappe-charts does do is stamp `data-point-index` onto every bar it
		// draws (utils/draw.js), unconditionally - the *listener* it ships is gated
		// behind `isNavigable`, but the attribute is always there. So one delegated
		// listener on the wrapper can read the index off whichever element was
		// clicked and look the label up in the fetched payload.
		const wrap = this.chart_wrapper && this.chart_wrapper[0];
		if (wrap && !wrap.sharm_click_bound) {
			// Guarded on the element, not the widget: `setup_container()` builds a
			// fresh wrapper on every refresh, so a fresh element gets exactly one
			// listener and a flag on the widget would leave the new one unbound.
			wrap.sharm_click_bound = true;
			wrap.sharm_resolve = (event) => sharm_clicked_index(self.dashboard_chart, event, wrap);
			// Asked here, not at draw time: see the note at the top. By the time
			// anyone clicks, meta is loaded, so the answer is the real one.
			//
			// The *series* has to be resolved here too, and that is the one part of a
			// click that is not just an index: a two-line chart draws two things per
			// label and they mean different things, so a click that cannot say which
			// line is not a click - it is half a click, and half a click is refused.
			const resolve_tap = (event) => {
				const index = wrap.sharm_resolve(event);
				if (index < 0) return null;
				const label = sharm_label_at(self, index);
				if (label === undefined || label === null) return null;
				const series = sharm_clicked_series(self.dashboard_chart, index, event);
				// A chart with one field per bar has no series to resolve, and
				// `go_to_drilldown` ignores a null one in that case. Only a chart
				// that needs the answer is held to it.
				const target = self.get_drilldown_target();
				if (target && target.by_series && series === null) return null;
				return { label: label, series: series };
			};
			wrap.addEventListener("click", (event) => {
				if (!self.get_drilldown_target()) return;
				const tap = resolve_tap(event);
				if (!tap) return;
				self.go_to_drilldown(tap.label, tap.series);
			});
			// A pointer over a clickable segment, or nothing looks clickable. The
			// cursor is set from the DOM too, because an SVG gives us no :hover hook.
			//
			// This is also where a chart that could not be judged at draw time becomes
			// visibly tappable: the same check that gates the click decides the pointer,
			// and it is asked again on the first hover, by which point meta has loaded.
			wrap.addEventListener("mouseover", (event) => {
				// `resolve_tap`, not `sharm_resolve`: a two-line chart whose lines are
				// too close to tell is not clickable anywhere, and the pointer has to
				// say so. A pointer over a point that would refuse is the same lie as a
				// working click that opens the wrong list.
				const over = !!self.get_drilldown_target() && !!resolve_tap(event);
				wrap.style.cursor = over ? "pointer" : "default";
				if (over && self.chart_wrapper && self.chart_wrapper.addClass) {
					self.chart_wrapper.addClass("sharm-clickable");
				}
			});
			wrap.addEventListener("mouseout", () => {
				wrap.style.cursor = "default";
			});
		}
		return args;
	};

	/** The label for one data point of a chart, whichever payload happens to hold it. */
function sharm_label_at(widget, index) {
	const chart = widget.dashboard_chart;
	const candidates = [
		chart && chart.data && chart.data.labels,
		widget.data && widget.data.labels,
		chart && chart.state && chart.state.labels,
		chart && chart.formatted_labels,
	];
	for (const labels of candidates) {
		if (labels && typeof labels.length === "number" && index < labels.length) return labels[index];
	}
	return undefined;
}

/**
 * Which data point was clicked, or -1 when the click did not land on one.
 *
 * `data-point-index` is not the answer to this, and treating it as one is what left
 * four chart types dead. frappe-charts stamps that attribute from exactly two
 * functions - `datasetBar` and `datasetDot` in `utils/draw.js` - so it is on a bar and
 * on a scatter dot and on nothing else. A pie slice does not carry it. A donut slice
 * does not. A percentage ring does not. A Line draws no per-point element at all, not
 * even a dot, so there is nothing for the attribute to sit on. Reading it, failing to
 * find it, and returning quietly looks exactly like correct code and leaves every one
 * of those charts unclickable.
 *
 * Four sources, most trustworthy first:
 *
 *   1. the stamped element itself - exact, and what a bar click gives us
 *   2. the chart's own component store. frappe-charts keeps the drawn elements in
 *      `component.store` in data-point order and identifies a hovered slice by its
 *      position in exactly that array (`PieChart.mouseMove`), so reading the position
 *      is the library's own reckoning rather than our guess at it
 *   3. the tooltip. An axis chart works out which point is under the cursor on
 *      `mousemove` and stamps the answer onto the tip (`SvgTip.fill`). A click is
 *      always preceded by the `mousemove` that put the cursor there, so for a Line -
 *      where there is no per-point element - this is the only answer available
 *   4. nothing, and -1
 *
 * The store lookup is length-checked against the number of labels, and that check is
 * load-bearing rather than defensive tidiness. A Line's component store holds one path
 * per *series*, so on a single-series line it holds a single path; looking a click up
 * in it would answer "0" for every point on the line and drill into the first company
 * no matter where on the line you clicked. Requiring the store to be as long as the
 * label list rejects it, and the tooltip answers instead.
 */
function sharm_clicked_index(chart, event, wrap) {
	if (!event) return -1;

	// 1. An element frappe-charts stamped. The tip carries one too and sits over the
	// plot, so it is left for source 3 rather than treated as a miss.
	const unit = event.target && event.target.closest && event.target.closest("[data-point-index]");
	if (unit && wrap.contains(unit) && !unit.closest(".graph-svg-tip")) {
		const stamped = parseInt(unit.getAttribute("data-point-index"), 10);
		if (!isNaN(stamped)) return stamped;
	}

	if (!chart) return -1;

	// 2. The chart's own per-point element store - a pie, donut or percentage ring.
	const stored = sharm_index_in_store(chart, event.target, wrap);
	if (stored !== -1) return stored;

	// 3. The point the cursor is currently over, as frappe-charts resolved it.
	const tip = chart.tip && chart.tip.container;
	if (tip && typeof tip.getAttribute === "function") {
		const hovered = parseInt(tip.getAttribute("data-point-index"), 10);
		if (!isNaN(hovered) && hovered >= 0) return hovered;
	}

	return -1;
}

/**
 * The data-point index of `target` among the chart's per-point elements, or -1.
 *
 * Only stores that are one element per data point count. A store holding one element
 * per series - the line itself, one filled area - is the same length as the label list
 * only by coincidence, and answering from it would be worse than not answering.
 */
function sharm_index_in_store(chart, target, wrap) {
	const components = chart.components;
	if (!components || typeof components.forEach !== "function") return -1;

	const labels =
		(chart.data && chart.data.labels) ||
		(chart.state && chart.state.labels) ||
		chart.formatted_labels;
	const per_point = labels && labels.length ? labels.length : 0;
	if (!per_point) return -1;

	let found = -1;
	components.forEach((component) => {
		if (found !== -1) return;
		const store = component && component.store;
		if (!store || typeof store.indexOf !== "function") return;
		if (store.length !== per_point) return;
		// The click may land on a child of the drawn element, so walk up to the wrap.
		let node = target;
		while (node && node !== wrap) {
			const at = store.indexOf(node);
			if (at !== -1) {
				found = at;
				return;
			}
			node = node.parentNode;
		}
	});
	return found;
}

/**
 * Which of a chart's several lines the click landed on, as a dataset index, or
 * null when that cannot be told.
 *
 * Needed because a two-line timeseries draws two things per x-axis label and they
 * mean different things. `Hiring vs Attrition Count` draws 9 hires and 0 leavers in
 * Feb 2026; tapping the hiring line must open the people who joined in February and
 * tapping the attrition line must open the people who left in February, and one
 * `date_of_joining` filter cannot be both.
 *
 * The chart cannot answer this, which is the reason it has to be asked. frappe-charts'
 * own Line click handler reads one attribute - the x index off the tooltip - and
 * `SvgTip.setValues` is handed the whole *column* of values, so nothing anywhere
 * records which line was hovered. There is no per-series element on a Line to read a
 * stamped index off either: `data-point-index` is put on bars and on scatter dots
 * and on nothing else.
 *
 * What there is, is geometry. `chart.dataByIndex[index].values[i].yPos` is where
 * frappe-charts puts that series' point at that x, in pixels from the top of the
 * chart - its own arithmetic rather than our guess at it. The series whose point lies
 * nearest the cursor is the one under the cursor, which is what the reader meant.
 *
 * The gap between the chart's y-positions and the cursor's is one constant offset for
 * every series at that index, so it cancels out of the comparison and no effort is
 * spent removing it.
 *
 * **The second half of this function is the refusal, and it is the honest part.**
 * Where two lines are closer together than `SERIES_PX` the question has no answer,
 * so the click navigates nowhere rather than to a coin toss - at February's
 * 9-against-0 the two are far enough apart to be certain, and at a month where the
 * lines cross they are not. Opening the wrong list is the one outcome this file
 * exists to prevent, and "I clicked there and it opened the other line" is that
 * outcome wearing a working clicker's clothes.
 */
function sharm_clicked_series(chart, index, event) {
	if (!chart || index < 0) return null;
	const column = chart.dataByIndex && chart.dataByIndex[index];
	const values = column && column.values;
	if (!Array.isArray(values) || !values.length) return null;
	// One line: there is nothing to tell apart, so it is that line.
	if (values.length === 1) return 0;

	if (!event || typeof event.pageY !== "number") return null;
	if (!chart.container || typeof chart.container.getBoundingClientRect !== "function") return null;
	const box = chart.container.getBoundingClientRect();
	const cursorY = event.pageY - box.top - (window.pageYOffset || 0);

	let nearest = -1;
	let nearestGap = Infinity;
	let secondGap = Infinity;
	values.forEach((value, series) => {
		if (!value || typeof value.yPos !== "number") return;
		const gap = Math.abs(value.yPos - cursorY);
		if (gap < nearestGap) {
			secondGap = nearestGap;
			nearestGap = gap;
			nearest = series;
		} else if (gap < secondGap) {
			secondGap = gap;
		}
	});

	// No line has a position, so the chart has not drawn one we can aim at.
	if (nearest === -1) return null;
	// Far from every line: the click was on the grid, not on a point.
	if (nearestGap > SERIES_PX) return null;
	// Two lines a whisker apart: genuinely ambiguous, so no.
	if (secondGap - nearestGap < SERIES_PX) return null;
	return nearest;
}

/**
 * The field a tapped series filters on, or null when the click does not say.
 *
 * A chart with no `by_series` is the ordinary one-bar-one-thing case and its single
 * `field` is the answer. A chart with one has to be told which line, and refuses when
 * it is not - including when it is told a line the map does not declare, because a
 * drilldown that has not described that line cannot describe its list.
 */
function sharm_series_field(target, series) {
	if (!target) return null;
	if (!target.by_series) return target.field || null;
	if (typeof series !== "number" || series < 0) return null;
	const field = target.by_series[String(series)];
	return typeof field === "string" && field.length ? field : null;
}
function sharm_index_in_store(chart, target, wrap) {
	const components = chart.components;
	if (!components || typeof components.forEach !== "function") return -1;

	const labels =
		(chart.data && chart.data.labels) ||
		(chart.state && chart.state.labels) ||
		chart.formatted_labels;
	const per_point = labels && labels.length ? labels.length : 0;
	if (!per_point) return -1;

	let found = -1;
	components.forEach((component) => {
		if (found !== -1) return;
		const store = component && component.store;
		if (!store || typeof store.indexOf !== "function") return;
		if (store.length !== per_point) return;
		// The click may land on a child of the drawn element, so walk up to the wrap.
		let node = target;
		while (node && node !== wrap) {
			const at = store.indexOf(node);
			if (at !== -1) {
				found = at;
				return;
			}
			node = node.parentNode;
		}
	});
	return found;
}

	/** Where one segment of this chart leads, or null if it is only a picture. */
	widget.prototype.get_drilldown_target = function () {
		const doc = this.chart_doc;
		if (!doc) return null;
		// A `Custom` chart buckets its own dates in Python, so it has no
		// `group_by_based_on` to filter the list on. It declares the mapping from bar
		// to filter in `custom_options` instead - see `sharm_custom_drilldown`.
		if (doc.chart_type === "Custom") return sharm_custom_drilldown(doc);
		// A Count and a Sum both group by a field; without one there is nothing to
		// filter the list on, so the chart is a picture and stays one. This is the
		// whole test - see the note above on why the module is not part of it.
		const field = doc.group_by_based_on;
		const doctype = doc.document_type;
		if (!field || !doctype || !doc.group_by_type) return null;
		// The field also has to exist on the destination doctype, or the desk
		// silently drops the filter: `parse_filters_from_route_options` asks
		// `frappe.meta.get_doctype_for_field()` to place it, and when that returns
		// nothing the filter is simply not pushed - the list opens showing every
		// row while the URL still claims to be filtered. The stock `Webpage Views`
		// chart is the live example: it groups `Web Page View` by `owner`, and that
		// doctype has no such field. So it stays a picture, because a click that
		// navigates and then lies about what it filtered is worse than one that
		// honestly does nothing.
		if (!sharm_field_exists(doctype, field)) return null;
		return { doctype: doctype, field: field, filters: sharm_chart_filters(doc, doctype) };
	};

	/**
	 * A `Custom` chart's `custom_options` -> the same shape a Group By target has,
	 * with `buckets` alongside it, or null.
	 *
	 * A Group By chart is drillable because its bars *are* values of a field: tapping
	 * `Bautex Misr` on a chart grouped by `company` means `company = "Bautex Misr"`.
	 * A `Custom` chart's bars are computed - "Ends in 30 days" is a window nobody
	 * stores on the row - so the bar cannot be the filter and the chart source has to
	 * say what each one means. It says so here, and `hr/dashboard.py` builds both
	 * halves from one definition so they cannot drift apart.
	 *
	 * Matched by label, not by bar position, so that reordering the bars cannot
	 * quietly point each one at another bucket's list.
	 */
	function sharm_custom_drilldown(doc) {
		// `frappe.utils.parse_json`, and the namespace is the whole point. There is no
		// `frappe.parse_json` on the desk - the desk's helper is `frappe.utils.
		// parse_json` - so the call used to raise a TypeError which the `catch` here
		// returned quietly, and every Custom chart read as an unclickable picture
		// while the "clickable" count said otherwise. Nothing on the page errors and
		// the banner reports the opposite of the truth.
		//
		// It also *returns* its input when the input is not JSON rather than throwing,
		// so the shape is checked rather than assumed: a bare string has no
		// `.drilldown`, so a malformed `custom_options` lands on the same `!spec`
		// branch a missing one does.
		const options = frappe.utils.parse_json(doc.custom_options);
		const spec = options && options.drilldown;
		if (!spec || !spec.doctype || !spec.field) return null;
		// An *empty* bucket list is as broken as a missing one: every bar looks up a
		// bucket, finds none, and navigates nowhere - so the chart would carry a
		// pointer and be counted as clickable while doing nothing at all. Python says
		// the same thing (`_custom_chart_is_tappable`), so the two cannot disagree
		// about which charts are only pictures.
		if (!Array.isArray(spec.buckets) || !spec.buckets.length) return null;
		// The same reason the Group By path checks the field exists: a filter on a
		// field the list has no column for is dropped in the browser, so the URL
		// would claim a filter the list never applied.
		if (!sharm_field_exists(spec.doctype, spec.field)) return null;
		// Every field `by_series` names has to exist too, or a click on that line
		// navigates with a filter the list drops - which is the "looks clickable,
		// opens the wrong list" failure. Checked here rather than at click time so
		// the chart with an unusable series is a picture from the start.
		if (spec.by_series && !sharm_series_fields_exist(spec.doctype, spec.by_series)) {
			return null;
		}
		return {
			doctype: spec.doctype,
			field: spec.field,
			by_series: spec.by_series,
			buckets: spec.buckets,
			filters: Object.assign({}, spec.base_filters),
			bucketed: true,
		};
	}

	/**
	 * Does the doctype carry every field a `by_series` map names?
	 *
	 * `by_series` is keyed by *dataset index*, not by series name, because the chart
	 * source labels its datasets with `_()` and a name-keyed map resolves for one
	 * language and silently stops resolving for another. See
	 * `hr/dashboard_chart_source/hiring_vs_attrition`.
	 */
	function sharm_series_fields_exist(doctype, by_series) {
		if (!by_series || typeof by_series !== "object") return false;
		const keys = Object.keys(by_series);
		if (!keys.length) return false;
		return keys.every((key) => {
			const field = by_series[key];
			return typeof field === "string" && field.length > 0 && sharm_field_exists(doctype, field);
		});
	}

	/** The bucket whose label is `label`, or null. Case and padding-insensitive:
	 * the labels arrive translated and the comparison happens in the browser. */
	function sharm_find_bucket(buckets, label) {
		if (typeof label !== "string") return null;
		const wanted = label.trim().toLowerCase();
		for (const bucket of buckets) {
			if (bucket && typeof bucket.label === "string" && bucket.label.trim().toLowerCase() === wanted) {
				return bucket;
			}
		}
		return null;
	}

	/**
	 * A bucket's day offsets -> the `[operator, value]` a list filter wants.
	 *
	 * Offsets rather than dates because the bucket is stored on the chart document
	 * (see `contract_end_dates.window_specs`): a date baked in at setup would still
	 * be next month's answer when someone tapped the bar in December.
	 *
	 * `floor` is the one thing taken literally, and deliberately: it is the cut-off
	 * that stops a blank Date from being admitted to a `between` - Frappe compares a
	 * Date as `ifnull(field, '0001-01-01')`, so "before today" on its own would list
	 * everyone with no date as well. See `DATED_FLOOR`.
	 *
	 * `date_from` / `date_to` are the exception, and they exist because offsets
	 * cannot say what an age band is: the band moves with the calendar year and an
	 * offset moves with the day. Those two are read as absolute dates, and the chart
	 * that needs them rewrites them on every migrate so they cannot go stale.
	 */
	function sharm_bucket_filter(bucket) {
		if (bucket.op === "is") return [bucket.op, bucket.value || "not set"];
		const today = frappe.datetime.now_date();
		const shift = (days) => moment(today).add(days, "days").format("YYYY-MM-DD");
		if (bucket.op === "between") {
			// `date_from` / `date_to` are absolute, and take the whole branch. An age
			// band cannot be expressed as a day offset: "25-29" moves with the
			// calendar year while an offset moves with the day, so thirty years is not
			// 10957 days and never will be. The chart that needs them writes them
			// fresh on every migrate - see
			// `hr/dashboard_chart_source/employees_by_age`.
			if (bucket.date_from || bucket.date_to) {
				if (!bucket.date_from || !bucket.date_to) return null;
				return ["between", [bucket.date_from, bucket.date_to]];
			}
			const from = bucket.floor || shift(bucket.from);
			return ["between", [from, shift(bucket.to)]];
		}
		if (bucket.op === ">") return [">", shift(bucket.offset)];
		return null;
	}

	widget.prototype.go_to_drilldown = function (label, series) {
		const target = this.get_drilldown_target();
		if (!target) return;

		// A computed bar names a window, not a stored value, so it cannot be handed
		// over as one. The chart source declared what it means; look that up.
		if (target.bucketed) {
			const bucket = sharm_find_bucket(target.buckets, label);
			if (!bucket) return;
			const condition = sharm_bucket_filter(bucket);
			if (!condition) return;
			// Which line the click was on decides *which field* the window filters,
			// so a two-line chart cannot use its own `field` here - see
			// `sharm_series_field`.
			const field = sharm_series_field(target, series);
			if (!field) return;
			sharm_navigate(
				"List/" + target.doctype,
				Object.assign({}, target.filters, { [field]: condition })
			);
			return;
		}

		// The chart's *own* scope, plus the segment that was tapped.
		//
		// Sending only the group-by field is what made this feel broken rather than
		// unfinished: "Headcount by Company" is filtered to Active staff, so tapping
		// a bar opened `List/Employee` with `company = "..."` and nothing else - and
		// the list then showed leavers, whose heads are in no bar on the chart. The
		// figure on the card and the number of rows behind it disagreed, which is the
		// one outcome this exists to prevent.  A number card avoids it by carrying its
		// whole filter set in `route_options`; a chart has to read it off
		// `filters_json`, because the segment is only known at click time.
		//
		// A blank bucket. The chart labels its empty group "Not Set" / "None", and
		// passing that through as a *value* filters on the string and lists nothing -
		// so the click appears dead on exactly the segments most worth looking at
		// (uninsured staff, missing IDs, no department). Frappe's "not set" covers
		// both NULL and the empty string, which is what the bucket actually holds.
		//
		// Only a *string* label can name the blank bucket.  A group-by whose field is
		// numeric hands back numbers, and reading those as text gave `""` - which is
		// in `blank`, so every band on "Life Insurance Band Split" (grouped by the
		// Currency `from_gross`) was filtered as `from_gross is not set` and opened the
		// empty bucket instead of the band that was tapped.
		const blank = ["not set", "none"];
		const is_blank =
			label === null ||
			label === undefined ||
			(typeof label === "string" &&
				(label.trim() === "" || blank.indexOf(label.trim().toLowerCase()) !== -1));
		const value = is_blank ? ["is", "not set"] : label;

		// Left for the List to consume and clear - see the note in set_route above.
		sharm_navigate(
			"List/" + target.doctype,
			Object.assign({}, target.filters, { [target.field]: value })
		);
	};

	/**
	 * A chart's `filters_json` -> the `{field: value}` shape the List reads.
	 *
	 * `filters_json` is `[[doctype, field, operator, value], ...]` - one entry per
	 * doctype that takes part, which is why the doctype is dropped: the List is
	 * already opened on `document_type`.  A plain "=" is the List's default operator
	 * and is left implicit; anything else (`>`, `like`, `in`, ...) has to be carried
	 * as `[operator, value]`, because a bare value would be read as equality.
	 *
	 * Returns {} rather than null so the caller can spread it unconditionally.
	 */
	function sharm_chart_filters(doc, doctype) {
		// `frappe.utils.parse_json` - see the note in `sharm_custom_drilldown` for why
		// the namespace matters. The bug it caused here was the expensive one: the
		// wrong name raised a TypeError that the `catch` turned into `{}`, so *every*
		// chart on the dashboard opened a list with no scope at all. "Headcount by
		// Company" is filtered to Active staff, and tapping it opened every employee
		// in the company, leavers included - which is the exact disagreement the cards
		// avoid by carrying their own `route_options`.
		//
		// It returns unparseable input unchanged instead of throwing, so the shape is
		// checked: a bare string is not a list of filter rows.
		const rows = frappe.utils.parse_json(doc.filters_json);
		const out = {};
		if (!Array.isArray(rows)) return out;
		for (const row of rows) {
			if (!Array.isArray(row) || row.length < 3) continue;
			const [row_doctype, field, operator, value] = row;
			// a filter set on another doctype cannot narrow this list; dropping it is
			// better than sending a field the List cannot apply
			if (row_doctype && doctype && row_doctype !== doctype) continue;
			if (!field) continue;
			if (operator === "=" || operator === undefined || operator === null) {
				out[field] = value;
			} else {
				out[field] = [operator, value];
			}
		}
		return out;
	}
});
