# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the employee importer's fixture and plan logic.

The write path is not exercised here: importing 182 real employees is not a
unit test.  What matters and is cheap to check is that the fixture is
self-consistent, that the join is stable, and that the validator catches the
conditions that would silently corrupt a payroll run.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.hr import importer


class TestFixture(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.data = importer.load_fixture()
		cls.records = cls.data["employees"]

	def test_every_record_has_an_employee_number(self):
		"""The number is the fallback join key and the desk's identity label."""
		missing = [r["employee_number"] for r in self.records if not r["employee_number"]]
		self.assertEqual(missing, [])

	def test_employee_numbers_are_unique(self):
		numbers = [r["employee_number"] for r in self.records]
		self.assertEqual(sorted(n for n in numbers if numbers.count(n) > 1), [], "duplicate employee numbers")

	def test_names_split_into_first_and_last(self):
		"""ERPNext derives employee_name from these, so last_name cannot be empty."""
		for record in self.records:
			self.assertTrue(record["first_name"], f"{record['employee_number']} has no first name")
			self.assertTrue(record["last_name"], f"{record['employee_number']} has no last name")

	def test_names_have_no_double_spaces(self):
		for record in self.records:
			for part in ("first_name", "middle_name", "last_name"):
				value = record[part]
				self.assertNotIn("  ", value, f"{record['employee_number']} {part}={value!r}")

	def test_summary_matches_the_records(self):
		summary = self.data["summary"]
		self.assertEqual(summary["people"], len(self.records))
		by_source: dict[str, int] = {}
		for record in self.records:
			by_source[record["source"]] = by_source.get(record["source"], 0) + 1
		self.assertEqual(summary["from_both"], by_source.get("both", 0))
		self.assertEqual(summary["master_only"], by_source.get("master", 0))
		self.assertEqual(summary["payroll_only"], by_source.get("payroll", 0))

	def test_payroll_line_count_reconciles(self):
		"""207 payroll lines belong to 170 people; both must survive the join."""
		total = sum(r.get("payroll_line_count", 0) for r in self.records)
		self.assertEqual(total, self.data["summary"]["payroll_lines"])
		self.assertEqual(total, 207, "the payroll workbook has 207 lines")

	def test_national_ids_are_unique_or_flagged(self):
		"""A duplicate National ID must be flagged, never silently imported."""
		seen: dict[str, str] = {}
		for record in self.records:
			nid = record["national_id"]
			if not nid:
				continue
			if nid in seen:
				self.assertIn(
					"duplicate_national_id",
					record["flags"],
					f"{record['employee_number']} shares {nid} with {seen[nid]} but is not flagged",
				)
			seen[nid] = record["employee_number"]

	def test_records_requiring_review_are_flagged(self):
		for record in self.records:
			if record["source"] == "payroll":
				self.assertIn("review_required", record["flags"], record["employee_number"])

	def test_fixture_is_valid_json_on_disk(self):
		"""A truncated fixture must fail loudly, not import half a company."""
		raw = importer.FIXTURE.read_text()
		self.assertTrue(json.loads(raw)["employees"])


class TestValidator(FrappeTestCase):
	"""The validator is the thing standing between bad data and a payroll run."""

	def _record(self, **overrides) -> dict:
		base = {
			"employee_number": "H0001",
			"national_id": "28308010104211",
			"first_name": "Sabry",
			"last_name": "Abouel-Fottouh",
			"date_of_joining": "2020-01-01",
			"company_source": "Bautex Misr",
			"designation_source": "Installation Technician",
			"flags": [],
		}
		base.update(overrides)
		return base

	def _resolved(self, **overrides) -> dict:
		base = {
			"company": "Bautex Misr",
			"designation": "Installation Technician",
			"starting_designation": None,
			"employment_category": None,
			"contract_type": None,
			"sub_department": None,
			"grade": None,
		}
		base.update(overrides)
		return base

	def test_a_good_record_has_no_errors(self):
		self.assertEqual(importer.validate(self._record(), self._resolved(), []), [])

	def test_missing_national_id_is_an_error(self):
		"""Without it the employee cannot be joined to payroll or medical."""
		errors = importer.validate(self._record(national_id=""), self._resolved(), [])
		self.assertTrue(any("National ID" in e for e in errors))

	def test_unresolved_company_is_an_error(self):
		errors = importer.validate(self._record(), self._resolved(company=None), [])
		self.assertTrue(any("company" in e for e in errors))

	def test_unresolved_designation_is_an_error(self):
		errors = importer.validate(self._record(), self._resolved(designation=None), [])
		self.assertTrue(any("designation" in e for e in errors))

	def test_duplicate_national_id_is_an_error(self):
		"""This is the real one: it breaks the join to payroll and medical."""
		record = self._record(flags=["duplicate_national_id"])
		errors = importer.validate(record, self._resolved(), [])
		self.assertTrue(any("shared with another employee" in e for e in errors))

	def test_missing_joining_date_is_a_warning_not_an_error(self):
		"""HR decided to import the master-only employees as active, so this
		must not block; it only leaves tenure empty."""
		advisories: list[str] = []
		errors = importer.validate(self._record(date_of_joining=""), self._resolved(), advisories)
		self.assertEqual(errors, [])
		self.assertTrue(advisories)

	def test_missing_first_name_is_an_error(self):
		errors = importer.validate(self._record(first_name=""), self._resolved(), [])
		self.assertTrue(any("first_name" in e for e in errors))

	def test_odd_length_national_id_is_an_error(self):
		errors = importer.validate(self._record(national_id="12345"), self._resolved(), [])
		self.assertTrue(any("odd length" in e for e in errors))


class TestPlanSafety(FrappeTestCase):
	def test_plan_does_not_write(self):
		"""The dry run must be side-effect free."""
		before = frappe.db.count("Employee")
		result = importer.build_plan()
		frappe.db.rollback()
		self.assertEqual(frappe.db.count("Employee"), before)
		self.assertEqual(len(result["plan"]), result["summary"]["people"])

	def test_plan_reports_every_unresolved_reference(self):
		result = importer.build_plan()
		frappe.db.rollback()
		for kind, values in result["unresolved"].items():
			for value in values:
				self.assertIsInstance(value, str)
				self.assertTrue(value.strip(), f"empty unresolved {kind}")

	def test_known_designation_typo_is_corrected(self):
		"""'Jonior' is a typo in the payroll file; 'Junior' is the real title."""
		self.assertIn("Jonior Web Developer", importer.DESIGNATION_TYPOS)
		self.assertEqual(importer.DESIGNATION_TYPOS["Jonior Web Developer"], "Junior Web Developer")


class TestJobProfileHtml(FrappeTestCase):
	"""The master's job profile is a bulleted list typed into a spreadsheet cell.

	Employee's field is a Text Editor, so the text has to become HTML or it renders
	as one collapsed paragraph.
	"""

	def test_bullets_become_a_list(self):
		html = importer.job_profile_html("\u2022 Deliver documents\n\u2022 Maintain records")
		self.assertEqual(html, "<ul><li>Deliver documents</li><li>Maintain records</li></ul>")

	def test_inline_bullets_are_split(self):
		"""65 of the 95 cells put every bullet on one physical line.

		Without splitting on the glyph these arrive as one long list item, which
		is the same unreadable wall of text the source had.
		"""
		html = importer.job_profile_html("Train staff. \u2022 Monitor attendance. \u2022 Manage ops")
		self.assertEqual(
			html,
			"<p>Train staff.</p><ul><li>Monitor attendance.</li><li>Manage ops</li></ul>",
		)

	def test_a_bulleted_line_stays_a_list_item(self):
		"""No lead-in sentence, so the whole thing is a list."""
		html = importer.job_profile_html("\u2022 Train staff\n\u2022 Monitor attendance")
		self.assertEqual(html, "<ul><li>Train staff</li><li>Monitor attendance</li></ul>")

	def test_plain_text_becomes_paragraphs(self):
		html = importer.job_profile_html("First line\nSecond line")
		self.assertEqual(html, "<p>First line</p><p>Second line</p>")

	def test_html_is_escaped(self):
		"""Ampersands and angle brackets in the source are not markup."""
		html = importer.job_profile_html("Review R&D <reports> & budgets")
		self.assertIn("R&amp;D", html)
		self.assertIn("&lt;reports&gt;", html)
		self.assertNotIn("<reports>", html)

	def test_numbered_items_become_list_items(self):
		html = importer.job_profile_html("1. Prepare the statement")
		self.assertEqual(html, "<ul><li>Prepare the statement</li></ul>")

	def test_empty_stays_empty(self):
		self.assertEqual(importer.job_profile_html(""), "")
		self.assertEqual(importer.job_profile_html("   "), "")

	def test_no_bullet_glyphs_survive(self):
		"""A leftover glyph means the text was never converted."""
		html = importer.job_profile_html("One \u2022 Two \u2022 Three")
		self.assertNotIn("\u2022", html)


class TestJobProfileField(FrappeTestCase):
	def test_field_exists_on_the_profile_tab(self):
		field = frappe.get_meta("Employee").get_field("sdg_job_profile")
		self.assertIsNotNone(field, "sdg_job_profile was not created")
		self.assertEqual(field.fieldtype, "Text Editor")
		meta = frappe.get_meta("Employee")
		tab = None
		for f in meta.fields:
			if f.fieldtype == "Tab Break":
				tab = f.label or f.fieldname
			elif f.fieldname == "sdg_job_profile":
				break
		self.assertEqual(tab, "Profile")

	def test_populated_count_matches_the_source(self):
		"""The master has a profile for 95 of 178 rows; the other 83 are blank."""
		fixture = importer.load_fixture()
		expected = sum(1 for r in fixture["employees"] if r.get("job_profile"))
		self.assertEqual(expected, 95)
		stored = frappe.db.sql("select count(*) from tabEmployee where ifnull(sdg_job_profile, '') != ''")[0][
			0
		]
		self.assertEqual(stored, expected)
