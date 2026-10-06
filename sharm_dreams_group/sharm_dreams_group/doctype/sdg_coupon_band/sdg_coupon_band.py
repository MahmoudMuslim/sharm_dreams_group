# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGCouponBand(Document):
	"""A cost-of-living coupon band, by family size.

	A band is reference data: the HR desk looks up what a family of this size is
	owed, then counts that much out of the drawer. The coupon itself is recorded in
	`SDG Coupon Granted`, which is deliberately not a payroll document - see that
	controller for why a stock `Employee Benefit Claim` cannot be used.
	"""

	def validate(self):
		self.validate_amount_is_positive()
		self.validate_unique_amount()

	def validate_amount_is_positive(self) -> None:
		"""A band worth nothing is a mistake, not a way to say "not eligible".

		Nobody is owed a coupon of 0, and a band with 0 in it would silently
		appear in the lookup and hand an employee nothing.
		"""
		if flt(self.coupon_amount) <= 0:
			frappe.throw("A coupon band must be worth more than zero.")

	def validate_unique_amount(self) -> None:
		"""Two bands cannot pay the same amount.

		`SDG Coupon Granted` looks the band up *by amount* when the amount is
		known, so a duplicate would make that lookup arbitrary - and the family
		size behind the amount is the whole reason the band exists.
		"""
		other = frappe.db.get_value(
			"SDG Coupon Band",
			{"coupon_amount": flt(self.coupon_amount), "name": ["!=", self.name]},
			"name",
		)
		if other:
			frappe.throw(
				f"Another band ({other}) already pays {flt(self.coupon_amount):,.0f}. "
				"The band is looked up by amount, so each amount must be unique."
			)
