# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Tests for the reference data seeded from the master workbook."""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.vocabularies import (
	LOOKS_LIKE_A_LOCATION,
	VOCABULARIES,
	load,
	resolve_existing,
)


class TestReferenceData(FrappeTestCase):
	def test_fixture_is_present_and_shaped(self):
		data = load()
		self.assertIn("vocabularies", data)
		self.assertIn("designations", data)
		for doctype in VOCABULARIES:
			with self.subTest(doctype=doctype):
				items = data["vocabularies"][doctype]
				self.assertGreater(len(items), 0, doctype)
				# the fixture uses one generic "name" key; the seeder maps it onto
				# each doctype's own naming field
				self.assertTrue(all("name" in item and item["name"] for item in items))

	def test_all_three_vocabularies_are_populated(self):
		data = load()
		self.assertGreaterEqual(len(data["vocabularies"]["SDG Employment Category"]), 5)
		self.assertGreaterEqual(len(data["vocabularies"]["SDG Contract Type"]), 3)
		self.assertGreaterEqual(len(data["vocabularies"]["SDG Sub Department"]), 15)

	def test_every_seeded_value_exists(self):
		data = load()
		for doctype in VOCABULARIES:
			for item in data["vocabularies"][doctype]:
				with self.subTest(doctype=doctype, name=item["name"]):
					self.assertTrue(
						frappe.db.exists(doctype, item["name"]),
						f"{doctype} {item['name']!r} was never seeded",
					)
					self.assertTrue(
						frappe.db.exists(
							"Custom Field",
							{
								"dt": "Employee",
								"fieldname": {
									"SDG Employment Category": "sdg_employment_category",
									"SDG Contract Type": "sdg_contract_type",
									"SDG Sub Department": "sdg_sub_department",
								}[doctype],
								"options": doctype,
							},
						),
						f"Employee link for {doctype} is not pointed at the table",
					)

	def test_sub_department_is_a_link_not_free_text(self):
		"""It was created as Data and had to be recreated; this pins the shape."""
		field = frappe.db.get_value(
			"Custom Field",
			{"dt": "Employee", "fieldname": "sdg_sub_department"},
			["fieldtype", "options"],
			as_dict=True,
		)
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "SDG Sub Department")

	def test_location_like_sub_departments_are_flagged(self):
		"""The master column mixes sub-departments with place names."""
		for name in ("Head Office", "Sharm El Sheikh"):
			with self.subTest(name=name):
				flag = frappe.db.get_value("SDG Sub Department", name, "looks_like_a_location")
				self.assertEqual(flag, 1, f"{name} should be flagged as a location")
		self.assertIn("Head Office", LOOKS_LIKE_A_LOCATION)

	def test_sub_department_values_are_unique_after_normalising(self):
		"""`administrative affairs` and `Administrative Affairs` must not both
		exist, or the link target fragments."""
		names = frappe.get_all("SDG Sub Department", fields=["name"], limit_page_length=0, pluck="name")
		lowered = [n.lower() for n in names]
		self.assertEqual(len(lowered), len(set(lowered)), "duplicate sub-departments")

	def test_designations_have_arabic_names(self):
		data = load()
		with_ar = [d for d in data["designations"] if d.get("name_ar")]
		self.assertGreaterEqual(len(with_ar), 90)
		for item in data["designations"]:
			existing = resolve_existing("Designation", item["name"])
			self.assertTrue(existing, f"Designation {item['name']!r} is not on the site")
			if item.get("name_ar"):
				with self.subTest(name=item["name"]):
					stored = frappe.db.get_value("Designation", existing, "sdg_designation_name_ar")
					self.assertEqual(stored, item["name_ar"])

	def test_every_starting_position_resolves_to_a_designation(self):
		"""Employee.sdg_starting_designation is a Link, so every title the master
		puts in "Starting Position Upon Joining" has to exist, including the 32
		that never appear as a current position."""
		data = load()
		known = {d["name"] for d in data["designations"]}
		lower = {n.lower() for n in known}
		starting_only = [
			d
			for d in data["designations"]
			if (d.get("note") or "").startswith("Seen only as a starting position")
		]
		self.assertEqual(len(starting_only), 32)
		for item in starting_only:
			with self.subTest(name=item["name"]):
				self.assertTrue(
					resolve_existing("Designation", item["name"]),
					f"starting position {item['name']!r} has no Designation to link to",
				)
		# and each is on the site
		for name in known:
			self.assertTrue(resolve_existing("Designation", name), f"{name!r} is not on the site")
		self.assertEqual(lower, {n.lower() for n in known})

	def test_resolve_existing_is_case_insensitive(self):
		"""17 master titles differ from the site's curated Designations only by
		capitalisation, and must resolve to the existing row rather than creating
		a near-duplicate."""
		data = load()
		names = [d["name"] for d in data["designations"]]
		# a case-sensitive list, unlike the database's own collation
		stored = frappe.get_all("Designation", fields=["name"], limit_page_length=0, pluck="name")
		case_only = [n for n in names if n not in stored and n.lower() in {s.lower() for s in stored}]
		for name in names:
			with self.subTest(name=name):
				resolved = resolve_existing("Designation", name)
				self.assertTrue(resolved, f"{name!r} did not resolve")
				self.assertIn(resolved, stored)
		self.assertEqual(len(names), 138, "the master should yield 138 titles (current + starting)")
		self.assertGreaterEqual(len(case_only), 15, "expected the master to differ by case on ~17 titles")

	def test_no_case_only_duplicate_designation_rows(self):
		"""A second row differing only by capitalisation would split the link
		target, so every stored name must be unique when lower-cased."""
		names = frappe.get_all("Designation", fields=["name"], limit_page_length=0, pluck="name")
		lowered = [n.lower() for n in names]
		self.assertEqual(len(lowered), len(set(lowered)), "case-only duplicate Designations")

	def test_seeding_is_idempotent(self):
		"""A second run must change nothing, otherwise it is writing the wrong
		values every time."""
		from sharm_dreams_group.vocabularies import seed

		before = {
			dt: sorted(frappe.get_all(dt, fields=["name"], limit_page_length=0, pluck="name"))
			for dt in VOCABULARIES
		}
		before_designations = {
			d.name: d.sdg_designation_name_ar
			for d in frappe.get_all(
				"Designation", fields=["name", "sdg_designation_name_ar"], limit_page_length=0
			)
		}
		seed()
		after = {
			dt: sorted(frappe.get_all(dt, fields=["name"], limit_page_length=0, pluck="name"))
			for dt in VOCABULARIES
		}
		self.assertEqual(before, after)
		self.assertEqual(
			before_designations,
			{
				d.name: d.sdg_designation_name_ar
				for d in frappe.get_all(
					"Designation", fields=["name", "sdg_designation_name_ar"], limit_page_length=0
				)
			},
		)
		frappe.db.rollback()
