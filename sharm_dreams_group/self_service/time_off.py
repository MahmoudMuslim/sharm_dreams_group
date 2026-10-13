"""Self-service time off: the balances screen and withdrawing a request.

Filing a request and listing them live in `self_service.api` (`request_leave` /
`my_leaves`); this module holds the two that need their own module - the
per-code balance rows and cancelling a request that is still pending approval.

Cancelling is not just `doc.cancel()`.  A request the employee filed from the
self-service may still be a draft (nothing has touched it) or may already be submitted
to an approver (HRMS submits on create), and the two need different handling:
a draft is deleted, a submitted one is cancelled so the approver sees the change
rather than the row disappearing.
"""

from datetime import date

import frappe
from frappe.utils import getdate

from sharm_dreams_group.self_service.api import as_employee_user
from sharm_dreams_group.self_service.vacation import (
	get_balance_context,
	get_vacation_balance_rows,
)
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	resolve_session,
)

CANCELLABLE_STATUSES = ("Open",)


@frappe.whitelist()
def leave_balances(token):
	"""The signed-in employee's vacation balance, from the tracker workbook.

	`context` carries the provenance - which workbook and sheet, the date the
	figures are as of, when carried-forward days expire - so the screen can say
	where a number came from instead of presenting it as if it were computed live.
	"""
	emp = resolve_session(token)
	year, rows = get_vacation_balance_rows(emp)
	return {
		"employee": emp.name,
		"year": year,
		"balances": rows,
		"context": get_balance_context(),
	}


@frappe.whitelist()
def cancel_leave(token, name, reason=None):
	"""Withdraw a leave request that has not started and is still open."""
	emp = resolve_session(token)
	if not name or not frappe.db.exists("Leave Application", name):
		frappe.throw("Leave request not found.")

	leave = frappe.get_doc("Leave Application", name)
	# The same message for "not yours" as for "not there": the token already proved
	# who is asking, so confirming that some other employee's request exists is
	# information they should not have.
	if leave.employee != emp.name:
		frappe.throw("Leave request not found.")

	if leave.status not in CANCELLABLE_STATUSES:
		frappe.throw(
			"This request cannot be cancelled. Only requests pending approval "
			"can be cancelled."
		)

	if leave.from_date and getdate(leave.from_date) <= date.today():
		frappe.throw(
			"A leave request that has already started cannot be cancelled. "
			"Please contact HR."
		)

	as_employee_user(emp, lambda: _withdraw(leave))

	return {"message": "Your time-off request has been cancelled."}


def _withdraw(leave):
	if leave.docstatus == 0:
		# Never reached an approver, so there is nothing to notify and nothing to
		# audit beyond the row - remove it rather than leave a dead draft.
		leave.delete(ignore_permissions=True)
	else:
		leave.status = "Cancelled"
		leave.cancel()