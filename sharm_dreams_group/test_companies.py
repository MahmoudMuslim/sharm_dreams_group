# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the master -> ERPNext company name mapping."""

from __future__ import annotations

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.companies import (
	NO_SOURCE,
	build_map,
	is_pure_arabic,
	normalise,
	split_bilingual,
)


class TestNormalise(unittest.TestCase):
	def test_case_and_punctuation_are_ignored(self):
		self.assertEqual(normalise("Bautex misr"), normalise("Bautex Misr"))
		self.assertEqual(
			normalise("Sharm Dreams Co . For Touristic investment"),
			normalise("Sharm Dreams Co For Touristic Investment"),
		)

	def test_the_company_token_is_dropped(self):
		"""The master says "I Fleet Transport Company" where ERPNext says
		"I Fleet Transport"."""
		self.assertEqual(normalise("I Fleet Transport Company"), normalise("I Fleet Transport"))
		self.assertEqual(
			normalise("Africa Real Estate Investment Company"),
			normalise("Africa Real Estate Investment"),
		)

	def test_ampersand_and_underscore_agree(self):
		self.assertEqual(
			normalise("Rowad Misr For Tourism investment"),
			normalise("Rowad Misr For Tourism Investment"),
		)
		self.assertIn("and", normalise("Egypt Development & Investment"))

	def test_arabic_is_removed_before_comparing(self):
		self.assertEqual(
			normalise("Concord For Touristic Development - كونكورد للتنمية السياحية"),
			normalise("Concord For Touristic Development"),
		)


class TestSplitBilingual(unittest.TestCase):
	def test_standard_dash_separated(self):
		english, arabic = split_bilingual("Concord For Touristic Development - كونكورد للتنمية السياحية")
		self.assertEqual(english, "Concord For Touristic Development")
		self.assertEqual(arabic, "كونكورد للتنمية السياحية")

	def test_no_space_before_the_dash(self):
		english, arabic = split_bilingual(
			"Sharm Dreams Co. For Touristic Investment- شركة شارم دريمز للإستثمار السياحي"
		)
		self.assertEqual(english, "Sharm Dreams Co For Touristic Investment")
		self.assertEqual(arabic, "شركة شارم دريمز للإستثمار السياحي")

	def test_no_dash_at_all(self):
		"""One master row jams the halves together.  Returning the whole cell as
		the Arabic name would store Latin words in an Arabic field."""
		english, arabic = split_bilingual(
			"Africa Real Estate Investment Company شركة افريقا للاستثمار العقارى"
		)
		self.assertEqual(english, "Africa Real Estate Investment Company")
		self.assertEqual(arabic, "شركة افريقا للاستثمار العقارى")
		self.assertNotIn("Africa", arabic)

	def test_arabic_is_always_pure(self):
		raws = [
			"Abo Dabbab For Touristic Projects - شركة ابو دباب للمشروعات السياحية",
			"Africa Real Estate Investment Company شركة افريقا للاستثمار العقارى",
			"I Fleet Transport Company - شركة اى فليت للنقل",
			"Sharm Dreams For Floating Hotels - شارم دريمز للفنادق العائمة",
		]
		for raw in raws:
			with self.subTest(raw=raw):
				_english, arabic = split_bilingual(raw)
				self.assertTrue(is_pure_arabic(arabic), f"not pure Arabic: {arabic!r}")

	def test_is_pure_arabic_rejects_latin(self):
		self.assertFalse(is_pure_arabic("Company شركة"))
		self.assertFalse(is_pure_arabic(""))
		self.assertTrue(is_pure_arabic("شركة"))


class TestCompanyMap(FrappeTestCase):
	def test_every_company_is_either_mapped_or_a_known_gap(self):
		_arabic_for, unresolved = build_map()
		self.assertEqual(
			sorted(unresolved),
			sorted(NO_SOURCE),
			"an unmapped company would block the employee import",
		)

	def test_all_fourteen_companies_are_accounted_for(self):
		companies = frappe.db.count("Company")
		arabic_for, unresolved = build_map()
		self.assertEqual(len(arabic_for) + len(unresolved), companies)
		self.assertEqual(companies, 14)

	def test_stored_arabic_names_are_pure(self):
		for row in frappe.get_all(
			"Company",
			filters={"company_name_ar": ("is", "set")},
			fields=["name", "company_name_ar"],
			limit_page_length=0,
		):
			with self.subTest(company=row.name):
				self.assertTrue(
					is_pure_arabic(row.company_name_ar),
					f"{row.name}: {row.company_name_ar!r} is not pure Arabic",
				)

	def test_seeding_is_idempotent(self):
		before = {
			c.name: c.company_name_ar
			for c in frappe.get_all("Company", fields=["name", "company_name_ar"], limit_page_length=0)
		}
		from sharm_dreams_group.companies import seed_arabic_names

		seed_arabic_names()
		after = {
			c.name: c.company_name_ar
			for c in frappe.get_all("Company", fields=["name", "company_name_ar"], limit_page_length=0)
		}
		self.assertEqual(before, after)
		frappe.db.rollback()

	def test_company_name_ar_sits_under_company_name(self):
		names = [
			f.fieldname
			for f in frappe.get_meta("Company").fields
			if f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button")
		]
		self.assertEqual(
			names.index("company_name_ar"),
			names.index("company_name") + 1,
			"company_name_ar must render directly under company_name",
		)


class TestGradeField(FrappeTestCase):
	def test_grade_level_duplicate_is_gone(self):
		"""Employee already links to Employee Grade through its stock `grade`
		field; a free-text sdg_grade_level was a second home for one value."""
		self.assertNotIn(
			"sdg_grade_level",
			[f.fieldname for f in frappe.get_meta("Employee").fields],
		)
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": "sdg_grade_level"}))
		self.assertNotIn("sdg_grade_level", frappe.db.get_table_columns("Employee"))

	def test_stock_grade_field_links_to_employee_grade(self):
		fields = {f.fieldname: f for f in frappe.get_meta("Employee").fields}
		self.assertIn("grade", fields)
		self.assertEqual(fields["grade"].fieldtype, "Link")
		self.assertEqual(fields["grade"].options, "Employee Grade")
		self.assertTrue(frappe.db.exists("DocType", "Employee Grade"))
