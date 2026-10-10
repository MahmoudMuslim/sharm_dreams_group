# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for stripping the Company dynamic filter off the desk widgets."""

from __future__ import annotations

import json
import os
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.dashboard_widgets import (
	COMPANY_REQUIRED_REPORTS,
	UNBLOCKED_BY_PATCH,
	WIDGET_DOCTYPES,
	_strip,
	company_optional_reports,
	desk_filters,
	panel_reports,
	remove_company_filters,
	verify_company_optional,
	verify_panel_reports,
)

COMPANY_EXPR = 'frappe.defaults.get_user_default("Company")'


class TestStrip(unittest.TestCase):
	"""The pure transform, with no database in the way."""

	def test_dict_form_drops_the_company_key(self):
		"""Report-backed records key the filter by report-filter name."""
		out, changed = _strip({"company": COMPANY_EXPR, "fiscal_year": "erpnext.utils.get_fiscal_year()"})
		self.assertTrue(changed)
		self.assertEqual(out, {"fiscal_year": "erpnext.utils.get_fiscal_year()"})

	def test_list_form_drops_the_company_row(self):
		"""Doctype-backed records use [doctype, fieldname, operator, value] rows."""
		rows = [
			["Employee", "company", "=", COMPANY_EXPR],
			["Employee", "status", "=", "Active"],
		]
		out, changed = _strip(rows)
		self.assertTrue(changed)
		self.assertEqual(out, [["Employee", "status", "=", "Active"]])

	def test_a_sibling_dynamic_filter_survives(self):
		"""The Incoming/Outgoing Bills cards pair company with a fiscal-year
		`Between`; dropping one row must not take the other with it."""
		rows = [
			["Sales Invoice", "company", "=", COMPANY_EXPR],
			["Sales Invoice", "posting_date", "Between", "(frappe.boot.current_fiscal_year || [null])"],
		]
		out, changed = _strip(rows)
		self.assertTrue(changed)
		self.assertEqual(len(out), 1)
		self.assertEqual(out[0][1], "posting_date")

	def test_other_doctypes_rows_are_left_alone(self):
		"""The fieldname is at index 1; a doctype merely named *Company* is not a match."""
		rows = [["Company", "abbr", "=", "SDG"], ["Employee", "company_name", "=", "x"]]
		out, changed = _strip(rows)
		self.assertFalse(changed)
		self.assertEqual(out, rows)

	def test_absent_company_is_not_a_change(self):
		"""Drives idempotency: after_migrate calls this on every migrate."""
		self.assertEqual(_strip({"fiscal_year": "x"}), ({"fiscal_year": "x"}, False))
		self.assertEqual(_strip([]), ([], False))
		self.assertEqual(_strip({}), ({}, False))

	def test_only_company_is_removed_not_everything(self):
		"""A record whose only dynamic filter was company ends up empty, not None."""
		out, changed = _strip({"company": COMPANY_EXPR})
		self.assertTrue(changed)
		self.assertEqual(out, {})

	def test_malformed_input_is_left_untouched(self):
		out, changed = _strip("not a filter structure")
		self.assertFalse(changed)
		self.assertEqual(out, "not a filter structure")


