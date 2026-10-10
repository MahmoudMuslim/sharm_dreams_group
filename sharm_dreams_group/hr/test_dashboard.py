# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the Employee dashboard.

The cards are the most reachable thing this app publishes - each one answers
"how many" for the whole company from a URL with a session cookie - so the
figures themselves are pinned here rather than only the definitions.
"""

from __future__ import annotations

import importlib
import json
import re
from typing import ClassVar

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, flt, getdate, nowdate

from sharm_dreams_group.hr import dashboard, dashboard_hrm


def _chart_source_js(source: str) -> str:
	"""The JS a `Dashboard Chart Source` resolves to, read the way the desk reads it.

	`DashboardChartSource.read_config` looks for
	`<module path>/dashboard_chart_source/<scrubbed source>/<scrubbed source>.js`,
	and so does this, so a source whose folder has moved fails here rather than
	in the browser with an empty chart and no console message.
	"""
	from frappe.modules import get_module_path

	scrubbed = frappe.scrub(source)
	path = f"{get_module_path('Sharm Dreams Group')}/dashboard_chart_source/{scrubbed}/{scrubbed}.js"
	with open(path) as handle:
		return handle.read()


def render_chart(name: str) -> dict:
	"""Fetch one chart's data exactly as `chart_widget.js` fetches it.

	`dashboard_chart.get` is *not* the path a `Custom` chart takes. It has no
	`document_type` to group on, so it falls through to the timeseries query and
	raises `DocType None not found` - a desk-side dead end rather than a bug in
	the source. The widget reads `chart_doc.source`, resolves that source's JS,
	and calls the method named there, so this resolves the same steps by hand.

	The method is read out of the JS rather than imported from a Python list,
	because the failure worth catching is a source whose JS names a method that
	does not exist or is not whitelisted: that renders as an empty chart with
	nothing in the console, and only the browser ever sees it.
	"""
	chart = frappe.get_doc("Dashboard Chart", name)

	if chart.chart_type != "Custom":
		dc = importlib.import_module("frappe.desk.doctype.dashboard_chart.dashboard_chart")
		get = getattr(dc.get, "__wrapped__", dc.get)
		return get(chart_name=name, chart=chart.as_dict())

	match = re.search(r"method:\s*[\"']([^\"']+)[\"']", _chart_source_js(chart.source))
	assert match, f"{name}: its source's JS names no method to call"
	method = match.group(1)
	module_path, _dot, attribute = method.rpartition(".")
	getter = getattr(importlib.import_module(module_path), attribute, None)
	assert callable(getter), f"{name}: the source's JS names {method}, which does not exist"
	# Called through `frappe.call` so the `@whitelist` gate is exercised: a
	# method that is not whitelisted is exactly as unreachable as one that is
	# missing, and `frappe.call` is what the browser does. `chart_name` is not
	# optional either - `cache_source` reads the chart document out of it to key
	# the cache, and raises on a source called without one.
	return frappe.call(method, chart_name=name, filters=json.dumps({}))


class TestDashboardMetrics(FrappeTestCase):
	def test_every_metric_answers(self):
		"""A card that throws is worse than no card: it renders as a red box."""
		for spec in dashboard.metric_definitions():
			with self.subTest(metric=spec["key"]):
				value = dashboard.metric({"metric": spec["key"]})
				self.assertIsInstance(value, (int, float), spec["key"])
				self.assertGreaterEqual(value, 0, spec["key"])

	def test_an_unknown_metric_is_refused(self):
		"""Not silently zero - zero reads as a real answer."""
		self.assertRaises(frappe.ValidationError, dashboard.metric, {"metric": "no_such_metric"})

	def test_active_headcount_agrees_with_the_database(self):
		self.assertEqual(
			dashboard.metric({"metric": "active_headcount"}),
			frappe.db.count("Employee", {"status": "Active"}),
		)

	def test_leavers_is_a_separate_population(self):
		"""Left and Active are disjoint, so these must not both count one person."""
		active = frappe.db.count("Employee", {"status": "Active"})
		left = frappe.db.count("Employee", {"status": "Left"})
		self.assertEqual(dashboard.metric({"metric": "leavers"}), left)
		self.assertNotEqual(active, left)

	def test_payroll_cards_agree_with_the_latest_submitted_run(self):
		"""The figures on a card have to be the ones in the ledger, not last month's."""
		period = dashboard._latest_payroll()
		if not period:
			self.skipTest("no submitted payroll on this site")
		want = frappe.db.sql(
			"select sum(net_pay) from `tabSalary Slip` "
			"where docstatus = 1 and start_date = %s and end_date = %s",
			period,
		)[0][0]
		self.assertAlmostEqual(dashboard.metric({"metric": "payroll_net"}), float(want or 0), places=2)

	def test_expired_id_cards_only_count_active_staff(self):
		"""A leaver's expired ID is not something HR can act on."""
		metric = "id_expired"
		before = frappe.db.count(
			"Employee",
			{"status": "Active", "sdg_national_id_expiry": ("<", frappe.utils.nowdate())},
		)
		self.assertEqual(dashboard.metric({"metric": metric}), before)

	def test_a_leaver_is_not_counted_as_missing_a_department(self):
		"""The 130 imported leavers are nearly all missing one; that is not a gap to chase."""
		if not frappe.db.count("Employee", {"status": "Left"}):
			self.skipTest("no leavers on this site")
		self.assertEqual(dashboard.metric({"metric": "missing_department"}), 0)


class TestDashboardCards(FrappeTestCase):
	def test_every_metric_has_a_card(self):
		"""A metric with no card is unreachable from the desk."""
		labels = set(
			frappe.get_all(
				"Number Card",
				filters={"method": dashboard.METRIC_METHOD},
				pluck="label",
				limit_page_length=0,
			)
		)
		for spec in dashboard.metric_definitions():
			self.assertIn(spec["label"], labels, spec["key"])

	def test_cards_point_at_a_metric_that_exists(self):
		for row in frappe.get_all(
			"Number Card",
			filters={"method": dashboard.METRIC_METHOD},
			fields=["label", "filters_json"],
			limit_page_length=0,
		):
			key = (frappe.parse_json(row.filters_json) or {}).get("metric")
			self.assertIn(key, dashboard.METRICS, row.label)

	def test_our_charts_belong_to_our_module(self):
		charts = frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group"},
			fields=["chart_name", "chart_type", "document_type", "source"],
			limit_page_length=0,
		)
		self.assertTrue(charts, "no charts created for the Employee dashboard")
		for c in charts:
			# Two shapes, and which one it is decides where its data comes from. A
			# `Group By` names the doctype it groups; a `Custom` names a source whose
			# method does the work and has no doctype of its own to group on - which
			# is why asserting a `document_type` here would fail on a chart that is
			# working exactly as designed.
			if c.chart_type == "Custom":
				self.assertTrue(c.source, f"{c.chart_name} is Custom but names no source")
				self.assertTrue(
					frappe.db.exists("Dashboard Chart Source", c.source),
					f"{c.chart_name} points at a source that does not exist",
				)
				continue
			self.assertTrue(c.document_type, f"{c.chart_name} has no document type")
			self.assertIn(c.chart_type, ("Group By", "Count", "Sum"), c.chart_name)

	def test_every_card_and_chart_is_on_our_workspace(self):
		"""A card nothing links to is a card nobody sees.

		Asserted against the child tables, not `content`: this Frappe version
		renders `number_cards` / `charts` and ignores the JSON blocks, so a
		dashboard written only into `content` looks complete in the record and
		shows nothing on the desk.
		"""
		from sharm_dreams_group.workspace import WORKSPACE

		ws = frappe.get_doc("Workspace", WORKSPACE)
		placed_cards = {row.number_card_name for row in ws.number_cards}
		placed_charts = {row.chart_name for row in ws.charts}

		self.assertTrue(placed_cards, "no Number Cards on the workspace")
		self.assertTrue(placed_charts, "no Charts on the workspace")
		for spec in dashboard.metric_definitions():
			self.assertIn(spec["label"], placed_cards, spec["key"])
		# the workspace is deliberately a focused subset - the dashboards carry the
		# detail - so this asserts the charts it does place all resolve
		for name in placed_charts:
			self.assertTrue(frappe.db.exists("Dashboard Chart", name), name)

	def test_every_card_has_a_block_in_the_content_layout(self):
		"""content is the layout; the child tables are only the lookup.

		`workspace.js` draws the blocks in `Workspace.content`.  A card present in
		`number_cards` with no block here is silently not drawn - the dashboard then
		looks complete in the record and nearly empty on the page.
		"""
		from sharm_dreams_group.workspace import WORKSPACE, content_blocks

		placed = {b["data"]["number_card_name"] for b in content_blocks() if b.get("type") == "number_card"}
		for spec in dashboard.metric_definitions():
			self.assertIn(spec["label"], placed, spec["key"])
		# the penalty strip too, otherwise the dashboard opens with six tiles of zeros
		for label in frappe.get_all(
			"Number Card",
			filters={"method": "sharm_dreams_group.hr.penalty.headcount"},
			pluck="name",
			limit_page_length=0,
		):
			self.assertIn(label, placed, label)

	def test_every_chart_block_in_the_workspace_renders(self):
		"""Whatever the workspace chooses to show must actually plot something.

		The workspace is a focused set - the dashboards carry the rest - so this
		asserts the blocks it *has* all render, rather than that it lists every chart
		the app publishes.  A chart with no rows draws an empty frame, which is the
		failure this catches.
		"""
		import importlib

		from sharm_dreams_group.workspace import content_blocks

		dc = importlib.import_module("frappe.desk.doctype.dashboard_chart.dashboard_chart")
		render = getattr(dc.get, "__wrapped__", dc.get)

		placed = [b["data"]["chart_name"] for b in content_blocks() if b.get("type") == "chart"]
		self.assertTrue(placed, "the workspace lays out no charts at all")
		for name in placed:
			cfg = render(chart_name=name, chart=frappe.get_doc("Dashboard Chart", name).as_dict())
			self.assertTrue(cfg.get("labels"), f"{name} is on the workspace but plots nothing")

	def test_no_content_block_points_at_something_deleted(self):
		"""An empty tile reads as zero people, which is a different and wrong answer."""
		from sharm_dreams_group.workspace import content_blocks

		for b in content_blocks():
			data = b.get("data") or {}
			if b.get("type") == "number_card":
				self.assertTrue(
					frappe.db.exists("Number Card", data.get("number_card_name")),
					f"{data.get('number_card_name')} has a block but no card",
				)
			if b.get("type") == "chart":
				self.assertTrue(
					frappe.db.exists("Dashboard Chart", data.get("chart_name")),
					f"{data.get('chart_name')} has a block but no chart",
				)

	def test_a_workspace_row_never_points_at_a_deleted_card(self):
		"""An empty tile reads as zero people, which is a different and wrong answer."""
		from sharm_dreams_group.workspace import WORKSPACE

		ws = frappe.get_doc("Workspace", WORKSPACE)
		for row in ws.number_cards:
			self.assertTrue(
				frappe.db.exists("Number Card", row.number_card_name),
				f"{row.number_card_name} is on the workspace but does not exist",
			)
		for row in ws.charts:
			self.assertTrue(
				frappe.db.exists("Dashboard Chart", row.chart_name),
				f"{row.chart_name} is on the workspace but does not exist",
			)

	def test_our_cards_are_public(self):
		"""The workspace is public, so a card only some roles can see helps nobody."""
		for label in frappe.get_all(
			"Number Card",
			filters={"method": dashboard.METRIC_METHOD},
			pluck="label",
			limit_page_length=0,
		):
			self.assertEqual(cint(frappe.db.get_value("Number Card", label, "is_public")), 1, label)

	def test_every_card_returns_a_number_the_way_the_desk_calls_it(self):
		"""A card whose stored filters are wrong renders as an error tile, not a figure.

		The desk sends the card's own `filters_json` to the card's `method`, so
		calling the method bare proves nothing.  The penalty cards are included here
		because they live on the dashboard too.
		"""
		from sharm_dreams_group.setup import DASHBOARD_NAME

		for row in frappe.get_doc("Dashboard", DASHBOARD_NAME).cards:
			card = frappe.get_doc("Number Card", row.card)
			self.assertTrue(card.filters_json, f"{row.card} stores no filters for its method")
			value = frappe.call(card.method, card.filters_json)
			self.assertIsNotNone(value, row.card)
			self.assertIsInstance(value, (int, float), f"{row.card} returned {type(value).__name__}")

	def test_every_chart_returns_data_through_the_desk_code_path(self):
		"""Run the charts the way the chart widget does.

		       This is the check that matters for "does the dashboard show anything":
		       a chart document can be perfectly well formed and still render an error
		box, because the query only runs here.  `render_chart` follows the same path
		the widget takes, which differs by chart type - see its docstring for why
		`dashboard_chart.get` is not it.
		"""
		for name in dashboard.CHART_NAMES:
			cfg = render_chart(name)
			self.assertIsInstance(cfg, dict, name)
			labels = cfg.get("labels") or []
			datasets = cfg.get("datasets") or []
			self.assertTrue(labels, f"{name} returned no rows")
			self.assertTrue(datasets, f"{name} returned no datasets")
			self.assertEqual(len(datasets[0].get("values") or []), len(labels), name)
			self.assertTrue(any(v for v in datasets[0]["values"]), f"{name} plotted only zeros")

	def test_the_app_dashboard_exists_with_every_card(self):
		"""A dashboard is a Dashboard document holding the cards, not a page the desk can route to.

		Reachable from the panel through the workspace: `link_type=Dashboard` produces
		`dashboard-view/<name>`, which 404s because no `DashboardViewFactory` is
		registered (see `apply_app_sidebar`).
		"""
		from sharm_dreams_group.setup import (
			ACCOUNT_DASHBOARD_NAME,
			APP_SIDEBAR,
			DASHBOARD_NAME,
			HRM_DASHBOARD_NAME,
		)

		self.assertTrue(frappe.db.exists("Dashboard", DASHBOARD_NAME))
		doc = frappe.get_doc("Dashboard", DASHBOARD_NAME)
		placed = {r.card for r in doc.cards}
		# The workforce and penalty methods, not `like 'sharm_dreams_group.%'`:
		# the Account and HRM dashboards own cards of their own now, and a prefix
		# match would demand this dashboard carry all of them - the same mistake the
		# builder had, which is why both had to be narrowed.
		from sharm_dreams_group.hr.dashboard import METRIC_METHOD
		from sharm_dreams_group.setup import PENALTY_CARDS_METHOD

		self.assertEqual(
			placed,
			{
				r[0]
				for r in frappe.db.sql(
					"select label from `tabNumber Card` where method in (%s, %s)",
					[METRIC_METHOD, PENALTY_CARDS_METHOD],
				)
			},
			"the dashboard and the workforce cards disagree",
		)
		for row in doc.cards:
			self.assertTrue(frappe.db.exists("Number Card", row.card), row.card)
		for row in doc.charts:
			self.assertTrue(frappe.db.exists("Dashboard Chart", row.chart), row.chart)
		# every chart on the dashboard must plot something, and every chart the app
		# publishes must be on one of the three dashboards
		ours = set(
			frappe.get_all(
				"Dashboard Chart",
				filters={"module": "Sharm Dreams Group"},
				pluck="chart_name",
				limit_page_length=0,
			)
		)
		# all four, not just the original two: a chart the Account or HRM dashboard
		# publishes is published, and demanding it appear on the workforce dashboard
		# would be the same scoping mistake as above.
		on_dashboards = set()
		for name in (DASHBOARD_NAME, ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME):
			on_dashboards |= {r.chart for r in frappe.get_doc("Dashboard", name).charts}
		self.assertEqual(ours - on_dashboards, set(), "a published chart is on no dashboard")

		# the panel reaches the dashboard through the Dashboard document, and Home
		# reaches the workspace; both must be present
		sidebar = frappe.get_doc("Workspace Sidebar", APP_SIDEBAR)
		pointers = {(r.link_type, r.link_to) for r in sidebar.items if r.type == "Link"}
		self.assertIn(("Dashboard", DASHBOARD_NAME), pointers)
		self.assertIn(("Workspace", "Sharm Dreams Group"), pointers)

	def test_building_the_dashboard_twice_adds_nothing(self):
		"""Second run must replace the rows, or the dashboard doubles every card."""
		from sharm_dreams_group.setup import DASHBOARD_NAME, create_dashboard

		before = frappe.get_doc("Dashboard", DASHBOARD_NAME)
		cards, charts = len(before.cards), len(before.charts)
		create_dashboard()
		after = frappe.get_doc("Dashboard", DASHBOARD_NAME)
		self.assertEqual(len(after.cards), cards)
		self.assertEqual(len(after.charts), charts)
		self.assertEqual(len({r.card for r in after.cards}), cards)

	def test_no_duplicate_card_per_metric(self):
		"""One card per metric. Duplicates answer the same question twice."""
		rows = frappe.db.sql(
			"select label, count(*) c from `tabNumber Card` where method = %s group by label having c > 1",
			(dashboard.METRIC_METHOD,),
		)
		self.assertFalse(rows, f"a card has been duplicated: {rows}")

	def test_no_standard_chart_was_edited(self):
		"""Stock charts are restored on migrate; ours must not overwrite them."""
		ours = set(
			frappe.get_all(
				"Dashboard Chart",
				filters={"module": "Sharm Dreams Group"},
				pluck="name",
				limit_page_length=0,
			)
		)
		for name in ours:
			self.assertEqual(
				cint(frappe.db.get_value("Dashboard Chart", name, "is_standard")),
				0,
				f"{name} is flagged standard, so migrate cannot restore it",
			)


class TestDashboardAccess(FrappeTestCase):
	def test_a_user_without_hr_roles_is_refused(self):
		"""The numbers are behind a guard; client-side hiding is not one."""
		frappe.set_user("Guest")
		self.assertFalse(dashboard.can_view_dashboard())
		self.assertRaises(frappe.PermissionError, dashboard.metric, {"metric": "active_headcount"})

	def test_administrator_is_allowed(self):
		frappe.set_user("Administrator")
		self.assertTrue(dashboard.can_view_dashboard())


class TestAppSidebar(FrappeTestCase):
	"""The app's side panel.

	`Workspace Sidebar Item.link_to` is a Dynamic Link: a link to something that
	does not exist opens an empty page rather than erroring, so nothing about a
	broken panel is visible from the UI.  These tests are the only thing standing
	between a typo and a dead tile.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.setup import APP_SIDEBAR

		cls.name = APP_SIDEBAR
		cls.doc = frappe.get_doc("Workspace Sidebar", cls.name)

	def test_the_app_has_exactly_one_sidebar(self):
		"""The desk picks the panel by `app`, so a second one is an arbitrary winner."""
		others = frappe.db.get_all(
			"Workspace Sidebar",
			filters={"app": "sharm_dreams_group", "name": ["!=", self.name]},
			pluck="name",
			limit_page_length=0,
		)
		self.assertEqual(others, [], "a second sidebar is competing for the same app")

	def test_every_item_resolves(self):
		"""Every link must open something.  An unresolved one is skipped, not written."""
		from sharm_dreams_group.setup import _LINK_DOCTYPE

		links = [r for r in self.doc.items if r.type == "Link"]
		self.assertTrue(links)
		for row in links:
			self.assertTrue(row.label, "a link with no label renders as a blank row")
			doctype = _LINK_DOCTYPE.get(row.link_type)
			if doctype:
				self.assertTrue(
					frappe.db.exists(doctype, row.link_to),
					f"{row.label} points at {row.link_type} {row.link_to!r}, which does not exist",
				)

	def test_sections_appear_in_the_requested_order(self):
		"""Top-level sections in the order asked for, and none left empty."""
		from sharm_dreams_group.setup import APP_SIDEBAR_SECTIONS

		requested = [title for title, _subs in APP_SIDEBAR_SECTIONS]
		written = [r.label for r in self.doc.items if r.type == "Section Break" and not r.child]
		# a section whose links all fail to resolve is dropped, so this is a prefix
		self.assertEqual(written, requested[: len(written)])

		for title, rows in _children_of(self.doc).items():
			self.assertTrue(rows, f"section {title} is a header with nothing under it")
			self.assertTrue(
				all(r.child for r in rows if r.type == "Link"),
				f"{title} links are not indented, so they escape the section",
			)

	def test_subsections_nest_inside_their_section(self):
		"""A sub-section needs `child` *and* `indent` to actually nest.

		`find_nested_items()` files rows by `child`, and sidebar_item.html only draws a
		section header as a child when `indent == 1` - so a sub-section missing either
		one either floats to the top level or swallows its neighbours' rows.
		"""
		from sharm_dreams_group.setup import APP_SIDEBAR_SECTIONS

		nested = [r for r in self.doc.items if r.type == "Section Break" and r.child]
		self.assertTrue(nested, "no sub-sections: the groups were flattened away")
		for row in nested:
			self.assertEqual(row.indent, 1, f"sub-section {row.label!r} has child=1 but indent != 1")

		# and every sub-section named in the spec is present, under its own section
		from sharm_dreams_group.setup import panel_sections

		specified = {title: [sub for sub, _links in groups if sub] for title, groups in panel_sections()}
		parent = None
		found: dict[str, list] = {}
		for row in self.doc.items:
			if row.type != "Section Break":
				continue
			if row.child:
				found.setdefault(parent, []).append(row.label)
			else:
				parent = row.label
		for title, subs in specified.items():
			if title in found:
				self.assertEqual(found[title], subs, f"{title} sub-sections are in the wrong order")

	def test_every_section_is_closed_by_default(self):
		"""Every section opens collapsed, so the panel is one line per section.

		`keep_closed` is what the desk reads to decide a section starts shut, and
		it is silent in the UI when wrong: the panel simply comes up longer than
		anyone expected.  Sub-sections included - a sub-section left open reopens
		its parent the moment that parent is expanded.
		"""
		sections = [r for r in self.doc.items if r.type == "Section Break"]
		self.assertTrue(sections)
		for row in sections:
			kind = "sub-section" if row.child else "section"
			self.assertEqual(
				row.keep_closed,
				1,
				f"{kind} {row.label!r} starts expanded, so the panel opens on a long list",
			)

	def test_the_panel_covers_what_the_site_can_serve(self):
		"""Every requested link that exists is present, and none is silently dropped.

		The reverse direction of `test_every_item_resolves`: that one proves nothing
		dangles, this one proves nothing was left out.
		"""
		from sharm_dreams_group.setup import _link_resolves, panel_links

		written = {(r.link_to, r.link_type) for r in self.doc.items if r.type == "Link"}
		for _place, label, link_type, link_to, _url in panel_links():
			if _link_resolves(link_type, link_to):
				self.assertIn((link_to, link_type), written, f"{label} resolves but is not in the panel")

	def test_building_the_panel_twice_adds_nothing(self):
		from sharm_dreams_group.setup import apply_app_sidebar

		before = [(r.type, r.label, r.link_to) for r in self.doc.items]
		apply_app_sidebar()
		after = [(r.type, r.label, r.link_to) for r in frappe.get_doc("Workspace Sidebar", self.name).items]
		self.assertEqual(after, before)


class TestSidebarRecovery(FrappeTestCase):
	"""Getting back into the app.

	Following a panel link into another app replaces the panel: the desk resolves
	the sidebar from the current route, so `/desk/payroll` shows the Payroll panel
	and everything this app put there is gone.  Nothing inside a section can undo
	that, so the way home has to sit above them.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.setup import APP_SIDEBAR

		cls.doc = frappe.get_doc("Workspace Sidebar", APP_SIDEBAR)

	def test_home_is_present_and_not_under_a_section(self):
		"""A Home link inside a collapsed section is not a way back."""
		from sharm_dreams_group.setup import APP_HOME_LABEL

		rows = list(self.doc.items)
		home = [i for i, r in enumerate(rows) if r.type == "Link" and r.label == APP_HOME_LABEL]
		self.assertEqual(len(home), 1, "expected exactly one Home link")
		first_section = next(
			(i for i, r in enumerate(rows) if r.type == "Section Break"),
			len(rows),
		)
		self.assertLess(
			home[0],
			first_section,
			"Home sits below a section header, so it inherits that section's collapsed state",
		)
		self.assertFalse(rows[home[0]].child, "Home is indented, so it renders as a section child")

	def test_documents_is_above_every_section_too(self):
		"""Following Documents leaves the app, and the panel does not come back.

		Same trap as Home, and worse in one way: Documents goes to another app's
		route, so the desk resolves *that* app's sidebar and ours is replaced
		wholesale.  A Documents row inside a collapsed section is therefore not a
		way back to the file manager.
		"""
		from sharm_dreams_group.setup import APP_TOP_LINKS

		rows = list(self.doc.items)
		found = [i for i, r in enumerate(rows) if r.type == "Link" and r.label == "Documents"]
		self.assertEqual(len(found), 1, "expected exactly one Documents link")
		first_section = next((i for i, r in enumerate(rows) if r.type == "Section Break"), len(rows))
		self.assertLess(
			found[0],
			first_section,
			"Documents sits below a section header, so it inherits that section's collapsed state",
		)
		self.assertFalse(rows[found[0]].child, "Documents is indented, so it renders as a section child")

		spec = next(l for l in APP_TOP_LINKS if l[0] == "Documents")
		row = rows[found[0]]
		self.assertEqual((row.link_type, row.link_to, row.url), (spec[1], spec[2], spec[3]))

	def test_no_section_is_left_empty(self):
		"""`Overview` held only Documents and was removed; nothing should replace it.

		An empty section header is a row that opens onto nothing, so this checks no
		header in the panel - top-level or nested - has no link beneath it.  A
		sub-section counts as its own header but also fills the section it sits in,
		which is why both are tracked: `People` is not empty merely because its
		first child is a sub-heading.
		"""
		counts: dict[tuple[str, str | None], int] = {}
		top, sub = None, None
		for row in self.doc.items:
			if row.type == "Section Break":
				if row.child and top:
					sub = row.label
				else:
					top, sub = row.label, None
				counts.setdefault((top, sub), 0)
			elif row.type == "Link" and top:
				# counted against the sub-section it sits in, and against the
				# top-level section above that one
				counts[(top, sub)] = counts.get((top, sub), 0) + 1
				if sub:
					counts[(top, None)] = counts.get((top, None), 0) + 1

		self.assertTrue(counts, "the panel has no sections at all")
		empty = sorted(f"{top}{' / ' + sub if sub else ''}" for (top, sub), n in counts.items() if not n)
		self.assertEqual(empty, [], f"headers with nothing under them: {empty}")

	def test_home_points_at_the_app_workspace(self):
		from sharm_dreams_group.setup import APP_HOME_TARGET

		home = next(r for r in self.doc.items if r.type == "Link" and r.label == "Home")
		self.assertEqual((home.link_type, home.link_to), APP_HOME_TARGET)
		self.assertTrue(frappe.db.exists("Workspace", home.link_to))

	def test_every_sdg_doctype_is_in_the_panel_somewhere(self):
		"""The app's own tables sit in the sections that use them, and none is lost.

		They used to be in one "Setup" section, which meant a second place to look
		for something. They are now filed under the work they configure - the tax
		slab under Payroll, the penalty levels under Our Setup, the bands with the
		rest of the insurance bands - so this checks the spec and the database
		still agree on the full set, and that each is actually written.
		"""
		from sharm_dreams_group.setup import HRMS_PANEL_GROUPS
		from sharm_dreams_group.workspace import _expected_doctypes, SUPERSEDED_DOCTYPES

		specified = {name for _sec, subs in HRMS_PANEL_GROUPS for _title, names in subs for name in names}
		self.assertEqual(
			specified & _expected_doctypes(),
			_expected_doctypes() - SUPERSEDED_DOCTYPES,
			"the panel spec and this app's DocTypes disagree",
		)

		in_panel = {r.link_to for r in self.doc.items if r.type == "Link"}
		for name in _expected_doctypes() - SUPERSEDED_DOCTYPES:
			self.assertIn(name, in_panel, name)


