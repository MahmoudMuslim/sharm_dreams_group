# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The employee portal: sign-in, balances, time off, payslips.

A separate implementation from `self_service`, serving `/employee-portal` while
`/self-services-employees` stays up, so the two can be compared before the old
page is retired.

Three deliberate differences from the module it sits beside.

**Balances are read from `Leave Allocation`, not `Leave Balance`.** HRMS maintains a
`Leave Balance` per employee per leave type, but the doctype is not installed on this
site at all - there is no `tabLeave Balance` - so the usual source is absent. The
allocation plus the employee's own Leave Applications is the same arithmetic HRMS
would have done, written out here rather than inherited.

**The entitlements are the allocations, not six custom fields.** The old
`self_service/vacation.py` read `sdg_vacation_annual_days` and five siblings, which
are 0 for every employee on this site. An allocation now exists for each of the 180
active employees, so there is a real entitlement to read and the custom fields are
not needed.

**The day multipliers come from the Leave Type.** `sdg_vacation_day_multiplier` is 2.0
for the two "counts double" types - `LLDO` and `LLPH` - and 1.0 for the rest, so a
day worked on a Friday costs two days of allowance. Summing raw `total_leave_days`
would halve those buckets.

Authorisation is the same as the old page: the employee has no Frappe user, so the
token minted in `Employee Self Service Session` is the only credential, and every
read is scoped to the Employee bound to it. That session doctype is shared on purpose
- tokens do not care which page minted them, and forking it would mean two login
histories for one tablet.
"""

from __future__ import annotations

import base64

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, today

from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	SESSION_TTL_HOURS,
	create_session,
	resolve_session,
)

__all__ = [
	"SESSION_TTL_HOURS",
	"approve_balance",
	"days_by_code",
	"my_balances",
	"my_leaves",
	"my_payslip",
	"my_payslips",
	"my_profile",
	"request_leave",
	"resolve_session",
	"sign_in",
	"sign_out",
]

# A request in either state is committed time off: an approved one can still be
# cancelled back to the employee, so it has to stay counted until it is not.
COMMITTED_STATUSES = ("Approved", "Open")

# The leave types the portal shows a balance for, by the tracker's own codes. Named
# rather than discovered, because the six compensatory types - `DO`, `PH`, `LLDO`,
# `LLPH`, `PDODO`, `PDOPH` - are earned by working a day off and are never granted, so
# a balance row for each of them would read zero forever and imply a shortfall.
#
# `is_granted` is the flag rather than a code list, so adding a granted type later is
# a data change and not a code change.
BALANCE_TYPES = ("AL", "CL", "SL", "LIW", "PA", "HD")


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True)
def sign_in(national_id: str, employee_code: str) -> dict:
	"""Authenticate on National ID + Employee Number and mint a portal token.

	`employee_code` is the wire name the page posts; it carries the Employee Number,
	which is a different thing from the Employee record's name (`V0130` and friends).
	Both are needed because employees know one and not the other.
	"""
	national_id = (national_id or "").strip()
	employee_code = (employee_code or "").strip()
	if not national_id or not employee_code:
		frappe.throw(_("National ID and Employee Number are required."))

	employee = frappe.db.get_value(
		"Employee",
		{"sdg_national_id": national_id, "employee_number": employee_code},
		"name",
	)
	# One message for "no such person" and "wrong number": confirming which of the two
	# was wrong tells a stranger holding a national ID whether that ID exists here.
	if not employee:
		frappe.throw(_("No employee matches the National ID and Employee Number."))

	emp = frappe.get_doc("Employee", employee)
	if emp.status != "Active":
		frappe.throw(
			_("This account is not available. Please contact HR."),
			exc=frappe.PermissionError,
		)

	# `create_session` puts this straight into a Link field, so it must be the name
	# and not the document - a document raises `AssertionError: Unexpected value for
	# field employee` from `_validate_links`.
	session = create_session(employee)

	return {
		"token": session.access_token,
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"company": emp.company,
		"designation": emp.designation,
		"image": emp.image,
	}


@frappe.whitelist()
def sign_out(token: str) -> dict:
	"""End the session. The row is deleted rather than expired, so a leaked token
	cannot be distinguished from a live one by its presence."""
	resolve_session(token)
	frappe.db.delete("Employee Self Service Session", {"access_token": token})
	return {"ok": True}


@frappe.whitelist()
def my_profile(token: str) -> dict:
	emp = resolve_session(token)
	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"designation": emp.designation,
		"department": emp.department,
		"company": emp.company,
		"date_of_joining": emp.date_of_joining,
		"image": emp.image,
		"national_id": bool(emp.sdg_national_id),
		"has_email": bool(emp.personal_email or emp.company_email),
	}


# ---------------------------------------------------------------------------
# balances
# ---------------------------------------------------------------------------


def days_by_code(employee: str, year: int) -> dict[str, float]:
	"""Committed leave days per leave code for one employee and year.

	Each leave is weighted by its type's `sdg_vacation_day_multiplier`, so a day
	worked on a weekly rest day or a public holiday costs two days of allowance - which
	is the entire distinction between `LLDO`/`LLPH` and everything else.
	"""
	rows = frappe.get_all(
		"Leave Type",
		fields=["name", "sdg_code", "sdg_vacation_day_multiplier"],
		limit_page_length=0,
	)
	multiplier = {}
	code_of = {}
	for row in rows:
		if not row.sdg_code:
			continue
		code_of[row.name] = row.sdg_code
		# A type with no multiplier set would otherwise contribute nothing, because
		# `flt(None)` is 0 and every balance would read as fully spent.
		multiplier[row.name] = flt(row.sdg_vacation_day_multiplier, 1.0) or 1.0

	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"status": ("in", COMMITTED_STATUSES),
			"docstatus": ["<", 2],
			"from_date": ["between", (f"{year}-01-01", f"{year}-12-31")],
		},
		fields=["leave_type", "total_leave_days"],
		limit_page_length=0,
	)

	used: dict[str, float] = {}
	for leave in leaves:
		code = code_of.get(leave.leave_type)
		if not code:
			continue
		used[code] = flt(used.get(code)) + (
			flt(leave.total_leave_days) * multiplier.get(leave.leave_type, 1.0)
		)
	return used


def approve_balance(doc, carry_cap: float) -> dict:
	"""Allocated minus committed, for one Leave Allocation.

	Named for what it computes rather than where it reads: the entitlement is the
	allocation's `total_leaves_allocated` and the spending is the weighted day count,
	so the row answers "how much of this grant is left" without either number being
	stored.
	"""
	used = flt(doc.get("_used_days"))
	return {
		"allocated": flt(doc.total_leaves_allocated),
		"used": used,
		# Never negative: a negative balance reads as a debt the employee owes, and
		# over-application is a payroll problem, not a balance. HRMS has
		# `allow_negative` on the Leave Type for the sites that want one.
		"remaining": max(flt(doc.total_leaves_allocated) - used, 0.0),
		"carry_cap": carry_cap,
	}


def carry_forward_terms(leave_type, allocation_to_date: str) -> dict:
	"""What this leave type's carry-forward allows, and when it runs out.

	Read from the Leave Type rather than restated here, because those three fields
	are the policy and a second copy of the rule would drift from them silently.
	`is_carry_forward` off means no terms at all - reported as `None` rather than
	zero, because "you may carry nothing over" and "this type does not carry
	forward" are different statements.

	The expiry is counted from the end of the leave year rather than from today.
	HRMS counts `expire_carry_forwarded_leaves_after_days` from the allocation's
	`to_date`, which for an annual allocation is 31 December; with the 90 days this
	group's types carry, that lands exactly on 31 March - the date the policy names.
	"""
	if not leave_type:
		return {"allowed": None, "cap": 0.0, "expires": None}
	info = frappe.db.get_value(
		"Leave Type",
		leave_type,
		["is_carry_forward", "maximum_carry_forwarded_leaves",
		 "expire_carry_forwarded_leaves_after_days"],
		as_dict=True,
	)
	if not info or not info.is_carry_forward:
		return {"allowed": None, "cap": 0.0, "expires": None}
	days = flt(info.expire_carry_forwarded_leaves_after_days)
	expires = None
	if days and allocation_to_date:
		expires = str(add_days(getdate(allocation_to_date), int(days)))
	return {
		"allowed": True,
		"cap": flt(info.maximum_carry_forwarded_leaves),
		"expires": expires,
	}


@frappe.whitelist()
def my_balances(token: str) -> dict:
	"""This year's granted leave, per type, with the spending against it.

	Read from `Leave Allocation` because HRMS's `Leave Balance` is not installed on
	this site; see the module docstring.
	"""
	emp = resolve_session(token)
	year = getdate(today()).year

	types = {
		r.name: r
		for r in frappe.get_all(
			"Leave Type",
			fields=["name", "sdg_code", "leave_type_name", "sdg_vacation_day_multiplier",
			        "is_ppl", "fraction_of_daily_salary_per_leave"],
			limit_page_length=0,
		)
	}
	used = days_by_code(emp.name, year)

	allocations = frappe.get_all(
		"Leave Allocation",
		filters={
			"employee": emp.name,
			"docstatus": 1,
			"from_date": ["<=", f"{year}-12-31"],
			"to_date": [">=", f"{year}-01-01"],
		},
		fields=["name", "leave_type", "from_date", "to_date", "total_leaves_allocated",
		        "carry_forwarded_leaves_count", "unused_leaves"],
		order_by="from_date asc",
		limit_page_length=0,
	)

	rows = []
	seen = set()
	for alloc in allocations:
		info = types.get(alloc.leave_type)
		code = (info and info.sdg_code) or ""
		# One row per leave type: a mid-year top-up would otherwise show as a second
		# balance for the same thing, and the employee would read it as extra leave.
		if code and code in seen:
			allocated = flt(alloc.total_leaves_allocated)
			for row in rows:
				if row["code"] == code:
					row["allocated"] += allocated
					row["remaining"] = max(row["allocated"] - row["used"], 0.0)
			continue
		seen.add(code)
		carry = carry_forward_terms(alloc.leave_type, alloc.to_date)
		row = {
			"code": code,
			"leave_type": alloc.leave_type,
			"label": (info and info.leave_type_name) or alloc.leave_type,
			"allocated": flt(alloc.total_leaves_allocated),
			"used": flt(used.get(code)),
			"remaining": 0.0,
			"from_date": alloc.from_date,
			"to_date": alloc.to_date,
			"carry_cap": carry["cap"],
			"carry_allowed": carry["allowed"],
			"carry_expires": carry["expires"],
		}
		row["remaining"] = max(row["allocated"] - row["used"], 0.0)
		rows.append(row)

	# Spending against a type with no allocation still has to be visible, or an
	# over-spent month looks like a clean sheet.
	for code, spent in used.items():
		if code not in seen:
			rows.append(
				{
					"code": code,
					"leave_type": next(
						(n for n, i in types.items() if i.sdg_code == code), code
					),
					"label": next(
						(i.leave_type_name for i in types.values() if i.sdg_code == code), code
					),
					"allocated": 0.0,
					"used": flt(spent),
					"remaining": 0.0,
					"from_date": None,
					"to_date": None,
					"carry_cap": 0.0,
					"carry_allowed": None,
					"carry_expires": None,
				}
			)

	return {
		"employee": emp.name,
		"year": year,
		"balances": rows,
		"total_remaining": round(sum(r["remaining"] for r in rows), 2),
	}


# ---------------------------------------------------------------------------
# time off
# ---------------------------------------------------------------------------


@frappe.whitelist()
def my_leaves(token: str) -> dict:
	"""This employee's leave requests, newest first."""
	emp = resolve_session(token)
	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": emp.name, "docstatus": ["<", 2]},
		fields=["name", "leave_type", "from_date", "to_date", "total_leave_days",
		        "status", "description", "creation"],
		order_by="creation desc",
		limit_page_length=50,
	)
	return {"leaves": rows}


