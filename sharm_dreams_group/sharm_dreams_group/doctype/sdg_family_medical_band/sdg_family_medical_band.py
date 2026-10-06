# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGFamilyMedicalBand(Document):
	"""Family medical premium for one combination of coverage band and family
	composition (spouse covered, number of children covered).

	Seeded from the legacy workbook. Where a combination is not present the
	calculator raises rather than guessing a price - see payroll/egypt/insurance.py
	"""

	def validate(self):
		self.validate_amount()
		self.validate_unique_combination()
		self.set_multiplier()

	def validate_amount(self):
		if flt(self.monthly_premium) < 0:
			frappe.throw(frappe._("Monthly family premium cannot be negative."))
		if self.children_count is not None and int(self.children_count) < 0:
			frappe.throw(frappe._("Children covered cannot be negative."))

	def _key(self, doc) -> tuple:
		return (doc.medical_band, int(doc.has_spouse or 0), int(doc.children_count or 0))

	def validate_unique_combination(self):
		mine = self._key(self)
		for other in frappe.get_all(
			"SDG Family Medical Band",
			filters={"name": ("!=", self.name), "active": 1},
			fields=["name", "medical_band", "has_spouse", "children_count"],
		):
			if mine == self._key(other):
				frappe.throw(
					frappe._("Row {0} already covers this exact combination.").format(other.name),
					title=frappe._("Duplicate family band"),
				)

	def set_multiplier(self):
		"""premium / annual per member rate, shown for audit only."""
		settings = frappe.get_single("SDG Payroll Settings")
		annual = flt(settings.family_member_annual_rate)
		if annual:
			self.member_multiplier = flt(self.monthly_premium) / (annual / 12)