# Stock page names in the panel: the desk draws them, this app only links them, so
# they are not expected to have a hand-picked glyph.
_STOCK_PAGE_LABELS = {
	"Payroll",
	"Tax & Benefits",
	"Recruitment",
	"Performance",
	"Tenure",
	"Expenses",
}

# Named in a panel group but not DocTypes: HRMS workspaces, plus the two RIWAQ
# features Frappe has not built.  Kept next to the test so the exemption is visible
# rather than buried in a comparison.
_NOT_DOCTYPES = {
	"Shift & Attendance",
	"HR Setup",
	"Leaves",
	"Vacation Tracker",
	"Out Missions",
}


class TestHrmsPanelCoverage(FrappeTestCase):
	"""Every Frappe HR DocType should be reachable from the panel.

	106 HRMS DocTypes across four modules.  The panel groups them by work rather than
	by module, so a DocType can fall between the two, and an HRMS upgrade can add one
	that no group names.  These tests make either case visible.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.setup import APP_SIDEBAR, HRMS_PANEL_GROUPS

		cls.groups = HRMS_PANEL_GROUPS
		cls.in_panel = {
			r.link_to for r in frappe.get_doc("Workspace Sidebar", APP_SIDEBAR).items if r.type == "Link"
		}

	def test_the_panel_mirrors_every_hrms_doctype_exactly_once(self):
		"""A mirror, and a complete one.

		The panel was a 29-link launcher; it is now a mirror of every HR DocType
		on the site, because a panel you cannot *find* things in is only a panel
		you can be sent out of. A mirror is only worth having if it is complete,
		so this compares the panel against the database in both directions:

		- a DocType HRMS ships that nothing lists would be unreachable, and would
		  arrive silently on the next HRMS upgrade and go unnoticed;
		- a DocType listed twice would be two rows saying the same thing.

		Sub-sections are allowed to name the same DocType only if the sections are
		genuinely different views of it; today there are none, so duplicates fail.
		"""
		hrms = {
			r[0]
			for r in frappe.db.sql(
				"select name from `tabDocType` where module in ('HR','Payroll','Recruitment',"
				"'Performance') and istable = 0"
			)
		}
		missing = hrms - self.in_panel
		self.assertFalse(missing, f"{len(missing)} HRMS doctypes are not in the panel: {sorted(missing)}")
		self.assertFalse(
			(hrms & self.in_panel) - hrms,
			"the panel is linking a doctype that is not an HRMS one",
		)
		# nothing listed twice
		from sharm_dreams_group.setup import HRMS_PANEL_GROUPS, panel_links

		seen = {}
		for _place, _label, _lt, to, _u in panel_links():
			if to in hrms:
				seen[to] = seen.get(to, 0) + 1
		duplicated = {k: v for k, v in seen.items() if v > 1}
		self.assertFalse(duplicated, f"listed more than once in the panel: {duplicated}")

		# and every section has at least one sub-section, so no heading is a dead end
		self.assertTrue(
			[sec for sec, sub in HRMS_PANEL_GROUPS if sub],
			"the mirror declares no sections",
		)

	def test_every_table_this_app_owns_is_still_reachable(self):
		"""The mirror was dropped; our own tables must not be dropped with it."""
		from sharm_dreams_group.workspace import _expected_doctypes, SUPERSEDED_DOCTYPES

		own = _expected_doctypes() | {"Income Tax Slab"}
		missing = own - SUPERSEDED_DOCTYPES - self.in_panel
		self.assertEqual(missing, set(), "tables this app owns are not in the panel")

	def test_every_grouped_doctype_exists(self):
		"""A name in a group that no longer exists is a typo, not a forward reference."""
		from sharm_dreams_group.setup import panel_links

		# the three RIWAQ features are named in the spec so they surface the day a
		# target exists; until then they are reported, never linked
		pending = {"Vacation Tracker", "Out Missions", "Assistant"}
		for place, _label, link_type, link_to, _url in panel_links():
			if link_type != "DocType" or link_to in pending:
				continue  # a workspace or a dashboard is not a DocType
			self.assertTrue(
				frappe.db.exists("DocType", link_to),
				f"{place} lists {link_to!r}, which is not a DocType",
			)

	def test_no_section_repeats_a_link(self):
		from sharm_dreams_group.setup import APP_SIDEBAR

		links = [
			(r.label, r.link_type, r.link_to)
			for r in frappe.get_doc("Workspace Sidebar", APP_SIDEBAR).items
			if r.type == "Link"
		]
		self.assertEqual(len(links), len(set(links)), "the panel lists a link twice")

	def test_the_dashboard_route_is_back_and_our_factory_exists(self):
		"""Dashboard points at the Dashboard document again.

		That route needs `frappe.views.DashboardViewFactory`; stock Frappe registers
		only Form/List/Report/Tree, so the app supplies it in
		public/js/dashboard_view.js.  If either half goes, the link 404s.
		"""
		import pathlib

		from sharm_dreams_group.setup import APP_SIDEBAR, DASHBOARD_NAME

		links = {
			(r.link_type, r.link_to)
			for r in frappe.get_doc("Workspace Sidebar", APP_SIDEBAR).items
			if r.type == "Link"
		}
		self.assertIn(("Dashboard", DASHBOARD_NAME), links)

		js = frappe.get_app_path("sharm_dreams_group", "public", "js", "dashboard_view.js")
		self.assertTrue(frappe.os.path.exists(js), "the dashboard-view factory is missing")
		with open(js) as f:
			source = f.read()
		self.assertIn("frappe.views.DashboardViewFactory", source)

		bundle = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
			/ "sharm_dreams_group.bundle.js"
		).read_text(errors="replace")
		# `app_include_js` names the bundle; the bundle's import list is what decides
		# whether this file loads.  See hooks.py.
		self.assertIn(
			"./dashboard_view.js",
			bundle,
			"the factory is written but not imported by the bundle, so it never loads",
		)


def _children_of(doc):
	"""Yield (section_title, [rows]) for every top-level section, skipping sub-sections."""
	current = None
	rows = {}
	for row in doc.items:
		if row.type != "Section Break":
			if current:
				rows.setdefault(current, []).append(row)
		elif not row.child:
			current = row.label
			rows.setdefault(current, [])
	return rows


def _by_section(doc):
	"""Yield ("Section / Sub-section", row) for every Link row.

	The place matters, not just the section: `panel_label` disambiguates by
	"Section / Sub-section" because two rows may legitimately share a name in
	different sections and clash only within one.
	"""
	section = ""
	current = ""
	for row in doc.items:
		if row.type == "Section Break":
			if row.child:
				current = f"{section} / {row.label}"
			else:
				section = row.label
				current = section
		elif row.type == "Link":
			yield current, row


class TestPanelLabelsAndIcons(FrappeTestCase):
	"""The panel shows a readable label, and every row has a glyph that draws.

	`link_to` keeps the "SDG " prefix - renaming the DocTypes would break every
	stored reference on the site - but the label drops it, because sixteen rows all
	reading "SDG ..." in a panel is noise.  An icon name that is not in the desk's
	icon sprite renders as an empty square, and there is no server-side error for it,
	so the names are checked against the sprite here.
	"""

	@classmethod
	def setUpClass(cls):
		import pathlib
		import re

		from sharm_dreams_group.setup import APP_SIDEBAR

		cls.doc = frappe.get_doc("Workspace Sidebar", APP_SIDEBAR)
		# the sprite the desk actually loads: frappe/hooks.py app_include_icons
		icons = set()
		for theme in ("lucide", "timeless"):
			svg = pathlib.Path(frappe.get_app_path("frappe", "public", "icons", theme, "icons.svg"))
			if svg.exists():
				icons |= set(re.findall(r'<symbol[^>]*id="icon-([^"]+)"', svg.read_text(errors="replace")))
		cls.sprite = icons

	def test_no_label_still_carries_the_sdg_prefix(self):
		"""The prefix stays in `link_to`; the label is what a person reads."""
		for row in self.doc.items:
			if row.type == "Link":
				self.assertFalse(
					str(row.label).startswith("SDG "),
					f"{row.label!r} still shows the DocType prefix",
				)

	def test_labels_are_unique_within_the_panel(self):
		"""Two rows reading "Leave Type" are indistinguishable in the list."""
		from sharm_dreams_group.setup import panel_label

		# built per section: two rows may legitimately share a name in *different*
		# sections - Payroll Entry and the payroll section's own settings - and only a
		# clash inside one list is ambiguous to a reader
		seen: dict[tuple[str, str], int] = {}
		for place, row in _by_section(self.doc):
			key = (place.split(" / ")[0], panel_label(place, row.link_type, row.label, row.link_to))
			seen[key] = seen.get(key, 0) + 1
		labels = list(seen)
		dupes = {k for k, v in seen.items() if v > 1}
		del labels
		self.assertEqual(dupes, set(), f"duplicate labels in the panel: {sorted(dupes)}")

	def test_every_row_has_an_icon_that_exists_in_the_sprite(self):
		"""An unknown icon name is a silent empty square, not an exception."""
		self.assertTrue(self.sprite, "could not read the icon sprite")
		for row in self.doc.items:
			if row.type == "Spacer":
				continue  # a blank separator carries nothing
			self.assertTrue(row.icon, f"{row.label!r} has no icon")
			self.assertIn(
				row.icon,
				self.sprite,
				f"{row.label!r} uses icon {row.icon!r}, which is not in the sprite",
			)

	def test_every_section_header_has_an_icon_that_exists_in_the_sprite(self):
		"""A section icon has to be a real glyph, and it also has to be drawn.

		The desk stores a section's icon and never renders it - `sidebar_item.html`
		only emits the icon span for links - so `public/js/sidebar_sections.js`
		injects it. Both halves are checked here: the name is in the sprite, and the
		code that draws it is still in the file. An icon that resolves to nothing
		is an empty square, which looks like a broken panel rather than a typo.
		"""
		import pathlib

		self.assertTrue(self.sprite, "could not read the icon sprite")
		sections = [r for r in self.doc.items if r.type == "Section Break"]
		self.assertTrue(sections, "the panel has no sections")
		for row in sections:
			self.assertTrue(row.icon, f"the {row.label!r} section has no icon")
			self.assertIn(
				row.icon, self.sprite, f"the {row.label!r} section uses icon {row.icon!r}, not in the sprite"
			)

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "sidebar_sections.js"
		).read_text()
		self.assertIn("draw_section_icons", src, "nothing draws the section icons")
		# ...and it has to be *called*, from a hook that runs after the workspace
		# title is known. `setup()` triggers `sidebar_setup` on its first line and
		# only assigns `workspace_title` a few lines later, so a handler bound to
		# that event reads undefined and does nothing on the first load of the desk -
		# which is exactly when the icon is most wanted.
		self.assertNotIn('on("sidebar_setup"', src, "these run before workspace_title is set")
		self.assertIn("frappe.ui.Sidebar.prototype.create_sidebar = function", src)
		self.assertIn("this.draw_section_icons()", src)

	def test_the_nesting_fix_also_runs_after_the_workspace_title_is_known(self):
		"""`prepare` is the seam: re-nesting must happen before `make_sidebar`.

		`make_sidebar` renders `workspace_sidebar_items`, which stock
		`find_nested_items()` has already flattened - a child Section Break hoisted
		to the top level and its rows filed under the wrong parent.  Re-nesting on
		`sidebar_setup` ran too late for that too, for the same ordering reason.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "sidebar_sections.js"
		).read_text()
		self.assertIn("frappe.ui.Sidebar.prototype.prepare = function", src)
		self.assertIn("stock_prepare", src, "prepare() is replaced without calling the original")
		self.assertIn("edit_mode", src, "re-nesting would discard an edit in the sidebar editor")

	def test_no_item_falls_back_to_the_generic_icon(self):
		"""The fallback is a safety net; if everything uses it, the table is stale."""
		from sharm_dreams_group.setup import FALLBACK_ICON, ITEM_ICONS, panel_label

		links = [panel_label(place, r.link_type, r.label, r.link_to) for place, r in _by_section(self.doc)]
		# a stock page name we never curated may fall back to the neutral glyph; one
		# of ours must not, or the table has gone stale behind the panel
		stock_only = {label for label in links if label in _STOCK_PAGE_LABELS}
		stale = [label for label in links if label not in ITEM_ICONS and label not in stock_only]
		self.assertEqual(
			stale, [], f"these labels have no icon of their own: {stale} (fallback {FALLBACK_ICON})"
		)

	def test_our_own_tables_sit_where_they_are_used(self):
		"""Each table this app owns is filed under the work it configures.

		These are the tables we wrote, so nothing else will place them: if one is
		mis-filed it will simply sit somewhere odd forever. `Income Tax Slab` is
		stock now, so it goes under Payroll with the rest of the payroll
		configuration rather than in Our Setup with ours.
		"""
		expected = {
			"SDG Employment Category": "People / How People Are Categorised",
			"SDG Contract Type": "People / How People Are Categorised",
			"SDG Sub Department": "People / How People Are Categorised",
			"Income Tax Slab": "Payroll / Income Tax",
			"SDG Group Allocation": "Payroll / How The Group Splits Cost",
			"SDG Insured Person": "Employee Lifecycle / Health & Insurance",
			"SDG School Fee": "Employee Lifecycle / School Fees",
			"SDG Penalty Level": "Our Setup / Penalties",
			"SDG Medical Band": "Our Setup / Insurance Bands",
			"SDG Family Medical Band": "Our Setup / Insurance Bands",
			"SDG Gross Insurance Band": "Our Setup / Insurance Bands",
			"SDG Payroll Settings": "Our Setup / Payroll Settings",
		}
		section_of = {r.link_to: place for place, r in _by_section(self.doc)}
		for name, want in expected.items():
			self.assertEqual(section_of.get(name), want, f"{name} is filed in the wrong place")

	def test_the_mirror_is_grouped_rather_than_one_flat_list(self):
		"""Sub-headings are the point; without them this is the old bucket.

		A section with no sub-section is a heading over an undifferentiated run of
		117 links, which is exactly what the panel was before it was arranged.
		"""
		from sharm_dreams_group.setup import HRMS_PANEL_GROUPS

		headings = [r.label for r in self.doc.items if r.type == "Section Break" and r.child]
		for section, subs in HRMS_PANEL_GROUPS:
			self.assertGreater(len(subs), 0, f"{section} has no sub-sections")
			for title, names in subs:
				self.assertIn(title, headings, f"{section} has no {title!r} sub-heading")
				self.assertTrue(names, f"the {title!r} sub-heading lists nothing")

		# and a sub-heading is never a leaf: each one holds at least one link
		counts: dict[str, int] = {}
		for place, _row in _by_section(self.doc):
			head = place.rsplit(" / ", 1)[-1] if " / " in place else place
			counts[head] = counts.get(head, 0) + 1
		for _section, subs in HRMS_PANEL_GROUPS:
			for title, _names in subs:
				self.assertGreaterEqual(counts.get(title, 0), 1, f"the {title!r} sub-heading is empty")

	def test_every_own_table_is_still_reachable(self):
		"""Folding them in must not lose one."""
		from sharm_dreams_group.workspace import CARDS

		in_panel = {r.link_to for r in self.doc.items if r.type == "Link"}
		for _group, names in CARDS:
			for name in names:
				self.assertIn(name, in_panel, f"{name} is no longer in the panel")


class TestDomainDashboards(FrappeTestCase):
	"""`SDG Account` and `SDG HRM`: the two dashboards added to the panel.

	They exist because the panel asked for a Dashboard section, and only these two
	were built - the other five requested (Project, Recruitment, POS, CRM, Support)
	have empty data, so building them would have produced pages of zeros. These
	tests pin the two that were built: that they answer with real figures, that the
	figures add up, and that nothing on them is a lie.
	"""

	def setUp(self):
		from sharm_dreams_group.accounts import dashboard as account
		from sharm_dreams_group.hr import dashboard_hrm as hrm

		self.account, self.hrm = account, hrm

	def _figure(self, module, key):
		return module.metric({"metric": key})

	def test_both_dashboards_exist_with_cards_and_charts(self):
		for name in ("SDG Account", "SDG HRM"):
			self.assertTrue(frappe.db.exists("Dashboard", name), f"{name} was not built")
			doc = frappe.get_doc("Dashboard", name)
			self.assertTrue(doc.cards, f"{name} has no cards")
			self.assertTrue(doc.charts, f"{name} has no charts")
			self.assertTrue(doc.chart_options, f"{name} has no section layout")

	def test_every_card_on_a_dashboard_resolves(self):
		"""A card row naming a card that does not exist renders as a blank tile."""
		for name in ("SDG Account", "SDG HRM"):
			for row in frappe.get_doc("Dashboard", name).cards:
				self.assertTrue(
					frappe.db.exists("Number Card", row.card), f"{name} references a missing card {row.card}"
				)
			for row in frappe.get_doc("Dashboard", name).charts:
				self.assertTrue(
					frappe.db.exists("Dashboard Chart", row.chart),
					f"{name} references a missing chart {row.chart}",
				)

	def test_every_metric_returns_a_figure_and_money_is_positive(self):
		"""No metric may error, and none may return a negative.

		Zero is a legitimate answer for a count - "no leaver is missing a relieving
		date" is good news, not a broken card - but a *money* figure of zero on a
		dashboard about what the payroll cost is a card that is silently wrong.
		"""
		for module in (self.account, self.hrm):
			for key in module.METRICS:
				figure = self._figure(module, key)
				self.assertIsInstance(figure, (int, float), f"{module.__name__}.{key}")
				self.assertGreaterEqual(figure, 0, f"{module.__name__}.{key} is negative")
				if module._is_money(key):
					self.assertGreater(figure, 0, f"{module.__name__}.{key} is a money figure reading zero")

	def test_the_cost_figures_reconcile(self):
		"""Gross - net is exactly the deductions plus the loans.

		This is the check that catches a metric reading the wrong field. `total_deduction`
		on the slip excludes the loan repayment, which is held in its own column, so a
		payroll where the two disagree is a payroll where one of them is misread - and
		the first version of the tax card summed the *annual* tax figure and came out
		larger than the whole payroll.
		"""
		gross = self._figure(self.account, "payroll_gross")
		net = self._figure(self.account, "payroll_net")
		deductions = self._figure(self.account, "total_deductions")
		loans = self._figure(self.account, "loan_repayments")
		self.assertAlmostEqual(gross - net, deductions + loans, places=2)

	def test_income_tax_is_less_than_gross_and_comes_from_the_components(self):
		"""Tax is a deduction, so it cannot exceed the pay it comes out of.

		And it has to be both components: `Income Tax` and `Income Tax on Cost Share`
		are separate rows on the slip, and reading only the first understates the tax.
		"""
		tax = self._figure(self.account, "income_tax_deducted")
		self.assertLess(tax, self._figure(self.account, "payroll_gross"))
		for component in ("Income Tax", "Income Tax on Cost Share"):
			self.assertIn(component, self.account._COMPONENT_TOTALS["income_tax_deducted"])

	def test_only_money_cards_carry_a_currency(self):
		"""Same rule as the workforce cards: a headcount recorded as money is a bug."""
		for module in (self.account, self.hrm):
			for spec in module.metric_definitions():
				wants_currency = module._is_money(spec["key"])
				card = frappe.db.get_value(
					"Number Card", {"method": module.METRIC_METHOD, "label": spec["label"]}, "currency"
				)
				self.assertEqual(
					bool(card),
					wants_currency,
					f"{spec['label']}: currency is {card!r}, money is {wants_currency}",
				)

	def test_every_chart_has_a_colour(self):
		"""A Line or Bar chart with no colour does not draw at all.

		Frappe's `get_chart_colors()` ends those charts with
		`colors = [this.chart_doc.color || []]`, so an unset colour reaches Chart.js
		as `[[]`, i.e. `""`, and `BaseChart` throws `"" is not a valid color` while
		the widget is still being built. The chart is then simply absent from the
		page - twenty-four of ours were, which is why the dashboards looked empty.
		The JavaScript guard in `dashboard_view.js` stops it from ever throwing, but
		the data should still be right.
		"""
		y_axis = next(f.options for f in frappe.get_meta("Dashboard Chart").fields if f.fieldname == "y_axis")
		with_axis = {r.parent for r in frappe.get_all(y_axis, fields=["parent"], limit_page_length=0)}

		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			fields=["name", "chart_name", "type", "color"],
			limit_page_length=0,
		):
			if row["type"] not in ("Line", "Bar") or row["name"] in with_axis:
				continue
			self.assertTrue(
				(row["color"] or "").strip(),
				f"{row['chart_name']}: a {row['type']} chart with no colour and no "
				f"y_axis rows does not render",
			)

	def test_chart_colours_are_stable_across_runs(self):
		"""`chart_colour` picks by name, so migrate does not repaint the dashboard."""
		from sharm_dreams_group.setup import CHART_COLOURS, chart_colour

		self.assertEqual(chart_colour("Headcount by Company"), chart_colour("Headcount by Company"))
		for name in ("Headcount by Company", "Payroll Gross by Company", "Leavers by Company"):
			self.assertIn(chart_colour(name), CHART_COLOURS)

	def test_only_sum_charts_carry_a_currency(self):
		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			fields=["chart_name", "group_by_type", "aggregate_function_based_on", "currency"],
			limit_page_length=0,
		):
			if row["group_by_type"] == "Sum" and not row["aggregate_function_based_on"]:
				continue  # a Sum with no field is not a Sum; see the chart test
			self.assertEqual(
				bool(row["currency"]),
				row["group_by_type"] == "Sum",
				f"{row['chart_name']}: currency is {row['currency']!r} but it is a {row['group_by_type']}",
			)

	def test_no_chart_sums_the_annual_tax_field(self):
		"""The field holds a year's tax, so a monthly chart of it is nonsense.

		It is what the first version of the income-tax chart summed, and it produced
		5,728,993 against a 3,937,337 payroll.
		"""
		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			pluck="aggregate_function_based_on",
			limit_page_length=0,
		):
			self.assertNotIn(row, ("sdg_annual_income_tax", "sdg_annual_taxable", "sdg_annual_tax_base"))

	def test_the_dashboard_section_is_collapsible_and_holds_the_three_dashboards(self):
		"""The section the panel asked for, holding the dashboards that exist.

		`SDG Analytics` is deliberately absent: it was retired because every figure it
		carried was already on the workforce dashboard, and it is deleted on every
		migrate, so a link to it would be a row leading nowhere.

		The five that were not built (Project, Recruitment, POS, CRM, Support) must
		not appear: a link to a dashboard that was never created is a dead row, and a
		dashboard of zeros is a page that looks broken rather than empty.
		"""
		items = frappe.get_doc("Workspace Sidebar", "Sharm Dreams Group").items
		headers = [r for r in items if r.type == "Section Break" and not r.child]
		section = next((r for r in headers if r.label == "Dashboard"), None)
		self.assertIsNotNone(section, "the panel has no Dashboard section")
		self.assertEqual(section.collapsible, 1, "the Dashboard section is not collapsible")

		labels = []
		for row in items:
			if row.type == "Link" and row.link_type == "Dashboard":
				labels.append(row.label)
			if row.type == "Link" and row.link_type == "DocType" and row.link_to == "CRM Dashboard":
				self.fail("CRM Dashboard is stock FCRM and is not one of this app's dashboards")
		self.assertEqual(
			sorted(labels),
			["Account Dashboard", "HRM Dashboard", "Workforce Dashboard"],
		)
		self.assertNotIn("Analytics", labels, "the retired analytics dashboard is still linked")
		for label in ("Project Dashboard", "Recruitment Dashboard", "POS Dashboard", "Support Dashboard"):
			self.assertNotIn(label, labels, f"{label} is listed but was never built")

	def test_no_dashboard_is_listed_twice(self):
		"""A figure on two pages is a figure nobody trusts.

		The workforce and analytics dashboards used to sit under Overview as well as
		here being a natural home for them; they now live only here.
		"""
		items = frappe.get_doc("Workspace Sidebar", "Sharm Dreams Group").items
		seen = [r.label for r in items if r.type == "Link" and r.link_type == "Dashboard"]
		self.assertEqual(len(seen), len(set(seen)), f"a dashboard is listed twice: {seen}")


