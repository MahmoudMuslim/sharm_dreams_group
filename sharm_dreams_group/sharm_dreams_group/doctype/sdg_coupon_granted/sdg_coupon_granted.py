"""A cost-of-living coupon handed to an employee, and the notes counted out for it.

Why this is not a stock `Employee Benefit Claim`
-----------------------------------------------
The obvious Frappe home for "an employee was given a benefit" is `Employee Benefit
Claim`, and it is wrong here for two reasons that are not stylistic:

1. **It pays through payroll.** `EmployeeBenefitClaim.on_submit` calls
   `create_additional_salary`, which submits an `Additional Salary` for the claimed
   amount. The coupon is handed over separately, as cash, from the HR drawer - the
   workbook has a notes-count column and a signature box for exactly that reason.
   Using a claim would put the money in the employee's net pay *and* in the
   drawer: the group pays twice.

2. **It refuses the date.** `validate_date_and_benefit_claim_amount` throws if
   `payroll_date` is in the past, so a claim for a month that has already been paid
   could not be recorded at all. A register of what was handed over is necessarily
   retrospective.

The claim would also need an `earning_component` and a `Salary Structure
Assignment` per employee, and it enforces one claim per employee per component per
month - rules about payroll entitlement that mean nothing here, where entitlement
is set by family size.

So the coupon is its own record: an amount, the notes that made it up, and whether
the signature came back. Nothing here is read by the payroll, by design - a test
asserts that.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, formatdate

from sharm_dreams_group.hr.coupon import (
	NOTE_DENOMINATIONS,
	_assert_notes_match_amount,
	notes_total,
)


class SDGCouponGranted(Document):
	def autoname(self) -> None:
		"""Name the row after the person and the month: `H0201 - 2026-08`.

		Frappe does not evaluate `{field}` expressions in an autoname - see the
		spec in tools/generate_doctypes.py - so the name is built here. One coupon
		per employee per month makes that pair unique, and putting both in the name
		matters for a cash register: a query for one person's month should be
		answerable by reading the row's name.
		"""
		if not self.employee:
			return
		month = formatdate(self.period_month, "yyyy-MM") if self.period_month else "no-period"
		self.name = f"{self.employee} - {month}"

	def validate(self):
		self.set_notes_total()
		self.validate_notes_match_amount()
		self.validate_one_coupon_per_month()
		self.set_band()

	def set_notes_total(self) -> None:
		"""Add up the notes, so the desk shows the count next to the amount.

		Kept as a stored field rather than only shown in the form so a report or an
		export can compare it without loading the document.
		"""
		self.notes_total = notes_total(self.notes_100, self.notes_200, self.notes_250)

	def validate_notes_match_amount(self) -> None:
		"""The notes must be worth what the employee was given.

		The workbook reconciles exactly - 100n + 200n + 250n equals the stated
		amount on all 126 rows - so a mismatch here means someone typed a different
		amount from the amount they counted out. That is the error this catches, and
		it is the reason the note counts are stored at all.  A row with no counts is
		allowed: the amount stands on its own until someone counts the notes.
		"""
		if not any(flt(self.get(f)) for f, _ in NOTE_DENOMINATIONS):
			return
		_assert_notes_match_amount(self.amount, self.notes_100, self.notes_200, self.notes_250)

	def validate_one_coupon_per_month(self) -> None:
		"""One coupon per employee per month.

		Two coupons in a month means either a duplicate import or a second handout
		that should have been recorded deliberately, and both are worth refusing -
		this is cash going out of a drawer.

		The name is `employee - month`, so a second coupon collides with the first
		on the primary key. That check has to come *first* and must not exclude by
		name: excluding the row whose name matches is what hides the duplicate from
		this method and lets the database report it as a raw integrity error.
		"""
		if not self.employee or not self.period_month:
			return
		if self.is_new() and frappe.db.exists("SDG Coupon Granted", self.name):
			frappe.throw(
				_("Employee {0} already has a coupon for {1}, recorded as {2}.").format(
					frappe.bold(self.employee),
					frappe.format(self.period_month, {"fieldtype": "Date"}),
					frappe.bold(self.name),
				),
				title=_("Duplicate coupon"),
			)

	def set_band(self) -> None:
		"""Look the band up from the amount, so the family size is recorded.

		Two people can be owed the same amount, so this fills the band in rather
		than enforcing it: the amount is what the workbook states, and the band is
		the reason. A later change to a band's amount must not silently reclassify
		what was handed over in an earlier month.
		"""
		if self.band or not self.amount:
			return
		band = frappe.db.get_value("SDG Coupon Band", {"coupon_amount": flt(self.amount)}, "name")
		if band:
			self.band = band