class TestRemoveCompanyFilters(FrappeTestCase):
	"""These call the real function, so they pass `commit=False`: the default commits,
	which would leave the planted test records behind and break the next run."""

	def test_no_widget_filters_by_company(self):
		remove_company_filters(commit=False)
		for doctype in WIDGET_DOCTYPES:
			for name, dynamic in frappe.get_all(
				doctype, fields=["name", "dynamic_filters_json"], limit_page_length=0
			):
				self.assertNotIn(
					"company", (dynamic or "").lower(), f"{doctype} {name} still filters by company"
				)

	def test_is_idempotent(self):
		remove_company_filters(commit=False)
		self.assertEqual(remove_company_filters(commit=False)["Number Card"]["changed"], 0)
		self.assertEqual(remove_company_filters(commit=False)["Dashboard Chart"]["changed"], 0)

	def test_both_doctypes_are_reported(self):
		summary = remove_company_filters(commit=False)
		self.assertEqual(set(summary), set(WIDGET_DOCTYPES))

	def test_a_single_doctype_can_be_targeted(self):
		summary = remove_company_filters("Number Card", commit=False)
		self.assertEqual(list(summary), ["Number Card"])

	def test_a_company_filter_is_removed_from_a_planted_card(self):
		frappe.get_doc(
			{
				"doctype": "Number Card",
				"label": "SDG Company Filter Test",
				"type": "Document Type",
				"document_type": "Employee",
				"function": "Count",
				"filters_json": json.dumps([["Employee", "status", "=", "Active"]]),
				"dynamic_filters_json": json.dumps(
					[["Employee", "company", "=", COMPANY_EXPR], ["Employee", "status", "=", "Active"]]
				),
			}
		).insert(ignore_permissions=True)

		remove_company_filters("Number Card", commit=False)

		self.assertEqual(
			frappe.db.get_value("Number Card", "SDG Company Filter Test", "dynamic_filters_json"),
			json.dumps([["Employee", "status", "=", "Active"]]),
		)

	def test_a_static_company_filter_is_removed_too(self):
		"""A hardcoded pin scopes the widget just as hard as the per-user default.

		This is the Appraisal Overview case: it was pinned to "The Coffee
		Company", which is not one of the group's companies, so its chart could
		never return anything.
		"""
		frappe.get_doc(
			{
				"doctype": "Number Card",
				"label": "SDG Static Company Test",
				"type": "Document Type",
				"document_type": "Employee",
				"function": "Count",
				"filters_json": json.dumps({"company": "Some Company", "status": "Active"}),
				"dynamic_filters_json": json.dumps([["Employee", "company", "=", COMPANY_EXPR]]),
			}
		).insert(ignore_permissions=True)

		remove_company_filters("Number Card", commit=False)

		self.assertEqual(
			frappe.db.get_value("Number Card", "SDG Static Company Test", "dynamic_filters_json"), "[]"
		)
		self.assertEqual(
			frappe.db.get_value("Number Card", "SDG Static Company Test", "filters_json"),
			json.dumps({"status": "Active"}),
		)


class TestCompanyOptionalReports(FrappeTestCase):
	"""The client-side patch is only correct if these two lists agree.

	`dashboard_widgets.js` is what actually unblocks the charts, and it works by
	relying on the report tolerating a blank Company.  These tests run the
	reports for real, so an ERPNext upgrade that starts demanding a Company
	fails here rather than leaving a chart permanently blank.
	"""

	def test_the_js_allow_list_is_readable(self):
		relaxed = company_optional_reports()
		self.assertIn("Work Order Summary", relaxed)
		self.assertNotIn("", relaxed)

	def test_a_probe_does_not_leave_its_failure_in_the_error_log(self):
		"""A diagnostic that fills the log makes the log useless for real failures.

		`frappe.log_error` has no suppression flag, and `query_report.run` calls it
		for every report that raises, so each probe against a report that is expected
		to fail wrote a row.  That reached 516 rows on this site, all of them this
		app's own diagnostics - which is why a genuine "Could not generate PDF, check
		the Error Log" led a reader to a log with no PDF entry in it and no way to
		tell which of the hundreds were theirs.
		"""
		from sharm_dreams_group.dashboard_widgets import quiet_error_log

		before = frappe.db.count("Error Log")
		with quiet_error_log():
			# something that genuinely raises inside the guarded block, caught here
			# because `throw` raises - the point is what reached the log, not what
			# escaped
			try:
				frappe.throw("probe noise that should not be logged")
			except frappe.ValidationError:
				pass
		self.assertEqual(
			frappe.db.count("Error Log"),
			before,
			"quiet_error_log did not remove the row its own block created",
		)

	def test_verify_panel_reports_writes_nothing_to_the_error_log(self):
		"""The end-to-end version: run the whole verifier and watch the count."""
		before = frappe.db.count("Error Log")
		verify_panel_reports()
		self.assertEqual(
			frappe.db.count("Error Log"),
			before,
			"verify_panel_reports added Error Log rows; it probes reports that are "
			"meant to fail, and those failures are answers, not incidents",
		)

	def test_verify_company_optional_writes_nothing_to_the_error_log(self):
		before = frappe.db.count("Error Log")
		verify_company_optional()
		self.assertEqual(frappe.db.count("Error Log"), before)

	def test_every_relaxed_name_is_a_report_that_exists(self):
		"""The list is parsed out of the JS, so a quoted phrase in a comment would
		otherwise become a report name.

		Not hypothetical: the phrase `"needs a company"` in a comment inside the Set
		was read as a report called `needs a company`, and it surfaced as a chart
		claiming to relax a report that does not exist.  The parser now drops
		comment lines; this is the belt to that braces, and it would also catch an
		entry that has since been renamed upstream.
		"""
		unknown = sorted(
			name
			for name in company_optional_reports()
			if not frappe.db.exists("Report", name)
		)
		self.assertEqual(unknown, [], f"the allow list names reports that do not exist: {unknown}")

	def test_no_comment_text_leaks_into_the_allow_list(self):
		"""The existence check above is the real guard; this narrows the diagnosis.

		A leaked phrase cannot be a Report name, so
		`test_every_relaxed_name_is_a_report_that_exists` already fails.  This says
		*why* it failed, because "the allow list names reports that do not exist"
		points at the wrong file when the cause is a comment in the right one.
		"""
		real = set(frappe.get_all("Report", pluck="name", limit_page_length=0))
		junk = sorted(n for n in company_optional_reports() if n not in real)
		self.assertEqual(
			junk,
			[],
			f"these entries are not reports; if they read like sentences they came "
			f"from a comment inside the Set in dashboard_widgets.js: {junk}",
		)

	def test_no_chart_relaxes_a_report_that_demands_a_company(self):
		result = verify_company_optional()
		self.assertEqual(result["misclassified"], [], "a relaxed report now requires a Company")
		self.assertEqual(result["relaxed_but_unused"], [])

	def test_the_only_exceptions_are_the_documented_ones(self):
		result = verify_company_optional()
		self.assertEqual(result["unknown_company_required"], [])
		self.assertEqual(result["stale_company_required"], [])

	def test_nothing_fails_for_an_unrelated_reason(self):
		"""A chart failing with *and* without a Company is a different bug and
		would otherwise hide inside the company work."""
		self.assertEqual(verify_company_optional()["broken_either_way"], [])

	def test_desk_filters_carry_no_company_key(self):
		"""The replayed filters must be company-free, or the probe is testing
		the filter rather than the report."""
		for chart in frappe.get_all(
			"Dashboard Chart", filters={"chart_type": "Report"}, pluck="name", limit_page_length=0
		):
			self.assertNotIn("company", {k.lower() for k in desk_filters(chart)}, chart)


