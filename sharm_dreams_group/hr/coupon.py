"""The cost-of-living coupon: a cash handout, counted out of the drawer.

Kept apart from the DocType controller so the arithmetic can be tested without a
document, and so the reason the coupon is not a payroll item is written down in
one place.

Why not a stock `Employee Benefit Claim`
---------------------------------------
`EmployeeBenefitClaim.on_submit` calls `create_additional_salary`, which submits an
`Additional Salary` for the claimed amount. The coupon is handed over separately,
as cash, which is why the workbook has a note-count column and a signature box. A
claim would put the money in the employee's net pay *and* in the drawer: the group
would pay twice for one coupon.

The claim also refuses the date - `validate_date_and_benefit_claim_amount` throws
if `payroll_date` is in the past - so a register of what has already been handed
over could not be recorded at all. It additionally needs an earning component and
a salary structure assignment per employee, and allows one claim per employee per
component per month: rules about payroll entitlement that mean nothing here, where
what a person is owed is set by their family size.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

# The notes the HR desk counts in, and what each is worth. All of them are in
# circulation; the workbook counts 100, 200 and 250.
NOTE_DENOMINATIONS = (("notes_100", 100), ("notes_200", 200), ("notes_250", 250))

PERIOD = "2026-08-01"


def notes_total(notes_100, notes_200, notes_250) -> float:
	"""What the counted notes come to."""
	return flt(notes_100) * 100 + flt(notes_200) * 200 + flt(notes_250) * 250


def _assert_notes_match_amount(amount, notes_100, notes_200, notes_250) -> None:
	"""Refuse a coupon whose notes do not make its amount.

	Raised as a `ValidationError` so the DocType controller can call it and a test
	can call it without a document.
	"""
	total = notes_total(notes_100, notes_200, notes_250)
	if abs(total - flt(amount)) > 0.005:
		frappe.throw(
			frappe._("The notes add up to {0}, but the amount is {1}.").format(
				frappe.format(total, {"fieldtype": "Currency"}),
				frappe.format(flt(amount), {"fieldtype": "Currency"}),
			),
			title=frappe._("Coupon notes do not match the amount"),
		)


@frappe.whitelist()
def month_summary(period_month: str | None = None) -> dict:
	"""What was handed over in a month, and what it came to.

	Reads the register rather than the bands, because the bands are the policy and
	the register is what happened - in 08-2026 the two disagreed by one person and
	1,500, and the register is the one HR signed for.
	"""
	from sharm_dreams_group.sharm_dreams_group.doctype.sdg_coupon_granted.sdg_coupon_granted import (
		month_summary as _impl,
	)

	return _impl(period_month or PERIOD)