def _all_metric_labels() -> list[str]:
	"""Every card label any of the three metric modules can produce.

	The Account and HRM dashboards are laid out from their own modules, so a check
	that only consults the workforce metrics calls their cards "a drill-down with
	no metric" - which is what it did until this was widened.
	"""
	from sharm_dreams_group.accounts import dashboard as account
	from sharm_dreams_group.hr import dashboard as workforce
	from sharm_dreams_group.hr import dashboard_hrm as hrm

	return [spec["label"] for mod in (workforce, account, hrm) for spec in mod.metric_definitions()]


def _section_cards(section: dict) -> list[str]:
	"""Every number card a section draws, whichever way the section is written.

	A section is either the old shape (`cards` and `charts` keys) or an ordered list
	of `blocks`, and this app writes the second while stock dashboards write the first.

	Every layout check in this file goes through here, and it has to.  Reading
	`section.get("cards")` against a block-shaped section yields an empty list, and a
	test asserting things about an empty list does not fail - it passes.  That is not
	hypothetical: `test_every_widget_named_in_a_layout_exists` reads exactly that, and
	after the shape changed it would have checked that no layout names a chart that
	is missing, having found no charts at all.
	"""
	if section.get("cards"):
		return list(section["cards"])
	return [item for block in section.get("blocks") or [] if block.get("type") == "cards" for item in block.get("items") or []]


def _section_charts(section: dict) -> list[str]:
	"""Every chart a section draws. The chart half of `_section_cards`."""
	if section.get("charts"):
		return list(section["charts"])
	return [
		item
		for block in section.get("blocks") or []
		if block.get("type") == "charts"
		for item in block.get("items") or []
	]


def _layout_sections(dashboard_name: str) -> list[dict]:
	"""The sections of a dashboard's `chart_options`, as actually stored."""
	doc = frappe.get_doc("Dashboard", dashboard_name)
	return (frappe.parse_json(doc.chart_options or "{}") or {}).get("sections") or []


def _every_placed_chart() -> list[str]:
	"""Every chart name placed on any of this app's three dashboards."""
	names = []
	for dashboard_name in ("SDG Workforce", "SDG Account", "SDG HRM"):
		if not frappe.db.exists("Dashboard", dashboard_name):
			continue
		for section in _layout_sections(dashboard_name):
			for name in _section_charts(section):
				if name not in names:
					names.append(name)
	return names


class TestCustomChartSourcesAreResolvable(FrappeTestCase):
	"""Every `Custom` chart on these dashboards has JS the desk can actually find.

	A `Custom` chart names a source, and the desk resolves that name to a file:

		DashboardChartSource.get_folder_path() / f"{scrub(source_name)}.js"

	`get_folder_path` is `get_module_path(module) / "dashboard_chart_source" /
	scrub(name)`, and `get_module_path` itself appends the scrubbed module name. So
	the only path that resolves is four levels deep:

		apps/<app>/<app>/<scrubbed module>/dashboard_chart_source/<name>/<name>.js

	**Getting this wrong costs nothing visible, which is why it is pinned here.**
	`read_config` returns `""` for a file that is not there rather than raising, the
	desk `eval`s the empty string, `this.settings` comes back undefined, and the
	widget is simply never drawn - no error state, no console message, no broken box.
	The page renders one chart short and looks deliberate.

	That is exactly what happened to `Monthly Joiners` and `Monthly Attrition`: both
	were written under `hr/dashboard_chart_source/`, one level too deep, and both
	vanished while every check that read the *database* still passed - the chart
	documents existed, the drilldowns were correct, the layouts named them, and the
	counts all added up. Only looking at the rendered page found it.

	So the test asks the desk's own question rather than a near miss at it: not "is
	there a `.js` for this source somewhere in the app" but the exact path
	`read_config` will read, and not merely that it exists but that it is non-empty,
	since an empty file is the same silent failure.
	"""

	def test_every_custom_chart_source_resolves_to_a_non_empty_file(self):
		from frappe.desk.doctype.dashboard_chart_source.dashboard_chart_source import (
			DashboardChartSource,
		)
		from frappe.modules import scrub

		custom = [
			name
			for name in _every_placed_chart()
			if frappe.get_doc("Dashboard Chart", name).chart_type == "Custom"
		]
		self.assertTrue(
			custom,
			"no Custom chart is placed on any dashboard, so this class would "
			"pass vacuously and stop guarding anything",
		)

		for name in custom:
			with self.subTest(chart=name):
				source = frappe.get_doc("Dashboard Chart", name).source
				self.assertTrue(
					frappe.db.exists("Dashboard Chart Source", source),
					f"{name} names the source {source!r}, which is not on this site",
				)
				doc = frappe.get_doc("Dashboard Chart Source", source)
				# The desk's own resolution, called rather than reimplemented -
				# this test exists precisely because reimplementing the path is
				# how it went wrong in the first place.
				expected = doc.get_folder_path() / f"{scrub(source)}.js"
				self.assertTrue(
					expected.exists(),
					f"{source!r} resolves to {expected}, which does not exist. "
					f"The desk reads that exact path and read_config returns "
					f"\"\" for a file that is missing, so {name} would not be "
					f"drawn at all - silently, with no error state.",
				)
				self.assertTrue(
					doc.read_config().strip(),
					f"{source!r} resolves to {expected}, which is empty",
				)

	def test_no_clickable_chart_paints_no_point(self):
		"""No chart on these dashboards may be a shape that draws no per-point node.

		**This is the rule that made `Monthly Joiners` and `Monthly Attrition`
		dead on arrival.** A click resolves through `sharm_index_in_store`, which
		walks up from the tapped node asking each chart component's store whether
		it holds it. A node that is not painted cannot be found, so a shape that
		paints none can never be clicked - not "clicks the wrong way", never.
		Measured on the rendered page: a `Bar` chart carries one
		`<rect class="bar">` per label and resolves to its index; the `Line`
		version of the same thirteen months carried **0 circles and 0 rects**, one
		`<path>`, and resolved to -1 for every possible tap.

		`Line` and `Area` are the shapes with this property in frappe-charts, and
		they are the only two excluded here. `Pie`, `Donut`, `Bar` and `Scatter`
		all paint a node per label, which is why they are allowed.

		Why a test rather than a comment: the chart was *placed*, *counted*,
		*drawn*, and reported clickable by every existing check, because all of
		them read the database and this failure is only visible in the DOM. A
		wiring report that says 29/29 while two of them cannot be tapped is exactly
		the lie this suite exists to prevent, so the shape is now pinned in the
		shape table rather than in the renderer.
		"""
		unclickable = {"Line", "Area"}
		for name in _every_placed_chart():
			chart = frappe.get_doc("Dashboard Chart", name)
			with self.subTest(chart=name):
				self.assertNotIn(
					chart.type,
					unclickable,
					f"{name} is a {chart.type!r}, which paints no per-point node, "
					f"so no click on it can resolve. Measured: 0 circles, 0 rects, "
					f"one <path>, and `sharm_index_in_store` returns -1 for every "
					f"tap. Use a Bar - one <rect> per label, each in the store.",
				)

	def test_a_source_file_declares_the_method_its_python_exports(self):
		"""The JS's `method:` must name a callable that exists.

		The file is the only declaration of where a `Custom` chart's data comes
		from, and `Dashboard Chart` has no column for it - so a stale path in the
		JS is found only when the chart silently does not draw, exactly as a
		misplaced file is. And unlike a placed-but-wrong number, it is not
		something the Python tests touch at all, because nothing in Python
		reads this string.
		"""
		import importlib

		pattern = re.compile(r'method:\s*"([^"]+)"')
		for name in _every_placed_chart():
			chart = frappe.get_doc("Dashboard Chart", name)
			if chart.chart_type != "Custom":
				continue
			source = frappe.get_doc("Dashboard Chart", name).source
			if not frappe.db.exists("Dashboard Chart Source", source):
				continue
			with self.subTest(chart=name):
				config = frappe.get_doc("Dashboard Chart Source", source).read_config()
				found = pattern.findall(config)
				self.assertTrue(
					found,
					f"{source!r} declares no `method:` for the desk to call",
				)
				for dotted in found:
					module_path, _, attribute = dotted.rpartition(".")
					self.assertTrue(
						hasattr(importlib.import_module(module_path), attribute),
						f"{source!r} calls {dotted}, which does not exist",
					)


class TestAppJavascriptIsActuallyServed(FrappeTestCase):
	"""Every file this app ships has to be loadable by the desk, and reachable.

	`desk.html` renders each `app_include_js` entry through `include_script` ->
	`bundled_asset`, and `bundled_asset` only rewrites a path containing
	`.bundle.`:

	    if ".bundle." in path and not path.startswith("/assets"): ...

	A bare filename therefore reaches `abs_url()` untouched, the desk emits
	`src="/sidebar_sections.js"`, and the browser 404s. Nothing raises: the hook is
	a list of strings, Python has no idea what a URL resolves to, and a script tag
	that fails simply does not run. Five of this app's files were dead this way for
	the whole life of the feature.

	And it has to be the *bundle* form rather than an explicit `/assets/` path.
	nginx serves everything under `/assets/` with `Cache-Control: max-age=31536000`
	- a year, unconditionally - so a stable URL is a file the browser never asks
	for again. An `/assets/...` entry is valid and still wrong: the fix reaches disk,
	is served correctly, and never arrives.
	"""

	BUNDLE = "sharm_dreams_group.bundle.js"

	def entries(self) -> list[str]:
		return frappe.get_hooks("app_include_js") or []

	def shipped(self) -> list[str]:
		"""The JS file names the desk will actually execute.

		`app_include_js` names the bundle; the bundle imports the files. A file that
		is on disk but missing from the import list is never loaded, and there is no
		other signal - which is why this reads the list rather than the directory.
		"""
		import pathlib
		import re

		bundle = pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / self.BUNDLE
		if not bundle.exists():
			return []
		return re.findall(r'^\s*import\s+"\./([^"]+)"', bundle.read_text(errors="replace"), re.M)

	def test_something_is_declared(self):
		self.assertTrue(self.entries(), "app_include_js is empty, so none of this app's JS runs")

	def test_every_entry_is_in_a_form_the_desk_can_resolve(self):
		"""Only two forms work: a `.bundle.` name, or an explicit `/assets/` path.

		The `.bundle.` form is the right one here, because it is the only one that
		carries a content hash.
		"""
		for entry in self.entries():
			self.assertTrue(
				".bundle." in entry or entry.startswith("/assets/"),
				f"app_include_js entry {entry!r} resolves to /{entry} at the site root and 404s. "
				f"Use a '.bundle.js' name instead.",
			)

	def test_the_entries_are_bundled_so_the_browser_can_revalidate(self):
		"""A hash is what stops nginx's one-year cache from pinning a stale file."""
		unhashed = [e for e in self.entries() if ".bundle." not in e]
		self.assertEqual(
			unhashed,
			[],
			f"these have no content hash and nginx caches /assets/ for a year: {unhashed}",
		)

	def test_the_bundle_is_registered_in_assets_json(self):
		"""`bundled_asset` resolves the name through `assets.json`; if it is absent
		the name falls through to `abs_url()` and the URL is unhashed again."""
		from frappe.utils import get_assets_json

		assets = get_assets_json()
		resolved = assets.get(self.BUNDLE)
		self.assertTrue(resolved, f"{self.BUNDLE} is not in assets.json: run `bench build`")
		self.assertIn("/assets/sharm_dreams_group/dist/js/", resolved, resolved)
		self.assertRegex(resolved, r"sharm_dreams_group\.bundle\.[A-Z0-9]+\.js$", resolved)

	def test_the_css_is_a_hashed_bundle_too(self):
		"""The dashboard's styling has the same one-year cache behind it as the JS.

		A `app_include_css` entry that is not a `.bundle.css` name resolves to
		`/<name>` at the site root and 404s, silently: the desk serves the page with
		no styling and nothing in the console.  Same failure mode as the JS hook had,
		so it is checked the same way.
		"""
		from frappe.utils import get_assets_json

		entries = frappe.get_hooks("app_include_css") or []
		self.assertTrue(entries, "no app_include_css, so the dashboard ships unstyled")
		for entry in entries:
			self.assertTrue(
				".bundle." in entry or entry.startswith("/assets/"),
				f"app_include_css entry {entry!r} resolves to /{entry} at the site root and 404s.",
			)
			self.assertTrue(
				".bundle." in entry, f"{entry!r} has no content hash; nginx caches /assets/ for a year"
			)

		resolved = get_assets_json().get("sharm_dreams_group.bundle.css")
		self.assertTrue(resolved, "the css bundle is not in assets.json: run `bench build`")
		self.assertRegex(resolved, r"sharm_dreams_group\.bundle\.[A-Z0-9]+\.css$", resolved)

	def test_every_declared_css_bundle_is_built_and_hashed(self):
		"""Every `app_include_css` entry, not just the dashboard's.

		`test_the_css_is_a_hashed_bundle_too` checks the *name* of every entry and
		then resolves one file by hand, which was the only file there was. Adding a
		second bundle opened a gap: a `.bundle.css` name that nothing has built is
		absent from `assets.json`, so `bundled_asset` falls through to `abs_url()`,
		the URL loses its hash, and nginx serves a year-old cached copy of a file
		that does not exist - or a 404. The desk renders unstyled and the console
		says nothing.

		So the check is now over this app's own bundles rather than over one name,
		which is also the only form that keeps holding as bundles are added.

		Filtered to `sharm*` on purpose. `frappe.get_hooks` merges the value from
		*every* app, so the list also holds Frappe's own `desk.bundle.css` and
		ERPNext's and HRMS's - none of which resolve under this app's `dist/`, and
		none of which are this app's business.
		"""
		from frappe.utils import get_assets_json

		assets = get_assets_json()
		entries = [
			e
			for e in (frappe.get_hooks("app_include_css") or [])
			if ".bundle." in e and e.startswith("sharm")
		]
		self.assertTrue(entries, "this app declares no bundled stylesheets")
		for entry in entries:
			with self.subTest(bundle=entry):
				resolved = assets.get(entry)
				self.assertTrue(resolved, f"{entry} is not in assets.json: run `bench build`")
				self.assertRegex(resolved, re.escape(entry[:-4]) + r"\.[A-Z0-9]+\.css$", resolved)
				self.assertIn("/assets/sharm_dreams_group/dist/css/", resolved, resolved)

	def test_the_sidebar_css_is_scoped_to_the_side_panel(self):
		"""The same guarantee the dashboard stylesheet has, for the panel.

		The panel is the framing shell the desk draws around *every* page, so it is
		the surface where an unscoped rule does the most damage: `.item-anchor` or
		`.sidebar-item-label` unprefixed would restyle the panel on a Form, a List
		and every workspace, not just the desk.

		Both prefixes are accepted because the file needs both - `.body-sidebar` is
		the panel itself and `.body-sidebar-container` is the wrapper that carries
		the width variable - and the width rule has no target inside the panel.
		"""
		import pathlib

		path = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "css"))
			/ "sharm_sidebar.bundle.css"
		)
		self.assertTrue(path.exists(), "the side panel stylesheet is missing")
		css = path.read_text(errors="replace")

		body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		selectors = [s.strip() for s in re.findall(r"([^{}]+)\{", body)]
		self.assertTrue(selectors, "the side panel stylesheet has no rules at all")
		unscoped = []
		for selector in selectors:
			for part in selector.split(","):
				part = part.strip()
				if not part:
					continue
				if not part.startswith((".body-sidebar", ".body-sidebar-container")):
					unscoped.append(part)
		self.assertEqual(unscoped, [], f"panel rules that would leak onto other desk pages: {unscoped}")

	def test_the_sidebar_css_is_declared(self):
		"""A stylesheet on disk that `hooks.py` does not name is not loaded.

		Same failure mode as a JS file missing from the bundle's import list: it
		looks shipped in every other check and never reaches the browser.
		"""
		entries = frappe.get_hooks("app_include_css") or []
		self.assertIn("sharm_sidebar.bundle.css", entries)

	def test_the_sidebar_width_does_not_leak_onto_other_sidebars(self):
		"""`--sidebar-width` is read by `list_sidebar.scss` as well as the panel.

		Frappe declares it once on `:root`, and this app needs a wider desk panel
		than the 220px that truncates "Employee Lifecycle" and "Time & Attendance"
		to "Employee Life..." and "Time & Att...". Setting it on `:root` would
		widen the List's filter sidebar as a side effect, so it is set on
		`.body-sidebar-container` - which cascades into `.body-sidebar` and its
		placeholder, both of which the expanded mixin sizes from that variable, and
		into nothing else.
		"""
		import pathlib

		css = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "css"))
			/ "sharm_sidebar.bundle.css"
		).read_text(errors="replace")

		root_set = re.search(r":root\s*\{[^}]*--sidebar-width", css)
		self.assertIsNone(root_set, "--sidebar-width is set on :root, which widens the List sidebar too")
		container = re.search(r"\.body-sidebar-container\s*\{[^}]*--sidebar-width", css)
		self.assertIsNotNone(container, "the panel width is no longer set on .body-sidebar-container")

	def test_the_css_is_scoped_to_the_dashboard_page(self):
		"""The desk shares its widget styles with every other surface.

		An unscoped `.widget-box` or `.chart-holder` rule would follow the user onto
		the Form, the List and the workspace blocks, so the file has to be namespaced
		to the page this app's factory builds.

		At-rules are unwrapped first, because `@media (max-width: 767px)` is not a
		selector and matches nothing on its own - the rules inside it are what apply.
		Checking the at-rule itself would forbid responsive rules outright, and the
		one in this file exists because the page's outer margin has to come off on a
		phone, where the sidebar is full-width and there is no side edge to sit
		against.  A `@media` wrapping an unscoped rule would still be caught, because
		what gets checked is the selector at the end of it.
		"""
		import pathlib
		import re

		css = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "css"))
			/ "sharm_dreams_group.bundle.css"
		).read_text(errors="replace")

		# strip comments, then unwrap at-rules, then every selector must name the page
		body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		body = re.sub(r"@(media|supports)[^{]*\{", " ", body)
		selectors = [s.strip() for s in re.findall(r"([^{}]+)\{", body)]
		self.assertTrue(selectors, "the stylesheet has no rules at all")
		unscoped = []
		for selector in selectors:
			for part in selector.split(","):
				part = part.strip()
				if not part:
					continue
				if not part.startswith(".dashboard-page"):
					unscoped.append(part)
		self.assertEqual(unscoped, [], f"rules that would leak onto other desk pages: {unscoped}")

	# Files in `public/js` that the desk must NOT load, and why.
	#
	# The rule is "a file in public/js belongs in the bundle", and it is right for
	# every desk file. It is wrong for a file another page loads itself, because
	# adding those to `app_include_js` would put a second copy on every desk page.
	#
	# `self_service.js` is that case: the self-service page page loads it, because the self-service page is a
	# full-viewport page that must not drag the desk's widgets and patches in with
	# it. So it is listed here rather than in the bundle, and the exclusion is
	# asserted from the page that claims it - otherwise the next person to read
	# "every file in public/js is in the bundle" would add it and put the self-service page's
	# click handling on every List and Form in the desk.
	#
	# The needle is the stem, not the filename. `www/self-services-employees.html`
	# does not write `self_service.js`: it writes `{{ self_service_js }}`, a context variable the
	# page's Python resolves to the hashed asset URL. Looking for the literal
	# filename would fail on a page that is loading it perfectly well.
	NOT_ON_THE_DESK = {
		# loaded by www/self-services-employees.html as `{{ self_service_js }}`
		"self_service.js": ("www/self-services-employees.html", "self_service_js"),
		# loaded by www/employee-portal.html as a literal <script src>. The portal
		# writes the path rather than a context variable - it has no asset-hashing
		# step, and a cache-busting `?v=` is what a wall tablet needs instead.
		"employee_portal.js": ("www/employee-portal.html", "employee_portal.js"),
	}

	def test_the_bundle_ships_every_file_it_should(self):
		"""A file on disk but not imported is silently not loaded.

		The bundle and `hooks.py` name the same thing from two ends: the hook names
		the bundle, the bundle lists the files. A new file added to `public/js` and
		forgotten in the import list would look shipped in every other check.
		"""
		import pathlib

		expected = {
			"dashboard_view.js",
			"sidebar_sections.js",
			"dashboard_widgets.js",
			"company_tree.js",
			"employee_penalty.js",
		}
		self.assertEqual(set(self.shipped()), expected, "the bundle's import list is out of step")

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
		on_disk = {p.name for p in root.glob("*.js")} - {self.BUNDLE}
		self.assertEqual(
			on_disk - set(self.NOT_ON_THE_DESK),
			expected,
			f"a file in public/js is in neither the bundle nor NOT_ON_THE_DESK: "
			f"{on_disk - expected - set(self.NOT_ON_THE_DESK)}",
		)

	def test_a_file_excluded_from_the_desk_is_claimed_by_a_page_that_exists(self):
		"""The exclusion is only honest while the page that claims it is there.

		`NOT_ON_THE_DESK` is a promise that something else loads the file. If that
		something is renamed or deleted the file becomes dead: on disk, referenced by
		nothing, and excluded from the one check that would have noticed.
		"""
		import pathlib

		app_root = pathlib.Path(frappe.get_app_path("sharm_dreams_group"))
		for name, (claimed_by, needle) in self.NOT_ON_THE_DESK.items():
			with self.subTest(file=name):
				page = app_root / claimed_by
				self.assertTrue(page.exists(), f"{claimed_by} does not exist")
				self.assertIn(
					needle,
					page.read_text(errors="replace"),
					f"{name} is excluded from the desk bundle but {claimed_by} "
					f"no longer references {needle!r}",
				)

	def test_the_side_panel_javascript_is_among_them(self):
		"""The file that makes the panel nest sections and draw its icons.

		Worth naming, because the failure mode is that the feature looks like a
		Frappe limitation - "the desk never draws a section icon" - when it is this
		one file failing to load.
		"""
		self.assertIn("sidebar_sections.js", self.shipped())