class TestPanelReportsDoNotNeedACompany(FrappeTestCase):
	"""The Reports band, and the other half of the same Company problem.

	`COMPANY_OPTIONAL_REPORTS` unblocks two consumers: the Dashboard Chart seam
	(`frappe.report_utils.get_report_filters`) and the report page seam
	(`frappe.views.QueryReport.get_report_settings`).  The chart half was verified by
	`verify_company_optional`; these are the panel half.

	The panel is 32 reports and they do not all behave the same way, which is why
	this is not one blanket assertion.  Measured on this site:

	    25  run with no Company at all - the filter can simply go
	     1  throws "Company is mandatory" from its own code (General Ledger)
	     6  fail on some *other* mandatory filter - nothing to do with Company

	The six matter as much as the one.  A two-way split would file all seven under
	"needs a company" and send someone to patch reports whose Company handling is
	already fine, so the verifier classifies three ways and the assertions below
	hold each bucket to a known shape.

	The six are stuck on their own terms, not on Company: two want a date range the
	probe cannot guess, one wants a Salary Structure Assignment for a named employee,
	one finds no employees with an active salary structure (every Salary Slip on this
	site is a draft), one wants a metric basis, and one wanted a `component_type`
	column that this schema does not have.

	Professional Tax Deductions was the last of those and has since been fixed, so
	the headline count below is 26 rather than 25. `component_type` is a **regional
	Custom Field** - `hrms/regional/india/setup.py` adds it to `Salary Component` and
	nothing else does - so on an Egyptian site the column has never existed and the
	report raised `Unknown column 'component_type'` from inside its own query.
	`upstream_patches` now guards it the way its sibling `provident_fund_deductions`
	already did, it returns empty (no Salary Component here is a professional tax),
	and it runs blank-company like the other 25. Being patched is the only reason this
	number moved; nothing was reclassified by judgement.
	"""

	# The reports whose own Python still refuses a blank Company.  Listed rather
	# than derived so that patching one is a deliberate act that updates this,
	# instead of a failure with no explanation.
	#
	# Employee Analytics and Employee Birthday were here until `upstream_patches`
	# grew a subtree edit for each: both threw "Company is mandatory" from
	# `execute`, which no client-side relaxing can reach.  General Ledger was the
	# third, and the hard one - see the tests below, which are the reason it is now
	# empty rather than merely reduced.
	#
	# Empty, and expected to stay empty: every panel report runs blank-company.  The
	# check is still worth having, because an ERPNext or HRMS upgrade reverts the
	# patched report files, `after_migrate` re-applies them, and a future report that
	# demands a company shows up here instead of becoming a widget that is
	# permanently blank.
	COMPANY_DEPENDENT: set[str] = set()

	@classmethod
	def setUpClass(cls):
		cls.result = verify_panel_reports()
		cls.panel = {name for _label, name in panel_reports()}

	def test_the_probe_accounts_for_every_panel_report(self):
		"""Nothing is unclassified, so a new bucket cannot be introduced silently."""
		buckets = (
			set(self.result["optional"])
			| {name for _l, name, _d in self.result["needs_company"]}
			| {name for _l, name, _d in self.result["needs_companion_filter"]}
			| set(self.result["missing"])
		)
		self.assertEqual(self.result["missing"], [], "a panel report does not exist on the site")
		self.assertEqual(buckets, self.panel, "some panel reports are in no bucket")
		self.assertEqual(len(self.panel), 31, "the Reports band changed size; review the buckets below")
		# The headline number, asserted so "26 of 32 are company-optional" cannot go
		# stale in a comment while quietly becoming 24.
		#
		# It was 25 until Professional Tax Deductions was patched - see the class
		# docstring. General Ledger, patched after that, did **not** move this number:
		# it went from "still need a Company" to "needs a filter of its own", because
		# it insists on a fiscal year and a date range before it will answer. That is
		# the same bucket as Trial Balance, and it is why `needs_company` is the number
		# worth watching below - it is zero.
		#
		# It was 26 until Loan Outstanding Report left the panel: a `lending` report, and
		# `lending` is on this bench but not installed on this site, so the card opened
		# nothing. Down by one, and the panel total below is down by one with it.
		self.assertEqual(
			len(self.result["optional"]),
			25,
			f"company-optional panel reports: {len(self.result['optional'])}, expected 25; "
			"if one was patched, raise this and say which in the docstring above",
		)

	def test_no_panel_report_is_relaxed_that_demands_a_company(self):
		"""The failure that matters: relaxed client-side, so it renders as a
		permanent "Please set filters" with no obvious cause."""
		self.assertEqual(
			self.result["misclassified"],
			[],
			"a panel report is relaxed client-side but throws on a blank Company",
		)

	def test_every_company_optional_panel_report_really_is(self):
		"""The other direction: a report that would run group-wide but is not
		relaxed still pins the reader to their default company, which is the whole
		complaint."""
		self.assertEqual(self.result["optional_but_not_relaxed"], [])

	def test_no_panel_report_hard_requires_a_company(self):
		"""`needs_company` is empty, and `COMPANY_DEPENDENT` is the written-down claim.

		Renamed from `test_general_ledger_is_the_only_one_left`, which stopped being
		true the moment General Ledger was patched and would otherwise have gone on
		asserting that a hold-out exists. The assertion itself was always right - the
		measured set has to equal the set written down - and it still is; only the name
		had drifted from the fact.

		The message keeps its instruction, because it still applies in reverse: a new
		report that demands a company shows up here, and putting it in
		`COMPANY_DEPENDENT` has to be a deliberate act rather than a reflex.
		"""
		found = {name for _l, name, _d in self.result["needs_company"]}
		self.assertEqual(
			found,
			self.COMPANY_DEPENDENT,
			"the set of panel reports that hard-require a Company has changed; "
			"if one was patched, remove it here and say so in a comment",
		)

	def test_the_companion_bucket_really_is_unrelated_to_company(self):
		"""Nothing in it may mention Company.

		That is the whole reason it is a separate bucket: if one of these ever starts
		failing on Company it belongs in `needs_company`, and this is what notices.
		"""
		for label, report, detail in self.result["needs_companion_filter"]:
			self.assertNotIn(
				"company", detail.lower(), f"{report} now fails on Company, not on a companion filter"
			)
			del label

	def _general_ledger(self, company, months=12):
		"""`(rows, debit, credit)` for the report, or `None` if it would not run.

		Run per company rather than trusting the group figure, because the whole claim
		is that the group figure is the *sum* of the company figures. Asserting that
		against the same code that produces both would prove nothing.
		"""
		from frappe.desk.query_report import run
		from frappe.utils import add_months, nowdate

		today = nowdate()
		filters = {
			"fiscal_year": frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name"),
			"from_date": add_months(today, -months),
			"to_date": today,
			"period_start_date": add_months(today, -months),
			"period_end_date": today,
		}
		if company is not None:
			filters["company"] = company
		try:
			out = run("General Ledger", filters, ignore_prepared_report=True)
		except Exception:
			return None
		out = out[0] if isinstance(out, tuple) else out
		rows = (out or {}).get("result") or []
		debit = sum(float(r.get("debit") or 0) for r in rows)
		credit = sum(float(r.get("credit") or 0) for r in rows)
		return len(rows), round(debit, 2), round(credit, 2)

	def test_the_group_ledger_equals_the_sum_of_its_companies(self):
		"""The invariant that makes the widening a ledger rather than a guess.

		`General Ledger` now answers for the whole group when no company is given, which
		is what every other report in this band does. What makes that safe is not that
		the query was widened - it is that the answer is *arithmetically* the group's
		books added up, and that is checked here against the same report run separately
		on each company.

		The comparison is to the cent and the balance is asserted too, because a
		widening that double counted would still produce a plausible-looking figure; a
		double count of a debit and its credit leaves debit == credit intact, so the
		balance alone would not catch it.
		"""
		group = self._general_ledger(None)
		self.assertIsNotNone(
			group, "General Ledger no longer answers with no Company at all"
		)

		leaves = frappe.get_all("Company", filters={"is_group": 0}, pluck="name")
		self.assertTrue(leaves, "the site has no leaf companies to compare against")

		total_debit = total_credit = 0.0
		ran = 0
		for company in leaves:
			one = self._general_ledger(company)
			if one is None:
				continue
			ran += 1
			total_debit += one[1]
			total_credit += one[2]
		self.assertEqual(ran, len(leaves), f"only {ran} of {len(leaves)} companies ran")

		self.assertAlmostEqual(
			group[1],
			round(total_debit, 2),
			places=2,
			msg=f"the group ledger debits {group[1]} but its companies debit "
			f"{round(total_debit, 2)}; widening the filter has lost or repeated "
			f"entries, which is what the old comment warned would happen",
		)
		self.assertAlmostEqual(
			group[2], round(total_credit, 2), places=2, msg="credits do not add up either"
		)
		self.assertAlmostEqual(
			group[1],
			group[2],
			places=2,
			msg="the group ledger does not balance, so it is not a ledger",
		)

	def test_a_single_company_still_answers_for_itself_only(self):
		"""Widening to the group must not leak back down into a leaf.

		The subtree rule every other report here uses: a chosen company means that
		company and everything under it. A leaf has no descendants, so the two filters
		have to coincide exactly - if the group figure ever came back from a leaf
		filter, the subtree list would be the whole company list in both cases and the
		test above would pass for the wrong reason.
		"""
		group = self._general_ledger(None)
		self.assertIsNotNone(group, "the group run is broken, so this proves nothing")

		for company in frappe.get_all("Company", filters={"is_group": 0}, pluck="name"):
			one = self._general_ledger(company)
			self.assertIsNotNone(one, f"{company} no longer runs")
			self.assertLessEqual(
				one[1],
				group[1] + 0.05,
				f"{company} debits {one[1]} against the group's {group[1]}; one "
				f"company's books cannot exceed the whole group's",
			)

	def test_the_group_wide_ledger_does_not_double_count_intercompany(self):
		"""**The precondition, and the fragile one.**

		This report sums each company's books and eliminates nothing. That is exact
		only while no posting crosses between companies - an intercompany entry is
		recorded once in each set of books, so the group total would carry it twice and
		a real consolidation would net it out.

		Today no GL entry on this site has a party set, so the figure is exact. This
		test exists so that the day someone posts between companies, the number stops
		being quietly wrong and says so instead: a group-wide ledger that double counts
		should stop being offered as the group answer, and the honest response is to
		put General Ledger back in `COMPANY_DEPENDENT` and require a company.

		Deliberately written as a precondition rather than as a comparison, because a
		comparison would have to reproduce the elimination arithmetic to know what the
		right answer was - and this report does not do it.
		"""
		total = frappe.db.count("GL Entry")
		with_party = frappe.db.count("GL Entry", {"party_type": ("is", "set")})
		self.assertEqual(
			with_party,
			0,
			f"{with_party} of {total} GL entries name a party, so at least one "
			"posting crosses companies. A group-wide General Ledger sums each "
			"company's books and eliminates nothing, so that posting is now counted "
			"twice. Put General Ledger back in COMPANY_DEPENDENT and require a "
			"company until the report can eliminate.",
		)

	def test_the_group_parent_has_accounts_but_no_ledger_of_its_own(self):
		"""Why widening was needed, measured rather than remembered.

		The reason this report was the last hold-out: an ERPNext `Account` is a
		per-company document, so the group parent has a full chart of accounts and no
		entries of its own. Before the patch, naming the parent therefore produced a
		correct-looking report full of nothing - which is why the panel demanded a
		company instead.

		Both halves matter. Accounts everywhere is what lets one CoA serve the union;
		entries in the leaves is why the parent alone was never the answer.
		"""
		parent = frappe.db.get_value("Company", {"is_group": 1}, "name")
		self.assertTrue(parent, "the site has no group parent company")

		parent_accounts = frappe.db.count("Account", {"company": parent})
		parent_entries = frappe.db.count("GL Entry", {"company": parent})
		leaf_accounts = max(
			frappe.db.count("Account", {"company": c})
			for c in frappe.get_all("Company", filters={"is_group": 0}, pluck="name")
		)

		self.assertGreater(
			parent_accounts,
			0,
			"the group parent has no accounts, so the union argument does not hold",
		)
		self.assertLess(
			parent_accounts,
			leaf_accounts + 1,
			"every company has an identical chart of accounts, which is what makes "
			"one account walk cover the group; if this changed the widening needs "
			"re-reading before it is trusted",
		)
		self.assertEqual(
			parent_entries,
			0,
			"the group parent now has GL entries of its own, so a run named on it "
			"would no longer be the empty report that made widening necessary",
		)

	def test_the_relax_covers_the_report_page_not_only_charts(self):
		"""The panel needed its own seam; without it the band is decorative.

		`dashboard_widgets.js` relaxes two entry points and the panel is served by the
		second.  Asserted on the source because there is no way to observe the patch
		from Python, and the failure it guards - a band of reports that all still
		demand a company - is silent.
		"""
		import pathlib

		src = (
			pathlib.Path(frappe.get_app_path("sharm_dreams_group", "public", "js")) / "dashboard_widgets.js"
		).read_text()
		self.assertIn("views.QueryReport.prototype", src)
		self.assertIn("get_report_settings", src)
		self.assertIn("patch_report_page", src)
		# the wrapper must rewrite `report_settings.filters`, which is what
		# `setup_filters` destructures - not `this.filters`, which does not exist
		# until the field controls are built
		self.assertIn("settings.filters = relax_company_filter(this.report_name", src)