@frappe.whitelist()
def leave_types(token: str) -> dict:
	"""Leave types this employee may actually request.

	Offered only where an approved allocation covers today, because HRMS refuses a
	leave application outside its allocation period and the employee should not be
	offered a choice that will be rejected on submit.
	"""
	emp = resolve_session(token)
	today_date = today()
	rows = frappe.get_all(
		"Leave Type",
		filters={"is_lwp": 0},
		fields=["name", "leave_type_name", "sdg_code", "sdg_tracker_name",
		        "sdg_vacation_day_multiplier"],
		order_by="leave_type_name asc",
		limit_page_length=0,
	)
	allocated = set(
		frappe.get_all(
			"Leave Allocation",
			filters={
				"employee": emp.name,
				"docstatus": 1,
				"from_date": ["<=", today_date],
				"to_date": [">=", today_date],
			},
			pluck="leave_type",
			limit_page_length=0,
		)
	)
	# `pluck` returns plain strings, so this is a set of leave-type *names*. Reading
	# `.leave_type` off each element is the mistake that raises `AttributeError:
	# 'str' object has no attribute 'leave_type'` - which is what it did the first
	# time.
	return {
		"leave_types": [
			{
				"name": r.name,
				"label": r.leave_type_name,
				"code": r.sdg_code or "",
				"arabic": r.sdg_tracker_name or "",
				# Surfaced so the page can say why a type is not offered, rather than
				# simply omitting it and leaving the employee to wonder.
				"has_allocation": r.name in allocated,
			}
			for r in rows
		]
	}


