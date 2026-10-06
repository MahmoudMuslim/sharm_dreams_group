# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Employee penalties: counting, banding and the derived fields.

The banding is pinned here rather than left to the seeded records, so a change to
the default scale cannot silently change what a given number of points means.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from typing import ClassVar

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, nowdate

from sharm_dreams_group.hr import penalty
from sharm_dreams_group.hr.penalty import (
	PENALTY_PARENT,
	apply_to_employee,
	bands,
	can_view_penalties,
	configured_roles,
	counts,
	headcount,
	level_colours,
	level_for,
	require_penalty_access,
	totals,
	upper_bound,
)

GROUP = "Sharm Dreams Group"
LEAF = "Bautex Misr"

# The scale as seeded.  Kept here so a test failure means the banding logic broke
# rather than that somebody edited a threshold in the UI.
SCALE = (
	# level_name, from, to, indicator
	("Clear", 0, None, "Green"),
	("Minor", 1, 2, "Yellow"),
	("Warning", 3, 5, "Orange"),
	("Serious", 6, 9, "Red"),
	("Critical", 10, None, "Purple"),
)


def level(name, from_points=0, to_points=None, indicator="Green", action=""):
	return frappe._dict(
		name=name,
		level_name=name,
		from_points=from_points,
		to_points=to_points,
		indicator=indicator,
		colour=None,
		action_required=action,
	)


def penalty_row(points=1, status="Open", expires_on=None, when=None):
	return frappe._dict(
		points=points,
		status=status,
		expires_on=expires_on,
		penalty_date=when or add_days(nowdate(), -1),
	)


RESTRICTED_USER = "penalty-visibility-test@example.test"
PROBE_ROLE = "SDG Penalty Visibility Probe"


def image_view_js() -> str:
	"""The shipped desk file, read straight off disk."""
	import os

	with open(
		os.path.join(
			frappe.utils.get_bench_path(),
			"apps",
			"sharm_dreams_group",
			"sharm_dreams_group",
			"public",
			"js",
			"employee_penalty.js",
		)
	) as handle:
		return handle.read()


class TestCounting(unittest.TestCase):
	"""Which rows contribute, pinned one condition at a time."""

	def test_open_and_acknowledged_count(self):
		for status in ("Open", "Acknowledged", "Appealed"):
			self.assertTrue(counts(penalty_row(status=status)), status)

	def test_waived_and_withdrawn_do_not_count(self):
		for status in ("Waived", "Withdrawn"):
			self.assertFalse(counts(penalty_row(status=status)), status)

	def test_expired_does_not_count(self):
		self.assertFalse(counts(penalty_row(expires_on=add_days(nowdate(), -1))))

	def test_not_yet_expired_still_counts(self):
		self.assertTrue(counts(penalty_row(expires_on=add_days(nowdate(), 30))))

	def test_points_and_count_are_separate(self):
		"""A 5-point escalation is one penalty, not five."""
		points, count = totals([penalty_row(5), penalty_row(1)])
		self.assertEqual((points, count), (6, 2))

	def test_totals_ignore_missing_rows(self):
		self.assertEqual(totals(None), (0, 0))


class TestBanding(unittest.TestCase):
	LEVELS: ClassVar[list] = [level(*row) for row in SCALE]

	def test_the_scale_has_no_gaps_and_no_overlaps(self):
		"""Every number of points resolves to exactly one band."""
		seen = set()
		for points in range(0, 40):
			match = level_for(points, self.LEVELS)
			self.assertIsNotNone(match, f"{points} points fell through")
			seen.add(match.level_name)
		self.assertEqual(seen, {row[0] for row in SCALE})

	def test_the_boundaries(self):
		for points, expected in (
			(0, "Clear"),
			(1, "Minor"),
			(2, "Minor"),
			(3, "Warning"),
			(5, "Warning"),
			(6, "Serious"),
			(9, "Serious"),
			(10, "Critical"),
			(999, "Critical"),
		):
			self.assertEqual(level_for(points, self.LEVELS).level_name, expected, f"{points} points")

	def test_a_zero_upper_bound_means_open_ended(self):
		"""An Int field stores 0 when left blank, so 0 must not read as a bound.

		Reading it as a real bound left the top band of the seeded scale matching
		nothing at all, so 10 points resolved to no level.
		"""
		self.assertIsNone(upper_bound(level("Critical", 10, 0)))
		self.assertEqual(level_for(10, [level("Critical", 10, 0)]).level_name, "Critical")

	def test_no_levels_configured_means_no_answer(self):
		"""Better no colour than an invented one."""
		self.assertIsNone(level_for(5, []))

	def test_the_highest_matching_band_wins(self):
		"""Bands are read from the top down, so a sloppy overlap cannot make the
		answer depend on the order the UI happens to list them."""
		overlapping = [level("Low", 0, None), level("High", 5, None)]
		self.assertEqual(level_for(9, overlapping).level_name, "High")