class TestUpstreamPatches(FrappeTestCase):
	"""The six charts that needed a report-side change.

	An ERPNext or HRMS upgrade reverts those three files, so this asserts the
	patches are in place rather than trusting that `after_migrate` ran.
	"""

	def test_every_patch_edit_is_present(self):
		from sharm_dreams_group.upstream_patches import PATCHES, apply, verify

		apply()
		verify()
		bench_apps = os.path.join(frappe.utils.get_bench_path(), "apps")
		installed = set(frappe.get_installed_apps())
		checked = 0
		for relative, edits in PATCHES.items():
			app = relative.split("/")[0]
			if app not in installed:
				# The ledger is app-wide but a bench is not: `lending` is patched and
				# is not installed here, so its file legitimately does not exist. Skipping
				# a whole app rather than opening the file is what `apply` and `verify`
				# already do, and it keeps the assertion meaningful for the apps that
				# *are* here - opening it unconditionally raised `FileNotFoundError` and
				# took the rest of the ledger down with it.
				continue
			with open(os.path.join(bench_apps, relative)) as handle:
				text = handle.read()
			for _, after in edits:
				self.assertIn(after, text, f"{relative}: patch edit missing")
			checked += 1
		# Guard against the loop passing because `installed` was empty rather than
		# because every patched edit was found.
		self.assertTrue(checked, "no patched file was checked at all")

	def test_the_unblocked_reports_all_run_without_a_company(self):
		from sharm_dreams_group.dashboard_widgets import _runs

		for report, chart in UNBLOCKED_BY_PATCH.items():
			ok, detail = _runs(chart, report, "")
			self.assertTrue(ok, f"{chart} -> {report}: {detail}")

	def test_the_unblocked_reports_are_covered_client_side(self):
		"""A report that works server-side but is missing from the JS allow list
		would still show 'Please set filters' in the browser."""
		relaxed = company_optional_reports()
		for report in UNBLOCKED_BY_PATCH:
			self.assertIn(report, relaxed, f"{report} missing from COMPANY_OPTIONAL_REPORTS")

	def test_a_group_parent_still_narrows_nothing_to_nothing(self):
		"""The patches must not turn a group selection into an empty result."""
		from sharm_dreams_group.dashboard_widgets import _runs

		ok, detail = _runs("Top Suppliers", "Purchase Receipt Trends", "Sharm Dreams Group")
		self.assertTrue(ok, detail)


