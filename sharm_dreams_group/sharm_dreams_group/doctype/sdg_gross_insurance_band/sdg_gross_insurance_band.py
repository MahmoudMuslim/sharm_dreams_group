# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

from itertools import pairwise

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGGrossInsuranceBand(Document):
	"""Maps a monthly gross salary range to the life insurance premium and the
	social insurance contribution base ("Final Limit").

	The bands must tile the gross range without gaps or overlaps, otherwise an
	employee can silently fall off the end of the table.
	"""

	def validate(self):
		self.validate_range()
		self.validate_no_overlap()
		self.validate_no_gaps()

	def validate_range(self):
		if flt(self.from_gross) < 0 or flt(self.to_gross) <= 0:
			frappe.throw(frappe._("Both From Gross and To Gross must be greater than zero."))
		if flt(self.to_gross) <= flt(self.from_gross):
			frappe.throw(
				frappe._("To Gross must be greater than From Gross (got {0} and {1}).").format(
					self.from_gross, self.to_gross
				)
			)
		if flt(self.life_insurance) < 0 or flt(self.si_base) < 0:
			frappe.throw(frappe._("Life insurance and SI base cannot be negative."))

	def _siblings(self):
		return frappe.get_all(
			"SDG Gross Insurance Band",
			filters={"name": ("!=", self.name), "active": 1},
			fields=["name", "from_gross", "to_gross"],
		)

	def validate_no_overlap(self):
		lo, hi = flt(self.from_gross), flt(self.to_gross)
		for other in self._siblings():
			o_lo, o_hi = flt(other.from_gross), flt(other.to_gross)
			if lo < o_hi and o_lo < hi:
				frappe.throw(
					frappe._("This band ({0}-{1}) overlaps {2} ({3}-{4}). Bands must not overlap.").format(
						lo, hi, other.name, o_lo, o_hi
					),
					title=frappe._("Overlapping band"),
				)

	def validate_no_gaps(self):
		"""Active bands must form one unbroken chain.

		The table is allowed to start above zero - the legacy payroll's lowest
		gross is well above it, and inventing a band from 0 would put made-up
		values into a live payroll.  ``lookup()`` therefore falls back to the
		lowest band for anything below the table.  What must never happen is a
		hole in the middle, which would silently drop an employee onto the
		wrong premium.
		"""
		if (
			self.name
			and not self.is_new()
			and not (
				self.has_value_changed("from_gross")
				or self.has_value_changed("to_gross")
				or self.has_value_changed("active")
			)
		):
			return

		active = [*self._siblings(), self]
		active.sort(key=lambda d: flt(d.from_gross))
		for lower, upper in pairwise(active):
			if flt(upper.from_gross) > flt(lower.to_gross):
				frappe.throw(
					frappe._(
						"There is a gap between {0} (ends at {1}) and {2} (starts at {3}). "
						"Bands must be contiguous."
					).format(lower.name, flt(lower.to_gross), upper.name, flt(upper.from_gross)),
					title=frappe._("Gap in band coverage"),
				)

	@staticmethod
	def lookup(gross: float) -> SDGGrossInsuranceBand | None:
		"""Return the band for `gross`, falling back to the lowest band when the
		gross sits below the table."""
		from frappe.utils import flt as _flt

		gross = _flt(gross)
		name = frappe.db.get_value(
			"SDG Gross Insurance Band",
			{"active": 1, "from_gross": ("<=", gross), "to_gross": (">", gross)},
			"name",
			order_by="from_gross desc",
		)
		if not name:
			name = frappe.db.get_value(
				"SDG Gross Insurance Band",
				{"active": 1},
				"name",
				order_by="from_gross asc",
			)
		return frappe.get_doc("SDG Gross Insurance Band", name) if name else None