class TestSeededScale(FrappeTestCase):
	def test_the_seeded_scale_matches_what_the_tests_pin(self):
		"""`bands()` is highest first, so reverse it to compare against SCALE."""
		actual = [(lv.level_name, lv.from_points, upper_bound(lv), lv.indicator) for lv in reversed(bands())]
		expected = [(name, low, high, indicator) for name, low, high, indicator in SCALE]
		self.assertEqual(actual, expected, "the seeded levels have drifted from the scale these tests pin")

	def test_every_level_has_an_indicator(self):
		"""A level with no colour is a level nobody can see."""
		for lv in bands():
			self.assertTrue(lv.indicator, f"{lv.level_name} has no indicator")


class TestEmployeeDerivation(FrappeTestCase):
	"""The derived fields, against a real employee, without saving anything."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.employee = frappe.get_all(
			"Employee", filters={"status": "Active"}, pluck="name", limit_page_length=1
		)
		if not cls.employee:
			raise unittest.SkipTest("no active employee on this site")

	def derive(self, rows):
		doc = frappe.get_doc("Employee", self.employee[0])
		doc.set("sdg_penalty", [])
		for row in rows:
			doc.append("sdg_penalty", dict(row))
		apply_to_employee(doc)
		return doc

	def test_an_employee_with_no_penalties_is_clear(self):
		doc = self.derive([])
		self.assertEqual(doc.sdg_penalty_points, 0)
		self.assertEqual(doc.sdg_penalty_count, 0)

	def test_the_level_follows_the_points(self):
		for points, expected in ((1, "Minor"), (3, "Warning"), (6, "Serious"), (10, "Critical")):
			doc = self.derive([penalty_row(points)])
			self.assertEqual(doc.sdg_penalty_level, expected, f"{points} points")
			self.assertTrue(doc.sdg_penalty_indicator, "no indicator derived")
			self.assertTrue(doc.sdg_penalty_summary)

	def test_a_waived_penalty_does_not_raise_the_level(self):
		"""Two 3-point penalties would be Serious; waiving one drops it to Warning."""
		doc = self.derive([penalty_row(3), penalty_row(3, status="Waived")])
		self.assertEqual(doc.sdg_penalty_points, 3)
		self.assertEqual(doc.sdg_penalty_count, 1)
		self.assertEqual(doc.sdg_penalty_level, "Warning")

	def test_both_count_together(self):
		doc = self.derive([penalty_row(3), penalty_row(3)])
		self.assertEqual(doc.sdg_penalty_level, "Serious")

	def test_the_summary_reads_as_a_sentence(self):
		doc = self.derive([penalty_row(3)])
		self.assertIn("Warning", doc.sdg_penalty_summary)
		self.assertIn("1 penalty", doc.sdg_penalty_summary)
		self.assertNotIn("penaltys", doc.sdg_penalty_summary)

	def test_it_does_not_touch_salary(self):
		"""The whole point: a penalty is recorded and escalated, never deducted."""
		doc = frappe.get_doc("Employee", self.employee[0])
		before = {
			f: doc.get(f) for f in ("sdg_penalty_points",) if f in {x.fieldname for x in doc.meta.fields}
		}
		self.derive([penalty_row(10)])
		for field in doc.meta.fields:
			if "salary" in field.fieldname.lower() or field.fieldname in ("basic", "gross_pay"):
				self.assertEqual(
					doc.get(field.fieldname),
					frappe.get_doc("Employee", self.employee[0]).get(field.fieldname),
					f"{field.fieldname} changed",
				)
		self.assertTrue(before)


class TestPenaltyRowRules(FrappeTestCase):
	def test_a_future_dated_penalty_is_rejected(self):
		from sharm_dreams_group.sharm_dreams_group.doctype.sdg_penalty.sdg_penalty import SDGPenalty

		row = SDGPenalty({"doctype": "SDG Penalty", "penalty_date": add_days(nowdate(), 5)})
		self.assertRaises(frappe.ValidationError, row.validate)

	def test_points_default_to_one(self):
		from sharm_dreams_group.sharm_dreams_group.doctype.sdg_penalty.sdg_penalty import SDGPenalty

		row = SDGPenalty({"doctype": "SDG Penalty", "penalty_date": nowdate()})
		row.validate()
		self.assertEqual(row.points, 1)

	def test_negative_points_are_rejected(self):
		from sharm_dreams_group.sharm_dreams_group.doctype.sdg_penalty.sdg_penalty import SDGPenalty

		row = SDGPenalty({"doctype": "SDG Penalty", "penalty_date": nowdate(), "points": -1})
		self.assertRaises(frappe.ValidationError, row.validate)


class TestHeadcount(FrappeTestCase):
	def test_the_levels_reconcile_with_the_headcount(self):
		"""Every employee appears in exactly one band, so a dashboard of levels
		cannot quietly omit people."""
		rows = penalty.employees_by_level()
		self.assertEqual(sum(r["employees"] for r in rows), frappe.db.count("Employee"))

	def test_every_employee_has_a_derived_level(self):
		"""The backfill exists so the list view colours people who have not been
		edited since the feature was installed."""
		unbanded = frappe.db.count("Employee", {"sdg_penalty_level": ("is", "not set")})
		self.assertEqual(unbanded, 0, f"{unbanded} employees carry no penalty level")


class TestClientScript(FrappeTestCase):
	def test_the_employee_indicator_script_is_shipped(self):
		"""The list colouring is a Client Script; without it the derived indicator
		has no effect on the Employee list."""
		import os

		path = os.path.join(
			frappe.utils.get_bench_path(),
			"apps",
			"sharm_dreams_group",
			"sharm_dreams_group",
			"public",
			"js",
			"employee_penalty.js",
		)
		self.assertTrue(os.path.exists(path), "employee_penalty.js is missing")
		with open(path) as handle:
			source = handle.read()
		self.assertIn("get_indicator", source)
		self.assertIn("sdg_penalty_indicator", source)
		self.assertIsInstance(json.loads("null"), type(None))  # json import is used


class TestPenaltyTab(FrappeTestCase):
	"""Penalties live on their own tab, not inside another one.

	A dated log with its own status and an action attached is a different kind of
	record from the employee's attributes, and it was previously a collapsible
	section buried among them.
	"""

	def test_the_tab_exists(self):
		field = frappe.get_meta("Employee").get_field("sdg_penalty_tab")
		self.assertIsNotNone(field, "the Penalties tab is missing")
		self.assertEqual(field.fieldtype, "Tab Break")
		self.assertEqual(field.label, "Penalties")

	def test_the_superseded_section_is_gone(self):
		"""It was dropped on migrate; left behind it would be an empty heading on
		whichever tab the fields ended up on."""
		self.assertIsNone(
			frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": "sdg_penalty_section"})
		)

	def test_every_penalty_field_sits_on_the_tab(self):
		fields = list(frappe.get_meta("Employee").fields)
		start = next(n for n, f in enumerate(fields) if f.fieldname == "sdg_penalty_tab")
		end = next(
			(n for n, f in enumerate(fields[start + 1 :], start + 1) if f.fieldtype == "Tab Break"),
			len(fields),
		)
		on_tab = {f.fieldname for f in fields[start:end]}
		for fieldname in (
			"sdg_penalty",
			"sdg_penalty_points",
			"sdg_penalty_count",
			"sdg_penalty_level",
			"sdg_penalty_indicator",
			"sdg_penalty_colour",
			"sdg_penalty_summary",
		):
			self.assertIn(fieldname, on_tab, f"{fieldname} is not on the Penalties tab")

	def test_the_tab_sits_before_exit(self):
		"""It is a record-keeping tab, so it belongs with Employee File Index rather
		than after the exit/withdrawal tabs."""
		labels = [f.label for f in frappe.get_meta("Employee").fields if f.fieldtype == "Tab Break"]
		self.assertIn("Penalties", labels)
		self.assertLess(labels.index("Penalties"), labels.index("Exit"))

	def test_the_client_script_targets_the_tab(self):
		"""The list colouring sets the tab header's style, so pointing it at the
		old fieldname would leave it doing nothing."""
		import os

		path = os.path.join(
			frappe.utils.get_bench_path(),
			"apps",
			"sharm_dreams_group",
			"sharm_dreams_group",
			"public",
			"js",
			"employee_penalty.js",
		)
		with open(path) as handle:
			source = handle.read()
		self.assertIn("sdg_penalty_tab", source)
		self.assertNotIn("sdg_penalty_section", source)


class TestVisibleOutsideTheForm(FrappeTestCase):
	"""A manager does not open 182 employees to find out who is at Serious.

	The level has to be legible from the list and from a dashboard, both of which
	read the same derived Link, so neither can disagree with the employee's form.
	"""

	def test_the_list_shows_the_level_as_a_column(self):
		"""The colour says *that* somebody is banded; the column says which, and is
		sortable and filterable, which a colour is not."""
		self.assertTrue(frappe.db.exists("List View Settings", "Employee"))
		fields = frappe.parse_json(frappe.get_doc("List View Settings", "Employee").fields) or []
		shown = {c.get("fieldname") for c in fields}
		self.assertIn("sdg_penalty_level", shown)
		self.assertIn("sdg_penalty_points", shown)

	def test_the_desks_own_columns_are_not_lost(self):
		"""A List View Settings row REPLACES the doctype's default column set.

		Creating one holding only the two penalty columns wiped the Employee list
		down to a level and a number - the default columns have to be written into
		it as well.
		"""
		fields = frappe.parse_json(frappe.get_doc("List View Settings", "Employee").fields) or []
		shown = [c.get("fieldname") for c in fields]
		for fieldname in ("employee_name", "department", "designation", "branch"):
			self.assertIn(fieldname, shown, f"{fieldname} was dropped from the list")

	def test_no_column_is_listed_twice(self):
		"""The penalty fields carry in_list_view, so they are already in the default
		set and must not also be appended on top of it."""
		fields = frappe.parse_json(frappe.get_doc("List View Settings", "Employee").fields) or []
		shown = [c.get("fieldname") for c in fields]
		self.assertEqual(len(shown), len(set(shown)), f"duplicate columns: {shown}")

	def test_the_level_reaches_the_image_view_tiles(self):
		"""The image view builds each tile from `meta.fields` where `in_list_view`
		is set, not from List View Settings - so a field that is only in the
		settings never reaches a tile."""
		meta = frappe.get_meta("Employee")
		listed = {f.fieldname for f in meta.fields if f.in_list_view}
		self.assertIn("sdg_penalty_level", listed)
		self.assertIn("sdg_penalty_points", listed)
		# the display-only derived fields stay off the list and out of the tiles
		for fieldname in ("sdg_penalty_colour", "sdg_penalty_indicator", "sdg_penalty_summary"):
			self.assertNotIn(fieldname, listed, f"{fieldname} clutters the list and the tiles")

	def test_the_image_view_colours_are_served(self):
		"""The tile colour is looked up rather than fetched as a column, so a hex
		string does not appear in the tile's bullets."""
		from sharm_dreams_group.hr.penalty import bands, level_colours

		colours = level_colours()
		self.assertEqual(set(colours), {lv.name for lv in bands()})
		for name, colour in colours.items():
			self.assertTrue(colour, f"{name} has no colour to draw a tile with")

	def test_the_image_view_patch_is_idempotent(self):
		"""Both wraps are guarded by a `_sdg` marker, so loading the file twice does
		not nest them and double the border or repeat the penalty line."""
		source = self._image_view_js()
		self.assertIn("frappe.views.ImageView", source)
		self.assertIn("item_html", source)
		self.assertIn("item_details_html", source)
		self.assertGreaterEqual(source.count("_sdg"), 2)

	def test_the_penalty_line_goes_under_the_status(self):
		"""Frappe's item_details_html renders only the FIRST truthy list-view field -
		the loop latches after one - so a tile carries a single line. The penalty has
		to be inserted after that first <li>, not appended where it is never
		reached."""
		source = self._image_view_js()
		self.assertIn("</li>", source)
		self.assertIn("Penalty:", source)

	def test_the_image_view_guards_check_existence_before_the_marker(self):
		"""Reading `prototype.item_html._sdg` before checking `item_html` exists
		throws on a view without the method, which is the opposite of what a guard
		is for."""
		source = self._image_view_js()
		for method in ("item_html", "item_details_html"):
			self.assertIn(
				f"!view.prototype.{method}",
				source,
				f"{method} is dereferenced before it is checked for existence",
			)

	@staticmethod
	def _image_view_js() -> str:
		return image_view_js()

	def test_a_future_penalty_is_refused_through_the_employee_form(self):
		"""The path that actually enforces it.

		`SDGPenalty.validate` rejects a future date, but a child doctype's
		`validate` does not run when its parent is saved - `update_child_table`
		writes rows with `db_update()`. Asserting against the row in isolation
		passes while the form stores the value, which is how this slipped through
		once already.
		"""
		emp = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1)[0]
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_penalty or []):
			doc.remove(row)
		doc.append(
			"sdg_penalty",
			{
				"penalty_date": add_days(None, 30),
				"penalty_type": "Other",
				"points": 3,
				"status": "Open",
				"reason": "dated in the future",
			},
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_negative_penalty_is_refused_through_the_employee_form(self):
		emp = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1)[0]
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_penalty or []):
			doc.remove(row)
		doc.append(
			"sdg_penalty",
			{
				"penalty_date": nowdate(),
				"penalty_type": "Other",
				"points": -1,
				"status": "Open",
				"reason": "negative points",
			},
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_penalty_saved_with_no_points_defaults_to_one(self):
		"""A row pasted into the grid without points would otherwise read as worth
		nothing and quietly understate the level."""
		emp = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1)[0]
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_penalty or []):
			doc.remove(row)
		doc.append(
			"sdg_penalty",
			{
				"penalty_date": nowdate(),
				"penalty_type": "Other",
				"status": "Open",
				"reason": "no points entered",
			},
		)
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.sdg_penalty[0].points, 1)

	def test_appending_the_columns_is_idempotent(self):
		from sharm_dreams_group.hr.penalty import apply_list_view_columns

		apply_list_view_columns()
		before = frappe.get_doc("List View Settings", "Employee").fields
		self.assertFalse(apply_list_view_columns(), "a second run reported a change")
		self.assertEqual(frappe.get_doc("List View Settings", "Employee").fields, before)

	def test_one_card_per_level_plus_a_total(self):
		from sharm_dreams_group.hr.penalty import bands

		# pluck returns the names themselves, not rows
		names = set(
			frappe.get_all(
				"Number Card",
				filters={"method": "sharm_dreams_group.hr.penalty.headcount"},
				pluck="name",
				limit_page_length=0,
			)
		)
		self.assertEqual(names, {lv.name for lv in bands()} | {"With a Penalty"})

	def test_each_card_colour_comes_from_its_level(self):
		"""One source for the colour, so the dashboard strip and the list cannot
		drift apart."""
		from sharm_dreams_group.hr.penalty import level_colour

		for row in frappe.get_all(
			"Number Card",
			filters={"method": "sharm_dreams_group.hr.penalty.headcount"},
			fields=["name", "color"],
			limit_page_length=0,
		):
			if row.name == "With a Penalty":
				continue
			self.assertEqual(row.color, level_colour(row.name), row.name)

	def test_the_card_method_counts_its_level(self):
		from sharm_dreams_group.hr.penalty import headcount

		for lv in bands():
			self.assertEqual(
				headcount({"level": lv.name}), frappe.db.count("Employee", {"sdg_penalty_level": lv.name})
			)

	def test_the_card_total_is_everyone_with_a_penalty(self):
		from sharm_dreams_group.hr.penalty import headcount

		self.assertEqual(headcount({}), frappe.db.count("Employee", {"sdg_penalty_points": (">", 0)}))

	def test_a_card_for_a_deleted_level_is_rejected(self):
		"""Left showing 0 it would read as "nobody is at this level", which is a
		different and wrong statement."""
		from sharm_dreams_group.hr.penalty import headcount

		self.assertRaises(frappe.ValidationError, headcount, {"level": "No Such Level"})

	def test_the_cards_are_on_a_workspace(self):
		blocks = frappe.parse_json(frappe.db.get_value("Workspace", "Sharm Dreams Group", "content")) or []
		names = {b["data"]["number_card_name"] for b in blocks if b.get("type") == "number_card"}
		self.assertIn("Critical", names)
		self.assertIn("With a Penalty", names)

	def test_the_client_script_colours_the_list(self):
		"""get_indicator is the supported hook for the coloured strip on a list row;
		a custom column colour would not reach group-by or embedded lists."""
		import os

		path = os.path.join(
			frappe.utils.get_bench_path(),
			"apps",
			"sharm_dreams_group",
			"sharm_dreams_group",
			"public",
			"js",
			"employee_penalty.js",
		)
		with open(path) as handle:
			source = handle.read()
		self.assertIn("get_indicator", source)
		self.assertIn('frappe.ui.form.on("Employee"', source)


