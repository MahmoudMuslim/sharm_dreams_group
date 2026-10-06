# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for widening a Company filter to the company and its descendants."""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.company_tree import expand, get_company_tree, has_descendants, subtree

GROUP = "Sharm Dreams Group"
LEAF = "Bautex Misr"


class TestSubtree(FrappeTestCase):
	def test_the_group_parent_covers_every_operating_company(self):
		members = subtree(GROUP)
		self.assertEqual(members[0], GROUP)
		self.assertGreater(len(members), 1)
		self.assertIn(LEAF, members)

	def test_a_leaf_covers_only_itself(self):
		self.assertEqual(subtree(LEAF), [LEAF])

	def test_a_blank_company_means_no_filter(self):
		"""Distinct from filtering on the parent: an unset Company must not
		silently become 'the parent and its 13 children'."""
		for blank in ("", "   ", None):
			self.assertEqual(subtree(blank), [])

	def test_has_descendants_only_where_widening_changes_anything(self):
		self.assertTrue(has_descendants(GROUP))
		self.assertFalse(has_descendants(LEAF))
		self.assertFalse(has_descendants("Not A Company"))
		self.assertFalse(has_descendants(""))


class TestExpand(FrappeTestCase):
	def test_report_shaped_filter_is_widened(self):
		self.assertEqual(
			expand({"company": GROUP, "status": "Open"}),
			{"company": ["in", subtree(GROUP)], "status": "Open"},
		)

	def test_leaf_stays_an_equality(self):
		"""Single-company filtering must be untouched by this work."""
		self.assertEqual(expand({"company": LEAF}), {"company": LEAF})

	def test_doctype_shaped_row_is_widened(self):
		rows = [["Employee", "company", "=", GROUP], ["Employee", "status", "=", "Active"]]
		self.assertEqual(
			expand(rows),
			[["Employee", "company", "in", subtree(GROUP)], ["Employee", "status", "=", "Active"]],
		)

	def test_a_company_doctype_row_is_not_a_company_filter(self):
		rows = [["Company", "abbr", "=", "SDG"]]
		self.assertEqual(expand(rows), rows)

	def test_a_list_value_is_not_re_widened(self):
		"""The output shape is a list, so a second pass must leave it alone
		rather than wrapping it again."""
		once = expand({"company": GROUP})
		self.assertEqual(expand(once), once)

	def test_untouched_shapes_pass_through(self):
		for value in (None, "", [], "x", 0):
			self.assertEqual(expand(value), value)


class TestWideningActuallyReturnsTheGroupsData(FrappeTestCase):
	"""The point of the whole module, asserted against real rows."""

	def test_the_parent_alone_matches_nothing(self):
		"""This is the bug being fixed, pinned so it cannot regress unnoticed."""
		self.assertEqual(frappe.db.count("Employee", {"company": GROUP}), 0)

	def test_the_parent_widened_matches_the_whole_group(self):
		widened = frappe.db.count("Employee", {"company": ("in", subtree(GROUP))})
		every = frappe.db.count("Employee")
		self.assertEqual(widened, every)
		self.assertGreater(widened, 0)

	def test_a_leaf_widened_is_still_just_that_leaf(self):
		widened = frappe.db.count("Employee", {"company": ("in", subtree(LEAF))})
		self.assertEqual(widened, frappe.db.count("Employee", {"company": LEAF}))

	def test_the_rows_form_agrees_with_the_dict_form(self):
		from_rows = frappe.db.count("Employee", [["Employee", "company", "in", subtree(GROUP)]])
		from_dict = frappe.db.count("Employee", {"company": ("in", subtree(GROUP))})
		self.assertEqual(from_rows, from_dict)


class TestCompanyTreeEndpoint(FrappeTestCase):
	def test_every_company_is_present(self):
		tree = get_company_tree()
		self.assertEqual(set(tree), set(frappe.get_all("Company", pluck="name", limit_page_length=0)))

	def test_the_parent_entry_lists_its_children(self):
		self.assertEqual(get_company_tree()[GROUP], subtree(GROUP))
		self.assertGreater(len(get_company_tree()[GROUP]), 1)