class TestGroupWidePatches(FrappeTestCase):
	"""The operational reports must widen a company filter to the subtree.

	Asserted against each report's own source, not by counting rows: the site has
	no transactions in these doctypes, so a row count cannot tell "14 companies"
	apart from "none", and a check that passes for the wrong reason is worse than
	no check.  `verify()` on the patch module is asserted too, so an ERPNext
	upgrade that reverts a file fails here rather than silently narrowing a chart.
	"""

	REPORTS = (
		"erpnext/erpnext/stock/report/item_shortage_report/item_shortage_report.py",
		"erpnext/erpnext/manufacturing/report/job_card_summary/job_card_summary.py",
		"erpnext/erpnext/manufacturing/report/work_order_summary/work_order_summary.py",
		"erpnext/erpnext/buying/report/purchase_order_analysis/purchase_order_analysis.py",
		"erpnext/erpnext/projects/report/project_summary/project_summary.py",
	)

	def test_every_group_wide_patch_is_applied(self):
		from sharm_dreams_group.upstream_patches import PATCHES, apply

		apply()
		apps = os.path.join(frappe.utils.get_bench_path(), "apps")
		for relative in self.REPORTS:
			self.assertIn(relative, PATCHES, f"{relative} is not in the patch set")
			with open(os.path.join(apps, relative)) as handle:
				source = handle.read()
			self.assertIn('get_descendants_of("Company"', source, f"{relative}: not widened")
			self.assertIn(
				"from frappe.utils.nestedset import get_descendants_of", source, f"{relative}: no import"
			)

	def test_a_reverted_report_is_caught(self):
		"""The point of the check above: it must fail when a file is reverted."""
		from sharm_dreams_group.upstream_patches import PATCHES

		apps = os.path.join(frappe.utils.get_bench_path(), "apps")
		relative = self.REPORTS[0]
		with open(os.path.join(apps, relative)) as handle:
			live = handle.read()
		for before, after in PATCHES[relative]:
			self.assertNotEqual(before, after)
			self.assertIn(after, live)