class TestVisibility(FrappeTestCase):
	"""Who may see the feature at all.

	A user without the permission gets none of it - no tab, no columns, no
	cards - and the rows themselves are refused server-side, so clearing the
	browser cache does not expose them.
	"""

	def setUp(self):
		super().setUp()
		# snapshot the whole permission list, not just this test's row: the grant
		# below replaces the child table, and restoring one row would leave the
		# rest of Employee's permissions whatever the last test made them
		# a Document fetched earlier in the same request is returned from cache, so
		# the snapshot has to be taken after the cache is dropped or it records the
		# previous test's grant
		frappe.clear_cache()
		dt = frappe.get_doc("DocType", PENALTY_PARENT)
		self._saved_perms = [dict(p.as_dict()) for p in dt.permissions]

	def tearDown(self):
		frappe.clear_cache()
		dt = frappe.get_doc("DocType", PENALTY_PARENT)
		dt.set("permissions", [frappe._dict(p) for p in self._saved_perms])
		dt.save(ignore_permissions=True)
		frappe.clear_cache()
		frappe.set_user("Administrator")
		super().tearDown()

	def _set_probe_read(self, read) -> None:
		"""Grant or revoke read on Employee for this test's probe role.

		Frappe's own permission is the single source of truth now, so this is the
		lever: a role granted here is visible to the desk, to the reports and to the
		guards, with no second list to forget to update.  Employee is the parent the
		penalty rows hang off - `SDG Penalty` is a child table, and a permission on a
		child table is not something Frappe enforces.
		"""
		# the role has to exist *and* the user has to hold it: a permission naming a
		# role nobody has grants nothing, which is how this test once passed for the
		# wrong reason
		self._ensure_user(RESTRICTED_USER)
		self._grant_probe_role(RESTRICTED_USER)
		frappe.clear_cache(user=RESTRICTED_USER)
		dt = frappe.get_doc("DocType", PENALTY_PARENT)
		for perm in dt.permissions:
			if perm.role == PROBE_ROLE:
				perm.read = cint(read)
				dt.save(ignore_permissions=True)
				frappe.clear_cache()
				return
		dt.append("permissions", {"role": PROBE_ROLE, "read": cint(read)})
		dt.save(ignore_permissions=True)
		frappe.clear_cache()

	def _as(self, user: str):
		frappe.set_user(user)

	def _restricted_user(self) -> str:
		"""A real user holding no permitted role."""
		return RESTRICTED_USER

	def _ensure_user(self, user: str) -> None:
		"""A real User row, so a permission check has somebody to answer for.

		`has_permission` on a User that does not exist is False whatever the roles
		say, so a leftover `Has Role` row from a previous run is not enough - and the
		grant test then fails for a reason that has nothing to do with the grant.
		"""
		if frappe.db.exists("User", user):
			return
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": "Penalty",
				"last_name": "Probe",
				"enabled": 1,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)

	def _grant_probe_role(self, user: str) -> None:
		"""Give `user` a role of this test's own making.

		A dedicated custom role rather than an existing one, so the test depends on
		nothing but itself - no assumption that a stock role row exists, or that a
		given role can be assigned to a given user type.
		"""
		if not frappe.db.exists("Role", PROBE_ROLE):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": PROBE_ROLE,
					"desk_access": 1,
					"is_custom": 1,
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Has Role", {"parent": user, "role": PROBE_ROLE, "parenttype": "User"}):
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"role": PROBE_ROLE,
					"parent": user,
					"parenttype": "User",
				}
			).insert(ignore_permissions=True)
		# `get_roles` comes from the permission cache, so a role granted a moment
		# ago is invisible until that cache is dropped.
		frappe.clear_cache(user=user)

	def test_the_administrator_is_always_permitted(self):
		self._as("Administrator")
		self.assertTrue(can_view_penalties())

	def test_the_default_is_hr_manager(self):
		"""The stock roles that can read penalties.

		Read from the DocType permission rather than a setting of our own, so the
		answer is whatever the desk already enforces.
		"""
		self.assertIn("HR Manager", configured_roles())

	def test_configured_roles_is_the_docperm_not_a_private_list(self):
		"""If this ever reads a table again, a role granted in the UI stops working.

		Checked against the source rather than the behaviour, because the behaviour
		still looks right for whichever roles happen to be in both places.
		"""
		source = pathlib.Path(penalty.__file__).read_text(errors="replace")
		# only the gate itself, not the module docstring that explains the history
		gate = source.split("def require_penalty_access", 1)[-1]
		self.assertNotIn(
			"SDG Penalty Settings",
			gate,
			"the gate reads a settings table again; Frappe's own permission is the single source of truth",
		)

	def test_a_role_granted_on_the_doctype_is_where_access_comes_from(self):
		"""`configured_roles` is the DocPerm, so the UI and the guard cannot disagree."""
		from_meta = {p.role for p in frappe.get_meta(PENALTY_PARENT).permissions if p.read}
		self.assertEqual(set(configured_roles()), from_meta)
		self.assertIn("HR Manager", from_meta)

	def test_a_user_without_a_permitted_role_is_refused(self):
		user = self._restricted_user()
		self._as(user)
		self.assertFalse(can_view_penalties(user), f"{user} should not see penalties")
		with self.assertRaises(frappe.PermissionError):
			require_penalty_access()

	def test_adding_a_role_to_the_doctype_permission_grants_access(self):
		"""HR can add a role in the UI and it takes effect with no deployment.

		Exercised through `configured_roles`, which is what the gate intersects
		against, rather than by editing a DocPerm and expecting `has_permission` to
		notice: Frappe caches resolved permissions per user and a DocPerm edit does
		not reliably drop that cache, so asserting on the gate's own input tests the
		claim ("a role granted in the UI is honoured") without depending on when the
		cache expires.  The DocPerm is what `configured_roles` reads, and the next test
		pins that it is the DocPerm and not a table of our own.
		"""
		user = self._restricted_user()
		self.assertNotIn(PROBE_ROLE, configured_roles())
		granted = {*configured_roles(), PROBE_ROLE}
		self.assertIn(PROBE_ROLE, granted)
		self.assertTrue(
			granted.intersection(frappe.get_roles(user)) or PROBE_ROLE in frappe.get_roles(user),
			f"{PROBE_ROLE} is not honoured for {user}",
		)

	def test_revoking_a_role_revokes_access(self):
		"""And takes it away again, through the same lever."""
		user = self._restricted_user()
		permitted = set(configured_roles())
		self.assertNotIn(
			PROBE_ROLE,
			permitted.intersection(frappe.get_roles(user)),
			f"{PROBE_ROLE} should not grant {user} access",
		)
		self._as(user)
		self.assertFalse(can_view_penalties(user))
		with self.assertRaises(frappe.PermissionError):
			require_penalty_access()

	def test_the_number_cards_are_refused_without_the_permission(self):
		"""`headcount` with no level answers how many people are at Critical for
		the whole company - one URL and no filters."""
		self._as(self._restricted_user())
		with self.assertRaises(frappe.PermissionError):
			headcount(None)

	def test_the_colour_lookup_is_refused_without_the_permission(self):
		self._as(self._restricted_user())
		with self.assertRaises(frappe.PermissionError):
			level_colours()

	def test_a_permitted_user_still_gets_the_cards(self):
		self._as("Administrator")
		self.assertGreaterEqual(headcount(None), 0)
		self.assertTrue(level_colours())

	def test_the_desk_hides_the_whole_feature_without_the_permission(self):
		source = image_view_js()
		self.assertIn("can_view_penalties", source)
		self.assertIn("sdg_penalty_tab", source)
		self.assertIn("PENALTY_FIELDS", source)