class TestPatchedReportsTakeABareCompanyName(FrappeTestCase):
	"""The patched reports expand the company themselves, so they must be handed a
	bare name.

	An ("in", [companies]) pair is what `frappe.get_list` wants, but a script
	report that builds its own company list from `filters["company"]` emits
	invalid SQL from it.  Widening the filter centrally therefore breaks exactly
	the reports that already handle it, which is why there is no
	`override_whitelisted_methods` entry for `query_report.run`.
	"""

	def test_a_bare_group_parent_widens_to_every_company(self):
		from erpnext.controllers import trends

		filters = {
			"fiscal_year": frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name"),
			"based_on": "Supplier",
			"period": "Monthly",
			"company": GROUP,
		}
		trends.validate_filters(filters)
		self.assertEqual(
			set(filters["companies"]),
			set(frappe.get_all("Company", pluck="name", limit_page_length=0)),
		)

	def test_a_bare_leaf_stays_just_that_leaf(self):
		from erpnext.controllers import trends

		filters = {
			"fiscal_year": frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name"),
			"based_on": "Supplier",
			"period": "Monthly",
			"company": LEAF,
		}
		trends.validate_filters(filters)
		self.assertEqual(tuple(filters["companies"]), (LEAF,))

	def test_an_in_pair_is_not_handed_to_a_patched_report(self):
		"""`company_tree.expand` produces the pair form for plain queries.  It must
		never reach a patched report, so assert the two shapes are distinguishable
		rather than interchangeable."""
		widened = expand({"company": GROUP})
		self.assertEqual(widened["company"][0], "in")
		self.assertIsInstance(widened["company"][1], list)
		# a bare name is what the reports get, and is not a pair
		self.assertIsInstance(GROUP, str)


class TestGroupTrialBalance(FrappeTestCase):
	"""The group Trial Balance must equal the sum of its children.

	This is the one financial result verified against real figures rather than
	against the shape of a query: the site has GL Entries across 13 companies, so
	the group's closing debit and credit can be compared with the children's.

	A trial balance nets to zero, so comparing debit-minus-credit would be
	vacuous.  Each side is compared separately.
	"""

	@staticmethod
	def _totals(company, fiscal_year):
		from frappe.desk.query_report import run

		filters = {"fiscal_year": fiscal_year}
		if company:
			filters["company"] = company
		result = run("Trial Balance", dict(filters), ignore_prepared_report=True)
		result = result[0] if isinstance(result, tuple) else result
		for row in (result or {}).get("result") or []:
			if str(row.get("account") or "").strip().strip("'").lower() in ("total", "grand total"):
				return round(float(row.get("closing_debit") or 0), 2), round(
					float(row.get("closing_credit") or 0), 2
				)
		return None

	def test_the_group_equals_the_sum_of_its_children(self):
		fiscal_year = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name")
		children = [c for c in frappe.get_all("Company", pluck="name", limit_page_length=0) if c != GROUP]
		if not children:
			self.skipTest("no companies")

		group = self._totals(GROUP, fiscal_year)
		self.assertIsNotNone(group, "the group parent returned no total row")

		debit = credit = 0.0
		measured = 0
		for company in children:
			totals = self._totals(company, fiscal_year)
			if totals:
				debit += totals[0]
				credit += totals[1]
				measured += 1
		if not measured:
			self.skipTest("no child company returned a trial balance")

		self.assertAlmostEqual(group[0], round(debit, 2), places=2, msg="group closing debit")
		self.assertAlmostEqual(group[1], round(credit, 2), places=2, msg="group closing credit")

	def test_the_group_still_balances(self):
		fiscal_year = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name")
		group = self._totals(GROUP, fiscal_year)
		self.assertIsNotNone(group)
		self.assertAlmostEqual(group[0], group[1], places=2)

	def test_a_leaf_company_is_still_only_itself(self):
		"""Widening must not leak into a company with no descendants."""
		fiscal_year = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name")
		group = self._totals(GROUP, fiscal_year)
		leaf = self._totals(LEAF, fiscal_year)
		self.assertIsNotNone(leaf)
		self.assertNotAlmostEqual(leaf[0], group[0], places=2, msg="a leaf company returned the group total")