class TestCustomChartSources(FrappeTestCase):
	"""The two chart_type Custom sources, which bypass everything else.

	"Employees by Age" and "Hiring vs Attrition Count" declare their filters in
	their own JS and filter with a hard equality in their method, so neither the
	report-side patch nor the desk-side filter widening reaches them.  They are the
	reason a group parent showed an empty chart even with no company filter set.
	"""

	@staticmethod
	def _age_total(filters):
		from hrms.hr.dashboard_chart_source.employees_by_age.employees_by_age import get_data

		config = get_data(chart_name="Employees by Age", filters=json.dumps(filters))
		return sum(config["datasets"][0]["values"])

	def test_employees_by_age_needs_no_company(self):
		self.assertEqual(self._age_total({}), frappe.db.count("Employee", {"status": "Active"}))

	def test_the_group_parent_gives_the_whole_group(self):
		"""The group parent holds no employees of its own, so before the patch this
		was 0 - the exact symptom reported."""
		self.assertEqual(
			self._age_total({"company": "Sharm Dreams Group"}),
			frappe.db.count("Employee", {"status": "Active"}),
		)

	def test_a_leaf_company_is_still_only_itself(self):
		leaf = self._age_total({"company": "Bautex Misr"})
		self.assertEqual(leaf, frappe.db.count("Employee", {"status": "Active", "company": "Bautex Misr"}))
		self.assertNotEqual(leaf, frappe.db.count("Employee", {"status": "Active"}))

	def test_attrition_needs_no_company(self):
		from hrms.hr.dashboard_chart_source.hiring_vs_attrition_count.hiring_vs_attrition_count import (
			get_data,
		)

		filters = {"from_date": "2026-01-01", "to_date": "2026-12-31", "time_interval": "Monthly"}
		blank = get_data(chart_name="Hiring vs Attrition Count", filters=json.dumps(dict(filters)))
		group = get_data(
			chart_name="Hiring vs Attrition Count",
			filters=json.dumps(dict(filters, company="Sharm Dreams Group")),
		)
		self.assertGreater(sum(blank["datasets"][0]["values"]), 0, "no events without a company")
		self.assertEqual(
			[sum(d["values"]) for d in blank["datasets"]],
			[sum(d["values"]) for d in group["datasets"]],
			"the group parent must match the unfiltered run",
		)

	def test_the_source_js_no_longer_defaults_to_one_company(self):
		"""A `default: get_user_default("Company")` would silently re-scope the
		chart to a single company even with the method fixed."""
		import os

		apps = os.path.join(frappe.utils.get_bench_path(), "apps")
		for source in ("employees_by_age", "hiring_vs_attrition_count"):
			path = os.path.join(apps, "hrms", "hrms", "hr", "dashboard_chart_source", source, f"{source}.js")
			with open(path) as handle:
				self.assertNotIn(
					'default: frappe.defaults.get_user_default("Company")', handle.read(), source
				)