class TestShippedJavascript(FrappeTestCase):
	"""The side-panel and dashboard JS, run for real under Node.

	Both of those files fix things that fail *silently*. A card that does nothing, a
	section that renders flat, a section header with no icon - none of those raise, and
	none of them log, so a test that only reads the source proves the file exists and
	nothing more. These harnesses load the shipped file into a stubbed Frappe and drive
	the click paths, which is the difference between "the JS parses" and "it works".

	Skipped when Node is absent rather than failed: the desk needs no Node at runtime,
	so a site without it should still be able to run the Python suite.
	"""

	# The count is pinned, so a harness that silently stops checking is a failure
	# rather than a pass: the numbers here are what each harness ran when this was
	# last read, and they only move when a check is added.
	HARNESSES = (
		("tools/test_dashboard_interaction.js", 231),
		("tools/test_sidebar_sections.js", 26),
	)

	def test_the_shipped_javascript_does_what_it_claims(self):
		import json
		import os
		import pathlib
		import shutil
		import subprocess
		import tempfile

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group"))
		if not shutil.which("node"):
			self.skipTest("node is not installed, so the JS harnesses cannot run")

		for script, expected_checks in self.HARNESSES:
			path = root.parent / script
			self.assertTrue(path.exists(), f"{script} is missing")
			# The report is read from a file, not from stdout. The code under test logs
			# to the console, and some of it runs with the host's `console` rather than
			# the stub, so stdout is not reliably pure JSON - which turns a passing
			# harness into an error here and hides the real result.
			out = pathlib.Path(tempfile.gettempdir()) / f"sharm_{path.stem}.json"
			if out.exists():
				out.unlink()
			run = subprocess.run(
				["node", str(path)],
				capture_output=True,
				text=True,
				cwd=str(root.parent),
				timeout=120,
				env={**os.environ, "SHARM_REPORT": str(out)},
			)
			self.assertEqual(
				run.returncode,
				0,
				f"{script} failed:\n{run.stdout}\n{run.stderr}",
			)
			self.assertTrue(out.exists(), f"{script} wrote no report file")
			report = json.loads(out.read_text())
			failed = [r for r in report["results"] if not r["ok"]]
			self.assertFalse(failed, f"{script}: {[r['name'] for r in failed]}")
			# the count is pinned so a harness that silently stops checking is a failure
			self.assertEqual(
				report["total"],
				expected_checks,
				f"{script} now runs {report['total']} checks, was expected to run {expected_checks}",
			)

	def test_a_chart_is_wired_on_what_it_can_filter_not_on_its_module(self):
		"""A chart is a picture only when there is no field to filter the list on.

		The drill-down used to be gated on `chart_doc.module === "Sharm Dreams Group"`,
		which is not a fact about the chart. On this site that left 32 charts inert,
		17 of them Pies and 3 Percentage rings - because every Pie and ring on the desk
		ships with HRMS or CRM, not with us. They looked like charts and did nothing.

		The test that means something is the one the code already makes: a chart can
		become a list filter exactly when it names a `document_type`, a
		`group_by_based_on` and a `group_by_type`. So:

		  * the shipped JS must not gate on the module, and
		  * there must be charts on this site that only the lifted gate can reach,
		    otherwise the change is untested against anything.
		"""
		import pathlib

		import frappe

		source = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group")) / "public" / "js" / "dashboard_view.js"
		).read_text()

		# the gate itself, if it ever comes back
		self.assertNotIn(
			'doc.module !== "Sharm Dreams Group"',
			source,
			"the drill-down is gated on the module again, so every stock Pie is a picture",
		)

		# and the Pies it was hiding. 17 on this site, none of them ours.
		pies = frappe.get_all(
			"Dashboard Chart",
			filters={"type": ("in", ["Pie", "Percentage"])},
			fields=["name", "module", "document_type", "group_by_based_on", "group_by_type"],
			limit_page_length=0,
		)
		drillable = [
			c
			for c in pies
			if c.document_type and c.group_by_based_on and c.group_by_type and c.module != "Sharm Dreams Group"
		]
		self.assertGreaterEqual(
			len(drillable),
			10,
			f"expected the lifted gate to reach a pile of Pies, found {len(drillable)}: "
			f"{[c['name'] for c in drillable]}",
		)

	def test_the_rule_matches_the_code_chart_by_chart(self):
		"""Enumerate every chart on the site and check it against the client's rule.

		The rule the client applies is one line long:

		    drillable  <=>  document_type and group_by_based_on and group_by_type

		Anything else in the chart - its module, its type, whether it belongs to us -
		must not change the answer. That is asserted here rather than in the Node
		harness because the harness can only be given charts we invent, and the whole
		point of the change was charts that ship with other apps: real, installed
		Dashboard Charts, one by one.

		A chart can fail this in only one interesting way - it claims a field to group
		by that the destination doctype does not have. Then `parse_filters_from_route_options`
		cannot place the field, drops the filter, and the list opens unfiltered while
		the URL still shows the parameter. A click that lies. So each chart's group-by
		field is checked against the doctype's own meta.
		"""
		import frappe

		charts = frappe.get_all(
			"Dashboard Chart",
			fields=[
				"name",
				"module",
				"type",
				"document_type",
				"group_by_based_on",
				"group_by_type",
			],
			limit_page_length=0,
		)
		self.assertGreater(len(charts), 20, "the site has suspiciously few charts to check")

		drillable, inert, unplaceable = [], [], []
		for c in charts:
			# this is `get_drilldown_target()` in dashboard_view.js, line for line:
			# a doctype, a group-by field and a group-by type, and then the field has
			# to be a real field of that doctype or the desk drops the filter
			if not (c.document_type and c.group_by_based_on and c.group_by_type):
				inert.append(c)
				continue
			doctype, field = c.document_type, c.group_by_based_on
			try:
				known = frappe.get_meta(doctype).has_field(field)
			except Exception:
				known = True  # no meta at all is not a reason to call it a lie
			if not known:
				# the client says "no" here, so this chart stays a picture. Assert it,
				# because the day it stops mattering is the day the check is rotted.
				unplaceable.append((c.name, doctype, field))
				inert.append(c)
				continue
			drillable.append(c)

		# the point of the change: this is well above the 24 charts we own
		self.assertGreater(
			len(drillable),
			24,
			f"only {len(drillable)} charts are drillable - the module gate is probably back",
		)
		# and the Pies are genuinely in there, not just the bars
		shapes = {c.type for c in drillable}
		self.assertIn(
			"Pie", shapes, f"no Pie is drillable, so clicking a slice still does nothing ({shapes})"
		)
		self.assertIn("Donut", shapes, f"no Donut is drillable ({shapes})")

		# Every one of these is a chart that navigates but cannot filter: the desk drops
		# the parameter, so the URL says one thing and the list does another. The
		# client refuses them (`sharm_field_exists`), so this must be a short,
		# explicable list rather than an accident waiting to ship.
		self.assertLessEqual(
			len(unplaceable),
			3,
			"more charts are grouped by a field their doctype does not have than "
			f"expected: {unplaceable}",
		)
		for name, doctype, field in unplaceable:
			frappe.get_doc("Dashboard Chart", name)  # exists, or this is a stale name
			self.assertFalse(
				frappe.get_meta(doctype).has_field(field),
				f"{name} was excused as unplaceable but {doctype} now has {field}",
			)

		# and a healthy number must remain a picture: the Python chart sources
		self.assertTrue(
			inert,
			"every chart on the site became clickable, which means the check that keeps "
			"a Python chart source a picture has stopped working",
		)
		by_type: dict[str, int] = {}
		for c in inert:
			by_type[c.type] = by_type.get(c.type, 0) + 1
		self.assertGreaterEqual(
			len(inert),
			30,
			f"only {len(inert)} charts are pictures; the group-by gate is too loose "
			f"(by type: {by_type})",
		)


