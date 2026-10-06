# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the salary structure build.

The build is mostly about not putting money in the wrong place, so the tests
cover the three things that could do that: the structure an employee is attached
to, the company it belongs to, and the amount recorded as their base.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt

from sharm_dreams_group import companies as companies_mod
from sharm_dreams_group.hr import importer, salary


class TestComponents(FrappeTestCase):
	"""Every column the workbook actually uses has a component."""

	def test_all_five_earnings_exist(self):
		for name, _label, _description, _abbr in salary.EARNINGS:
			self.assertTrue(frappe.db.exists("Salary Component", name), f"missing component {name}")

	def test_they_are_earnings_not_deductions(self):
		for name, _label, _description, _abbr in salary.EARNINGS:
			kind = frappe.db.get_value("Salary Component", name, "type")
			self.assertEqual(kind, "Earning", f"{name} is a {kind}")

	def test_no_overtime_component(self):
		"""Overtime is zero on all 207 rows.

		A component nothing uses is a column someone will eventually fill in by
		hand, and a figure typed into it would never have been in the workbook.
		"""
		self.assertNotIn("Overtime", [name for name, _l, _d, _a in salary.EARNINGS])

	def test_abbreviations_cover_every_component(self):
		for name, _label, _description, abbr in salary.EARNINGS:
			self.assertTrue(abbr, f"{name} has no abbreviation")
			self.assertEqual(salary.ABBR[name], abbr)


class TestStructures(FrappeTestCase):
	def test_one_structure_per_payroll_company(self):
		"""Each structure belongs to exactly the company it is named for."""
		rows = frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		)
		self.assertTrue(rows, "no SDG salary structures were created")
		for name in rows:
			company = frappe.db.get_value("Salary Structure", name, "company")
			self.assertEqual(name, salary.structure_name(company), name)

	def test_structures_carry_all_earning_rows(self):
		name = salary.structure_name(
			frappe.db.get_value("Salary Structure", {"name": ["like", "SDG %"]}, "company")
		)
		doc = frappe.get_doc("Salary Structure", name)
		self.assertEqual(
			[row.salary_component for row in doc.earnings],
			[name for name, _l, _d, _a in salary.EARNINGS],
		)

	def test_every_structure_declares_the_statutory_deductions(self):
		"""A slip made from the structure should already carry these rows.

		The engine appends the statutory three if they are missing, so that is not
		strictly required - but an empty deductions list leaves HR looking at a
		structure with no statutory deductions on it at all.

		The two pre-tax components (penalties, absenteeism) belong in the same
		table: they come off gross before tax, so the structure has to declare
		them alongside.  They are *deductions* and must never appear in `earnings`,
		where they would inflate gross pay.
		"""
		want = [
			c
			for c, _a, _d in (
				*salary.DEDUCTIONS,
				*salary.PRE_TAX_DEDUCTIONS,
				*salary.POST_TAX_DEDUCTIONS,
			)
		]
		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			doc = frappe.get_doc("Salary Structure", name)
			self.assertEqual([r.salary_component for r in doc.deductions], want, name)

	def test_no_earning_row_is_actually_a_deduction(self):
		"""Penalties and absenteeism are deductions, not earnings.

		The workbook prints them in the same block as the earnings, which is
		exactly how they first ended up in a structure's `earnings` table and were
		silently added to gross pay.
		"""
		deduction_names = {c for c, _a, _d in (*salary.DEDUCTIONS, *salary.PRE_TAX_DEDUCTIONS)}
		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			doc = frappe.get_doc("Salary Structure", name)
			earnings = {r.salary_component for r in doc.earnings}
			self.assertEqual(sorted(earnings & deduction_names), [], name)

	def test_every_structure_declares_the_employer_contributions(self):
		want = [c for c, _a, _d in salary.EMPLOYER_CONTRIBUTIONS]
		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			doc = frappe.get_doc("Salary Structure", name)
			self.assertEqual([r.salary_component for r in doc.employer_contributions], want, name)

	def test_the_six_components_the_engine_writes_are_all_declared(self):
		"""`REQUIRED_COMPONENTS` is the engine's own list; every one must appear."""
		from sharm_dreams_group.payroll.egypt.deductions import REQUIRED_COMPONENTS

		declared = {c for c, _l, _d, _a in salary.EARNINGS}
		declared |= {c for c, _a, _d in salary.DEDUCTIONS}
		declared |= {c for c, _a, _d in salary.EMPLOYER_CONTRIBUTIONS}
		self.assertEqual(sorted(set(REQUIRED_COMPONENTS) - declared), [])

	def test_no_deduction_demands_an_erpnext_income_tax_slab(self):
		"""`variable_based_on_taxable_salary` would trigger the requirement.

		hrms.get_tax_component() returns the first deduction with that set and no
		formula or amount, and Salary Structure Assignment.validate_income_tax_slab
		then insists on an ERPNext `Income Tax Slab`.  This app computes tax from
		the SDG Tax Bracket table instead, so nothing must set that flag.
		"""
		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			doc = frappe.get_doc("Salary Structure", name)
			for row in doc.deductions:
				self.assertFalse(
					cint(row.variable_based_on_taxable_salary),
					f"{name}/{row.salary_component} would require an Income Tax Slab",
				)

	def test_get_tax_component_finds_nothing(self):
		"""Proves the ERPNext tax-slab machinery stays switched off."""
		from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
			get_tax_component,
		)

		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			self.assertIsNone(get_tax_component(name), name)

	def test_structure_rows_are_templates_at_zero(self):
		"""The structure cannot hold a per-employee figure in this HRMS version."""
		for name in frappe.get_all(
			"Salary Structure", filters={"name": ["like", "SDG %"]}, pluck="name", limit_page_length=0
		):
			doc = frappe.get_doc("Salary Structure", name)
			for table in ("earnings", "deductions", "employer_contributions"):
				for row in doc.get(table):
					self.assertEqual(flt(row.amount), 0.0, f"{name}/{row.salary_component}")