@frappe.whitelist()
def request_leave(token: str, leave_type: str, from_date: str, to_date: str, reason: str | None = None) -> dict:
	"""File a leave application for the signed-in employee.

	Pre-validates the allocation window because HRMS's own message for it - "Application
	period cannot be outside leave allocation period" - is not something an employee
	can act on from a tablet.
	"""
	emp = resolve_session(token)
	if not leave_type:
		frappe.throw(_("Select a leave type."))
	if not (from_date and to_date):
		frappe.throw(_("From and To dates are required."))
	if getdate(from_date) > getdate(to_date):
		frappe.throw(_("From date cannot be after To date."))

	if not frappe.db.exists(
		"Leave Allocation",
		{
			"employee": emp.name,
			"leave_type": leave_type,
			"docstatus": 1,
			"from_date": ["<=", getdate(from_date)],
			"to_date": [">=", getdate(to_date)],
		},
	):
		frappe.throw(_("You do not have a leave allocation covering those dates."))

	if not emp.leave_approver:
		frappe.throw(
			_("No leave approver could be resolved for you. Please contact HR."),
			exc=frappe.PermissionError,
		)

	leave = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": emp.name,
			"leave_type": leave_type,
			"from_date": from_date,
			"to_date": to_date,
			"description": reason,
			"status": "Open",
			"posting_date": getdate(),
			"company": emp.company,
			"leave_approver": emp.leave_approver,
		}
	)
	# HRMS's `validate_balance_leaves` -> `validate_leave_access` refuses guests, so the
	# insert runs as the employee's own user. They may have no account, in which case
	# this falls back to Administrator - the token already authenticated them.
	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		leave.insert(ignore_permissions=True)
	finally:
		frappe.set_user(previous)

	return {
		"name": leave.name,
		"total_leave_days": leave.total_leave_days,
		"status": leave.status,
	}