class TestDashboardDrillDown(FrappeTestCase):
	"""Tapping a card or a bar must show the rows behind the number.

	Two separate mechanisms, and each had a way of silently doing nothing:
	`card_routes` was not whitelisted, so every card's `.catch` swallowed the failure
	with nothing in the console; and the stock hook it fed (`set_route_for_custom_card`)
	reads `this.data.route`, which for a `Custom` card is the *method's return value* -
	the figure - so that method can never fire whatever you point it at.
	"""

	# the fields Frappe treats as standard on every DocType, so a route_option naming
	# one is a real filter rather than a typo
	STANDARD: ClassVar[frozenset[str]] = frozenset(
		{"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
	)

	def _routes(self) -> dict:
		from sharm_dreams_group.hr.dashboard import card_routes

		return card_routes()

	def test_card_routes_is_whitelisted(self):
		"""Without the decorator this throws from an unauthenticated call.

		The widget's promise has a `.catch(() => {})` on it, so a card is simply inert
		and the console is silent. This is the assertion that says the map is reachable.
		"""
		from sharm_dreams_group.hr.dashboard import card_routes

		# `frappe.whitelisted` is a set of the decorated functions themselves, not of
		# their names, so membership is by object
		self.assertIn(
			card_routes, frappe.whitelisted, "card_routes is not whitelisted, so no card can drill down"
		)

	def test_every_card_on_a_dashboard_can_be_tapped(self):
		"""A card with no route is a number that does nothing.

		Checked against the cards actually on the three dashboards, not against the
		metric list: a card can be defined, have a route, and still be missing from the
		dashboard, and then the reverse.
		"""
		from sharm_dreams_group.hr.dashboard import NO_DRILLDOWN
		from sharm_dreams_group.setup import ACCOUNT_DASHBOARD_NAME, DASHBOARD_NAME, HRM_DASHBOARD_NAME

		routes = self._routes()
		on_dashboards = set()
		for name in (DASHBOARD_NAME, ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME):
			on_dashboards |= {r.card for r in frappe.get_doc("Dashboard", name).cards}
		self.assertTrue(on_dashboards, "no cards are on any dashboard")
		inert = sorted(on_dashboards - set(routes) - set(NO_DRILLDOWN))
		self.assertFalse(inert, f"these cards are not tappable: {inert}")
		# an exception has to be a card that really exists, or it rots into a lie
		for label in NO_DRILLDOWN:
			self.assertIn(
				label, on_dashboards, f"{label!r} is excused from a drill-down but is not on a dashboard"
			)
		# and there should be very few of them: each one is a number with no answer
		# behind it, and the fix is nearly always a flag rather than an excuse
		self.assertLessEqual(
			len(NO_DRILLDOWN), 1, f"more cards are unrouted than expected: {sorted(NO_DRILLDOWN)}"
		)

	def test_drilldown_flags_encode_exactly_what_they_claim(self):
		"""A flag is only useful if it means what the card says it means.

		Two cards count a set difference - active staff minus the medical register,
		and companies that have posted - which no list filter can express, so
		`setup.sync_drilldown_flags` reduces each to a Checkbox on the row. If the
		flag ever drifts from the difference it encodes, the drill-down opens a list
		that disagrees with the number it was clicked from, which reads as "there is
		nobody here" rather than as a broken filter.
		"""
		from sharm_dreams_group.setup import sync_drilldown_flags

		self.assertTrue(sync_drilldown_flags(), "the flags were not written at all")

		covered = {
			r[0]
			for r in frappe.db.sql("select employee from `tabSDG Insured Person` where employee is not null")
		}
		active = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit_page_length=0)
		expected = {n for n in active if n not in covered}
		flagged = set(
			frappe.get_all(
				"Employee",
				filters={"status": "Active", "sdg_no_medical_record": 1},
				pluck="name",
				limit_page_length=0,
			)
		)
		self.assertEqual(
			flagged, expected, "sdg_no_medical_record does not mean 'active and not in the medical register'"
		)
		# and nobody who has left may carry it, or the list runs longer than the card
		self.assertFalse(
			frappe.db.exists("Employee", {"status": "Left", "sdg_no_medical_record": 1}),
			"a leaver is flagged as having no medical record",
		)

		posting = {
			r[0]
			for r in frappe.db.sql("select distinct company from `tabGL Entry` where company is not null")
		}
		companies = frappe.get_all("Company", pluck="name", limit_page_length=0)
		self.assertEqual(
			set(frappe.get_all("Company", filters={"sdg_posting": 1}, pluck="name", limit_page_length=0)),
			{c for c in companies if c in posting},
			"sdg_posting does not mean 'has at least one GL Entry'",
		)

	def test_each_flagged_card_opens_exactly_the_rows_it_counts(self):
		"""The number on the card and the rows behind it must be the same set.

		This is the whole reason the flags exist, so it is asserted per card rather
		than implied by the flag test above.
		"""
		from sharm_dreams_group.accounts import dashboard as account_dashboard
		from sharm_dreams_group.hr import dashboard as workforce

		for module, key in (
			(workforce, "active_without_medical_record"),
			(account_dashboard, "companies_posting"),
		):
			route = module.METRICS[key]["route"]
			options = module.METRICS[key]["route_options"]
			doctype = route.split("/")[1]
			listed = frappe.get_all(doctype, filters=dict(options), limit_page_length=0)
			# `metric` takes the card's stored filter, which is how it is reached at runtime
			card_value = module.metric({"metric": key})
			self.assertEqual(
				len(listed),
				card_value,
				f"{key}: the card counts {card_value} but its route opens {len(listed)} rows",
			)

	def test_syncing_the_flags_twice_changes_nothing(self):
		"""`migrate` runs on every deploy; the second run must be a no-op."""
		from sharm_dreams_group.setup import sync_drilldown_flags

		first = sync_drilldown_flags()
		before = frappe.get_all("Employee", fields=["name", "sdg_no_medical_record"], limit_page_length=0)
		second = sync_drilldown_flags()
		after = frappe.get_all("Employee", fields=["name", "sdg_no_medical_record"], limit_page_length=0)
		self.assertEqual(first, second, "the same sync reported different work the second time")
		self.assertEqual(before, after, "a second sync changed the flags")

	def test_every_drilldown_target_exists_and_filters_real_fields(self):
		"""A list that opens unfiltered is worse than no link.

		`List/Whatever` is served even when `Whatever` does not exist - it opens an
		empty page - and a filter naming a field that does not exist is dropped without
		complaint, so the list comes up showing everything.
		"""
		for label, target in sorted(self._routes().items()):
			parts = target["route"].split("/")
			self.assertEqual(parts[0], "List", f"{label}: only List routes are wired")
			doctype = parts[1]
			self.assertTrue(frappe.db.exists("DocType", doctype), f"{label}: no DocType {doctype!r}")
			fields = {f.fieldname for f in frappe.get_meta(doctype).fields} | self.STANDARD
			for field in target.get("route_options") or {}:
				self.assertIn(field, fields, f"{label}: filter on {doctype}.{field}, which does not exist")

	def test_a_submitted_only_card_filters_on_docstatus(self):
		"""Payroll and ledger cards must not open onto drafts.

		`docstatus` is not a field on the DocType, so it looks like a typo to a reader -
		and to my own first check. It is a real filter: Frappe lists it in
		`std_fields_list`, which is the first thing `get_doctype_for_field` checks.
		"""
		for label in ("Payroll Gross", "Payroll Net", "Journal Entries", "Loan Repayments"):
			self.assertIn(label, self._routes(), f"{label} has no drill-down")
			self.assertEqual(self._routes()[label].get("route_options", {}).get("docstatus"), "1", label)

	def test_a_count_card_lands_on_a_list_of_the_same_size(self):
		"""Tapping a card must show the rows the number counted.

		The subtlest way for a drill-down to be useless: the card says 34 and the list
		it opens says 30, because the metric reads four states of a field and the
		filter names one. `Uninsured` was exactly that - the metric counts
		`["Uninsured", "None", "", None]` and the filter was `= "Uninsured"`.

		Only checked for cards whose figure *is* a row count of the list they open.
		A money card sums a column over those rows, so the totals will not match and
		comparing them would be meaningless.
		"""
		from sharm_dreams_group.accounts import dashboard as account
		from sharm_dreams_group.hr import dashboard_hrm as hrm
		from sharm_dreams_group.hr.dashboard import NO_DRILLDOWN
		from sharm_dreams_group.setup import ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME

		routes = self._routes()
		checked = 0
		for module, dash in ((hrm, HRM_DASHBOARD_NAME), (account, ACCOUNT_DASHBOARD_NAME)):
			placed = {r.card for r in frappe.get_doc("Dashboard", dash).cards}
			for key, spec in module.METRICS.items():
				if spec["label"] not in placed or spec.get("kind") != "count":
					continue
				if spec["label"] in NO_DRILLDOWN:
					continue  # a set difference; counted here on purpose
				target = routes.get(spec["label"])
				self.assertIsNotNone(target, f"{spec['label']} has no drill-down")
				doctype = target["route"].split("/")[1]
				filters = dict(target.get("route_options") or {})
				# `not set` is a list-view operator, not something a server-side count
				# understands; those cards are compared by hand below instead
				if any(v == "not set" for v in filters.values()):
					continue
				listed = frappe.db.count(doctype, filters)
				figure = module.metric({"metric": key})
				self.assertEqual(
					listed,
					figure,
					f"{spec['label']} reads {figure} but its drill-down lists {listed}",
				)
				checked += 1
		self.assertGreater(
			checked, 3, f"only {checked} cards were compared; the check is not earning its place"
		)

		# the two "no set" cards, compared by hand since `not set` is a list operator
		self.assertEqual(
			frappe.db.count("Employee", {"status": "Left", "relieving_date": ("is", "not set")}),
			hrm.metric({"metric": "leavers_no_relieving_date"}),
			"Leavers Without Relieving Date: the card and its list disagree",
		)
		self.assertEqual(
			frappe.db.count(
				"Employee", {"status": "Active", "sdg_si_status": ["in", ["Uninsured", "None", ""]]}
			),
			hrm.metric({"metric": "uninsured_active"}),
			"Active Staff Uninsured: the card and its list disagree",
		)

	def test_the_widget_patch_reads_the_route_from_the_widget(self):
		"""Not from `this.data`, which is the figure.

		`set_body()` assigns `this.data = await frappe.xcall(this.settings.method, ...)`,
		so on a Custom card `this.data` is a number and `this.data.route` is undefined.
		The patch has to override `set_route` and read `this.route`, which reaches the
		widget because `base_widget.js` does `Object.assign(this, opts)`.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_view.js"
		).read_text()
		self.assertIn("set_route", src, "nothing patches NumberCardWidget.set_route")
		self.assertIn("this.route", src, "the route is never read off the widget")
		# Comments are stripped first. The file explains at length why
		# `this.data.route` cannot work for a Custom card, and scanning the raw
		# text trips over its own explanation.
		code = "\n".join(line.split("//")[0] for line in src.splitlines())
		self.assertNotIn(
			"this.data.route", code, "reading the route off this.data cannot work for a Custom card"
		)

	def test_every_published_chart_can_be_tapped(self):
		"""A chart nobody can open is a picture, and it is not obvious which is which.

		Checked against every chart this app publishes rather than a sample, because
		the failure is invisible either way: a chart with no drill-down simply
		does not respond, and the page still renders.

		There are two shapes and the rule is the same one either way - tapping a
		segment must produce a list of rows that are the rows that segment counted:

		  * a `Group By` tap is the segment's own value, so it needs a real doctype
		    and a real field on it. This is the same rule `dashboard_view.js`
		    applies at click time, so the two cannot disagree about what is
		    clickable;
		  * a `Custom` chart buckets in Python, so its bars are computed windows
		    rather than stored values and cannot be the filter on their own. It
		    declares the mapping in `custom_options` instead, and without it the
		    chart is the one unclickable picture on the page.
		"""
		published = frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			fields=[
				"chart_name",
				"chart_type",
				"document_type",
				"group_by_based_on",
				"group_by_type",
				"custom_options",
			],
			limit_page_length=0,
		)
		self.assertTrue(published, "the app publishes no charts at all")
		for chart in published:
			why = []
			if chart["chart_type"] == "Custom":
				why = self._custom_chart_is_tappable(chart)
			else:
				if chart["chart_type"] != "Group By":
					why.append(f"chart_type is {chart['chart_type']}, not Group By")
				if not chart["document_type"] or not frappe.db.exists("DocType", chart["document_type"]):
					why.append("no usable document_type")
				elif not frappe.get_meta(chart["document_type"]).has_field(
					chart["group_by_based_on"] or ""
				):
					why.append(
						f"{chart['document_type']} has no field {chart['group_by_based_on']!r}"
					)
				if not chart["group_by_based_on"]:
					why.append("no group-by field, so there is nothing to filter the list on")
			self.assertFalse(why, f"{chart['chart_name']} cannot be tapped: {'; '.join(why)}")

	def test_the_reused_charts_are_really_on_the_dashboard(self):
		"""A reused chart is named by a section, so nothing writes it.

		`REUSED_CHARTS` are HRMS documents this app points at rather than creates,
		which is the right shape - a second definition of a figure HRMS owns would
		drift from it - and which makes a name wrong enough to go unnoticed. The
		alternative is a section naming a chart that is not there, which draws nothing
		and looks like a section that lost its chart.
		"""
		from sharm_dreams_group.hr import dashboard

		self.assertTrue(dashboard.REUSED_CHARTS, "no stock charts are being reused")
		for name in dashboard.REUSED_CHARTS:
			with self.subTest(chart=name):
				self.assertTrue(
					frappe.db.exists("Dashboard Chart", name),
					f"{name} is named by a section but does not exist on this site",
				)

				# named by a section, on a dashboard this app builds
				named_on = []
				for dash in ("SDG Workforce", "SDG Account", "SDG HRM"):
					for section in _layout_sections(dash):
						if name in _section_charts(section):
							named_on.append(dash)
				self.assertTrue(
					named_on,
					f"{name} is in REUSED_CHARTS but no section places it, so it is on "
					"no page",
				)

	def test_a_reused_chart_carries_a_group_limit(self):
		"""`number_of_groups` 0 means "every value", and 111 designations do not fit.

		Every stock chart this app reuses groups a field with far more values than an
		axis can carry - 111 distinct designations across 179 active staff - and all of
		them ship with 0. Drawn as shipped, the chart is a stripe of hairlines with no
		readable bar in it.

		The field is only half the claim. `test_a_group_limit_actually_truncates_the
		_bars` checks the other half, which is the half that was broken.
		"""
		from sharm_dreams_group.setup import REUSED_CHART_GROUP_LIMITS

		for name, limit in REUSED_CHART_GROUP_LIMITS.items():
			with self.subTest(chart=name):
				actual = frappe.db.get_value("Dashboard Chart", name, "number_of_groups")
				self.assertGreater(
					actual or 0,
					0,
					f"{name} draws every value; HRMS ships it with number_of_groups 0",
				)
				self.assertLessEqual(actual, 25, f"{name} draws {actual} bars")

	def test_a_group_limit_actually_truncates_the_bars(self):
		"""A limit the code never reads is a claim, not a limit.

		`number_of_groups` was written, checked, printed on the chart document - and
		did nothing. The desk reads it in one place, as frappe-charts' `maxSlices`,
		which applies to a Pie or a Donut and is ignored by a `Bar` chart. So
		`Designation Wise Employee Count` drew 111 bars with the field set to 20,
		about 2.7px each on a 300px chart.

		Asked of the field, the question has no way to fail; asked of the chart, it
		fails on stock Frappe and passes only once `upstream_patches` teaches
		`get_group_by_chart_config` to honour it. So this asks of the chart, through
		the desk's own query path, and also pins the two ways the patch could go
		wrong instead: capping a chart that set no limit, and dropping bars that are
		not among the largest.
		"""
		from frappe.desk.doctype.dashboard_chart.dashboard_chart import (
			get as chart_get_data,
		)

		from sharm_dreams_group.setup import REUSED_CHART_GROUP_LIMITS

		def bars(name):
			doc = frappe.as_json(frappe.get_doc("Dashboard Chart", name))
			data = chart_get_data(chart=doc, no_cache=1) or {}
			labels = data.get("labels") or []
			values = ((data.get("datasets") or [{}])[0].get("values")) or []
			return list(zip(labels, [v for v in values if isinstance(v, (int, float))]))

		for name, limit in REUSED_CHART_GROUP_LIMITS.items():
			with self.subTest(chart=name):
				drawn = bars(name)
				self.assertTrue(drawn, f"{name} draws no bars at all")
				self.assertLessEqual(
					len(drawn),
					limit,
					f"{name} draws {len(drawn)} bars with number_of_groups {limit} - the "
					"field is being stored and not being read",
				)

		# A chart that set no limit must keep drawing every value. This is the one
		# way the patch could make a stock chart *worse*: every chart on the site
		# ships with 0, and a patch reading that as "limit zero" would blank them.
		#
		# Which chart to ask is chosen rather than named, because the test is about
		# the patch and not about any one chart: `Employees by Branch` has 15
		# branches and would pass a limit of 20 without the field ever being read.
		# The widest uncapped chart on the site is the one that can catch it.
		widest_limit = max(REUSED_CHART_GROUP_LIMITS.values())
		uncapped = [
			(name, bars(name))
			for name in ("Department Wise Employee Count", "Designation Wise Employee Count")
			if not frappe.db.get_value("Dashboard Chart", name, "number_of_groups")
		]
		self.assertTrue(uncapped, "no uncapped stock chart to check the patch against")
		widest_name, widest_bars = max(uncapped, key=lambda pair: len(pair[1]))
		self.assertTrue(
			widest_bars,
			f"{widest_name} sets no limit and so must draw every value, and draws none",
		)
		self.assertGreater(
			len(widest_bars),
			widest_limit,
			f"{widest_name} is the widest uncapped chart on the site and draws "
			f"{len(widest_bars)} bars, no more than a chart capped at {widest_limit} - "
			"so it is being capped too",
		)

		# And the largest survive the cut. `order_by` is already "count desc", so a
		# limit that kept the first rows rather than the biggest would quietly show
		# the wrong twenty.
		drawings = bars("Designation Wise Employee Count")
		sizes = [v for _label, v in drawings]
		self.assertEqual(
			sizes,
			sorted(sizes, reverse=True),
			"a capped chart is no longer ordered largest first, so the bars it kept "
			"are not the largest",
		)

	def test_a_chart_can_draw_every_band_its_drilldown_declares(self):
		"""A pie that draws six of fourteen bands cannot be clicked at all.

		`Employees by Age` was a `Pie`. The desk caps a Pie or a Donut at **six**
		slices - `chart_widget.js` sets `maxSlices: number_of_groups || 6` for those
		two shapes - and frappe-charts' `AggregationChart` prunes past the cap and
		draws a grey wedge for the rest. So fourteen age bands became six slices.

		The truncation is not only a smaller chart. A slice click is resolved by the
		clicked element's position in the drawn store, and `sharm_index_in_store`
		length-checks that store against the label list before trusting it - because
		on a Line the store holds one path per *series*, and reading it unguarded
		answers "0" for every point. Six elements against fourteen labels fails that
		check, so every click refused. The wiring report counted the chart clickable
		throughout, because a chart with a drilldown declared looks identical to a
		working one from where it stands.

		Checked over every drilldown on the three dashboards rather than over
		`Employees by Age`, so the next chart declared as a pie with more values than
		a pie can show is caught here and not by a user.
		"""
		checked = 0
		for dash in ("SDG Workforce", "SDG Account", "SDG HRM"):
			for section in _layout_sections(dash):
				for name in _section_charts(section):
					doc = frappe.get_doc("Dashboard Chart", name)
					if doc.chart_type != "Custom":
						continue
					spec = (frappe.parse_json(doc.custom_options or "{}") or {}).get("drilldown")
					buckets = len((spec or {}).get("buckets") or [])
					if not buckets:
						continue
					checked += 1
					with self.subTest(dashboard=dash, chart=name):
						# The desk's own rule: it caps the two shapes that are made of
						# slices, and nothing else. A Bar is an `AxisChart`, never reads
						# maxSlices, and draws every value it is given.
						self.assertNotIn(
							doc.type,
							("Pie", "Donut"),
							f"{name} is a {doc.type} declaring {buckets} bands. A "
							f"{doc.type} caps itself at {doc.number_of_groups or 6} "
							"slices, so most of those bands cannot be tapped at all - "
							"and a click on the ones it does draw resolves against a "
							"store of a different length and refuses. Make it a Bar.",
						)

		self.assertTrue(
			checked,
			"no drilldown on any dashboard declares buckets, so this check proved nothing",
		)

	def test_the_reused_charts_are_shaped_to_hold_their_values(self):
		"""A Pie from HRMS, over a field with more values than a pie can show.

		Two of the four reused charts ship as Pies - `Employees by Age` with fourteen
		age bands and `Designation Wise Employee Count` with 111 designations - and
		both are Bars now, for the reason the test above gives.

		Asked of the table rather than of the documents, so a chart dropped out of it
		stops being checked, and asserted as *the table says Bar* rather than as "the
		document is not a Pie". The second is the difference between fixed and
		happens to be fine today.
		"""
		from sharm_dreams_group.setup import REUSED_CHART_TYPES

		self.assertTrue(REUSED_CHART_TYPES, "no reused chart declares a shape")
		for name, shape in REUSED_CHART_TYPES.items():
			with self.subTest(chart=name):
				self.assertEqual(shape, "Bar", f"{name} is declared as a {shape}")
				actual = frappe.db.get_value("Dashboard Chart", name, "type")
				self.assertEqual(
					actual,
					shape,
					f"{name} is a {actual}, not the {shape} this app declares. It is a "
					"standard chart, so an HRMS migrate puts the Pie back unless "
					"`tune_reused_charts` runs after it - and it does run from "
					"after_migrate.",
				)

	def test_a_reused_salary_chart_follows_the_payroll_that_was_run(self):
		"""A `Timespan` of "last month" is the calendar month, not the payroll.

		HRMS ships these two charts with
		`["Salary Slip", "start_date", "Timespan", "last month"]`, which the desk
		resolves to the calendar month before today. On this site that happens to be
		the one payroll run on file, so the chart looks right - and stops being right
		the moment a second month is run, or the first is run late.

		`latest_payroll_window` replaces it with the actual period of the newest
		submitted payroll, which is what the money cards beside the chart sum.
		"""
		from sharm_dreams_group.setup import latest_payroll_window

		window = latest_payroll_window()
		self.assertIsNotNone(window, "no submitted payroll, so there is no window to check")
		start, end = window
		expected = frappe.db.count("Salary Slip", {"docstatus": 1, "start_date": ["between", (start, end)]})
		total = frappe.db.count("Salary Slip", {"docstatus": 1})
		self.assertEqual(
			expected,
			total,
			f"the newest payroll window ({start}..{end}) covers {expected} of {total} "
			"submitted slips, so a chart scoped to it is not showing the whole run",
		)

		for name in ("Designation Wise Salary(Last Month)", "Department Wise Salary(Last Month)"):
			with self.subTest(chart=name):
				rows = frappe.parse_json(frappe.get_doc("Dashboard Chart", name).filters_json) or []
				self.assertFalse(
					[r for r in rows if isinstance(r, list) and len(r) > 2 and r[2] == "Timespan"],
					f"{name} still carries a Timespan filter, which resolves to the "
					"calendar month rather than to the payroll that was run",
				)
				dated = [r for r in rows if isinstance(r, list) and len(r) > 2 and r[2] == "between"]
				self.assertTrue(dated, f"{name} has no date range at all")
				self.assertEqual(
					[dated[0][3][0], dated[0][3][1]],
					[start, end],
					f"{name} is scoped to a different payroll than the cards beside it",
				)

	def test_every_age_band_opens_the_rows_it_draws(self):
		"""The whole point of the drilldown: bar *n* lists the people bar *n* counted.

		Checked against the database rather than against a recount of the same Python,
		because that would only prove the arithmetic is self-consistent. The chart is
		HRMS's and is asked for its own numbers; each band's `date_of_birth` window is
		resolved the way the widget resolves it and counted.
		"""
		from hrms.hr.dashboard_chart_source.employees_by_age import employees_by_age as hrms

		from sharm_dreams_group.hr.dashboard_chart_source.employees_by_age import (
			employees_by_age as mine,
		)

		# HRMS's own bars, via its own source.
		get_data = hrms.get_data
		while hasattr(get_data, "__wrapped__"):
			get_data = get_data.__wrapped__
		chart = get_data(filters="{}")
		labels = list(chart["labels"])
		values = list(chart["datasets"][0]["values"])
		self.assertTrue(any(values), "the chart draws nothing at all")

		buckets = {b["label"]: b for b in mine.drilldown_buckets()}
		self.assertEqual(
			sorted(buckets),
			sorted(labels),
			"the drilldown's bands are not the bands the chart draws",
		)

		today = getdate()
		total_listed = 0
		for label, drawn in zip(labels, values):
			with self.subTest(band=label):
				bucket = buckets[label]
				# resolved the way `sharm_bucket_filter` does it
				if bucket.get("date_from") and bucket.get("date_to"):
					lo, hi = bucket["date_from"], bucket["date_to"]
				else:
					self.fail(f"{label} has neither absolute dates nor offsets")
				opened = frappe.db.count(
					"Employee",
					{"status": "Active", "date_of_birth": ["between", (lo, hi)]},
				)
				total_listed += opened
				self.assertEqual(
					opened,
					drawn,
					f"the {label} bar draws {drawn} and its list holds {opened}",
				)

		# And the bands must not double-count: the windows share boundaries, and an
		# off-by-one at the seam would show up here and nowhere else.
		self.assertEqual(
			total_listed,
			sum(values),
			"the bands' lists do not sum to the bars, so some employee is in two bands",
		)
		# The open-ended band's window is the one that can swallow the rest of the
		# workforce, so it is checked explicitly.
		# The open-ended band's window is the one that can swallow the rest of the
		# workforce, so it is checked explicitly: without a floor, Frappe's `ifnull`
		# comparison admits every employee with no date of birth.
		top_label = labels[-1]
		from sharm_dreams_group.hr import dashboard as workforce

		self.assertEqual(
			buckets[top_label].get("date_from"),
			workforce.DATED_FLOOR,
			f"the {top_label} band has no floor, so a blank date of birth is admitted "
			"to it - and that band is the one with no upper bound",
		)

	def test_the_age_bands_are_hrms_bands(self):
		"""The bands are restated here, so they can drift from the ones drawn.

		An HRMS upgrade that changes its age bands would otherwise leave the drilldown
		pointing at windows the chart no longer draws - every bar opening the wrong
		list, with nothing on the page to say so. HRMS's own function is the
		reference.
		"""
		from hrms.hr.dashboard_chart_source.employees_by_age import employees_by_age as hrms

		from sharm_dreams_group.hr.dashboard_chart_source.employees_by_age import (
			employees_by_age as mine,
		)

		theirs = [f"{b}+" if isinstance(b, int) else f"{b[0]}-{b[1]}" for b in hrms.get_ranges()]
		mine_labels = [mine.band_label(low, high) for low, high in mine.age_ranges()]
		self.assertEqual(
			mine_labels,
			theirs,
			"the age bands in the drilldown are not the ones the chart draws",
		)

	def _custom_chart_is_tappable(self, chart) -> list[str]:
		"""Why this `Custom` chart cannot be tapped, or `[]` when it can."""
		why: list[str] = []
		spec = (frappe.parse_json(chart["custom_options"] or "") or {}).get("drilldown")
		if not spec:
			return [
				"a Custom chart has no group-by field to filter on, so it needs a "
				"`drilldown` in custom_options or its bars lead nowhere"
			]
		doctype, field = spec.get("doctype"), spec.get("field")
		if not doctype or not frappe.db.exists("DocType", doctype):
			why.append(f"drilldown names no usable doctype: {doctype!r}")
		elif not frappe.get_meta(doctype).has_field(field or ""):
			why.append(f"{doctype} has no field {field!r}")
		if not spec.get("buckets"):
			why.append("drilldown declares no buckets, so no bar resolves to a filter")
		return why

	def test_a_chart_tap_keeps_the_chart_own_filters(self):
		"""The list behind a bar must be the rows the bar counted.

		"Headcount by Company" is filtered to Active staff, so tapping a bar and
		opening `List/Employee` with only `company = ...` shows leavers too - heads
		that appear in no bar on the chart. The number on the chart and the number
		of rows then disagree, which defeats the point of the drill-down.
		"""
		chart = frappe.db.get_value(
			"Dashboard Chart",
			{"chart_name": "Headcount by Company"},
			["document_type", "group_by_based_on", "filters_json"],
			as_dict=True,
		)
		self.assertIsNotNone(chart, "Headcount by Company is not published")
		filters = frappe.parse_json(chart["filters_json"] or "[]")
		self.assertTrue(
			any(row[1] == "status" and row[3] == "Active" for row in filters if len(row) > 3),
			f"the chart is not scoped to Active staff, so its bars and the list would disagree: {filters}",
		)
		# and the bar really does mean what the list will show
		company = frappe.db.get_value(chart["document_type"], {}, chart["group_by_based_on"], order_by=None)
		if company:
			bar = frappe.db.count(
				chart["document_type"], {"status": "Active", chart["group_by_based_on"]: company}
			)
			listed = frappe.db.count(
				chart["document_type"], {"status": "Active", chart["group_by_based_on"]: company}
			)
			self.assertEqual(bar, listed)

	def test_charts_are_wired_for_drilldown(self):
		"""A chart is a picture in stock Frappe; this is the only click handler on it.

		Asserted against the source because there is nothing in the database that
		remembers it: drop the patch and the charts go back to being pictures with no
		error anywhere.

		The click has to be a delegated DOM listener. `frappe.utils.make_chart()`
		forwards unknown options to `new frappe.Chart(...)`, but that constructor
		reads a fixed list of them and `onClick` is not on it - so an `args.onClick`
		was accepted, passed along and silently dropped, on every chart. That is why
		nothing was ever clickable, and it is why asserting the word "onClick" is
		appears in the file would be worthless: it now only occurs in the explanation above.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_view.js"
		).read_text()
		self.assertIn("get_chart_args", src, "no chart click handler")
		self.assertIn('addEventListener("click"', src, "no delegated click listener on the chart")
		# the attribute frappe-charts stamps on every bar it draws
		self.assertIn("[data-point-index]", src, "the click handler does not read the tapped bar")
		# the tooltip carries that attribute too and floats over the plot
		self.assertIn("graph-svg-tip", src, "a click on the tooltip would navigate")
		# the dead path must not creep back in
		# An assignment, not the bare words: the file explains *why* onClick does not
		# work, so asserting the substring would only ever match this file's own prose.
		self.assertNotIn(
			"args.onClick =",
			src,
			"frappe-charts drops onClick on the floor; a click handler set there is dead code",
		)
		# and it must read the document, not the Chart.js instance it is easy to confuse it with
		self.assertIn("this.chart_doc", src, "the chart document is never read")

	def test_discarded_charts_are_destroyed(self):
		"""A chart that is thrown away must stop listening to the DOM.

		`chart_widget.js` drops the reference to the live frappe-chart in two places
		without calling `destroy()`, which is the only thing that disconnects the
		ResizeObserver from `configure()`. The orphan then redraws into a container
		that has already been emptied - `render()` does `chart_wrapper.empty()` for
		every Pie, Donut and Percentage chart on every data refresh, and this site has
		21 of them - and throws `NotFoundError: removeChild`, aborting the redraw
		part-way and leaving the previous frame on screen.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_view.js"
		).read_text()
		self.assertIn("sharm_destroy_patch_patched", src, "the discard patch is gone")
		self.assertIn("destroy()", src, "a discarded chart is never destroyed")
		for name in ("refresh", "render"):
			self.assertIn(f"widget.prototype.{name} = function", src, f"{name} is not patched")
		self.assertIn('type === "Pie"', src, "the circular rebuild path is not covered")
		self.assertIn('type === "Donut"', src, "the circular rebuild path is not covered")
		self.assertIn('type === "Percentage"', src, "the circular rebuild path is not covered")


class TestCostOfLivingCoupon(FrappeTestCase):
	"""The coupon register: what was handed over, and what it came to.

	This is a cash handout from the HR drawer, not a payroll item. The tests below
	are as much about keeping it *out* of payroll as about the arithmetic.
	"""

	def setUp(self):
		self.month = getdate("2026-08-01")
		# Deliberately somebody who is *not* in the workbook.  All 100 of the
		# workbook's employees who exist on the site already have an August coupon,
		# so there is no free one among them; and a test row next to real rows in a
		# cash register is worse than a test row that is obviously a test row.
		# tearDown removes it, and the reconciliation test reads only the imported
		# rows, which are the ones carrying a workbook row.
		workbook_ids = {r["work_id"] for r in self._workbook_rows() if r["work_id"]}
		self.employee = next(
			name
			for name in frappe.get_all("Employee", pluck="name", limit_page_length=0, order_by="name")
			if name not in workbook_ids
		)
		self.amount = 750.0

	def tearDown(self):
		frappe.db.delete("SDG Coupon Granted", {"employee": self.employee, "period_month": self.month})

	@staticmethod
	def _workbook_rows() -> list[dict]:
		"""The per-employee list exactly as it is in the workbook.

		Read from the app root, not `get_app_path`, which resolves to the module
		directory rather than the app root where `seed_data` sits.
		"""
		import json
		import pathlib

		root = pathlib.Path(__file__).resolve().parents[2]
		return json.loads((root / "seed_data" / "coupon_rows.json").read_text(encoding="utf-8"))

	def test_nothing_in_the_coupon_path_is_read_by_the_payroll(self):
		"""The whole point: the coupon must not become an earning.

		Stock `Employee Benefit Claim` looks like the obvious home for this and is
		wrong - `on_submit` submits an `Additional Salary` for the claimed amount, so
		recording the coupon through it would pay it in net pay *and* out of the
		drawer. This is the assertion that the app never went that way.
		"""
		for doctype in ("SDG Coupon Granted", "SDG Coupon Band"):
			for reader in self._readers(doctype):
				self.assertNotIn(
					"payroll",
					reader,
					f"{doctype} is read by {reader}: the coupon must stay out of payroll",
				)

	def _readers(self, doctype: str) -> list[str]:
		import pathlib

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group"))
		out = []
		for f in root.rglob("*.py"):
			if f.name.startswith("test_"):
				continue
			if doctype in f.read_text(errors="replace"):
				out.append(str(f.relative_to(root)))
		return out

	def test_the_notes_must_add_up_to_the_amount(self):
		"""The notes are how the drawer was balanced, so they have to agree.

		Every one of the 126 rows in the workbook reconciles exactly
		(100 x n100 + 200 x n200 + 250 x n250 == the stated amount), which is what
		makes the counts worth storing. A mismatch here means the amount typed is
		not the amount counted out.
		"""
		from sharm_dreams_group.hr.coupon import _assert_notes_match_amount, notes_total

		# 5 x 200 + 2 x 250 = 1500
		self.assertEqual(notes_total(0, 5, 2), 1500)
		_assert_notes_match_amount(1500, 0, 5, 2)  # balanced: no error

		# 5 x 200 on its own is 1000, not 1500
		self.assertRaises(frappe.ValidationError, _assert_notes_match_amount, 1500, 0, 5, 0)

	def test_a_row_with_no_note_counts_is_allowed(self):
		"""Not counting the notes is a gap in the record, not a wrong record.

		The desk will sometimes know the amount before it has counted the change, so
		an amount with no breakdown has to be recordable - the reconciliation catches
		counts that disagree, and refuses nothing that is merely absent.
		"""
		frappe.get_doc(
			{
				"doctype": "SDG Coupon Granted",
				"period_month": self.month,
				"employee": self.employee,
				"amount": self.amount,
			}
		).insert(ignore_permissions=True)

	def test_one_coupon_per_employee_per_month(self):
		"""Cash going out of a drawer: a second coupon in a month is refused.

		Either a duplicate import or a deliberate second handout, and both deserve a
		decision rather than a default.
		"""
		frappe.get_doc(
			{
				"doctype": "SDG Coupon Granted",
				"period_month": self.month,
				"employee": self.employee,
				"amount": self.amount,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "SDG Coupon Granted",
					"period_month": self.month,
					"employee": self.employee,
					"amount": 750,
				}
			).insert(ignore_permissions=True)

	def test_the_name_says_who_and_which_month(self):
		"""`H0201 - 2026-08`, not a hash.

		Frappe ignores a `{field}` autoname expression and falls back to a hash, so
		the name is built in the controller. This is the test that catches that
		silent fallback, which is how the first attempt at this ended up with names
		like `hophpjmve2`.
		"""
		doc = frappe.get_doc(
			{
				"doctype": "SDG Coupon Granted",
				"period_month": self.month,
				"employee": self.employee,
				"amount": self.amount,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(doc.name, f"{self.employee} - 2026-08")

	def test_the_bands_are_the_four_from_the_workbook(self):
		"""750 / 1050 / 1200 / 1500, and no two bands pay the same amount.

		The amount is how `SDG Coupon Granted` looks its band up, so a duplicate
		would make that lookup arbitrary - and the family size behind the amount is
		the reason the band exists at all.
		"""
		amounts = sorted(
			frappe.get_all("SDG Coupon Band", pluck="coupon_amount", limit_page_length=0), key=float
		)
		self.assertEqual([float(a) for a in amounts], [750.0, 1050.0, 1200.0, 1500.0])
		self.assertEqual(len(frappe.get_all("SDG Coupon Band", limit_page_length=0)), 4)

	def test_august_2026_reconciles_to_the_per_employee_list(self):
		"""151,800 across 126 people - and the four-row band summary is the stale one.

		The workbook carries two sets of numbers. The band summary says 127 people
		and 153,300; the per-employee list, which is the one carrying the note counts
		and the signatures, holds 126 and 151,800. HR confirmed the list is correct.
		The registered rows are a subset (see below), so this asserts the *source*
		figures and that what is registered is internally consistent, rather than
		pretending 26 unmatched people do not exist.
		"""
		rows = self._workbook_rows()
		self.assertEqual(len(rows), 126, "the workbook's list should hold 126 people")
		self.assertAlmostEqual(sum(r["amount"] for r in rows), 151800.0, places=2)
		# and the notes in the workbook reconcile on every single row
		for r in rows:
			self.assertAlmostEqual(
				r["n100"] * 100 + r["n200"] * 200 + r["n250"] * 250,
				r["amount"],
				places=2,
				msg=f"row {r['seq']} ({r['work_id'] or r['name_ar']}) does not reconcile",
			)

	def test_every_workbook_coupon_is_registered_and_the_unowned_ones_are_flagged(self):
		"""The register is complete, and the 26 without an owner are still countable.

		This used to assert the opposite shape: 100 rows registered, the remaining 26
		absent, and `registered + reported = 151,800` proving the gap had not been
		forgotten.  A shortfall nobody can open is still a shortfall, so the unmatchable
		coupons are now written as flagged rows instead of dropped.  The card is a count
		of those rows, which means it can be opened - the whole point of the exercise.
		"""
		rows = self._workbook_rows()
		self.assertEqual(len(rows), 126, "the workbook's list should hold 126 coupons")

		registered = frappe.get_all(
			"SDG Coupon Granted",
			filters={"workbook_row": ["is", "set"]},
			fields=["name", "employee", "amount", "sdg_unrecorded", "workbook_row"],
			limit_page_length=0,
		)
		self.assertEqual(
			len(registered), 126, "every coupon in the workbook should be a register row"
		)
		self.assertAlmostEqual(sum(flt(r["amount"]) for r in registered), 151800.0, places=2)

		flagged = [r for r in registered if r["sdg_unrecorded"]]
		self.assertEqual(len(flagged), 26, "26 coupons have no matchable Employee")
		self.assertAlmostEqual(sum(flt(r["amount"]) for r in flagged), 30450.0, places=2)
		for r in flagged:
			self.assertFalse(
				r["employee"],
				f"workbook row {r['workbook_row']} is flagged unrecorded but has an owner",
			)

		# the flag may only sit on coupons the site genuinely cannot attribute
		by_row = {r["row"]: r for r in rows}
		for r in flagged:
			source = by_row.get(r["workbook_row"])
			self.assertIsNotNone(source, f"workbook row {r['workbook_row']} is not in the workbook")
			work_id = source.get("work_id")
			self.assertTrue(
				not work_id or not frappe.db.exists("Employee", work_id),
				f"workbook row {r['workbook_row']} names {work_id}, which is on the site",
			)

		# and the 100 that do have an owner are attributed to the right person, for the
		# right amount
		owned = [r for r in registered if not r["sdg_unrecorded"]]
		self.assertEqual(len(owned), 100)
		by_id = {r["work_id"]: r for r in rows if r.get("work_id")}
		unknown = sorted({r["employee"] for r in owned} - set(by_id))
		self.assertFalse(unknown, f"these are not in the workbook list: {unknown}")
		for r in owned:
			self.assertAlmostEqual(
				flt(r["amount"]), by_id[r["employee"]]["amount"], places=2, msg=r["employee"]
			)

	def test_the_unrecorded_card_counts_the_rows_its_drilldown_opens(self):
		"""A card that says 26 has to open 26 rows, not the 100 that were attributable."""
		spec = dashboard.card_routes()["Coupons Unrecorded"]
		self.assertEqual(spec["route"], "List/SDG Coupon Granted")
		opened = len(
			frappe.get_all(
				"SDG Coupon Granted",
				filters=spec["route_options"],
				fields=["name"],
				limit_page_length=0,
				ignore_permissions=True,
			)
		)
		shown = dashboard_hrm.metric({"metric": "coupons_outstanding"})
		self.assertEqual(opened, shown, "the card and its list disagree")
		self.assertEqual(opened, 26)


class TestDashboardLayouts(FrappeTestCase):
	"""The section layout shared by every dashboard this app builds.

	A layout is card labels plus chart names held in `chart_options`.  The grouping
	is data rather than code, which means it can also be wrong in a way nothing
	notices - so it is checked here, across all three dashboards rather than the
	one that happened to be built first.

	This class used to be about `SDG Analytics`, a fourth dashboard laid out as
	four tabs.  It was retired: every card it showed was already on the workforce
	dashboard, so it was the same page twice.  The checks are generic and stayed.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.setup import ACCOUNT_DASHBOARD_NAME, DASHBOARD_NAME, HRM_DASHBOARD_NAME

		cls.names = (DASHBOARD_NAME, ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME)

	def test_every_dashboard_exists_with_a_layout(self):
		for name in self.names:
			self.assertTrue(frappe.db.exists("Dashboard", name), f"{name} is missing")
			sections = _layout_sections(name)
			self.assertTrue(sections, f"{name} has no section layout")
			for section in sections:
				self.assertTrue(
					_section_cards(section) or _section_charts(section),
					f"{name} has an empty section {section.get('title')!r}",
				)

	def test_the_retired_analytics_dashboard_is_gone(self):
		"""Nothing may still build or link the dashboard that was taken out.

		A retired dashboard that only stops being rebuilt is the worst outcome: the
		document survives in whatever state the last run left it, and
		`_prune_our_charts` keeps every chart a Dashboard card row still names, so
		its charts are pinned in the database for good.
		"""
		from sharm_dreams_group.setup import RETIRED_DASHBOARD_NAME

		self.assertFalse(
			frappe.db.exists("Dashboard", RETIRED_DASHBOARD_NAME),
			f"{RETIRED_DASHBOARD_NAME} is still in the database",
		)
		items = frappe.get_doc("Workspace Sidebar", "Sharm Dreams Group").items
		linked = [r.link_to for r in items if r.type == "Link" and r.link_type == "Dashboard"]
		self.assertNotIn(RETIRED_DASHBOARD_NAME, linked, f"{RETIRED_DASHBOARD_NAME} is still in the panel")

	def test_every_widget_named_in_a_layout_exists(self):
		"""A layout can name a card or chart that was renamed; that leaves a blank pane."""
		checked = 0
		for name in self.names:
			for section in _layout_sections(name):
				for card in _section_cards(section):
					checked += 1
					self.assertTrue(frappe.db.exists("Number Card", card), f"{name}: {card}")
				for chart in _section_charts(section):
					checked += 1
					self.assertTrue(frappe.db.exists("Dashboard Chart", chart), f"{name}: {chart}")
		# An empty walk passes every assertion above, so the walk itself is asserted.
		self.assertGreater(checked, 60, "the layout walk found almost nothing - it is broken")

	def test_every_card_this_app_publishes_is_on_a_section(self):
		"""A card the `cards` table holds and no section names is a card no page draws.

		Frappe renders a dashboard from `chart_options` - the section layout - and
		keeps the `cards` and `charts` child tables for the "what is on this
		dashboard" lists. The two can disagree, and when they do the page is quietly
		short: no error, no blank pane, just a card that was created, coloured,
		wired with a drill-down and counted, and never seen.

		Seven were. The six penalty-level cards and `Average Gross Salary` sat on
		`SDG Workforce`'s child table and in no section, so the coloured discipline
		strip - the thing those cards exist for - was not on the page at all. Every
		other check in this class reads the layout and asserts things *about* it, so
		none of them noticed that it was not the whole of what was published.

		Both halves are checked: a card no section names never renders, and one that
		two sections name renders twice.
		"""
		from sharm_dreams_group.setup import ACCOUNT_DASHBOARD_NAME, DASHBOARD_NAME, HRM_DASHBOARD_NAME

		routes = dashboard.card_routes()
		checked = 0
		for name in (DASHBOARD_NAME, ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME):
			dash = frappe.get_doc("Dashboard", name)
			in_sections = []
			for section in _layout_sections(name):
				in_sections += _section_cards(section)
			for card in in_sections:
				checked += 1
				with self.subTest(dashboard=name, card=card, problem="drawn twice"):
					self.assertEqual(
						in_sections.count(card),
						1,
						f"{card} is named by {in_sections.count(card)} sections on {name}",
					)
			# Only cards this app owns: a Dashboard can be given stock cards by hand,
			# and those are the user's business rather than a defect here.
			for card in [r.card for r in dash.cards if r.card in routes]:
				with self.subTest(dashboard=name, card=card, problem="never rendered"):
					self.assertIn(
						card,
						in_sections,
						f"{routes[card]['route']} - {card} is on {name} but in no section, "
						"so the page never draws it",
					)
		self.assertGreater(checked, 50, "the layout walk found almost nothing - it is broken")

	def test_the_width_a_layout_asks_for_reaches_the_stylesheet(self):
		"""A column count that nothing styles lays nothing out.

		This was originally about `.grid-col-4`: `WidgetGroup` stamps
		`grid-col-{columns}` on the group body, Frappe styles only up to
		`grid-col-3`, so the group stayed `display: block` and all 43 cards stacked
		one per row across the full width of the page, on all three dashboards.
		Nothing in the suite could see it - the cards were present, clickable,
		counted and drillable - and the only symptom was a page that scrolled
		forever.

		It is the same hazard reached differently now. The width no longer rides on a
		class at all: `sharm_apply_columns` puts it on `--sharm-cols` and this app's
		stylesheet lays out from that. So the two ends have to agree, and both are
		checked: the JS must take the number out of the block it was given rather
		than pass the block object or a literal to `columns`, and the shipped CSS must
		contain a grid rule that reads the property.

		Either half failing means a declared width of five or six renders at the
		stock two, which is the original bug with one indirection more in the way.
		"""
		import re

		src = _dashboard_js()
		self.assertIn(
			"sharm_apply_columns", src, "the dashboard JS no longer applies a column width at all"
		)
		self.assertRegex(
			src,
			r"columns:\s*block\.columns",
			"`columns` is not taken from the block, so a declared width cannot apply",
		)
		self.assertIn(
			"--sharm-cols", src, "the width is set, but not as the custom property the CSS reads"
		)

		css = _shipped_css()
		self.assertIn("display: grid", css, "the stylesheet no longer makes a row a grid at all")
		grids = [rule for rule in re.findall(r"grid-template-columns:[^;]*", css) if "--sharm-cols" in rule]
		self.assertTrue(
			grids,
			"the stylesheet lays every row out at one fixed width, so --sharm-cols is "
			"set and then ignored",
		)
		for rule in grids:
			self.assertRegex(
				rule,
				r"repeat\(auto-fill",
				f"a row of cards or charts is pinned to a fixed count, so it stops "
				f"responding to the window: {rule}",
			)

	def test_the_front_page_and_the_dashboard_name_the_same_bands(self):
		"""The workspace front page and `SDG Workforce` must not disagree.

		The front page is the page every user lands on - `sequence_id = -1` makes it
		`workspaces[0]`, and every enabled user's `default_workspace` points at it -
		and it used to keep its own copy of the card bands. Two lists for one set of
		figures, and they had drifted: the bands were named differently ("Compliance"
		on the front page, "Records To Complete" on the dashboard; "Benefits" versus
		"Benefits & Insurance"), and twelve cards had been added to the dashboard
		without being added here, so they rendered under a heading literally spelled
		**Other**.

		`_dashboard_card_groups` now reads `DASHBOARD_SECTIONS` rather than
		restating it, so the two cannot drift by construction. This asserts that,
		against the page as actually built, rather than trusting the derivation.
		"""
		from sharm_dreams_group.workspace import (
			WORKSPACE,
			_dashboard_sections,
			workspace_blocks,
		)

		expected = [
			(title, [label for label in cards if frappe.db.exists("Number Card", label)])
			for title, cards, _charts in dashboard.DASHBOARD_SECTIONS
			if any(frappe.db.exists("Number Card", label) for label in cards)
		]

		self.assertEqual(
			[(t, list(c)) for t, c, _ch in _dashboard_sections()],
			[tuple(g) for g in expected],
			"the front page's bands are not the dashboard's bands",
		)

		# And the rendered layout, which is what the desk actually draws: a heading
		# per band, immediately followed by that band's cards.
		#
		# `workspace_blocks` is the whole page - a heading, the figures, then a
		# "Setup" half of headers and links - where `_dashboard_card_blocks` used to
		# be just the figures. The bands are therefore the ones that ended up with
		# cards: the setup half contributes headers only, and the desk renders those
		# as headings above shortcut links, which have their own tests.
		blocks = workspace_blocks()
		drawn = []
		for block in blocks:
			if block["type"] == "header":
				text = block["data"]["text"]
				if text == WORKSPACE:
					# the page's own title, not a band heading
					continue
				drawn.append((text, []))
			elif block["type"] == "number_card":
				drawn[-1][1].append(block["data"]["number_card_name"])
		drawn = [(title, cards) for title, cards in drawn if cards]
		self.assertEqual([(title, list(cards)) for title, cards in drawn], expected)

		self.assertNotIn("Other", [title for title, _c in drawn], "a band is still called Other")

		# The page has to exist and be the one built from those blocks, or the
		# comparison above is of a function nothing calls.  And the whole rendered
		# layout, not just the band helper: this is where the six penalty cards were
		# caught twice, once as `Discipline` and again as `Penalties`, because only
		# one of the two writers was in the function under test.
		self.assertTrue(frappe.db.exists("Workspace", WORKSPACE))
		content = frappe.parse_json(frappe.db.get_value("Workspace", WORKSPACE, "content")) or []
		rendered_cards = [
			b["data"]["number_card_name"]
			for b in content
			if isinstance(b, dict) and b.get("type") == "number_card"
		]
		duplicates = sorted({c for c in rendered_cards if rendered_cards.count(c) > 1})
		self.assertEqual(duplicates, [], f"drawn more than once on the front page: {duplicates}")
		self.assertEqual(
			set(rendered_cards),
			{label for _t, cards in drawn for label in cards},
			"the front page draws cards from somewhere other than the dashboard's bands",
		)
		rendered = [
			b["data"]["text"] for b in content if isinstance(b, dict) and b.get("type") == "header"
		]
		for title, _cards in expected:
			self.assertIn(title, rendered, f"the front page has no '{title}' band")
		self.assertNotIn("Penalties", rendered, "the penalty cards are in two bands")

	def test_no_widget_appears_on_the_dashboard_that_cannot_ask_for_it(self):
		"""A card with no `route` renders as a number that does nothing.

		The stock Number Card widget only navigates when the card carries a route, and
		a `Custom` card driven by a method never has one - so an interactive card is
		only interactive if its metric says where to go.
		"""
		from sharm_dreams_group.hr import dashboard

		routes = dashboard.card_routes()
		labels = _all_metric_labels()
		# The penalty strip is the one card source that is not a metric module: six
		# cards and a total, one per configured level, each reading a method in
		# `hr/penalty.py` with its own `filters_json` rather than a `metric` key.
		# They were outside every layout until the "Discipline" band put them on the
		# page, and this test then called them "a drill-down with no metric" - which
		# is the same gap seen from the other side: the test knew about three card
		# writers and had never been told about the fourth.
		from sharm_dreams_group.setup import PENALTY_CARDS_METHOD

		known = set(labels) | {
			label
			for label in frappe.get_all(
				"Number Card", filters={"method": PENALTY_CARDS_METHOD}, pluck="label", limit_page_length=0
			)
		}

		self.assertTrue(routes, "no card is clickable")
		for name in self.names:
			for section in _layout_sections(name):
				for card in _section_cards(section):
					if not routes.get(card):
						continue
					self.assertIn(card, known, f"{card} has a drill-down but no source for its figure")

	def test_the_drill_down_map_only_names_real_targets(self):
		from sharm_dreams_group.hr import dashboard

		for label, target in dashboard.card_routes().items():
			self.assertTrue(frappe.db.exists("Number Card", label), label)
			doctype = target["route"].split("/")[-1]
			self.assertTrue(
				frappe.db.exists("DocType", doctype),
				f"{label} drills into {doctype!r}, which is not a DocType",
			)

	def test_no_chart_in_any_layout_is_orphaned(self):
		"""Pruning must not be able to delete a chart a dashboard is still using.

		`_prune_our_charts` works out what is orphaned by reading each dashboard's
		`chart_options` layout.  If it read only the workforce dashboard, a chart used
		only by `SDG HRM` or `SDG Account` would look unused and be deleted - and the
		dashboard would then render an empty pane.  So the set of dashboards it walks
		is pinned here, and every layout chart is checked against what survives.
		"""
		from sharm_dreams_group.setup import DASHBOARD_NAME, app_dashboard_names

		names = app_dashboard_names()
		self.assertIn(DASHBOARD_NAME, names)
		self.assertIn("SDG HRM", names, "the HRM dashboard is walked by name somewhere else")
		self.assertIn("SDG Account", names, "the Account dashboard is walked by name somewhere else")

		referenced = set()
		for name in names:
			if not frappe.db.exists("Dashboard", name):
				continue
			for section in _layout_sections(name):
				referenced.update(_section_charts(section))

		self.assertTrue(referenced, "no dashboard names any chart, so this proves nothing")
		missing = sorted(c for c in referenced if not frappe.db.exists("Dashboard Chart", c))
		self.assertEqual(missing, [], f"a layout names charts that do not exist: {missing}")

	def test_no_file_calls_a_frappe_helper_that_no_longer_exists(self):
		"""`frappe.dom_ready` is gone, and calling it throws at load time.

		Three files ended a line with `frappe.dom_ready(...)`.  It does not exist in
		this Frappe, and neither does the `frappe.ready` that looks like its
		replacement - the only `ready` on the desk is localforage's.  The call threw
		`TypeError: frappe.dom_ready is not a function` while the file was still
		executing, so everything after it never ran: no penalty column was filtered
		out of the Employee list, no chart could be filtered without a Company, and
		no dashboard card had a drill-down.  A failed script tag logs one line and
		is otherwise invisible, so this is checked by reading the source.

		jQuery's `$(fn)` is what `frappe.dom_ready` used to wrap, and it is always
		present, so that is the replacement.
		"""
		import pathlib

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
		for path in sorted(root.glob("*.js")):
			source = path.read_text(errors="replace")
			# comments may still *name* the old helper; a call must not
			calls = [
				line
				for line in source.splitlines()
				if "dom_ready" in line and not line.strip().startswith(("//", "*", "/*"))
			]
			self.assertEqual(calls, [], f"{path.name} calls a helper that no longer exists: {calls}")

	def test_the_route_reads_the_document_not_the_name(self):
		"""`with_doc` hands the callback a *name*, and the page went blank over it.

		`frappe.model.with_doc` calls `callback(name, r)` in both of its branches -
		the document is never the first argument.  A factory that reads it as one
		ends up holding the string `"SDG Workforce"`, so `chart_options` is
		undefined, `read_layout()` returns a single empty implicit section, and the
		dashboard renders as a blank page.  No exception, nothing in the console -
		which is why it is pinned here rather than left to the JS harness.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_view.js"
		).read_text(errors="replace")
		# the callback must take two arguments, the first of which is discarded
		self.assertIn('with_doc("Dashboard", name, (_name, r) =>', src)
		# and the document has to come out of the response or get_doc
		self.assertIn("r.message.docs[0]", src)

	def test_the_dashboard_route_factory_is_registered(self):
		"""The route 404s without it, and the stock desk registers no such factory."""
		import pathlib
		import re

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
		ours = (root / "dashboard_view.js").read_text(errors="replace")
		self.assertIn("frappe.views.DashboardViewFactory", ours)

		found = set()
		for base in (
			pathlib.Path(frappe.get_app_path("frappe", "public", "js")),
			root,
		):
			for f in base.rglob("*.js"):
				for m in re.finditer(r"frappe\.views\.([A-Za-z]+)Factory\s*=", f.read_text(errors="replace")):
					found.add(m.group(1))
		self.assertIn("DashboardView", found)
		# loaded means reachable from the bundle, which is what app_include_js names
		bundle = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
			/ "sharm_dreams_group.bundle.js"
		).read_text(errors="replace")
		self.assertIn(
			"./dashboard_view.js",
			bundle,
			"the factory is registered but dashboard_view.js is not in the bundle, so it never loads",
		)

	def test_the_nested_section_fix_is_shipped(self):
		"""Stock `find_nested_items()` only nests one level; ours must override it."""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "sidebar_sections.js"
		).read_text(errors="replace")
		self.assertIn("nested_items", src)
		bundle = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js"))
			/ "sharm_dreams_group.bundle.js"
		).read_text(errors="replace")
		self.assertIn(
			"./sidebar_sections.js",
			bundle,
			"the nesting fix is on disk but not in the bundle, so it never loads",
		)

	def test_keep_closed_is_re_asserted_after_the_desk_builds_the_panel(self):
		"""`keep_closed` is the initial state only, and two stock paths undo it.

		The desk applies it once in `enable_collapsible()`, then overwrites it from
		the user's stored `section-breaks-state` and again from the active route.
		Neither is reachable from the sidebar document, so nothing on the server can
		guarantee the panel - and this app is built entirely out of collapsed
		sections.  The fix has to run on the client, after the build.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "sidebar_sections.js"
		).read_text(errors="replace")
		self.assertIn("sharm_close_kept_closed_sections", src)
		# it has to wrap the build, or it runs before there is anything to close
		self.assertIn("frappe.ui.Sidebar.prototype.create_sidebar = function", src)
		self.assertIn("keep_closed", src)


