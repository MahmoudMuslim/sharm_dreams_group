# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from itertools import pairwise

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.sharm_dreams_group.doctype.sdg_gross_insurance_band.sdg_gross_insurance_band import (
	SDGGrossInsuranceBand,
)

BANDS = [
	# from, to, life insurance, SI base
	(0, 10_000, 50, 10_000),
	(10_000, 20_000, 75, 20_000),
	(20_000, 999_999_999, 100, 30_000),
]


class TestSDGGrossInsuranceBand(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Deactivate the live table rather than deleting it, so a failed run can
		# never leave the real payroll configuration missing.
		cls.live = frappe.get_all("SDG Gross Insurance Band", fields=["name", "active"])
		for row in cls.live:
			frappe.db.set_value("SDG Gross Insurance Band", row.name, "active", 0, update_modified=False)
		cls.created = []
		for from_gross, to_gross, life, si_base in BANDS:
			doc = frappe.get_doc(
				{
					"doctype": "SDG Gross Insurance Band",
					"from_gross": from_gross,
					"to_gross": to_gross,
					"life_insurance": life,
					"si_base": si_base,
					"active": 1,
				}
			).insert(ignore_permissions=True)
			cls.created.append(doc.name)
		frappe.db.commit()
		cls.names = frappe.get_all(
			"SDG Gross Insurance Band", filters={"active": 1}, pluck="name", order_by="from_gross asc"
		)

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("SDG Gross Insurance Band", {"name": ("in", cls.created)})
		for row in cls.live:
			frappe.db.set_value(
				"SDG Gross Insurance Band", row.name, "active", row.active, update_modified=False
			)
		frappe.db.commit()
		super().tearDownClass()

	def test_lookup_finds_the_right_band(self):
		cases = [(0, 0), (1, 0), (9_999.99, 0), (10_000, 1), (15_000, 1), (19_999.99, 1), (20_000, 2)]
		for gross, expected_index in cases:
			band = SDGGrossInsuranceBand.lookup(gross)
			self.assertIsNotNone(band, f"no band for {gross}")
			self.assertEqual(band.name, self.names[expected_index], f"wrong band for gross {gross}")

	def test_lookup_returns_values(self):
		band = SDGGrossInsuranceBand.lookup(15_000)
		self.assertEqual(band.life_insurance, 75)
		self.assertEqual(band.si_base, 20_000)

	def test_lookup_falls_back_below_the_table(self):
		"""The legacy table's lowest band starts at 3,825.50, not zero.  Anything
		below it must still resolve rather than returning nothing."""
		band = SDGGrossInsuranceBand.lookup(1)
		self.assertIsNotNone(band)
		self.assertEqual(band.name, self.names[0])

	def test_to_gross_must_exceed_from_gross(self):
		band = frappe.get_doc("SDG Gross Insurance Band", self.names[1])
		band.to_gross = band.from_gross
		self.assertRaises(frappe.ValidationError, band.validate_range)

	def test_overlap_is_rejected(self):
		band = frappe.get_doc("SDG Gross Insurance Band", self.names[1])
		band.from_gross = 5_000
		self.assertRaises(frappe.ValidationError, band.validate_no_overlap)

	def test_negative_amounts_are_rejected(self):
		band = frappe.get_doc("SDG Gross Insurance Band", self.names[1])
		band.life_insurance = -1
		self.assertRaises(frappe.ValidationError, band.validate_range)
		band = frappe.get_doc("SDG Gross Insurance Band", self.names[1])
		band.si_base = -1
		self.assertRaises(frappe.ValidationError, band.validate_range)

	def test_zero_upper_bound_is_rejected(self):
		band = frappe.get_doc("SDG Gross Insurance Band", self.names[1])
		band.to_gross = 0
		self.assertRaises(frappe.ValidationError, band.validate_range)

	def test_hole_in_the_table_is_rejected(self):
		"""Deactivating a band leaves a gap, which would silently drop an employee
		onto the wrong premium."""
		frappe.db.set_value("SDG Gross Insurance Band", self.names[1], "active", 0)
		try:
			band = frappe.get_doc("SDG Gross Insurance Band", self.names[2])
			self.assertRaises(frappe.ValidationError, band.validate_no_gaps)
		finally:
			frappe.db.set_value("SDG Gross Insurance Band", self.names[1], "active", 1)

	def test_table_has_no_holes_as_seeded(self):
		rows = frappe.get_all(
			"SDG Gross Insurance Band",
			filters={"active": 1},
			fields=["from_gross", "to_gross"],
			order_by="from_gross asc",
		)
		for lower, upper in pairwise(rows):
			self.assertAlmostEqual(lower.to_gross, upper.from_gross, places=2)
