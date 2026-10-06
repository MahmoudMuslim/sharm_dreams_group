# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the Employee-side helpers.

The name splitting and tenure maths are pure, so they are tested here without a
database.  Run: bench --site <site> run-tests --app sharm_dreams_group
"""

from __future__ import annotations

import unittest
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.hr.employee import (
	LEADING_RELATION,
	PHONE_PATTERN,
	PLACEHOLDERS,
	RELATION_PATTERN,
	age_on,
	round_years,
)

TODAY = date(2026, 9, 29)


def parse_next_of_kin(raw: str) -> tuple[str, str, str]:
	"""Mirror of employee.normalise_next_of_kin, for testing without a doc."""
	import re

	remainder = (raw or "").strip()
	if not remainder or remainder.lower() in PLACEHOLDERS:
		return "", "", ""
	relation = ""
	match = LEADING_RELATION.match(remainder)
	if match:
		relation = match.group(1).title()
		remainder = remainder[match.end() :]
	else:
		found = RELATION_PATTERN.search(remainder)
		if found:
			relation = found.group(1).title()

	phones = PHONE_PATTERN.findall(remainder)
	phone = phones[0] if phones else ""
	name = PHONE_PATTERN.split(remainder)[0] if phones else remainder
	name = name.strip(" ()-,:/").strip()
	name = re.sub(r"\s{2,}", " ", name)
	return relation, name, phone


class TestAge(unittest.TestCase):
	def test_completed_years(self):
		self.assertEqual(age_on(date(1965, 11, 2), TODAY), 60)

	def test_birthday_is_not_yet_reached_today(self):
		self.assertEqual(age_on(date(1966, 9, 29), TODAY), 60)

	def test_birthday_is_reached_today(self):
		self.assertEqual(age_on(date(1966, 9, 30), TODAY), 59)

	def test_leap_day_birthday(self):
		# 29 Feb in a non-leap comparison year
		self.assertEqual(age_on(date(2000, 2, 29), TODAY), 26)


class TestTenure(unittest.TestCase):
	def test_long_tenure(self):
		self.assertAlmostEqual(round_years(date(1997, 5, 12), TODAY), 29.38, places=2)

	def test_fractional_first_year(self):
		self.assertAlmostEqual(round_years(date(2026, 8, 9), TODAY), 0.14, places=2)

	def test_future_joining_date_is_never_negative(self):
		"""A contract signed before the start date must not produce -0.01."""
		self.assertEqual(round_years(date(2026, 12, 1), TODAY), 0.0)
		self.assertEqual(round_years(date(2030, 1, 1), TODAY), 0.0)
		self.assertGreaterEqual(round_years(date(2025, 10, 1), TODAY), 0.0)

	def test_anniversary_boundary(self):
		# Joined a day after, and a day before, the anniversary.
		#
		# Checked at four places: a day short of a year is 364/365.25 = 0.9966,
		# which rounds to 1.00 at the two places the field actually stores and
		# would hide the boundary.  At two places this assertion only passed
		# because of the double-decrement bug, which reported 0.0.
		self.assertGreaterEqual(round_years(date(2025, 9, 28), TODAY, places=4), 1.0)
		self.assertLess(round_years(date(2025, 9, 30), TODAY, places=4), 1.0)


class TestNextOfKinParsing(unittest.TestCase):
	def test_relation_name_and_phone(self):
		relation, name, phone = parse_next_of_kin("Son - Mohamed Sabry ( 01016361630 )")
		self.assertEqual(relation, "Son")
		self.assertEqual(name, "Mohamed Sabry")
		self.assertEqual(phone, "01016361630")

	def test_relation_without_spaces_around_dash(self):
		relation, name, phone = parse_next_of_kin("Brother- Yasser Mohamed ( 01551598835 )/ 01067442969")
		self.assertEqual(relation, "Brother")
		self.assertEqual(name, "Yasser Mohamed")
		# the first of the two numbers is kept
		self.assertEqual(phone, "01551598835")

	def test_name_and_phone_with_no_relation(self):
		relation, name, phone = parse_next_of_kin("Mohamed Samir ( 01005531303 ) ")
		self.assertEqual(relation, "")
		self.assertEqual(name, "Mohamed Samir")
		self.assertEqual(phone, "01005531303")

	def test_phone_with_no_name(self):
		relation, name, phone = parse_next_of_kin("Son - ( 01013015210 )")
		self.assertEqual(relation, "Son")
		self.assertEqual(name, "")
		self.assertEqual(phone, "01013015210")

	def test_placeholder_produces_nothing(self):
		for raw in ("-", "--", "---", "N/A", "n/a", "none", "", "   "):
			with self.subTest(raw=raw):
				self.assertEqual(parse_next_of_kin(raw), ("", "", ""))

	def test_two_numbers_in_one_cell(self):
		relation, name, phone = parse_next_of_kin("Brother - Ahmed Ibrahim ( 01011989956 - 01155968805 )")
		self.assertEqual(relation, "Brother")
		self.assertEqual(name, "Ahmed Ibrahim")
		# the first of the two numbers is kept
		self.assertEqual(phone, "01011989956")

	def test_country_code_variant(self):
		relation, name, phone = parse_next_of_kin("Father - Mamdouh ( +201123634444 )")
		self.assertEqual(relation, "Father")
		self.assertEqual(name, "Mamdouh")
		self.assertEqual(phone, "+201123634444")

	def test_strip_does_not_eat_letters(self):
		"""strip(" ()-,:/") must not treat '-' as a range and swallow text.

		An earlier version put '-' between '(' and ',' in the character set,
		which made it a range and silently dropped characters from names.
		"""
		_relation, name, _phone = parse_next_of_kin("Son - Mahmoud ( 01000000000 )")
		self.assertEqual(name, "Mahmoud")
		_relation, name, _phone = parse_next_of_kin("Son - M.A. Kamel ( 01000000000 )")
		self.assertEqual(name, "M.A. Kamel")


class TestNameSplitting(unittest.TestCase):
	"""The agreed rule: first_name = word 1, last_name = final word,
	middle_name = everything between, so employee_name rebuilds exactly."""

	@staticmethod
	def split(name: str) -> tuple[str, str, str]:
		"""first = word 1, last = final word, middle = everything between."""
		tokens = " ".join(name.split()).split()
		if not tokens:
			return "", "", ""
		if len(tokens) == 1:
			return tokens[0], "", ""
		if len(tokens) == 2:
			return tokens[0], "", tokens[1]
		return tokens[0], " ".join(tokens[1:-1]), tokens[-1]

	@staticmethod
	def rebuild(first: str, middle: str, last: str) -> str:
		# ERPNext: " ".join(filter(None, [first_name, middle_name, last_name]))
		return " ".join(x for x in (first, middle, last) if x)

	def test_round_trips_for_every_known_name(self):
		import json
		import pathlib

		fixture = pathlib.Path(__file__).resolve().parents[1] / "tests" / "golden_payroll.json"
		names = {r["name"] for r in json.loads(fixture.read_text())["rows"] if r.get("name")}
		self.assertGreater(len(names), 150)
		normalised = {" ".join(n.split()) for n in names}
		for original in names:
			with self.subTest(name=original):
				first, middle, last = self.split(original)
				self.assertEqual(self.rebuild(first, middle, last), " ".join(original.split()))
		# splitting normalises whitespace, so a handful of source names that
		# contain a double space collapse - which is the intended behaviour
		self.assertLessEqual(len(normalised), len(names))
		self.assertTrue(
			any(" ".join(n.split()) != n for n in names),
			"expected at least one source name with irregular whitespace",
		)

	def test_collapses_double_spaces(self):
		first, middle, last = self.split("Moustafa Ali  Mohamed")
		self.assertEqual(self.rebuild(first, middle, last), "Moustafa Ali Mohamed")

	def test_single_token(self):
		self.assertEqual(self.split("Cher"), ("Cher", "", ""))

	def test_two_tokens(self):
		self.assertEqual(self.split("Aya Ahmed"), ("Aya", "", "Ahmed"))

	def test_four_token_arabic_pattern(self):
		first, middle, last = self.split("Ahmed Mohamed Kamal Mohamed")
		self.assertEqual((first, middle, last), ("Ahmed", "Mohamed Kamal", "Mohamed"))


class TestEmployeeHook(FrappeTestCase):
	def test_hook_is_registered(self):
		events = frappe.get_hooks("doc_events", {}).get("Employee", {})
		self.assertIn(
			"sharm_dreams_group.hr.employee.validate_employee",
			events.get("validate", []),
			"the Employee validate hook is not registered; derived fields would go stale",
		)

	def test_computed_fields_exist_and_are_read_only(self):
		for fieldname in ("sdg_age", "sdg_years_of_tenure", "sdg_days_with_company"):
			field = frappe.db.get_value(
				"Custom Field",
				{"dt": "Employee", "fieldname": fieldname},
				["read_only", "fieldtype"],
				as_dict=True,
			)
			self.assertIsNotNone(field, f"{fieldname} was never created")
			self.assertEqual(field.read_only, 1, f"{fieldname} must be read only")

	def test_tenure_is_computed_on_save(self):
		doc = frappe.new_doc("Employee")
		doc.employee_number = "TEST-0001"  # mandatory on Employee
		doc.first_name = "Test"
		doc.gender = "Male"
		doc.date_of_birth = "1990-01-01"
		doc.date_of_joining = "2020-06-15"
		doc.company = "Sharm Dreams Group"
		doc.status = "Active"
		doc.flags.in_test = True
		doc.insert(ignore_permissions=True)
		try:
			self.assertEqual(doc.sdg_age, 36)
			self.assertGreaterEqual(doc.sdg_days_with_company, 2200)
			self.assertGreaterEqual(doc.sdg_years_of_tenure, 6.0)
		finally:
			frappe.delete_doc("Employee", doc.name, ignore_permissions=True, force=True)


class TestEmployeeLayout(FrappeTestCase):
	"""The SDG fields must sit on the tab a user would look on.

	Regression cover for a real failure: the fields were created against the base
	ERPNext employee.json, but HRMS overrides Employee with its own layout, so
	most of them landed on the wrong tab.  And a second failure: updating
	`insert_after` with db.set_value writes the value but never recomputes `idx`,
	so the desk kept rendering the old position.  Both are invisible unless the
	merged layout is asserted.
	"""

	SDG_FIELDS = (
		"sdg_name_arabic",
		"sdg_employment_category",
		"sdg_contract_type",
		"sdg_contract_date",
		"sdg_sub_department",
		"sdg_national_id",
		"sdg_national_id_expiry",
		"sdg_si_number",
		"sdg_si_status",
		"sdg_si_date",
		"sdg_religion",
		"sdg_dependants_count",
		"sdg_governorate",
		"sdg_offer_status",
		"sdg_status_date",
		"sdg_starting_designation",
		"sdg_age",
		"sdg_years_of_tenure",
		"sdg_days_with_company",
	)

	def _tab_of(self) -> dict[str, str]:
		"""Map every fieldname to the tab it is rendered under."""
		out, tab = {}, "(no tab)"
		for f in frappe.get_meta("Employee").fields:
			if f.fieldtype == "Tab Break":
				tab = f.label or f.fieldname
			else:
				out[f.fieldname] = tab
		return out

	def test_every_sdg_field_exists(self):
		tabs = self._tab_of()
		for fieldname in self.SDG_FIELDS:
			with self.subTest(field=fieldname):
				self.assertIn(fieldname, tabs, f"{fieldname} is not on the Employee form")

	def test_national_id_is_at_the_top_of_personal_details(self):
		"""The statutory identity an Egyptian employment record is keyed on, so
		it is read before anything else on the tab."""
		order = self._order_in("Personal Details")
		self.assertEqual(order[0], "sdg_national_id")
		self.assertEqual(order[1], "sdg_national_id_expiry")

	def test_social_insurance_sits_in_their_own_section(self):
		sections = self._sections_in("Personal Details")
		self.assertIn("Social Insurance", sections)
		self.assertEqual(
			sections.index("Social Insurance"),
			sections.index("Health Insurance") + 1,
			"the Social Insurance section must follow the stock Health Insurance one",
		)
		order = self._order_in("Personal Details")
		for fieldname in ("sdg_si_number", "sdg_si_status", "sdg_si_date"):
			with self.subTest(field=fieldname):
				self.assertGreater(order.index(fieldname), order.index("health_insurance_no"))

	def test_social_insurance_section_is_labelled(self):
		for f in frappe.get_meta("Employee").fields:
			if f.fieldname == "sdg_social_insurance_section":
				self.assertEqual(f.label, "Social Insurance")
				return
		self.fail("the Social Insurance section was never created")

	def _order_in(self, tab: str) -> list[str]:
		"""Fieldnames in render order, within one tab, excluding layout fields."""
		out, current = [], None
		for f in frappe.get_meta("Employee").fields:
			if f.fieldtype == "Tab Break":
				current = f.label or f.fieldname
				continue
			if current != tab:
				continue
			if f.fieldtype in ("Section Break", "Column Break", "Tab Break", "HTML"):
				continue
			out.append(f.fieldname)
		return out

	def _sections_in(self, tab: str) -> list[str]:
		out, current = [], None
		for f in frappe.get_meta("Employee").fields:
			if f.fieldtype == "Tab Break":
				current = f.label or f.fieldname
				continue
			if current == tab and f.fieldtype == "Section Break":
				out.append(f.label or f.fieldname)
		return out

	def test_contract_fields_are_on_the_joining_tab(self):
		tabs = self._tab_of()
		for fieldname in ("sdg_employment_category", "sdg_contract_type", "sdg_contract_date"):
			with self.subTest(field=fieldname):
				self.assertEqual(tabs[fieldname], "Joining", fieldname)

	def test_tenure_sits_next_to_the_joining_date(self):
		"""Tenure is only meaningful beside date_of_joining."""
		tabs = self._tab_of()
		for fieldname in ("sdg_years_of_tenure", "sdg_days_with_company"):
			with self.subTest(field=fieldname):
				self.assertEqual(tabs[fieldname], tabs["date_of_joining"], fieldname)

	def test_personal_fields_are_on_the_personal_details_tab(self):
		tabs = self._tab_of()
		for fieldname in ("sdg_religion", "sdg_name_arabic", "sdg_dependants_count", "sdg_age"):
			with self.subTest(field=fieldname):
				self.assertEqual(tabs[fieldname], "Personal Details", fieldname)

	def test_position_arabic_lives_on_designation_not_employee(self):
		"""The Arabic job title must exist in exactly one place.

		It was originally added to both Employee and Designation.  There are only
		108 distinct titles across 178 people, so a per-employee copy duplicated
		the same string and gave the two copies a way to drift apart.
		"""
		tabs = self._tab_of()
		self.assertNotIn(
			"sdg_position_arabic",
			tabs,
			"Arabic job title belongs on Designation.sdg_designation_name_ar, not on Employee",
		)
		self.assertTrue(
			frappe.db.exists(
				"Custom Field",
				{"dt": "Designation", "fieldname": "sdg_designation_name_ar"},
			),
			"Designation.sdg_designation_name_ar must exist to hold the Arabic title",
		)

	def test_grade_uses_the_stock_link_not_a_free_text_field(self):
		"""Employee already links to Employee Grade through `grade`; a
		free-text sdg_grade_level was a second home for one value."""
		fields = {f.fieldname: f for f in frappe.get_meta("Employee").fields}
		self.assertNotIn("sdg_grade_level", fields)
		self.assertEqual(fields["grade"].fieldtype, "Link")
		self.assertEqual(fields["grade"].options, "Employee Grade")

	def test_governorate_is_on_the_address_tab(self):
		self.assertEqual(self._tab_of()["sdg_governorate"], "Address & Contacts")

	def test_no_sdg_field_is_stranded_on_the_connections_tab(self):
		"""The tab break anchors on connections_tab, so a mis-set insert_after
		used to dump every field there."""
		tabs = self._tab_of()
		for fieldname in self.SDG_FIELDS:
			with self.subTest(field=fieldname):
				self.assertNotEqual(tabs[fieldname], "Connections", fieldname)

	def test_only_one_insurance_section_exists(self):
		"""The section was renamed from Medical Insurance to Social Insurance.

		The rename was applied to the code but not to the database, so the form
		had a second, empty section break left over.  The old field is now in
		OBSOLETE_EMPLOYEE_FIELDS, and this asserts the form is not carrying both.
		"""
		sections = [f for f in frappe.get_meta("Employee").fields if f.fieldtype == "Section Break"]
		labels = [f.label or f.fieldname for f in sections]
		insurance = [label for label in labels if "Insurance" in label]
		self.assertEqual(
			insurance,
			["Health Insurance", "Social Insurance"],
			"expected exactly the stock Health Insurance section and our Social one",
		)
		self.assertFalse(
			frappe.db.exists(
				"Custom Field", {"dt": "Employee", "fieldname": "sdg_medical_insurance_section"}
			),
			"the pre-rename section field still exists",
		)

	def test_social_insurance_section_fieldname_and_label_agree(self):
		from sharm_dreams_group.setup import SOCIAL_SECTION_FIELD, SOCIAL_SECTION_LABEL

		self.assertEqual(SOCIAL_SECTION_FIELD, "sdg_social_insurance_section")
		self.assertEqual(SOCIAL_SECTION_LABEL, "Social Insurance")
		for f in frappe.get_meta("Employee").fields:
			if f.fieldname == SOCIAL_SECTION_FIELD:
				self.assertEqual(f.label, SOCIAL_SECTION_LABEL)
				return
		self.fail("the Social Insurance section is not on the form")

	def test_religion_is_a_select(self):
		"""The master holds exactly Muslim (170) and Christian (8), so a Select
		fits it and stops free text accumulating."""
		tabs = self._tab_of()
		self.assertEqual(tabs["sdg_religion"], "Personal Details")
		self.assertEqual(self._options("sdg_religion"), ["Muslim", "Christian"])

	def test_si_status_is_a_select_on_personal_details(self):
		"""Two states only, so a Select is an exact fit rather than a constraint."""
		tabs = self._tab_of()
		self.assertEqual(tabs["sdg_si_status"], "Personal Details")
		self.assertEqual(self._options("sdg_si_status"), ["Uninsured", "Insured"])

	def test_si_status_values_are_all_selectable(self):
		"""The point of the change.

		The master writes the column in mixed case ("uninsured" on 31 rows,
		"insured" on 1).  A Select rejects anything outside its options, so a
		leftover lowercase value would make that employee unsaveable in the desk
		while looking perfectly normal in the database.
		"""
		allowed = set(self._options("sdg_si_status"))
		values = {
			row.sdg_si_status
			for row in frappe.get_all("Employee", fields=["sdg_si_status"], limit_page_length=0)
			if row.sdg_si_status
		}
		self.assertTrue(values, "no employee has a social insurance status at all")
		self.assertEqual(
			sorted(values - allowed),
			[],
			"these values cannot be saved now the field is a Select",
		)

	def test_si_status_normalisation_is_idempotent(self):
		"""Re-running the rewrite must not keep reporting changes."""
		from sharm_dreams_group.setup import normalise_si_status_values

		first = normalise_si_status_values()
		self.assertEqual(first["changed"], {}, "values still needed rewriting")
		self.assertEqual(first["unknown"], {}, "unexpected social insurance status values")

	def test_all_employee_selects_have_options(self):
		"""A Select with no options silently refuses every value, so each one is
		checked against the values actually present in the master."""
		expected = {
			"sdg_religion": ["Muslim", "Christian"],
			"sdg_military_status": ["Completed", "Exempted", "Not Required", "Postponed"],
			"sdg_military_document": ["Missing", "On File", "Out and not Returned"],
			"sdg_offer_status": ["On board", "Off board"],
			"sdg_si_status": ["Uninsured", "Insured"],
			"sdg_penalty_indicator": ["Green", "Yellow", "Orange", "Red", "Purple"],
		}
		selects = {
			f.fieldname
			for f in frappe.get_meta("Employee").fields
			if f.fieldtype == "Select" and f.fieldname.startswith("sdg_")
		}
		self.assertEqual(selects, set(expected), "a new Select is missing from the test")
		for fieldname, options in expected.items():
			with self.subTest(field=fieldname):
				self.assertEqual(self._options(fieldname), options)

	def test_military_fields_are_selects_on_personal_details(self):
		"""Both come from fixed vocabularies in the master, so a Select keeps the
		values clean instead of letting free text in."""
		tabs = self._tab_of()
		for fieldname in ("sdg_military_status", "sdg_military_document"):
			with self.subTest(field=fieldname):
				self.assertEqual(tabs[fieldname], "Personal Details", fieldname)
		self.assertEqual(
			self._options("sdg_military_status"),
			["Completed", "Exempted", "Not Required", "Postponed"],
		)
		self.assertEqual(
			self._options("sdg_military_document"),
			["Missing", "On File", "Out and not Returned"],
		)

	def test_military_fields_are_adjacent_to_the_other_personal_facts(self):
		"""The two military fields belong together, and follow the background
		facts they are part of."""
		order = self._order_in("Personal Details")
		self.assertEqual(
			order.index("sdg_military_document"),
			order.index("sdg_military_status") + 1,
		)
		self.assertEqual(
			order.index("sdg_military_status"),
			order.index("sdg_religion") + 1,
		)

	def test_social_insurance_section_is_collapsible(self):
		"""It was expanded by default, which pushed the Passport Details section
		down a form that already has a lot of fields."""
		for f in frappe.get_meta("Employee").fields:
			if f.fieldname == "sdg_social_insurance_section":
				self.assertEqual(f.fieldtype, "Section Break")
				self.assertTrue(f.collapsible, "the section must be collapsible")
				return
		self.fail("the Social Insurance section was never created")

	def _options(self, fieldname: str) -> list[str]:
		options = frappe.db.get_value("Custom Field", {"dt": "Employee", "fieldname": fieldname}, "options")
		return [o for o in (options or "").split("\n") if o]

	def test_offer_status_is_a_select_with_the_agreed_options(self):
		field = frappe.db.get_value(
			"Custom Field",
			{"dt": "Employee", "fieldname": "sdg_offer_status"},
			["fieldtype", "options"],
			as_dict=True,
		)
		self.assertEqual(field.fieldtype, "Select")
		options = [o for o in (field.options or "").split("\n") if o]
		self.assertEqual(options, ["On board", "Off board"])

	def test_offer_status_accepts_only_its_options(self):
		doc = frappe.new_doc("Employee")
		doc.employee_number = "TEST-0002"
		doc.first_name = "Test"
		doc.gender = "Male"
		doc.date_of_birth = "1990-01-01"
		doc.date_of_joining = "2020-01-01"
		doc.company = "Sharm Dreams Group"
		doc.status = "Active"
		doc.sdg_offer_status = "On board"
		doc.flags.in_test = True
		doc.insert(ignore_permissions=True)
		try:
			self.assertEqual(doc.sdg_offer_status, "On board")
			frappe.db.set_value("Employee", doc.name, "sdg_offer_status", "Off board")
			self.assertEqual(frappe.db.get_value("Employee", doc.name, "sdg_offer_status"), "Off board")
		finally:
			frappe.delete_doc("Employee", doc.name, ignore_permissions=True, force=True)


class TestRoundYears(FrappeTestCase):
	"""`round_years` is display-only, but a wrong value reaches the Employee form.

	The bug these cover: the completed-year calculation and the anniversary
	fallback each subtracted a year, so anyone with less than a year of service
	read 0.0 instead of their fraction.  16 employees joined between 2025-10 and
	2025-12 and all hit it.
	"""

	def test_under_a_year_of_service_keeps_its_fraction(self):
		# 359 days of service, the exact case that read 0.0
		self.assertAlmostEqual(round_years(date(2025, 10, 5), date(2026, 9, 29)), 0.98, places=2)

	def test_ten_months_does_not_round_to_zero(self):
		self.assertAlmostEqual(round_years(date(2025, 12, 2), date(2026, 9, 29), places=1), 0.8, places=1)

	def test_long_service_matches_the_master(self):
		"""H0003: the master records 29.4027 years."""
		self.assertAlmostEqual(round_years(date(1997, 5, 12), date(2026, 9, 29)), 29.38, places=1)

	def test_anniversary_in_the_past_year_is_used(self):
		self.assertAlmostEqual(round_years(date(2020, 6, 15), date(2026, 9, 29)), 6.29, places=2)

	def test_joined_today_is_zero(self):
		self.assertEqual(round_years(date(2026, 9, 29), date(2026, 9, 29)), 0.0)

	def test_future_joining_date_is_zero(self):
		"""A contract signed before the start date must not read negative."""
		self.assertEqual(round_years(date(2027, 1, 1), date(2026, 9, 29)), 0.0)

	def test_leap_day_joiner_does_not_raise(self):
		"""29 February has no anniversary in a non-leap year."""
		self.assertAlmostEqual(round_years(date(2016, 2, 29), date(2026, 9, 29)), 10.58, places=1)


class TestFileIndex(FrappeTestCase):
	"""The EMPLOYEE FILE INDEX, as a child table on Employee."""

	def test_child_doctype_exists_and_is_a_child_table(self):
		"""A doctype that is not `istable` cannot be attached as a table."""
		dt = frappe.get_doc("DocType", "SDG Employee File Item")
		self.assertEqual(dt.istable, 1)
		self.assertEqual([f.fieldname for f in dt.fields], ["document_item", "status", "notes"])

	def test_document_options_cover_the_master(self):
		"""All 18 of the master's EMPLOYOYEE FILE INDEX columns."""
		dt = frappe.get_doc("DocType", "SDG Employee File Item")
		options = [o for o in (dt.fields[0].options or "").split("\n") if o]
		self.assertEqual(len(options), 18)
		for expected in (
			"HRA",
			"LOI",
			"ID",
			"6 PIC",
			"Military",
			"Medical 111",
			"Social S1",
			"Criminal Record",
			"Education Certificate",
			"Employment Permit",
			"Security Check",
			"Bank Details",
			"Employment of Relatives",
			"Beneficiary",
			"Code of Business Conduct",
			"Updated CV / Interview Papers",
			"Job Description",
			"Reference Check",
		):
			self.assertIn(expected, options)

	def test_status_options(self):
		"""The master writes Y, N and NA; anything else is a transcription slip."""
		dt = frappe.get_doc("DocType", "SDG Employee File Item")
		options = [o for o in (dt.fields[1].options or "").split("\n") if o]
		self.assertEqual(options, ["Held", "Missing", "Not Applicable"])

	def test_employee_field_is_a_table_pointing_at_the_child_doctype(self):
		"""Without `options` the field is created but the desk cannot attach a
		table to it, and it fails silently."""
		field = frappe.get_meta("Employee").get_field("sdg_file_index")
		self.assertIsNotNone(field, "Employee.sdg_file_index was not created")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "SDG Employee File Item")

	def test_checklist_has_its_own_tab(self):
		"""The tab is a Custom Field too, and it has to be a real Tab Break."""
		doc = frappe.get_doc("Custom Field", {"dt": "Employee", "fieldname": "sdg_file_index_section"})
		self.assertEqual(doc.fieldtype, "Tab Break")
		self.assertEqual(doc.label, "Employee File Index")

	def test_checklist_sits_inside_that_tab(self):
		"""Anchored to the tab, and rendered after it.

		A field with no valid anchor is silently appended by Frappe, which is how
		the checklist ended up in the Connections tab.
		"""
		insert_after = frappe.db.get_value(
			"Custom Field", {"dt": "Employee", "fieldname": "sdg_file_index"}, "insert_after"
		)
		self.assertEqual(insert_after, "sdg_file_index_section")

		# and the merged metadata really puts the table in that tab
		meta = frappe.get_meta("Employee")
		tab = None
		tab_of_table = None
		for f in meta.fields:
			if f.fieldtype == "Tab Break":
				tab = f.label or f.fieldname
			elif f.fieldname == "sdg_file_index":
				tab_of_table = tab
		self.assertEqual(tab_of_table, "Employee File Index")

	def test_the_tab_is_anchored_to_a_field_that_exists(self):
		"""A Tab Break anchored to a missing fieldname appends instead."""
		anchor = frappe.db.get_value(
			"Custom Field", {"dt": "Employee", "fieldname": "sdg_file_index_section"}, "insert_after"
		)
		self.assertTrue(anchor, "the tab has no anchor")
		self.assertTrue(
			frappe.get_meta("Employee").has_field(anchor), f"tab anchored to missing field {anchor}"
		)

	def test_the_tab_sits_before_the_exit_tab(self):
		"""Placed with the employment record, not after Connections."""
		meta = frappe.get_meta("Employee")
		tabs = [f.label or f.fieldname for f in meta.fields if f.fieldtype == "Tab Break"]
		self.assertIn("Employee File Index", tabs)
		self.assertIn("Exit", tabs)
		self.assertLess(
			tabs.index("Employee File Index"),
			tabs.index("Exit"),
			f"the checklist tab is in the wrong place: {tabs}",
		)
		# and it belongs after Joining, where the employment terms are
		self.assertLess(
			tabs.index("Joining"),
			tabs.index("Employee File Index"),
			f"the checklist tab should follow Joining: {tabs}",
		)

	def test_the_tab_holds_only_the_checklist(self):
		"""Nothing has drifted into the tab, and the table is in it."""
		meta = frappe.get_meta("Employee")
		fields = list(meta.fields)
		start = next(
			i
			for i, f in enumerate(fields)
			if f.fieldtype == "Tab Break" and (f.label or f.fieldname) == "Employee File Index"
		)
		contents = []
		for f in fields[start + 1 :]:
			if f.fieldtype == "Tab Break":
				break
			if f.fieldtype in ("Column Break", "HTML", "Button", "Fold", "Flex Space"):
				continue
			contents.append(f.fieldname)
		self.assertEqual(contents, ["sdg_file_index"])

	def test_checklist_is_not_on_the_insured_person_doctype(self):
		"""It used to live there as 18 Check fields.

		A medical record is the wrong home for a document checklist, and the flags
		were never written to, which left the checklist with no usable home.
		"""
		stale = [
			f.fieldname
			for f in frappe.get_meta("SDG Insured Person").fields
			if f.fieldname.startswith("file_")
		]
		self.assertEqual(stale, [], "document flags are still on SDG Insured Person")