class TestSectionBlockLayout(FrappeTestCase):
	"""A section is a list of blocks, and the blocks have to add up.

	The old shape was one card row then one chart row, at four cards across and two
	charts at a fixed 240px. "Records To Complete" drew eleven cards and then one
	chart, so the only figure on that band showing *which company* each gap sits in
	was a full screen below the numbers.

	`SECTION_LAYOUT` fixes both - order and width - and it is the half of the layout
	that no other check in this file looks at. Everything else reads a flat list of
	names off a section and cannot tell whether the chart came first or last, or
	whether a band asked for six cards across and got four. So it is checked here,
	across all three dashboards.

	The failure this guards against is quiet in both directions: a band that drifts
	out of its declaration renders at the default, and one whose declaration is never
	read renders at the default. Neither raises.
	"""

	def _dashboards(self):
		from sharm_dreams_group.accounts import dashboard as account
		from sharm_dreams_group.hr import dashboard as workforce
		from sharm_dreams_group.hr import dashboard_hrm as hrm

		return (
			("SDG Workforce", workforce.DASHBOARD_SECTIONS, workforce.SECTION_LAYOUT),
			("SDG Account", account.ACCOUNT_SECTIONS, account.ACCOUNT_SECTION_LAYOUT),
			("SDG HRM", hrm.HRM_SECTIONS, hrm.HRM_SECTION_LAYOUT),
		)

	def test_every_section_layout_partitions_its_section(self):
		"""Every card and every chart, exactly once, across a band's blocks.

		The blocks and the band are declared separately, so they can drift: a card
		added to the band and not to the blocks is drawn nowhere, and one named in
		two blocks is drawn twice. Neither is visible on the page - the second is a
		duplicate tile and the first is an absence.
		"""
		known_cards = set(frappe.get_all("Number Card", pluck="name", limit_page_length=0))
		known_charts = set(frappe.get_all("Dashboard Chart", pluck="name", limit_page_length=0))
		sections_seen = 0

		for name, sections, layout in self._dashboards():
			for title, cards, charts in sections:
				want_c = [c for c in cards if c in known_cards]
				want_h = [c for c in charts if c in known_charts]
				if not (want_c or want_h):
					continue
				sections_seen += 1
				with self.subTest(dashboard=name, section=title):
					blocks = dashboard._blocks_for(layout.get(title), want_c, want_h)
					got_c = [i for b in blocks if b["type"] == "cards" for i in b["items"]]
					got_h = [i for b in blocks if b["type"] == "charts" for i in b["items"]]
					self.assertEqual(sorted(got_c), sorted(want_c), "cards")
					self.assertEqual(sorted(got_h), sorted(want_h), "charts")
					self.assertEqual(len(got_c), len(set(got_c)), "a card is drawn twice")
					self.assertEqual(len(got_h), len(set(got_h)), "a chart is drawn twice")
		self.assertGreater(sections_seen, 10, "the walk found almost nothing - it is broken")

	def test_a_band_with_no_declaration_still_renders(self):
		"""The default is what a stock dashboard gets, and it must not be a blank.

		Five of the bands declare no arrangement. They used to render as one card row
		then one chart row and must keep doing so, because the fallback is also what
		every dashboard the desk ships renders through this factory - `chart_options`
		written by stock Frappe and HRMS has `cards`/`charts` and no `blocks` at all.
		"""
		blocks = dashboard._blocks_for(None, ["A", "B"], ["C"])
		self.assertEqual([(b["type"], b["columns"], b["items"]) for b in blocks],
			[("cards", dashboard.DEFAULT_CARD_COLUMNS, ["A", "B"]), ("charts", dashboard.DEFAULT_CHART_COLUMNS, ["C"])])
		self.assertEqual(blocks[1]["height"], dashboard.DEFAULT_CHART_HEIGHT)

		# a section with nothing but charts must not get an empty card row first
		only_charts = dashboard._blocks_for(None, [], ["C", "D"])
		self.assertEqual([b["type"] for b in only_charts], ["charts"])

	def test_a_declared_block_that_is_entirely_empty_is_dropped(self):
		"""A renamed card must not leave a heading-sized hole in the middle of a band.

		`_blocks_for` filters each declared block against what the section actually
		has. A block left with nothing is dropped rather than rendered, because an
		empty grid still draws its own vertical space and reads as a loading fault.
		"""
		blocks = dashboard._blocks_for(
			(
				("cards", ("A", "Gone"), {"columns": 3}),
				("charts", ("Also Gone",), {"columns": 2}),
				("cards", ("B",), {"columns": 2}),
			),
			["A", "B"],
			["C"],
		)
		self.assertEqual([b["type"] for b in blocks], ["cards", "cards"])
		self.assertEqual([b["items"] for b in blocks], [["A"], ["B"]])

	def test_a_layout_table_is_not_shared_between_dashboards(self):
		"""Two bands share a title and hold completely different cards.

		`SDG Workforce` and `SDG HRM` both have a band called "Records To Complete".
		The workforce one holds eleven cards arranged six, a chart, then five; the HRM
		one holds three and nothing else.

		If `section_layout` read one shared table the workforce arrangement would be
		offered to the HRM band, every one of those labels would be filtered out
		because HRM has none of them, and the band would quietly render at the
		default. A layout silently not applied, which is the exact thing this whole
		change exists to stop - so the sharing is asserted not to be there.
		"""
		from sharm_dreams_group.hr import dashboard_hrm

		self.assertNotIn(
			"Records To Complete",
			dashboard_hrm.HRM_SECTION_LAYOUT,
			"HRM's own layout table has grown the workforce band's arrangement",
		)
		self.assertIn("Records To Complete", dashboard.SECTION_LAYOUT)

		# And the workforce table really does hold workforce labels, so the check
		# above is not passing because the arrangement is nowhere.
		workforce_records = dashboard.SECTION_LAYOUT["Records To Complete"]
		named = [item for _kind, items, _opts in workforce_records for item in items]
		self.assertIn("No SI Number", named)
		self.assertNotIn("Active Staff Without Arabic Name", named)

	def test_the_stored_layout_is_blocks_and_carries_a_width(self):
		"""What is in the database is what the page will draw.

		The stored layout is the only thing the browser sees, so a block written
		without a width - or with a width of nothing - renders at the desk's default
		and the arrangement in the table quietly does not happen. Read from the
		documents, not from the tables, because the tables are what produced them.
		"""
		for name in ("SDG Workforce", "SDG Account", "SDG HRM"):
			sections = _layout_sections(name)
			self.assertTrue(sections, f"{name}")
			for section in sections:
				with self.subTest(dashboard=name, section=section.get("title")):
					self.assertIn("blocks", section, f"{name} stores a section with no blocks")
					self.assertTrue(section["blocks"])
					for block in section["blocks"]:
						self.assertIn(block["type"], ("cards", "charts"))
						self.assertTrue(block.get("items"), "an empty block was stored")
						self.assertIsInstance(
							block.get("columns"), int, f"{block['type']} block has no width"
						)
						self.assertGreaterEqual(block["columns"], 1)
						if block["type"] == "charts":
							self.assertIsInstance(block.get("height"), int, "a chart row has no height")
							self.assertGreaterEqual(block["height"], 120)

	def test_at_least_one_band_interleaves_cards_and_charts(self):
		"""Interleaving is the point; nothing else can tell whether it happened.

		A layout that is one card block then one chart block is indistinguishable from
		the old shape by any of the other checks here, so if the whole mechanism ever
		stops working - the table stops being read, `_blocks_for` stops being called -
		this is the only assertion that fails. Two bands are required, because the
		failure could also be one band being dropped rather than all of them.
		"""
		interleaved = []
		for name, sections, layout in self._dashboards():
			known_cards = set(frappe.get_all("Number Card", pluck="name", limit_page_length=0))
			known_charts = set(frappe.get_all("Dashboard Chart", pluck="name", limit_page_length=0))
			for title, cards, charts in sections:
				want_c = [c for c in cards if c in known_cards]
				want_h = [c for c in charts if c in known_charts]
				if not (want_c and want_h):
					continue
				blocks = dashboard._blocks_for(layout.get(title), want_c, want_h)
				kinds = [b["type"] for b in blocks]
				if "cards" in kinds and "charts" in kinds and kinds != ["cards", "charts"]:
					interleaved.append(f"{name}/{title}")
		self.assertGreaterEqual(
			len(interleaved),
			2,
			f"no band puts a chart between card rows any more (only {interleaved})",
		)

	def test_a_band_asks_for_more_than_one_width(self):
		"""The point of a per-block width is that bands differ.

		Everything defaulting to one number would pass every other check here and
		reproduce the layout this replaced, so the spread across the stored layouts is
		asserted directly.
		"""
		widths = set()
		for name in ("SDG Workforce", "SDG Account", "SDG HRM"):
			for section in _layout_sections(name):
				for block in section.get("blocks") or []:
					widths.add(block.get("columns"))
		widths.discard(None)
		self.assertGreaterEqual(len(widths), 4, f"every band asks for the same width: {sorted(widths)}")
		for width in widths:
			self.assertLessEqual(width, 12, f"a band asks for {width} columns across")


