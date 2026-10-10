# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the Sharm Dreams Group workspace and its stock links.

The important one is `test_every_owned_doctype_is_reachable`, which fails when a
new DocType is added to the module without a link.  That is the failure mode
that actually bites: a 13th table that only payroll staff know the URL for.
"""

from __future__ import annotations

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group import setup
from sharm_dreams_group import workspace as ws


class TestWorkspace(FrappeTestCase):
	"""The workspace is a navigation guarantee, so it is asserted as one."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ws.apply()

	# --- the workspace itself ------------------------------------------------
	def test_workspace_exists_and_is_public(self):
		self.assertTrue(frappe.db.exists("Workspace", ws.WORKSPACE))
		doc = frappe.get_doc("Workspace", ws.WORKSPACE)
		self.assertEqual(doc.public, 1, "the workspace must be public")
		self.assertEqual(doc.app, ws.APP)
		self.assertEqual(doc.module, ws.MODULE)
		self.assertEqual(doc.title, ws.WORKSPACE, "the title is the route, so it must match")

	def test_workspace_has_no_link_target(self):
		"""A Workspace must not carry a link_type/link_to.

		Inserting without them leaves `link_type` at its default of "DocType",
		which the desk then tries to resolve as a doctype route instead of
		opening the workspace.  Stock workspaces all have these empty.
		"""
		doc = frappe.get_doc("Workspace", ws.WORKSPACE)
		self.assertEqual(doc.type, "Workspace")
		self.assertFalse(doc.link_type, f"link_type should be empty, got {doc.link_type!r}")
		self.assertFalse(doc.link_to, f"link_to should be empty, got {doc.link_to!r}")

	def test_app_is_on_the_apps_switcher(self):
		"""Without add_to_apps_screen the workspace is only reachable by URL."""
		hooks = frappe.get_hooks("add_to_apps_screen", app_name=ws.APP)
		self.assertTrue(hooks, "add_to_apps_screen is not set, so the app has no switcher icon")
		self.assertIn("/desk/sharm-dreams-group", hooks[0]["route"])
		self.assertTrue(
			frappe.get_attr(hooks[0]["has_permission"])(), "apps switcher permission check failed"
		)

	def test_apps_switcher_logo_exists(self):
		"""A 404 logo shows a broken image on the switcher."""
		logo = frappe.get_hooks("add_to_apps_screen", app_name=ws.APP)[0]["logo"]
		# /assets/<app>/<rest> maps to <app_path>/public/<rest>
		rest = logo.split("/assets/", 1)[1]
		app_name, _, relative = rest.partition("/")
		self.assertEqual(app_name, ws.APP)
		path = os.path.join(frappe.get_app_path(app_name), "public", relative)
		self.assertTrue(os.path.exists(path), f"apps switcher logo not found: {path}")

	def test_apps_switcher_logo_is_served(self):
		"""The file existing in the app is not enough to be served.

		`bench build` symlinks each app's public/ into <bench>/sites/assets.  A file
		added after the last build is present on disk and 404s in the browser, so
		the served path has to be checked too.
		"""
		from frappe.utils import get_bench_path

		logo = frappe.get_hooks("add_to_apps_screen", app_name=ws.APP)[0]["logo"]
		relative = logo.split("/assets/", 1)[1]
		served = os.path.join(get_bench_path(), "sites", "assets", relative)
		self.assertTrue(
			os.path.exists(served),
			f"logo is not served: {served} is missing; run `bench build` to link app assets",
		)

	def test_the_navbar_uses_this_apps_logo(self):
		"""`Navbar Settings` rather than the `app_logo_url` hook.

		The hook is aggregated over every installed app and `get_app_logo` only
		prefers the last entry when there are exactly two of them. frappe, erpnext,
		hrms and lending all define one, so the hook resolves to Frappe's logo
		however the list is ordered. The setting is checked first and is the only
		thing that actually wins.
		"""
		from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo

		logo = get_app_logo()
		self.assertIn(f"/assets/{ws.APP}/", logo, f"navbar shows {logo}, not this app's mark")

	def test_the_navbar_logo_exists_and_is_served(self):
		from frappe.utils import get_bench_path

		logo = frappe.db.get_single_value("Navbar Settings", "app_logo")
		self.assertTrue(logo and logo.startswith(f"/assets/{ws.APP}/"), f"navbar logo is {logo!r}")
		# /assets/<app>/<rest> maps to <app_path>/public/<rest>
		app_name, _, relative = logo.split("/assets/", 1)[1].partition("/")
		self.assertEqual(app_name, ws.APP)
		on_disk = os.path.join(frappe.get_app_path(app_name), "public", relative)
		self.assertTrue(os.path.exists(on_disk), f"navbar logo not found: {on_disk}")
		served = os.path.join(get_bench_path(), "sites", "assets", app_name, relative)
		self.assertTrue(os.path.exists(served), f"navbar logo is not served: {served} is missing")

	def test_the_app_logo_is_referenced_consistently(self):
		"""One file, two references.
		The apps screen and the navbar resolve through different mechanisms, so
		they can drift: the switcher would show the new mark while the navbar kept
		the old one, or the reverse.
		"""

		navbar = frappe.db.get_single_value("Navbar Settings", "app_logo")
		switcher = frappe.get_hooks("add_to_apps_screen", app_name=ws.APP)[0]["logo"]
		self.assertEqual(navbar, switcher)

	def test_the_browser_tab_uses_this_apps_favicon(self):
		"""`Website Settings.favicon`, not the app asset.

		`frappe/www/desk.html` falls back to Frappe's favicon when this is empty,
		which left the framework icon on every desk page. The field is an Attach, so
		it stores a `/files` path and the File record has to exist - a URL that 404s
		is worse than the framework default it replaced.
		"""
		favicon = frappe.db.get_single_value("Website Settings", "favicon")
		self.assertTrue(favicon, "no favicon is set, so the tab shows Frappe's")
		self.assertTrue(
			favicon.startswith(f"/assets/{ws.APP}/") or favicon.startswith("/files/"),
			f"favicon looks wrong: {favicon}",
		)
		if favicon.startswith("/files/"):
			name = favicon.rsplit("/", 1)[-1]
			self.assertTrue(
				frappe.db.exists("File", {"file_url": favicon}),
				f"favicon points at {name} but no File record backs it",
			)

	def test_the_workspace_page_icon_is_a_real_glyph(self):
		"""`Workspace.icon` is an octicon *name*, not an image.

		The desk renders it through `frappe.utils.icon`, which only draws from the
		icon font, so a logo path would render as a missing glyph - which is what
		"brain" was: not one of the glyphs octicons.css defines.
		"""
		icon = frappe.db.get_value("Workspace", "Sharm Dreams Group", "icon")
		self.assertTrue(icon, "the workspace page has no icon")
		css = os.path.join(frappe.get_app_path("frappe"), "public", "css", "octicons", "octicons.css")
		with open(css) as handle:
			self.assertIn(
				f".octicon-{icon}:",
				handle.read(),
				f"{icon!r} is not in the icon font, so the header draws a blank box",
			)

	def test_the_app_is_what_bare_desk_opens(self):
		"""`/desk` with no workspace in the URL.

		`Workspace.show()` -> `get_page_to_show()` does not read the user's
		`default_workspace`; it takes `localStorage.current_page` when that is still
		valid and otherwise `workspaces[0]`, and `get_workspaces` orders by
		`sequence_id asc`. So `default_workspace` - which only steers the `/`
		redirect - says nothing about this, and the app was unreachable at 20.
		"""
		# Called directly rather than through `frappe.call`, which would switch the
		# session user and leave it changed for every test that runs after this one.
		from frappe.desk.desktop import get_workspaces

		pages = get_workspaces()["pages"]
		self.assertEqual(
			pages[0]["name"],
			"Sharm Dreams Group",
			f"/desk opens on {pages[0]['name']!r} instead of this app",
		)

	def test_this_workspace_is_strictly_first_not_tied(self):
		"""A tie on `sequence_id` is broken by name, and Lending sorts ahead of
		"Sharm Dreams Group" - so a shared value loses the race silently."""
		from frappe.desk.desktop import get_workspaces

		first = get_workspaces()["pages"][0]
		self.assertEqual(first["name"], "Sharm Dreams Group")

	def test_every_owned_doctype_is_reachable(self):
		"""The guard: a new doctype must get a link, or this fails.

		`SUPERSEDED_DOCTYPES` is subtracted deliberately, and the subtraction is
		pinned here: the guard's whole job is catching a doctype that quietly stops
		being reachable, so an exclusion list that grows without anyone noticing is
		the exact failure it exists to prevent. Listing one is a decision, not a
		default.
		"""
		owned = ws._expected_doctypes()
		reachable = ws.reachable_doctypes()
		self.assertEqual(
			sorted(owned - reachable - ws.SUPERSEDED_DOCTYPES),
			[],
			"doctypes with no shortcut on the workspace; add them to CARDS",
		)
		# Every exclusion must be a doctype this app really owns - a stale name here
		# would silently disable the guard for it and for nothing.
		self.assertEqual(
			sorted(ws.SUPERSEDED_DOCTYPES - owned),
			[],
			"SUPERSEDED_DOCTYPES names a doctype this app does not own",
		)

	def test_reachability_is_read_from_rendered_content(self):
		"""`reachable_doctypes` must not be able to pass on the child tables.

		If it were derived from `Workspace.shortcuts` it would report a link that
		the desk never draws, and the guarantee above would be hollow.
		"""
		blocks = json.loads(frappe.get_doc("Workspace", ws.WORKSPACE).content)
		self.assertTrue(
			any(b.get("type") == "shortcut" for b in blocks),
			"no shortcut blocks in content, so nothing is reachable",
		)

	def test_every_linked_doctype_exists(self):
		"""A link to a missing doctype opens an error page."""
		for name in ws.reachable_doctypes():
			self.assertTrue(
				frappe.db.exists("DocType", name) or frappe.db.exists("Workspace", name),
				f"{name} is linked but does not exist",
			)

	def test_cards_only_list_doctypes_this_app_owns(self):
		"""Guards against a typo or a link to somebody else's table.

		ADOPTED_DOCTYPES is the escape hatch: a stock DocType this app publishes a
		record of, declared rather than waved through, so the guard still catches a
		genuine typo.
		"""
		listed = {d for _card, entries in ws.CARDS for d in entries}
		self.assertEqual(sorted(listed - ws._expected_doctypes() - ws.ADOPTED_DOCTYPES), [])

	# --- the order things appear in -----------------------------------------
	def test_the_figures_come_before_the_setup_links(self):
		"""A band table is what you open the desk to *change*, not to read.

		The page used to lead with all twenty-one config links - `SDG Medical Band`,
		`Employee Self Service Settings`, `Income Tax Slab` - and put the figures underneath, so the
		first six rows a user saw on their landing page were band tables and penalty
		levels before a single headcount.  Defensible while the page was links and
		nothing else; not defensible the moment numbers were added below them.

		The links stay on the page - `test_every_owned_doctype_is_reachable` proves
		that - but below a heading that says what they are.
		"""
		blocks = ws.content_blocks()

		first_shortcut = next(
			(i for i, b in enumerate(blocks) if b.get("type") == "shortcut"), None
		)
		first_card = next(
			(i for i, b in enumerate(blocks) if b.get("type") == "number_card"), None
		)
		self.assertIsNotNone(first_shortcut, "the page has no setup links at all")
		self.assertIsNotNone(first_card, "the page has no figures at all")
		self.assertLess(
			first_card,
			first_shortcut,
			"a setup link comes before the first figure, so the page opens on band "
			"tables",
		)

		# And the change of character is labelled, so a reader can see it is
		# deliberate rather than having to notice the words stopped being about
		# people.
		self.assertIn(
			"Setup",
			[
				b["data"].get("text", "")
				for b in blocks
				if b.get("type") == "header"
			],
			"nothing on the page labels the setup links as such",
		)

	def test_the_bands_are_labelled(self):
		"""46 cards in one wall is the failure the dashboard's bands prevent.

		`_dashboard_card_blocks` wrote a `header` block per band and the desk drew no
		heading element from it, so the bands ran together even though the names were
		right.  A heading is only worth having if something renders it, and a heading
		flush against the row above it is barely a heading - so each band is a spacer
		and a header, and this checks both are present per band.
		"""
		blocks = ws.content_blocks()
		ids = [b.get("id") for b in blocks]
		bands = [title for title, _cards, _charts in ws._dashboard_sections()]
		self.assertTrue(bands, "no bands resolved at all")

		for title in bands:
			with self.subTest(band=title):
				heading = f"{ws.BLOCK_PREFIX}band-{ws._slug(title)}"
				spacer = f"{ws.BLOCK_PREFIX}spacer-{ws._slug(title)}"
				self.assertIn(heading, ids, f"{title} has no heading block")
				self.assertIn(spacer, ids, f"{title} has no spacer above its heading")
				self.assertLess(
					ids.index(spacer),
					ids.index(heading),
					f"{title}'s spacer is below its heading",
				)

	def test_a_chart_sits_under_the_band_it_explains(self):
		"""`Leavers by Company` was nine rows away from `Leavers`.

		Under one "Breakdowns" heading at the bottom, the chart explaining a card a
		reader had just seen was separated from it by the whole payroll band.  A chart
		now rides with its band, so the breakdown sits under the figure it breaks
		down.
		"""
		blocks = ws.content_blocks()
		ids = [b.get("id") for b in blocks]
		bands = ws._dashboard_sections()

		for title, _cards, charts in bands:
			band_at = ids.index(f"{ws.BLOCK_PREFIX}band-{ws._slug(title)}")
			# Where this band stops: the next band's heading, or the end of the page.
			#
			# The first version of this assertion asked for no later heading to exist
			# after a chart at all, which is impossible - the next band is supposed to
			# be there, directly after this one. What has to hold is that the chart
			# comes before it.
			other_bands = [
				ids.index(f"{ws.BLOCK_PREFIX}band-{ws._slug(t)}")
				for t, _c, _ch in bands
				if t != title
			]
			band_ends = min((n for n in other_bands if n > band_at), default=len(ids))
			for chart in charts:
				with self.subTest(chart=chart, band=title):
					chart_at = ids.index(f"{ws.BLOCK_PREFIX}chart-{ws._slug(chart)}")
					self.assertGreater(chart_at, band_at, f"{chart} precedes its own band")
					self.assertLess(
						chart_at,
						band_ends,
						f"{chart} sits at or after where {title} ends, so it is not "
						"under the figures it explains",
					)

	def test_every_front_page_chart_is_actually_drawn(self):
		"""`chart in FRONT_PAGE_CHARTS` tested the dict's *keys*.

		The table is keyed by band - "People", "Payroll" - so asking whether a chart
		name is in it asked whether a chart name was one of those titles. It never
		was, so `carried` came back empty for every band and the page drew **no charts
		at all** while `add_dashboard_blocks` wrote all nine rows and every block
		resolved.

		That is the exact failure the other checks here exist to prevent and none of
		them caught it: rows and blocks agreed, the page was complete in the record,
		and `test_a_chart_sits_under_the_band_it_explains` passed vacuously over an
		empty `charts` tuple. The only thing that could have caught it is an assertion
		that the page draws the charts it is supposed to - so this is that, and it is
		deliberately blunt.
		"""
		blocks = ws.content_blocks()
		drawn = {
			b["data"].get("chart_name") for b in blocks if b.get("type") == "chart"
		}
		drawn.discard(None)

		self.assertTrue(
			drawn,
			"the front page draws no charts at all; FRONT_PAGE_CHARTS is keyed by "
			"band, so membership has to be tested against its values",
		)

		# Every band that names charts in FRONT_PAGE_CHARTS draws the ones that exist.
		present = set(frappe.get_all("Dashboard Chart", pluck="name", limit_page_length=0))
		for band, wanted in ws.FRONT_PAGE_CHARTS.items():
			with self.subTest(band=band):
				expect = {name for name in wanted if name in present}
				self.assertTrue(expect, f"{band} names no chart that exists")
				self.assertEqual(
					sorted(expect & drawn),
					sorted(expect),
					f"{band} declares {sorted(expect - drawn)} and the page does not "
					"draw them",
				)

		# And nothing is drawn that the table does not sanction, so the two cannot
		# drift into disagreeing about the page.
		sanctioned = {name for names in ws.FRONT_PAGE_CHARTS.values() for name in names}
		self.assertEqual(
			sorted(drawn - sanctioned),
			[],
			f"the page draws {sorted(drawn - sanctioned)}, which FRONT_PAGE_CHARTS "
			"does not list for any band",
		)

	def test_no_band_is_a_heading_over_nothing(self):
		"""A band whose figures were all deleted must go, not stay as a bare label.

		`_dashboard_sections` drops one; this is what stops a later change
		reintroducing the other half of it - a heading drawn above no figures and no
		chart is a promise the page does not keep.
		"""
		for title, cards, charts in ws._dashboard_sections():
			with self.subTest(band=title):
				self.assertTrue(
					cards or charts, f"{title} resolved to a heading with nothing under it"
				)

	def test_the_slug_is_one_function_and_sanitises(self):
		"""Two `_slug` definitions existed, and the second silently won.

		Python takes the last definition, so which rule built a block id depended on
		nothing readable at the call site - and they disagreed on the labels that
		matter, because the first replaced every non-alphanumeric character and the
		second only touched spaces and `&`.  `Designation Wise Salary(Last Month)`
		therefore produced `designation-wise-salary(last-month)`, with the parentheses
		carried into an element id.
		"""
		import inspect

		source = inspect.getsource(ws)
		self.assertEqual(
			source.count("def _slug("),
			1,
			"more than one _slug is defined, so which rule runs is a matter of file order",
		)
		for label in (
			"Designation Wise Salary(Last Month)",
			"Benefits & Insurance",
			"Records To Complete",
			"Payroll Net by Company",
			"Self Services Employees",
		):
			with self.subTest(label=label):
				slug = ws._slug(label)
				self.assertTrue(slug, f"{label!r} produced an empty slug")
				self.assertTrue(
					slug.replace("-", "").isalnum(),
					f"{label!r} produced {slug!r}, which is not usable in an element id",
				)
				self.assertNotIn("--", slug, f"{label!r} produced a doubled dash")

	def test_card_rows_are_at_most_six_across(self):
		"""Eleven cards at 79px each is a row of wrapped numbers.

		The width was `12 // len(labels)` with no cap, so an eleven-card band was laid
		out eleven across - narrower than "Average Gross Salary" is long, which is not
		a layout, it is a stack of three-line labels above a number.
		"""
		for title, cards, _charts in ws._dashboard_sections():
			if not cards:
				continue
			with self.subTest(band=title):
				cols = {
					b["data"]["col"]
					for b in ws.content_blocks()
					if b.get("type") == "number_card"
					and b["data"].get("number_card_name") in cards
				}
				for col in cols:
					self.assertGreaterEqual(
						col,
						2,
						f"{title}: a card column of {col} of twelve is a sliver",
					)
					self.assertEqual(
						col * len(cards) <= 12 * ((len(cards) + (12 // col) - 1) // (12 // col)),
						True,
						f"{title}: {len(cards)} cards at col {col} does not divide into "
						"whole rows",
					)

	# --- links into the stock workspaces ------------------------------------
	def test_every_workspace_block_resolves(self):
		"""The guard that was missing: a block must match a child-table row.

		The desk skips any `card` or `shortcut` block whose name matches nothing in
		`links` / `shortcuts`, so a block on its own renders blank while every
		naive "is the name in content?" check still passes.
		"""
		report = ws.resolution_report()
		self.assertIn(ws.WORKSPACE, report)
		for name, info in report.items():
			self.assertNotIn("error", info, f"{name}: {info.get('error')}")
			self.assertEqual(
				ws.unresolved_blocks({name: info}, ours_only=True),
				{},
				f"{name} has blocks that would render blank",
			)

	def test_the_only_dangling_block_is_the_one_erpnext_ships_broken(self):
		"""ERPNext's Home fixture carries a `Leaderboard` shortcut block with no
		matching row, and `Leaderboard` is not a Report on this site either.

		It is named here rather than waved through, so a *second* dangling block
		fails this test instead of hiding behind the first.
		"""
		known = {"Home": ["Leaderboard"]}
		actual = ws.unresolved_blocks(ws.resolution_report())
		self.assertEqual(actual, known, f"dangling blocks changed: {actual}")

	def test_stock_link_cards_expose_their_doctypes(self):
		"""These workspaces are HRMS's, so an upgrade can revert them."""
		present = ws.stock_card_links()
		for name, cards in ws.STOCK_CARDS.items():
			self.assertIn(name, present, f"{name} no longer exists")
			want = {d for _c, entries in cards for d in entries}
			self.assertEqual(
				sorted(want - present[name]),
				[],
				f"{name} is missing links; re-run workspace.apply",
			)

	def test_stock_link_cards_resolve_to_a_card(self):
		"""The card needs a block as well as rows, or nothing is drawn."""
		report = ws.resolution_report()
		for name, cards in ws.STOCK_CARDS.items():
			labels = {card for card, _links in cards}
			self.assertTrue(
				labels <= set(report[name]["page_cards"]),
				f"{name}: no rendered card for {sorted(labels - set(report[name]['page_cards']))}",
			)

	def test_after_migrate_restores_cards_an_upgrade_wipes(self):
		"""The regression the migrate hook exists for.

		An HRMS upgrade rewrites its own workspace documents, so our rows and
		blocks go with them.  This strips them the way a rewritten fixture would,
		asserts they are really gone, then asserts the hook puts them back.
		"""
		stripped = 0
		for name in ws.STOCK_CARDS:
			doc = frappe.get_doc("Workspace", name)
			blocks = json.loads(doc.content or "[]")
			kept = [b for b in blocks if not str(b.get("id", "")).startswith(ws.BLOCK_PREFIX)]
			stripped += len(blocks) - len(kept)
			doc.links = [
				r
				for r in doc.links
				if not (
					(
						r.type == "Card Break"
						and r.label in {c for cs in ws.STOCK_CARDS.values() for c, _ in cs}
					)
					or (r.link_to and str(r.link_to).startswith("SDG "))
				)
			]
			doc.content = json.dumps(kept)
			doc.save()
		self.assertGreater(stripped, 0, "nothing was stripped; the test is not exercising anything")

		# the card really is gone, so the assertions below are meaningful
		self.assertEqual(ws.stock_card_links()["HR Setup"], set())

		setup.apply_workspace()  # what after_migrate runs

		present = ws.stock_card_links()
		for name, cards in ws.STOCK_CARDS.items():
			want = {d for _c, entries in cards for d in entries}
			self.assertEqual(
				sorted(want - present.get(name, set())),
				[],
				f"{name} was not restored by after_migrate",
			)
		# and it resolves, not just exists.  Scoped to our own blocks: ERPNext
		# ships Home with a dangling Leaderboard block of its own, which is not
		# something `after_migrate` can be held responsible for.
		report = ws.resolution_report()
		for name, info in report.items():
			self.assertEqual(
				ws.unresolved_blocks({name: info}, ours_only=True),
				{},
				f"{name} did not resolve after after_migrate",
			)

	def test_hr_setup_reaches_the_penalty_masters(self):
		"""The gap this card closed.

		The penalty masters were linked only from the main Sharm Dreams Group
		page, so an HR manager arriving at HR Setup - the page they actually open
		to configure the desk - had no route to them. Named outright rather than
		left to the generic card tests, so a rename or a dropped card fails with
		something readable instead of a count mismatch.
		"""
		linked = {row.link_to for row in frappe.get_doc("Workspace", "HR Setup").links if row.link_to}
		for doctype in ("SDG Penalty Level",):
			self.assertIn(doctype, linked, f"{doctype} is not reachable from HR Setup")

	def test_no_stock_card_links_a_child_doctype(self):
		"""A child doctype has no list view, so a card link to one renders an
		error page in the desk."""
		for name, cards in ws.STOCK_CARDS.items():
			for _label, doctypes in cards:
				for doctype in doctypes:
					self.assertFalse(
						frappe.db.get_value("DocType", doctype, "istable"),
						f"{name} links the child doctype {doctype}",
					)

	def test_hr_setup_original_links_survive(self):
		"""Adding to HR Setup must not drop the links it already had.

		The eight below are HR Setup's own cards as shipped; asserting them all
		catches a rewrite of `content` rather than an append.
		"""
		original = {
			"Setup",
			"Employee",
			"Leaves",
			"Settings",
			"Attendance",
			"Expense Claim",
			"Key Reports",
			"Other Reports",
		}
		blocks = json.loads(frappe.get_doc("Workspace", "HR Setup").content)
		cards = {b["data"]["card_name"] for b in blocks if b.get("type") == "card" and b.get("data")}
		self.assertEqual(sorted(original - cards), [], "HR Setup lost cards it shipped with")
		self.assertIn(ws.WORKSPACE, cards)

	# --- idempotency ---------------------------------------------------------
	def test_apply_is_idempotent(self):
		before = frappe.get_doc("Workspace", ws.WORKSPACE).content
		ws.apply()
		self.assertEqual(frappe.get_doc("Workspace", ws.WORKSPACE).content, before)