class TestAssignments(FrappeTestCase):
	"""Reconciled against the workbook, not just "a row exists"."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.expected = {}
		for record in importer.load_fixture()["employees"]:
			who = record["employee_number"]
			base = next((l for l in record.get("payroll_lines", []) if not l.get("is_delegated")), None)
			if not base or not frappe.db.exists("Employee", who):
				continue
			# Where the master and the payroll sheet disagree on the employing
			# company, the Employee record wins - HRMS forces it anyway, because
			# `Salary Structure Assignment.company` is `fetch_from: employee.company`.
			company = frappe.db.get_value("Employee", who, "company")
			cls.expected[who] = (company, base["earnings"])

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		super().tearDownClass()

	def test_expected_count(self):
		"""All 170 people with a payroll line, including the 2 company conflicts."""
		self.assertEqual(len(self.expected), 170)

	def test_one_assignment_per_expected_employee(self):
		stored = set(frappe.get_all("Salary Structure Assignment", pluck="employee", limit_page_length=0))
		self.assertEqual(sorted(set(self.expected) - stored), [])
		self.assertEqual(sorted(stored - set(self.expected)), [])

	def test_each_assignment_uses_the_structure_of_its_company(self):
		for row in frappe.get_all(
			"Salary Structure Assignment",
			fields=["name", "employee", "company", "salary_structure"],
			limit_page_length=0,
		):
			if row["employee"] not in self.expected:
				continue
			company, _earnings = self.expected[row["employee"]]
			self.assertEqual(row["company"], company, row["employee"])
			self.assertEqual(row["salary_structure"], salary.structure_name(company), row["employee"])

	def test_base_equals_the_workbook_basic(self):
		"""`base` is the only per-employee amount the assignment can hold."""
		for row in frappe.get_all(
			"Salary Structure Assignment",
			fields=["name", "employee", "base"],
			limit_page_length=0,
		):
			if row["employee"] not in self.expected:
				continue
			_company, earnings = self.expected[row["employee"]]
			self.assertAlmostEqual(
				flt(row["base"]), flt(earnings.get("Basic", 0.0)), places=2, msg=row["employee"]
			)

	def test_basic_total_matches_the_workbook(self):
		# submitted only: a cancelled assignment left behind by an amendment still
		# carries its `base`, and counting it would double the total
		total = sum(
			flt(r["base"])
			for r in frappe.get_all(
				"Salary Structure Assignment",
				filters={"docstatus": 1},
				fields=["name", "base"],
				limit_page_length=0,
			)
			if r["name"]
		)
		expected = sum(flt((e or {}).get("Basic", 0.0)) for _c, e in self.expected.values())
		self.assertAlmostEqual(total, expected, places=2)

	def test_from_date_is_inside_the_fiscal_year_and_after_joining(self):
		"""HRMS wants from_date >= joining; ERPNext wants it in a Fiscal Year."""
		start, end = salary.current_fiscal_year()
		# only submitted assignments are in effect: a cancelled one - the amendment
		# chain keeps them for the audit trail - pays nobody
		for row in frappe.get_all(
			"Salary Structure Assignment",
			filters={"docstatus": 1},
			fields=["name", "employee", "from_date"],
			limit_page_length=0,
		):
			self.assertGreaterEqual(str(row["from_date"]), start, row["employee"])
			self.assertLessEqual(str(row["from_date"]), end, row["employee"])
			joining = frappe.db.get_value("Employee", row["employee"], "date_of_joining")
			if joining:
				self.assertGreaterEqual(str(row["from_date"]), str(joining), row["employee"])

	def test_no_delegated_share_was_assigned(self):
		"""A second assignment would be a second payslip for the same month.

		37 payroll lines are cost shares, not employment, so no employee may have
		more than one assignment.
		"""
		rows = frappe.get_all(
			"Salary Structure Assignment",
			filters={"docstatus": 1},
			fields=["name", "employee"],
			limit_page_length=0,
		)
		counts: dict[str, int] = {}
		for row in rows:
			counts[row["employee"]] = counts.get(row["employee"], 0) + 1
		self.assertEqual(sorted(e for e, c in counts.items() if c > 1), [])


class TestFiscalYear(FrappeTestCase):
	def test_a_current_fiscal_year_exists(self):
		start, end = salary.current_fiscal_year()
		self.assertLess(start, end)

	def test_from_date_never_predates_joining(self):
		"""A 1990s joiner must not get a 1990s from_date.

		The site has one fiscal year, so using date_of_joining raised
		FiscalYearError on the first long-tenured employee.
		"""
		window = salary.current_fiscal_year()
		self.assertEqual(salary.assignment_from_date("1996-05-26", window), window[0])

	def test_a_later_joiner_keeps_their_own_date(self):
		window = salary.current_fiscal_year()
		self.assertEqual(salary.assignment_from_date("2026-08-09", window), "2026-08-09")

	def test_no_date_uses_the_fiscal_year_start(self):
		window = salary.current_fiscal_year()
		self.assertEqual(salary.assignment_from_date(None, window), window[0])


class TestPlanSafety(FrappeTestCase):
	def test_plan_writes_nothing(self):
		before = (
			frappe.db.count("Salary Structure Assignment"),
			frappe.db.count("Salary Structure"),
		)
		salary.build_plan()
		frappe.db.rollback()
		self.assertEqual(
			(
				frappe.db.count("Salary Structure Assignment"),
				frappe.db.count("Salary Structure"),
			),
			before,
		)

	def test_company_conflicts_are_reported_and_assigned_under_the_employee_record(self):
		"""The master's employer and the payroll's cannot both be right.

		They are reported, and the employee is paid under the Employee record's
		company, because that is the only one HRMS can assign: the assignment's
		company is fetched from the employee, so leaving these two unassigned would
		have silently left them out of the payroll entirely.
		"""
		result = salary.build_plan()
		frappe.db.rollback()
		assigned = {a["employee_number"]: a for a in result["assignments"]}
		self.assertTrue(result["conflicts"], "the two known conflicts should be reported")
		for row in result["conflicts"]:
			self.assertNotEqual(row["employee_company"], row["payroll_company"])
			self.assertIn(row["employee_number"], assigned)
			self.assertEqual(
				assigned[row["employee_number"]]["structure"],
				salary.structure_name(row["employee_company"]),
			)

	def test_delegated_shares_are_never_assigned(self):
		result = salary.build_plan()
		frappe.db.rollback()
		assigned = {a["employee_number"] for a in result["assignments"]}
		self.assertTrue(result["delegated"], "the fixture should carry delegated shares")
		# a delegated share belongs to someone who also has a base line
		for row in result["delegated"]:
			self.assertIn(row["employee_number"], assigned)


class TestSpecPeriod(FrappeTestCase):
	"""The spec's month, and the refusal to fill an entry from a different one.

	Filling an October entry with August figures produced slips that looked
	entirely ordinary and were wrong on every line, so the month is a declared
	fact and a mismatch stops the fill rather than being noted afterwards.
	"""

	def _entry_for(self, month: str):
		import calendar

		start = f"{month}-01"
		year, mon = (int(x) for x in month.split("-"))
		last_day = calendar.monthrange(year, mon)[1]
		# modelled on a real entry so the mandatory accounting fields are present;
		# the period check does not care about them, but the insert would refuse.
		template = frappe.get_all(
			"Payroll Entry",
			filters={"docstatus": 1},
			fields=["company", "payroll_payable_account", "currency", "exchange_rate"],
			limit_page_length=1,
		)
		if not template:
			self.skipTest("no submitted Payroll Entry to model the fields on")
		ref = template[0]
		return frappe.get_doc(
			{
				"doctype": "Payroll Entry",
				"company": ref.company,
				"currency": ref.currency,
				"exchange_rate": ref.exchange_rate or 1,
				"payroll_payable_account": ref.payroll_payable_account,
				"start_date": start,
				"end_date": f"{month}-{last_day:02d}",
				"posting_date": start,
				"payroll_frequency": "Monthly",
				"payment_mode": "Bank",
			}
		).insert(ignore_permissions=True)

	def test_the_spec_declares_its_month(self):
		from sharm_dreams_group.payroll import run

		period = run.spec_period()
		self.assertTrue(period, "the spec must say which month it pays for")
		self.assertRegex(period, r"^\d{4}-\d{2}$", period)

	def test_a_matching_entry_is_allowed(self):
		from sharm_dreams_group.payroll import run

		entry = self._entry_for(run.spec_period())
		self.assertIsNone(run.period_mismatch(entry))

	def test_a_different_month_is_refused_and_says_which(self):
		from sharm_dreams_group.payroll import run

		spec = run.spec_period()
		year, mon = (int(x) for x in spec.split("-"))
		other = f"{year + (mon == 12)}-{(mon % 12) + 1:02d}"
		entry = self._entry_for(other)

		problem = run.period_mismatch(entry)
		self.assertIsNotNone(problem, "a mismatch must be reported")
		self.assertIn(other, problem)
		self.assertIn(spec, problem)

	def test_the_refusal_actually_stops_the_fill(self):
		from sharm_dreams_group.payroll import run

		spec = run.spec_period()
		year, mon = (int(x) for x in spec.split("-"))
		other = f"{year + (mon == 12)}-{(mon % 12) + 1:02d}"
		entry = self._entry_for(other)
		entry.employees = []

		self.assertRaises(frappe.ValidationError, run.build_for_entry, entry.name)