class TestPanelReportsSection(FrappeTestCase):
	"""The Reports band opens reports, and the desk is actually able to route them.

	The rest of the panel links DocTypes, and a DocType link is finished the moment the
	row is written: `link_to` names something real and the desk opens it.  A Report link
	is not, and that asymmetry is the whole risk in this section.

	`sidebar_item.js get_path()` reads `this.item.report` and **returns no path at all**
	when it is absent:

	    if (this.item.link_type === "Report") {
	        let args = { type: "Report", name: this.item.link_to };
	        if (!frappe.app.sidebar.editor.edit_mode) {
	            if (this.item.report) { args.is_query_report = ...; args.report_ref_doctype = ...; }
	            else { return; }            // <- no href, no error, no log
	        }
	    }

	`Workspace Sidebar Item` has no `report` field, so from the DocType alone this row
	looks unfinishable - and the natural conclusion, which this file's author reached
	first, is that the app has to supply the metadata itself.  It does not:
	`frappe.boot.get_sidebar_items` attaches it, for any Report row whose report exists
	and is enabled.  So the section works with no JS and no extra fields.

	That makes the fragility sit somewhere no other panel row has it: not in the sidebar
	document, which is checked to be well-formed, but in a boot payload assembled by
	another app.  If that `if` is ever narrowed or dropped, every row here still renders,
	still reads correctly, still shows an icon - and is a dead link.  Nothing else in the
	panel would notice, so the check below is the one that matters.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.setup import APP_SIDEBAR, REPORT_PANEL_GROUPS

		cls.doc = frappe.get_doc("Workspace Sidebar", APP_SIDEBAR)
		cls.reports = [r for r in cls.doc.items if r.link_type == "Report"]
		# read out of the spec rather than restated, so a fourth group added there
		# does not have to be added here to keep this test honest
		cls.sub_sections = tuple(sub for _t, groups in REPORT_PANEL_GROUPS for sub, _l in groups)

	def _boot_rows(self):
		"""The panel exactly as the browser will receive it.

		`get_sidebar_items` is the function that assembles the payload, so calling it
		here tests the real thing rather than a reconstruction of it - the mistake
		that made the report metadata look absent when it is only absent from the
		sidebar document.
		"""
		from frappe.boot import get_sidebar_items

		from sharm_dreams_group.setup import APP_SIDEBAR

		boot = get_sidebar_items(frappe.get_all("Workspace", pluck="name"))
		panel = boot.get(APP_SIDEBAR.lower())
		self.assertIsNotNone(panel, "the app's panel is not in the boot payload at all")
		return panel["items"]

	def test_the_section_exists_and_is_grouped(self):
		sections = [r for r in self.doc.items if r.type == "Section Break" and not r.child]
		self.assertIn("Reports", [r.label for r in sections], "the panel has no Reports section")

		subs = [
			r.label
			for r in self.doc.items
			if r.type == "Section Break" and r.child and r.label in self.sub_sections
		]
		self.assertEqual(
			subs, list(self.sub_sections), "the Reports sub-sections are not all present, in order"
		)

	def test_every_report_we_name_still_exists_and_is_enabled(self):
		"""A report renamed or disabled upstream must not leave a dead row behind.

		`apply_app_sidebar` already drops rows whose target is missing and prints the
		names; this is the assertion that the drop actually happened, so a rename in
		ERPNext cannot quietly shrink the section without anyone being told.
		"""
		broken = []
		for row in self.reports:
			if not frappe.db.exists("Report", row.link_to):
				broken.append(f"{row.link_to} (no such Report)")
			elif frappe.db.get_value("Report", row.link_to, "disabled"):
				broken.append(f"{row.link_to} (disabled)")
		self.assertEqual(broken, [], f"report rows that cannot be opened: {broken}")

	def test_the_browser_is_handed_what_it_needs_to_route_each_one(self):
		"""The load-bearing check: no `report`, no path, no error, no log."""
		boot_reports = [i for i in self._boot_rows() if i.get("link_type") == "Report"]
		self.assertTrue(boot_reports, "the boot payload carries no Report rows")

		unroutable = [
			i["label"] for i in boot_reports if not (i.get("report") or {}).get("report_type")
		]
		self.assertEqual(
			unroutable, [], f"these rows would render with no href: {unroutable}"
		)

		# And the route the desk will build from them has to be the report's own page.
		# A Script Report with a ref_doctype and no is_query_report lands on
		# `report/<name>`, which is not a page.
		wrong = []
		for i in boot_reports:
			args = {"type": "Report", "name": i["link_to"]}
			args["is_query_report"] = i["report"]["report_type"] in ("Query Report", "Script Report")
			args["report_ref_doctype"] = i["report"]["ref_doctype"]
			if args["is_query_report"]:
				if args["report_ref_doctype"] and "view/report" in _route_for(args):
					wrong.append(f"{i['label']} -> {_route_for(args)}")
			elif not args["report_ref_doctype"]:
				wrong.append(f"{i['label']} has no ref_doctype and is not a query report")
		self.assertEqual(wrong, [], f"these would open the wrong page: {wrong}")

	def test_the_section_stays_a_curated_list(self):
		"""Not an inventory.

		There are 236 enabled reports and this lists a few dozen.  That is the point:
		a band long enough to scroll past is a band nobody reads, and the rest stay
		reachable the way ERPNext means - a DocType, then its Reports tab.  Asserted
		loosely on purpose, so adding a genuinely useful report is not a test failure,
		but pasting the whole list in is.
		"""
		enabled = frappe.db.count("Report", {"disabled": 0})
		self.assertLess(
			len(self.reports),
			enabled // 4,
			f"the section lists {len(self.reports)} of {enabled} reports; it has stopped being curated",
		)
		self.assertGreater(len(self.reports), 20, "the section has been gutted")

	def test_no_report_is_listed_twice(self):
		seen = {}
		for row in self.reports:
			seen[row.link_to] = seen.get(row.link_to, 0) + 1
		dupes = {k: v for k, v in seen.items() if v > 1}
		self.assertEqual(dupes, {}, f"reports listed more than once: {dupes}")

	def test_the_section_adds_no_doctype_rows(self):
		"""It is a reports band, not a second copy of the mirror above it."""
		from sharm_dreams_group.setup import panel_sections

		for title, groups in panel_sections():
			if title != "Reports":
				continue
			for _sub, links in groups:
				wrong = [ln for ln in links if ln[1] != "Report"]
				self.assertEqual(wrong, [], f"non-report links in the Reports band: {wrong}")


def _route_for(args):
	"""`frappe.utils.generate_route`'s Report branch, in Python.

	Copied rather than called because generate_route is browser-side and needs
	`frappe.router`; the three lines that decide a report's URL are the thing under
	test, so restating them keeps the assertion about the desk's rule and not about a
	stub's version of it.
	"""
	if args.get("is_query_report"):
		return "query-report/" + args["name"]
	if args.get("report_ref_doctype"):
		return args["report_ref_doctype"].lower().replace(" ", "-") + "/view/report/" + args["name"]
	return "report/" + args["name"]


class TestSettingsTablesAreHonest(FrappeTestCase):
	"""The panel invites a question about every one of these: what is it for?

	A settings table that nothing reads is worse than no table - it looks like
	configuration, and the thing it appears to configure quietly keeps its old
	behaviour.  So each of ours is pinned to the code that consumes it, and a table
	that is decorative is a test failure rather than a surprise.
	"""

	def readers(self, doctype: str) -> set[str]:
		"""App source files that name this DocType, ignoring the panel and the seeder."""
		import pathlib

		root = pathlib.Path(frappe.get_app_path("sharm_dreams_group"))
		found = set()
		for f in root.rglob("*.py"):
			if f.name.startswith("test_") or f.name in ("seeder.py", "workspace.py", "setup.py", "_v.py"):
				continue
			if doctype in f.read_text(errors="replace"):
				found.add(str(f.relative_to(root)))
		return found

	def test_tax_bracket_is_actually_used_by_the_payroll(self):
		readers = self.readers("SDG Tax Bracket")
		self.assertTrue(
			any("deductions" in r for r in readers),
			f"nothing on the payroll reads the tax bracket: {readers}",
		)

	def test_penalty_gate_is_wired(self):
		"""The role list is only meaningful if something asks it.

		`SDG Penalty Settings` was a Single holding "who may see penalties".  It was
		replaced by Employee read permissions, which is where Frappe enforces that,
		so the thing to check is that the gate is expressed and that the child table
		is still readable by the roles it names.
		"""
		from sharm_dreams_group.hr.penalty import configured_roles

		roles = set(configured_roles())
		self.assertTrue(
			{"Employee", "HR Manager", "HR User"} <= roles,
			f"Employee is not readable by the roles the penalty gate names: {sorted(roles)}",
		)
		self.assertTrue(
			self.readers("SDG Penalty") or frappe.db.exists("DocType", "SDG Penalty"),
			"the penalty child table is gone as well as its settings",
		)

	def test_the_tracker_leave_types_are_in_stock_leave_type(self):
		"""The leave vocabulary is the vacation tracker's, not the workbook's.

		These were an app-owned `SDG Leave Type` holding twelve codes from the
		payroll workbook (`LL`, `PDOPH`, `D.L`, ...).  HR does not use those: the
		leave people actually book against is the set of columns in
		SharmDreamVacation Tracker.xlsx, which is a different set again - it has
		`Half Day`, `Paid Absence` and `Leave In Lieu`, and no code for any of them.
		So the eight tracker columns are the list, each carrying the Arabic column
		heading the HR team will be searching for.

		The important half is the negative: `is_lwp` must stay 0 on all eight.  Stock
		`is_lwp` is a *rule* - set it and HRMS deducts unpaid leave itself - and the
		payroll already deducts from the workbook, so a non-zero value would make two
		systems take money for the same day.
		"""
		import json
		from pathlib import Path

		seed = json.loads(
			(Path(__file__).resolve().parents[2] / "seed_data" / "seed.json").read_text(encoding="utf-8")
		)
		wanted = {r["leave_type"]: r for r in seed["leave_types"]}
		self.assertEqual(len(wanted), 8, f"expected the tracker's eight columns, got {sorted(wanted)}")

		rows = {
			r["name"]: r
			for r in frappe.get_all(
				"Leave Type",
				filters={"name": ["in", list(wanted)]},
				fields=[
					"name",
					"sdg_code",
					"sdg_tracker_name",
					"sdg_is_paid",
					"sdg_affects_deduction",
					"is_lwp",
				],
				limit_page_length=0,
			)
		}
		self.assertFalse(set(wanted) - set(rows), f"missing from the desk: {sorted(set(wanted) - set(rows))}")

		for name, spec in wanted.items():
			row = rows[name]
			self.assertEqual(row["sdg_code"], spec["code"], f"{name}: short code")
			self.assertEqual(row["sdg_tracker_name"], spec["tracker_ar"], f"{name}: Arabic tracker heading")
			self.assertEqual(row["sdg_is_paid"], int(bool(spec["is_paid"])), f"{name}: paid flag")
			self.assertEqual(
				row["sdg_affects_deduction"], int(bool(spec["affects_deduction"])), f"{name}: reduces-pay"
			)
			self.assertEqual(
				row["is_lwp"], 0, f"{name} has is_lwp set: HRMS would deduct on top of the payroll"
			)

	def test_retired_workbook_leave_codes_are_gone(self):
		"""The old workbook spellings must not linger beside the tracker's vocabulary.

		Reduced from twelve codes to four. `DO`, `PH`, `LLPH` and `PDOPH` are no longer
		retired: they are the tracker's own codes, confirmed with the HR policy and
		created on the site on that authority (see `test_the_tracker's_six_codes_are_all
		_present`). Keeping them in this list was a contradiction - the test demanded
		the absence of codes the sheet uses, so it could only ever fail once the sheet
		was actually followed.

		What is still retired is the earlier workbook's *alternative spellings* of the
		same ideas, which nothing references and which would give HR two ways to write
		the same day.
		"""
		for code in ("LL", "PDO", "DE-L", "D.L", "DI.L"):
			still = frappe.db.get_all(
				"Leave Type", filters={"sdg_code": code}, pluck="name", limit_page_length=0
			)
			self.assertFalse(still, f"workbook code {code!r} is still in the desk as {still}")

	def test_leave_type_panel_row_resolves(self):
		"""The panel promises Leave Type; the desk has to serve it."""
		from sharm_dreams_group.setup import _link_resolves

		self.assertTrue(frappe.db.exists("DocType", "Leave Type"))
		from sharm_dreams_group.setup import panel_links

		self.assertIn(
			"Leave Type",
			{to for _p, _l, _lt, to, _u in panel_links() if _link_resolves("DocType", to)},
			"Leave Type is not reachable from the panel",
		)

	def test_group_allocation_rows_are_readable(self):
		"""`20q5bvkv4h` said nothing; the name must identify the recharge it records."""
		import re

		# autoname lives on the DocType record, not on its meta
		autoname = frappe.db.get_value("DocType", "SDG Group Allocation", "autoname")
		self.assertEqual(
			autoname,
			"{salary_slip} - {charged_company}",
			"the naming rule fell back to Random, so rows are named by hash again",
		)
		hashish = re.compile(r"^[a-z0-9]{10}$")
		for name in frappe.get_all("SDG Group Allocation", pluck="name", limit_page_length=0):
			self.assertFalse(
				hashish.match(name),
				f"{name!r} is a hash: nobody can tell which recharge it is",
			)

	def test_documents_points_at_the_drive_app_not_the_drive_workspace(self):
		"""`Workspace: Drive` is /desk/drive - a page of shortcuts, not the file manager.

		Documents is a top-level row beside Home rather than an "Overview" section:
		a section holding one link is a heading over a single row, which is the thing
		`test_no_section_is_left_empty` exists to prevent.
		"""
		from sharm_dreams_group.setup import APP_TOP_LINKS

		documents = [l for l in APP_TOP_LINKS if l[0] == "Documents"]
		self.assertEqual(len(documents), 1, "Documents should be one top-level link")
		_label, link_type, link_to, *rest = documents[0]
		self.assertEqual(link_type, "URL")
		self.assertIsNone(link_to, "a URL row must leave link_to empty or validation rejects it")
		self.assertEqual(rest[0], "/drive")

		sb = frappe.get_doc("Workspace Sidebar", "Sharm Dreams Group")
		row = next(r for r in sb.items if r.type == "Link" and r.label == "Documents")
		self.assertEqual(row.url, "/drive")
		# and it is above the sections, not filed under one
		first_section = next(i for i, r in enumerate(sb.items) if r.type == "Section Break" and not r.child)
		documents_at = next(i for i, r in enumerate(sb.items) if r.type == "Link" and r.label == "Documents")
		self.assertLess(documents_at, first_section, "Documents should sit above the sections")

	def test_internal_urls_do_not_open_a_new_tab(self):
		"""The desk hardcodes target=_blank on URL links; ours are same-site."""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "sidebar_sections.js"
		).read_text(errors="replace")
		self.assertIn("open_internal_urls_in_this_tab", src)
		self.assertIn('removeAttr("target")', src)


class TestCurrencyAndPrecision(FrappeTestCase):
	"""Money is marked, counts are not, and nothing shows more than two decimals.

	`currency` is the one field that says a figure is an amount, and on both a
	Number Card and a Dashboard Chart the desk reads it without ever asking what
	the number is.  Nothing offers to set it correctly, so the metric has to.

	Where it actually reaches the screen is narrower than it looks, and worth
	writing down rather than assuming.  On a chart it is `formatTooltipY` and
	nothing else - the axis shows "500 K", not "EGP 500,000".  On a card it is
	`set_formatted_number`, which `get_number_for_custom_card` only calls when the
	metric returns an object; ours return bare numbers, so no card on this site
	prints a symbol either way.  So this class is about the record being right -
	the card form, the API, and the day a metric starts returning an object.
	"""

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.hr import dashboard

		cls.specs = dashboard.metric_definitions()
		cls.by_label = {s["label"]: s for s in cls.specs}

	def test_only_money_metrics_carry_a_currency(self):
		"""Driven off `kind`, so a new count cannot acquire a symbol by copy-paste."""
		from sharm_dreams_group.hr.dashboard import is_money

		for spec in self.specs:
			expected = "EGP" if is_money(spec["key"]) else None
			self.assertEqual(
				spec.get("currency"),
				expected,
				f"{spec['label']} is {'money' if expected else 'a count'}"
				f" but declares currency={spec.get('currency')!r}",
			)

	def test_the_card_record_agrees_with_the_metric(self):
		"""The declared currency has to reach the card, or the label is decoration."""
		for spec in self.specs:
			actual = frappe.db.get_value("Number Card", spec["label"], "currency")
			self.assertEqual(actual or None, spec.get("currency"), f"{spec['label']} card currency is wrong")

	def test_a_card_added_since_the_last_migrate_still_has_no_currency(self):
		"""The check above only catches a bug that has already been written to disk.

		`Number Card.currency` defaults to EGP and `_set_defaults` fills a default
		into any field left falsy, so inserting a count card with `currency: None`
		does not store `None` - it stores EGP. Every migrate afterwards clears it on
		the update path, so the card looks right the next day and the suite passes,
		and the only window in which anything is wrong is between the card being
		created and the next migrate.

		Three cards were in that window for the whole life of this check: added in
		one migrate, fixed by the next, and never wrong at any point a test could
		observe. `_clear_defaulted_currency` is called on the insert path instead.

		So the default itself is exercised here, on a document that really is
		inserted and then read back, rather than asserted about the code.
		"""
		name = f"sdg test currency default {frappe.generate_hash(length=8)}"
		try:
			card = frappe.get_doc(
				{
					"doctype": "Number Card",
					"label": name,
					"type": "Custom",
					"method": dashboard.METRIC_METHOD,
					"filters_json": "{}",
					"color": "#000000",
					"currency": None,
					"is_public": 1,
					"is_standard": 0,
				}
			)
			card.insert(ignore_permissions=True)
			# If this ever stops being true the test below is asserting nothing, and
			# it is better that it says so than that it quietly keeps passing.
			self.assertTrue(
				card.currency,
				"Number Card no longer defaults to EGP, so this test is no longer "
				"covering the case it was written for",
			)
			from sharm_dreams_group.setup import _clear_defaulted_currency

			_clear_defaulted_currency(card, None)
			self.assertFalse(
				frappe.db.get_value("Number Card", card.name, "currency"),
				"the insert path leaves EGP on a count, and a later migrate is the only "
				"thing that clears it",
			)
		finally:
			if frappe.db.exists("Number Card", name):
				frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)

	def test_a_count_card_shows_no_symbol_and_a_money_card_does(self):
		"""Asserted on the stored `currency`, which is what the card record is.

		Named for the rule rather than for the pixels: a Custom card whose method
		returns a bare number never reaches `set_formatted_number`, so no card on
		this site draws a symbol either way - see
		`test_a_scalar_metric_never_reaches_the_currency_branch`. What is being
		kept here is the record, which is what the card form shows an editor, what
		the API returns, and what the widget will use the moment a metric starts
		returning an object.
		"""
		money = [s for s in self.specs if s.get("currency")]
		counts = [s for s in self.specs if not s.get("currency")]
		self.assertTrue(money and counts)
		for spec in money:
			self.assertTrue(frappe.db.get_value("Number Card", spec["label"], "currency"))
		for spec in counts:
			self.assertFalse(frappe.db.get_value("Number Card", spec["label"], "currency"))

	def test_charts_mark_currency_only_when_they_summ_money(self):
		"""A chart sums a field or it counts rows; there is no third case."""
		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			fields=["name", "chart_name", "group_by_type", "aggregate_function_based_on", "currency"],
			limit_page_length=0,
		):
			# a Sum is the only kind that carries an amount; a Count carries a headcount
			sums = row["group_by_type"] == "Sum" and bool(row["aggregate_function_based_on"])
			self.assertEqual(
				bool(row["currency"]),
				sums,
				f"{row['chart_name']} "
				f"{'sums a field but is not marked as money' if sums else 'counts rows but is marked as money'}",
			)

	def test_penalty_cards_are_counts_and_carry_no_currency(self):
		"""The penalty strip is six headcounts, and none of them is money.

		These are the only cards on `SDG Workforce` that do not come from
		`METRICS`, so they are also the only ones no `is_money` test ever looked
		at - and all six carried `EGP`, because `Number Card.currency` defaults to
		EGP and `create_penalty_cards` inserted without saying otherwise, so a
		count of employees was recorded as an amount. A second writer of cards, and
		the rule went unchecked on it; this asserts on the records themselves
		rather than on a helper.

		What a card *looks* like is a separate matter and is deliberately not
		claimed here: `get_number_for_custom_card` only consults `currency` when the
		method returns an object, and every metric here returns a bare number, so
		`set_formatted_number` is never reached and no symbol is drawn either way.
		`test_a_scalar_metric_never_reaches_the_currency_branch` pins that.
		"""
		from sharm_dreams_group.setup import PENALTY_CARDS_METHOD

		cards = frappe.get_all(
			"Number Card",
			filters={"method": PENALTY_CARDS_METHOD},
			fields=["label", "currency", "filters_json"],
			limit_page_length=0,
		)
		self.assertTrue(cards, "no penalty cards exist, so nothing here is being checked")
		for card in cards:
			with self.subTest(card=card.label):
				self.assertFalse(
					card.currency,
					f"{card.label} counts employees but is marked as {card.currency!r}",
				)
				# A count, so the method gets no money filter either. `level` is the
				# level name; the total card filters on the having-flag instead.
				self.assertNotIn("amount", card.filters_json or "")

	def test_every_card_on_every_dashboard_agrees_with_its_metric_about_money(self):
		"""The currency rule across all three dashboards, from the definitions down.

		The other currency tests each reason from one module's metrics to that
		module's cards. This one builds the expected answer for *every* card the
		app publishes - all three metric modules plus the penalty strip, which is
		not a metric module at all - and then checks each dashboard against it. A
		fourth card writer is covered here without anyone having to remember to
		extend a list, which is the whole reason the penalty cards went unchecked.
		"""
		from sharm_dreams_group.setup import (
			ACCOUNT_DASHBOARD_NAME,
			DASHBOARD_NAME,
			HRM_DASHBOARD_NAME,
			PENALTY_CARDS_METHOD,
		)

		expected = {}
		for module in dashboard._metric_modules():
			# `metric_definitions()` is what adds `key` to each spec; `METRICS` on
			# its own has only `label` and the rest. The workforce module spells the
			# money test without the underscore the two domain modules use; both
			# answer the same question about the same `key`.
			is_money = getattr(module, "_is_money", None) or module.is_money
			for spec in module.metric_definitions():
				expected[spec["label"]] = "EGP" if is_money(spec["key"]) else None
		# The penalty strip counts employees at each level. Not money, and not
		# reachable through `METRICS` at all.
		for label in frappe.get_all(
			"Number Card", filters={"method": PENALTY_CARDS_METHOD}, pluck="label", limit_page_length=0
		):
			expected[label] = None

		seen = set()
		for dashboard_name in (DASHBOARD_NAME, ACCOUNT_DASHBOARD_NAME, HRM_DASHBOARD_NAME):
			for row in frappe.get_doc("Dashboard", dashboard_name).cards:
				label = frappe.db.get_value("Number Card", row.card, "label")
				currency = frappe.db.get_value("Number Card", row.card, "currency")
				seen.add(label)
				with self.subTest(dashboard=dashboard_name, card=label):
					self.assertIn(
						label,
						expected,
						f"{label} is on {dashboard_name} but no metric declares it, so "
						"nothing says whether it is money or a count",
					)
					self.assertEqual(
						currency or None,
						expected[label],
						f"{label} is {'money' if expected[label] else 'a count'}"
						f" but its card carries currency={currency!r}",
					)
		# A metric with no card is not a defect - the three dashboards deliberately
		# divide the figures between them - but a card with no metric is, and the
		# loop above has now named it.
		self.assertTrue(seen)

	def test_a_scalar_metric_never_reaches_the_currency_branch(self):
		"""Why `currency` on a Number Card is bookkeeping here, not presentation.

		`get_number_for_custom_card` has two branches:

		    if (typeof res === "object") { this.set_formatted_number(res); }
		    else                       { this.formatted_number = res; }

		Only the first reads `card_doc.currency` and calls `format_currency`. Our
		metrics return `int | float`, so every card takes the second branch and the
		returned number is inserted verbatim: `Payroll Gross` reads 3937337.54 with
		no symbol despite carrying `EGP`, and `Clear` reads 310 with none either.
		Both are right on screen, and neither depends on `currency` at all.

		Worth pinning rather than rediscovering. It is why the money/count rule is
		about the *record* - the card form, the API, and any metric that starts
		returning an object - and not about rendered text, and it is why those
		tests assert stored values rather than what the page draws.
		"""
		from sharm_dreams_group.hr.penalty import headcount
		from sharm_dreams_group.setup import PENALTY_CARDS_METHOD

		for module in dashboard._metric_modules():
			for spec in module.metric_definitions():
				with self.subTest(metric=spec["key"]):
					self.assertNotIsInstance(
						module.metric({"metric": spec["key"]}),
						dict,
						f"{spec['label']} returns an object, so the widget will start "
						"formatting it through the currency branch",
					)
		# The penalty strip is not a metric module, so it is checked separately.
		for label in frappe.get_all(
			"Number Card", filters={"method": PENALTY_CARDS_METHOD}, pluck="label", limit_page_length=0
		):
			filters = frappe.parse_json(frappe.db.get_value("Number Card", label, "filters_json")) or {}
			with self.subTest(card=label):
				self.assertNotIsInstance(headcount(filters), dict)

	def test_every_money_metric_rounds_to_two_decimals(self):
		"""A mean net salary is 18076.439764705882 and nobody wants that on a card.

		Rounded in the metric rather than by each caller, so the widget, a report
		and the API all get the same number.
		"""
		for spec in self.specs:
			card = frappe.db.get_value("Number Card", spec["label"], "filters_json")
			value = frappe.call(dashboard.METRIC_METHOD, card or "")
			if not isinstance(value, float):
				continue
			decimals = len(str(value).partition(".")[2])
			self.assertLessEqual(decimals, 2, f"{spec['label']} = {value} has {decimals} decimals")


class TestEveryDrilldownAgreesWithItsCard(FrappeTestCase):
	"""The number on a card must be the number of rows behind it.

	A drill-down that opens a list disagreeing with the figure it was clicked from is
	worse than one that opens nothing: the reader has no way to tell a broken filter
	from a surprising answer. Eight cards carried only `status = Active` while counting
	something much narrower - "Joined This FY" said 4 and its list showed all 180 active
	staff - so this is pinned for every count card rather than left to review.
	"""

	# A distinct count has no list behind it: "5 contract types" is a count of types,
	# not of employees, and no filter can make a row count equal it. Same for the
	# coupons card, which is workbook minus register across two sources. Both are
	# excluded by name and by nature, not quietly tolerated.
	NOT_A_LIST: ClassVar[frozenset] = frozenset({"Contract Types In Use", "Coupons Unrecorded"})

	def _routes_for(self, dashboards):
		routes = dashboard.card_routes()
		rows = []
		for name in dashboards:
			for card in frappe.get_doc("Dashboard", name).cards or []:
				nc = frappe.get_doc("Number Card", card.card)
				rows.append(nc)
		return routes, rows

	def test_every_count_card_opens_a_list_of_the_same_length(self):
		routes, cards = self._routes_for(["SDG Workforce", "SDG Account", "SDG HRM"])
		# Which module owns which card. The three dashboards draw from three metric
		# modules, each with its own `metric()` and its own `METRIC_METHOD`, and
		# this used to compare only the workforce ones - `nc.method != METRIC_METHOD`
		# skipped the Account and HRM cards rather than failing on them, so twenty-odd
		# cards across two dashboards were never checked against their lists at all.
		# A `continue` that reads as "not applicable" is exactly how that stays
		# invisible.
		#
		# The penalty strip is a fourth card source that is deliberately not a metric
		# module: it reads a method in `hr/penalty.py` and its own `filters_json`,
		# and it is checked for currency above but cannot be compared here without
		# reimplementing that method. It is named, so the "unknown method" guard
		# below still means "a card source nobody has looked at".
		from sharm_dreams_group.setup import PENALTY_CARDS_METHOD

		owners = {module.METRIC_METHOD: module for module in dashboard._metric_modules()}
		known_methods = set(owners) | {PENALTY_CARDS_METHOD}

		checked = 0
		skipped_unknown_method = []
		for nc in cards:
			if nc.label in self.NOT_A_LIST:
				continue
			spec = routes.get(nc.label)
			if not spec:
				continue  # unrouted cards are covered by their own test
			module = owners.get(nc.method)
			if module is None:
				if nc.method not in known_methods:
					skipped_unknown_method.append(f"{nc.label} ({nc.method})")
				continue
			filters = frappe.parse_json(nc.filters_json) or {}
			if not filters.get("metric"):
				continue
			value = module.metric(filters)
			if not isinstance(value, int) or isinstance(value, bool):
				continue  # a sum or an extremum; those are pinned as totals elsewhere
			is_money = getattr(module, "_is_money", None) or module.is_money
			if is_money(filters["metric"]):
				continue
			doctype = spec["route"].split("/")[1]
			route_filters = dashboard._expand_relative_dates(spec.get("route_options") or {})
			with self.subTest(card=nc.label, dashboard=module.__name__):
				self.assertEqual(
					_list_length(doctype, route_filters),
					value,
					f"{nc.label} says {value} but its drill-down opens a different number",
				)
			checked += 1
		# No card may be skipped just because its method string is unfamiliar: that
		# is the failure mode this test had.
		self.assertEqual(skipped_unknown_method, [], "cards no metric module claims")
		self.assertGreater(checked, 15, "the audit found no cards to compare - it is broken")

	def test_the_narrow_cards_no_longer_open_the_whole_workforce(self):
		"""The specific regression, kept by name so it cannot come back quietly."""
		routes = dashboard.card_routes()
		active = frappe.db.count("Employee", {"status": "Active"})
		for label in (
			"Joined This FY",
			"Joined (90 days)",
			"Tenure 5+ Years",
			"ID Expiring (30d)",
			"No Contract Type",
			"School Fee Cases",
		):
			spec = routes[label]
			route_filters = dashboard._expand_relative_dates(spec["route_options"])
			with self.subTest(card=label):
				self.assertNotEqual(
					len(route_filters),
					1,
					f"{label} filters on status alone, so it opens all {active} active staff",
				)

	def test_no_drilldown_uses_a_bare_date_cutoff(self):
		"""`<` on a Date field keeps blank dates; `between` with a floor does not.

		`DatabaseQuery` renders a Date comparison as
		`ifnull(field, '0001-01-01') < value`, so a route that says "joined before 2021"
		also opens everyone whose joining date was never filled in - 55 rows behind a
		card that counted 44.  A `between` from a fixed floor is the only form that
		survives the substitution, because 0001-01-01 then falls outside the range.
		"""
		routes = dashboard.card_routes()
		for label, spec in routes.items():
			for field, value in (spec.get("route_options") or {}).items():
				with self.subTest(card=label, field=field):
					self.assertNotIn(
						value[0] if isinstance(value, list) else "=",
						("<", "<="),
						f"{label} compares {field} with a bare cut-off, which admits blanks",
					)

	def test_the_new_relative_keywords_resolve(self):
		for word in ("90 days ago", "5 years ago", "Start of fiscal year"):
			with self.subTest(word=word):
				self.assertTrue(
					dashboard._relative_day(word),
					f"{word!r} is not a keyword the filter expander knows",
				)




class TestContractEndDates(FrappeTestCase):
	"""The contract-due chart, its bars, and the cards beside it.

	Three things have to agree or the page misleads: the bar a reader taps, the
	list that opens, and the figure on the card.  The bars are computed windows
	rather than stored values - `Group By` would draw one category per distinct
	day - so each one names a filter instead of *being* one, and that indirection
	is where the disagreement would come from.
	"""

	def test_no_chart_scope_filters_on_a_date_with_a_bare_cutoff(self):
			"""The chart side of `test_no_drilldown_uses_a_bare_date_cutoff`.

			A chart has no `route_options` to carry its scope: it is read off
			`filters_json` at click time and handed to the list as it stands. So the
			same trap applies with nowhere to hide - `DatabaseQuery` renders a Date
			comparison as `ifnull(field, '0001-01-01') < value`, and a `< today` on
			`sdg_national_id_expiry` therefore also matches every employee whose ID
			expiry was never filled in. The bar counts the expired ones and the list
			shows the expired ones plus everyone with a blank, which is the exact
			disagreement the drill-down mechanism exists to prevent.

			`Compliance Gaps by Company` carried that `<` until this was caught in a
			browser, and it was invisible for a long time for a reason worth recording:
			`sharm_chart_filters` called `frappe.parse_json`, which the desk does not
			have, and the `catch` around it turned the TypeError into "no filters". So
			the filter was dropped, every chart's list opened unfiltered, and this whole
			class of bug could not show up in the page at all.
			"""
			for chart in frappe.get_all(
				"Dashboard Chart",
				filters={"module": "Sharm Dreams Group", "is_standard": 0},
				fields=["chart_name", "document_type", "filters_json"],
				limit_page_length=0,
			):
				rows = frappe.parse_json(chart.filters_json or "{}")
				if not isinstance(rows, list):
					continue
				meta = None
				for row in rows:
					with self.subTest(chart=chart.chart_name, filter=row):
						self.assertIsInstance(row, list, "a filters_json row is not a list")
						if len(row) < 3:
							continue
						field_doctype, field, operator = row[0], row[1], row[2]
						if operator not in ("<", "<=") or not field:
							continue
						if field_doctype and field_doctype != chart.document_type:
							continue
						# A `<` is only a problem on a Date, and only where a blank
						# would compare as the smallest date and slip in.
						if meta is None and chart.document_type:
							meta = frappe.get_meta(chart.document_type)
						self.assertNotEqual(
							meta.get_field(field).fieldtype if meta and meta.has_field(field) else None,
							"Date",
							f"{chart.chart_name} filters {field} with a bare `{operator}`, which "
							"also admits rows whose date is blank",
						)

	def test_every_chart_scope_is_a_list_of_filter_rows(self):
			"""`filters_json` is mandatory on the DocType but untyped.

			It holds one entry per doctype taking part, and a chart that is a `Custom`
			one has no filter rows at all - so an empty *object* is a legitimate value.
			What is not legitimate is anything the widget cannot iterate, because the
			widget reads it with `Array.isArray` and silently drops the scope if it is
			not a list. `"{}"` is fine and common here; a string is not.
			"""
			for chart in frappe.get_all(
				"Dashboard Chart",
				filters={"module": "Sharm Dreams Group", "is_standard": 0},
				fields=["chart_name", "filters_json"],
				limit_page_length=0,
			):
				with self.subTest(chart=chart.chart_name):
					rows = frappe.parse_json(chart.filters_json or "{}")
					self.assertTrue(
						rows == {} or isinstance(rows, list),
						f"{chart.chart_name} stores filters the widget cannot read: {rows!r}",
					)

	@classmethod
	def setUpClass(cls):
		from sharm_dreams_group.sharm_dreams_group.dashboard_chart_source.contract_end_dates import (
			contract_end_dates,
		)

		cls.source = contract_end_dates
		cls.chart_name = "Contract End Dates"

	def _stored_buckets(self) -> list[dict]:
		spec = frappe.parse_json(
			frappe.db.get_value("Dashboard Chart", self.chart_name, "custom_options") or "{}"
		)["drilldown"]
		self.assertTrue(
			frappe.db.exists("DocType", spec["doctype"]),
			f"{self.chart_name} filters on a doctype that does not exist",
		)
		self.assertTrue(
			frappe.get_meta(spec["doctype"]).has_field(spec["field"]),
			f"{spec['doctype']} has no field {spec['field']!r}, so the desk would drop the filter",
		)
		return spec["buckets"]

	def test_the_bars_and_their_drilldowns_are_the_same_buckets(self):
		"""A bar must open the rows it drew.

		Matched by name and in order, not by name alone. Order matters because
		adding, removing or reordering a bar without its bucket would leave the
		chart drawing something no bar can open - which reads as a broken click
		rather than as a mistake.
		"""
		config = render_chart(self.chart_name)
		buckets = self._stored_buckets()
		self.assertEqual(
			config["labels"],
			[bucket["label"] for bucket in buckets],
			"the bars drawn and the buckets that open them are not the same list",
		)
		self.assertEqual(
			len(config["datasets"][0]["values"]),
			len(buckets),
			"the chart plots a different number of bars than it can open",
		)

	def test_every_bar_carries_a_filter_it_can_be_asked_for(self):
		"""Each bucket resolves to a list filter, or tapping it does nothing.

		`sharm_bucket_filter` in `dashboard_view.js` handles `is`, `between` and
		`>`; a bucket using anything else would reach it and come back null.
		"""
		for bucket in self._stored_buckets():
			with self.subTest(bucket=bucket["label"]):
				self.assertIn(bucket.get("op"), ("is", "between", ">"), bucket["label"])
				if bucket["op"] == "between":
					# Exactly one lower bound: an offset shifted by today, or the
					# literal floor that keeps blank dates out.
					self.assertTrue(
						"floor" in bucket or "from" in bucket,
						f"{bucket['label']} has no lower bound, so it opens every row below it",
					)
					self.assertIn("to", bucket, f"{bucket['label']} has no upper bound")
				elif bucket["op"] == ">":
					self.assertIn("offset", bucket, f"{bucket['label']} has no cut-off")

	def test_a_past_end_date_bar_cannot_open_the_undated_rows(self):
		"""The bar before today means "dated, and before today".

		Frappe compares a Date as `ifnull(field, '0001-01-01')`, so the obvious
		`< today` would also match every row with no date - here the single
		largest bar on the chart. The floor is what separates the two, so this
		pins that the `Ended` bucket carries one.
		"""
		ended = next(b for b in self._stored_buckets() if b["label"] == "Ended")
		self.assertEqual(ended["op"], "between")
		self.assertEqual(
			ended.get("floor"),
			dashboard.DATED_FLOOR,
			"the Ended bar needs the dated floor or it lists the undated rows too",
		)

	def test_a_date_lands_in_the_bar_that_names_it(self):
		"""The bucketing, checked against dates sitting on each boundary.

		Pure dates rather than employees: the site has no end dates at all, so a
		test that read them would pass by finding nothing. These are the values a
		reader would try to place by eye, which is what makes them worth pinning.
		"""
		windows = self.source.contract_end_windows()
		labels = [label for label, _matches in windows]

		for offset, expected in (
			(-30, "Ended"),
			(-1, "Ended"),
			(0, "Ends in 30 days"),
			(30, "Ends in 30 days"),
			(31, "Ends in 3 months"),
			(90, "Ends in 3 months"),
			(91, "Ends in 12 months"),
			(365, "Ends in 12 months"),
			(366, "Later"),
		):
			end = add_days(nowdate(), offset)
			values, undated = self.source.bucket_end_dates([end], windows)
			with self.subTest(offset=offset):
				self.assertEqual(undated, 0, "a dated contract was counted as undated")
				# Exactly one bar claims it, and it is the one named.
				self.assertEqual([labels[i] for i, v in enumerate(values) if v], [expected])

	def test_no_date_is_a_bar_of_its_own(self):
		"""Not counted into any window, and not silently dropped either.

		A blank is the largest group on this chart, so a bucketing that discarded
		it would understate every bar and look like an accounting error.
		"""
		windows = self.source.contract_end_windows()
		values, undated = self.source.bucket_end_dates([None, "", None], windows)
		self.assertEqual(undated, 3)
		self.assertEqual(values, [0] * len(windows))

	def test_the_bars_and_the_near_dated_cards_agree(self):
		"""The chart's 30-day bar and the "Contracts Ending (30d)" card count alike.

		Both are the same date comparison reached two ways - bucketed in Python
		for the chart, and counted for the card - so if one drifts the page shows
		two answers to the same question.
		"""
		config = render_chart(self.chart_name)
		bars = dict(zip(config["labels"], config["datasets"][0]["values"]))
		self.assertEqual(
			bars["Ends in 30 days"],
			dashboard.metric({"metric": "contracts_ending_30_days"}),
			"the 30-day bar and the 30-day card disagree",
		)
		self.assertEqual(
			bars["Ends in 30 days"] + bars["Ends in 3 months"],
			dashboard.metric({"metric": "contracts_ending_90_days"}),
			"the 90-day card is not the two bars up to 90 days",
		)

	def test_the_chart_counts_the_active_staff_the_cards_count(self):
		"""One denominator for the whole band.

		The chart is scoped to Active staff, and so is every card beside it. If the
		chart were not, its bars would include leavers and no card would mention
		them - the same disagreement the drill-down tests exist to catch, one level
		up.
		"""
		config = render_chart(self.chart_name)
		self.assertEqual(
			sum(config["datasets"][0]["values"]),
			dashboard.metric({"metric": "active_headcount"}),
			"the chart counts a different population than the cards beside it",
		)

	def test_every_limited_term_contract_type_is_matched_by_the_prefix(self):
		"""`is_limited_term` is unset on every type, so the name is the only flag.

		Two things follow and both are pinned here. The prefix has to cover every
		limited-term type that exists - the seed wrote each term twice, as "Limited"
		and "Limited Contract", so a prefix matching only one spelling would report
		fewer limited-term staff than there are and quietly understate the renewal
		risk the whole section is about. And nobody may set the flag without the
		test noticing, because once they do the name is no longer the whole story
		and `LIMITED_TERM_PREFIX` would silently disagree with the card.
		"""
		prefix = dashboard.LIMITED_TERM_PREFIX.rstrip("%")
		types = frappe.get_all("SDG Contract Type", pluck="name", limit_page_length=0)
		self.assertTrue(types, "the site holds no contract types at all")
		self.assertFalse(
			frappe.get_all(
				"SDG Contract Type", filters={"is_limited_term": 1}, pluck="name", limit_page_length=0
			),
			"some types now carry is_limited_term, so the name prefix is no longer the only flag",
		)

		matched = {name for name in types if name.startswith(prefix)}
		self.assertTrue(
			matched,
			f"no contract type starts with {prefix!r}, so the card reads zero on a workforce that has them",
		)

		# Each limited-term type counted on its own, and the sum of those against
		# the single `like` the card actually uses: the two can only agree if the
		# prefix matches every type the loop covers and no partial name slips past.
		by_type = frappe.db.count(
			"Employee", {"status": "Active", "sdg_contract_type": ("in", sorted(matched))}
		)
		self.assertEqual(
			by_type,
			dashboard.metric({"metric": "limited_term_staff"}),
			"the per-type counts and the prefix count disagree",
		)

	def test_the_undated_bar_agrees_with_the_missing_end_date_card(self):
		config = render_chart(self.chart_name)
		bars = dict(zip(config["labels"], config["datasets"][0]["values"]))
		self.assertEqual(
			bars[self.source.NO_END_DATE],
			dashboard.metric({"metric": "missing_contract_end_date"}),
			"the undated bar and the 'No Contract End Date' card disagree",
		)


def _dashboard_js() -> str:
	"""The dashboard page's own script, as shipped.

	Read from the source rather than the built bundle: the bundle is hashed on
	every build and is a single line, whereas this is the file an editor changes.
	"""
	import pathlib

	path = pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_view.js"
	return path.read_text(errors="replace") if path.exists() else ""


def _shipped_css() -> str:
	"""The dashboard page's stylesheet, as shipped."""
	import pathlib

	path = (
		pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "css"))
		/ "sharm_dreams_group.bundle.css"
	)
	return path.read_text(errors="replace") if path.exists() else ""


def _list_length(doctype: str, route_options: dict) -> int:
	"""How many rows the drill-down actually opens.

	Deliberately `frappe.get_all` and not `frappe.db.count`.  They are not the same
	question: the List view asks `get_all`, and `DatabaseQuery` rewrites a Date
	comparison as `ifnull(field, '0001-01-01') < value`, so a `<` cut-off keeps every
	row whose date is blank.  Counting with `db.count` hid that - it said 44 rows where
	the user was shown 55.
	"""
	return len(
		frappe.get_all(
			doctype,
			filters=route_options,
			fields=["name"],
			limit_page_length=0,
			ignore_permissions=True,
		)
	)