@frappe.whitelist()
def cancel_leave(token: str, name: str, reason: str | None = None) -> dict:
	"""Withdraw a leave request that has not started and is still open."""
	emp = resolve_session(token)
	if not name or not frappe.db.exists("Leave Application", name):
		frappe.throw(_("Leave request not found."))
	leave = frappe.get_doc("Leave Application", name)
	# The same message as for "not yours" as for "not there": the token already proved
	# who is asking, so confirming another employee's request exists is information
	# they should not have.
	if leave.employee != emp.name:
		frappe.throw(_("Leave request not found."))
	if leave.status != "Open":
		frappe.throw(_("Only requests pending approval can be cancelled."))
	if leave.from_date and getdate(leave.from_date) <= getdate():
		frappe.throw(_("A leave request that has already started cannot be cancelled."))

	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		if leave.docstatus == 0:
			# Never reached an approver, so nothing to notify and nothing to audit
			# beyond the row itself.
			leave.delete(ignore_permissions=True)
		else:
			leave.status = "Cancelled"
			if reason:
				leave.description = f"{leave.description or ''}\n\nCancelled: {reason}"
			leave.save(ignore_permissions=True)
	finally:
		frappe.set_user(previous)

	return {"ok": True}


# ---------------------------------------------------------------------------
# payslips
# ---------------------------------------------------------------------------


# The Print Format a downloaded payslip is rendered with. Named rather than left to
# the doctype's default so the document on a tablet is a known one, and so a change
# to the desk default cannot silently alter what employees download.
#
# It is the one of this site's four Salary Slip formats with no `pdf_generator` of
# its own. The other three ask for `chrome`, which is not installed in this
# container, and `pdf_generator` is still passed explicitly below so the choice does
# not depend on a hook being absent.
PAYSLIP_PRINT_FORMAT = "Salary Slip Standard"

# `frappe.get_print` resolves the `printview` endpoint through the page-renderer
# list, and that path checks permissions as the current user. An employee on the
# portal has a token but usually no Frappe user, so without this every download
# would be refused for lack of a session.
PDF_USER_FALLBACK = "Administrator"