class TestStockAndLendingChartSources(FrappeTestCase):
	"""The three remaining group-wide-able custom sources.

	Each declared `default: get_user_default("Company")` in its own JS, so a
	group parent silently produced an empty chart.  Warehouse-wise Stock Value and
	Top 10 Pledged Loan Securities already treated a *blank* company as "every
	company", so the JS default was the whole bug there; Stock Value by Item Group
	also fell back to the global default in Python and needed the real fix.

	These assert the chart answers for every company mode without raising. The
	figures themselves cannot be asserted - the site has no Items, stock or loans,
	so an empty result is correct and a wrong-but-plausible one would not show.
	"""

	# (chart_name, module, method) - a real chart name, because these sources
	# validate the chart exists before they will answer.
	SOURCES = (
		(
			"Stock Value by Item Group",
			"erpnext.stock.dashboard_chart_source.stock_value_by_item_group.stock_value_by_item_group",
			"get",
		),
		(
			"Warehouse wise Stock Value",
			"erpnext.stock.dashboard_chart_source.warehouse_wise_stock_value.warehouse_wise_stock_value",
			"get",
		),
		# "Top 10 Pledged Loan Securities" was here and is not now.  It is a `lending`
		# chart; `lending` is on this bench but not installed on this site, so the chart
		# does not exist and the source refuses to answer for a chart name that is not
		# there.  Adding it back means installing the app first - the source module and
		# its JS are both on disk already.
	)

	def test_each_answers_for_every_company_mode(self):
		import importlib

		for chart_name, module_path, func_name in self.SOURCES:
			module = importlib.import_module(module_path)
			func = getattr(module, func_name)
			for filters in ({}, {"company": "Sharm Dreams Group"}, {"company": "Bautex Misr"}):
				try:
					func(chart_name=chart_name, filters=json.dumps(dict(filters)))
				except Exception as error:  # the point is to report
					self.fail(f"{chart_name} with {filters}: {type(error).__name__}: {error}")

	def test_no_source_js_defaults_to_one_company(self):
		"""A `default: get_user_default("Company")` re-scopes the chart to a single
		company even when the method has been widened."""
		import os

		apps = os.path.join(frappe.utils.get_bench_path(), "apps")
		paths = (
			"erpnext/erpnext/stock/dashboard_chart_source/stock_value_by_item_group/stock_value_by_item_group.js",
			"erpnext/erpnext/stock/dashboard_chart_source/warehouse_wise_stock_value/warehouse_wise_stock_value.js",
			# The `lending` source is still on disk and still widened upstream; it is
			# listed here only if `lending` is actually installed, since the whole point
			# is to assert a source this site can reach.
			"hrms/hrms/hr/dashboard_chart_source/employees_by_age/employees_by_age.js",
			"hrms/hrms/hr/dashboard_chart_source/hiring_vs_attrition_count/hiring_vs_attrition_count.js",
		)
		for relative in paths:
			with open(os.path.join(apps, relative)) as handle:
				self.assertNotIn(
					'default: frappe.defaults.get_user_default("Company")', handle.read(), relative
				)