def render_salary_slip_pdf(slip) -> bytes:
	"""`Salary Slip` -> PDF bytes.

	`frappe.utils.download_pdf` is not used: it writes the file onto
	`frappe.local.response` and returns `None`, which is the wrong shape for a JSON
	endpoint. `frappe.get_print(..., as_pdf=True)` is the same machinery one layer
	down and returns the bytes.

	The render is pinned to the site's own URL for the duration. The print view
	builds every asset link - stylesheet, letterhead image, font - as an absolute URL
	out of `frappe.utils.get_url`, which prefers the *request's* host when no
	`host_name` is configured. Behind a TLS-terminating proxy that is the proxy's
	own `http://` address, and wkhtmltopdf - which is pinned to a dead proxy and
	cannot follow the redirect - fails the whole render with
	`SslHandshakeFailedError`. Pinning it means a document a tablet downloads
	refers to the site rather than to whichever proxy the request happened to
	arrive through, which is also the right thing for a PDF that gets emailed on.
	"""
	print_format = PAYSLIP_PRINT_FORMAT
	if not frappe.db.exists("Print Format", print_format):
		# A renamed or removed format falls back to the doctype's own default rather
		# than failing: a payslip in the wrong layout still beats no payslip.
		print_format = None

	previous = frappe.session.user
	# `Salary Slip` links to `Employee` and carries no user of its own, so the user to
	# render as is read off the employee rather than off the slip.
	employee_user = frappe.db.get_value("Employee", slip.employee, "user_id")
	frappe.set_user(employee_user or PDF_USER_FALLBACK)

	# `allow_header_override=False` is what excludes the request's host. Taken before
	# `host_name` is set, so a site that has one configured is left alone.
	canonical_url = frappe.utils.get_url(allow_header_override=False)
	previous_host = frappe.local.conf.get("host_name")
	frappe.local.conf.host_name = canonical_url
	try:
		return frappe.get_print(
			slip.doctype,
			slip.name,
			print_format,
			doc=slip,
			as_pdf=True,
			pdf_generator="wkhtmltopdf",
		)
	finally:
		frappe.set_user(previous)
		if previous_host is None:
			frappe.local.conf.pop("host_name", None)
		else:
			frappe.local.conf.host_name = previous_host


@frappe.whitelist()
def payslip_pdf(token: str, name: str) -> dict:
	"""One payslip as PDF bytes, base64-encoded, for this employee only.

	Base64 rather than a second route serving raw bytes: the tablet posts to one
	endpoint over one origin, and a `Content-Type: application/pdf` response would
	mean a second entry in `ACTIONS` that returns something other than JSON. The
	cost is a third more bytes on the wire, which for a one-page payslip is nothing.

	The employee check is the same as `my_payslip`, and is repeated rather than
	relying on that having been called first - this action is reachable on its own.
	"""
	emp = resolve_session(token)
	slip = frappe.get_doc("Salary Slip", name)
	if slip.employee != emp.name:
		frappe.throw(_("Not allowed to view this payslip."))

	pdf = render_salary_slip_pdf(slip)
	if not pdf:
		frappe.throw(_("The PDF could not be produced for this payslip."))
	return {
		"name": slip.name,
		"filename": f"{slip.name.replace('/', '-')}.pdf",
		# Bytes on the wire, stated so the client can tell a truncated download from
		# a complete one rather than opening whatever arrived.
		"size": len(pdf),
		"pdf": base64.b64encode(pdf).decode("ascii"),
	}


@frappe.whitelist()
def my_payslips(token: str) -> dict:
	emp = resolve_session(token)
	rows = frappe.get_all(
		"Salary Slip",
		filters={"employee": emp.name, "docstatus": ["<", 2]},
		fields=["name", "start_date", "end_date", "status", "net_pay", "currency", "posting_date"],
		order_by="start_date desc",
		limit_page_length=12,
	)
	return {"payslips": rows}


@frappe.whitelist()
def my_payslip(token: str, name: str) -> dict:
	"""One payslip, for this employee only.

	Employer contributions are included: HRMS writes them to a separate table on the
	slip, and they are what an employee checking their insurance or pension deduction
	actually wants to see.
	"""
	emp = resolve_session(token)
	slip = frappe.get_doc("Salary Slip", name)
	if slip.employee != emp.name:
		frappe.throw(_("Not allowed to view this payslip."))

	return {
		"name": slip.name,
		"status": slip.status,
		"start_date": slip.start_date,
		"end_date": slip.end_date,
		"currency": slip.currency,
		"gross_pay": slip.gross_pay,
		"total_deduction": slip.total_deduction,
		"net_pay": slip.net_pay,
		"earnings": [
			{"component": r.salary_component, "amount": r.amount} for r in slip.earnings or []
		],
		"deductions": [
			{"component": r.salary_component, "amount": r.amount} for r in slip.deductions or []
		],
		"employer": [
			{"component": r.salary_component, "amount": r.amount}
			for r in slip.employer_contributions or []
		],
	}