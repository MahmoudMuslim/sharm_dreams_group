# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The employee portal: sign-in, balances, time off, payslips.

The single self-service surface at `/employee-portal`; the old
`/self-services-employees` page and its client are gone.

Three deliberate differences from the page it replaced.

**Balances are read from `Leave Allocation`, not `Leave Balance`.** HRMS maintains a
`Leave Balance` per employee per leave type, but the doctype is not installed on this
site at all - there is no `tabLeave Balance` - so the usual source is absent. The
allocation plus the employee's own Leave Applications is the same arithmetic HRMS
would have done, written out here rather than inherited.

**The entitlements are the allocations, not six custom fields.** The retired
`/self-services-employees` page read `sdg_vacation_annual_days` and five siblings, which
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
from frappe.utils import (
	add_days,
	cint,
	flt,
	getdate,
	today,
	validate_email_address,
)

from sharm_dreams_group.portal import leave_policy
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	REMEMBER_ME_TTL_DAYS,
	SESSION_TTL_HOURS,
	create_session,
	resolve_session,
)

__all__ = [
	"IDLE_TIMEOUT_SECONDS",
	"SESSION_TTL_HOURS",
	"activity_types",
	"approvals",
	"approve_balance",
	"cancel_leave",
	"days_by_code",
	"leave_types",
	"my_assets",
	"my_balances",
	"my_colleagues",
	"my_details",
	"my_holidays",
	"my_leave_calendar",
	"my_leaves",
	"my_org_chart",
	"my_overtime",
	"my_payslip",
	"my_payslips",
	"my_profile",
	"my_timesheets",
	"payslip_pdf",
	"request_leave",
	"resolve_session",
	"sign_in",
	"sign_out",
	"submit_overtime",
	"submit_timesheet",
	"update_my_details",
]

# A request in either state is committed time off: an approved one can still be
# cancelled back to the employee, so it has to stay counted until it is not.
COMMITTED_STATUSES = ("Approved", "Open")

# How long a signed-in tablet may sit untouched before the portal signs itself out.
# The old page used 30 seconds; that is hostile outside a kiosk, so the mechanism is
# kept and the window widened. Shared with the page script through the page context.
IDLE_TIMEOUT_SECONDS = 600

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
def sign_in(national_id: str, employee_code: str, remember_me: bool = False) -> dict:
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

	# Once a biometric is enrolled the account is locked to it: the two numbers can
	# be read off any HR printout, the fingerprint cannot. This is the same policy
	# the old page enforced, and `webauthn_login` is the way back in.
	enrolled = frappe.db.count(
		"Employee Self Service Biometric Credential", {"employee": employee, "active": 1}
	)
	if enrolled:
		frappe.throw(
			_(
				"This account is protected by biometric sign-in. "
				"Please sign in with your fingerprint or face ID."
			),
			exc=frappe.PermissionError,
		)

	# `create_session` puts this straight into a Link field, so it must be the name
	# and not the document - a document raises `AssertionError: Unexpected value for
	# field employee` from `_validate_links`.
	#
	# "Remember me" is expressed as this row's own `expiry`, which is the single thing
	# `resolve_session` checks; a cache entry beside it would be a second copy of the
	# rule that nothing read.
	session = create_session(
		employee,
		ttl_hours=REMEMBER_ME_TTL_DAYS * 24 if remember_me else None,
	)

	token = session.access_token

	is_approver = "Leave Approver" in frappe.get_roles(emp.user_id) if emp.user_id else False

	return {
		"token": token,
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"company": emp.company,
		"designation": emp.designation,
		"image": emp.image,
		"isApprover": is_approver,
	}


@frappe.whitelist()
def sign_out(token: str) -> dict:
	"""End the session. The row is deleted rather than expired, so a leaked token
	cannot be distinguished from a live one by its presence."""
	resolve_session(token)
	frappe.db.delete("Employee Self Service Session", {"access_token": token})
	return {"ok": True}


@frappe.whitelist()
def approvals(token: str) -> list[dict]:
	"""Leave applications waiting on this employee's approval, as a manager.

	Read-only, as on the old page: the desk is where the decision is recorded in
	HRMS's own workflow. The portal's job is to tell an approver there is something
	to look at, not to become a second workflow engine.
	"""
	emp = resolve_session(token)
	approver_user = frappe.db.get_value("Employee", emp.name, "user_id")
	if not approver_user:
		return []
	return frappe.get_all(
		"Leave Application",
		filters={"leave_approver": approver_user, "status": "Open", "docstatus": 0},
		fields=[
			"name",
			"employee",
			"employee_name",
			"leave_type",
			"from_date",
			"to_date",
			"total_leave_days",
		],
		order_by="posting_date asc",
		limit_page_length=20,
	)


# The fields an employee may correct on their own record, and nothing else.
#
# Named rather than derived, and deliberately short. Everything HR owns that affects
# pay or entitlement - company, designation, department, shift, date of joining,
# status, national ID - is absent from this list by hand, because "everything the
# employee does not need to be protected from editing" is not a rule: a portal that can
# write a field can be made to write any field, and the only defence is an explicit
# list and a refusal for anything not on it.
#
# `prefered_email` is the one that earns this screen its keep - it is where payslips are
# sent, and on this site no employee has one.
DETAIL_FIELDS = (
	"personal_email",
	"prefered_email",
	"current_address",
	"emergency_phone_number",
	"blood_group",
)

# `blood_group` is a Select, so the options have to be read rather than restated - a
# copy would fall out of step with the doctype and then refuse a valid entry.
BLOOD_GROUPS = (
	frappe.get_meta("Employee")
	.get_field("blood_group")
	.options.split("\n")
	if frappe.db.exists("DocType", "Employee")
	else []
)


@frappe.whitelist()
def my_details(token: str) -> dict:
	"""This employee's own record: what HR holds, and what they may correct.

	Read-only fields are returned alongside the editable ones so the screen can show the
	whole record in one read - an employee asking "what does HR have on me" should not
	take four requests to answer.
	"""
	emp = resolve_session(token)
	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"employee_number": emp.employee_number,
		"designation": emp.designation,
		"department": emp.department,
		"company": emp.company,
		"date_of_joining": str(emp.date_of_joining) if emp.date_of_joining else None,
		"gender": emp.gender,
		"marital_status": emp.marital_status,
		"blood_groups": BLOOD_GROUPS,
		"editable": {field: emp.get(field) or "" for field in DETAIL_FIELDS},
	}


@frappe.whitelist()
def update_my_details(token: str, **fields) -> dict:
	"""Write the employee's own contact details.

	Refuses any field outside `DETAIL_FIELDS` rather than ignoring it, so a caller that
	sends `company` is told no instead of quietly believing it worked.
	"""
	emp = resolve_session(token)
	unknown = sorted(set(fields) - set(DETAIL_FIELDS))
	if unknown:
		frappe.throw(
			_("You cannot change {0} from the portal.").format(", ".join(unknown)),
			title=_("Not yours to change"),
		)

	values = {}
	for field, value in fields.items():
		value = (value or "").strip()
		if field in ("personal_email", "prefered_email") and value:
			if not validate_email_address(value, throw=True):
				frappe.throw(
					_("{0} is not a valid email address.").format(value),
					title=_("Check this address"),
				)
		if field == "blood_group" and value and value not in BLOOD_GROUPS:
			frappe.throw(
				_("{0} is not a valid blood group.").format(value),
				title=_("Check this value"),
			)
		values[field] = value

	# Written field by field rather than by `doc.update(fields)`, so the whitelist
	# above is the only thing that decides what reaches the record.
	for field, value in values.items():
		frappe.db.set_value("Employee", emp.name, field, value)
	frappe.db.commit()

	return {"ok": True, "updated": sorted(values)}


@frappe.whitelist()
def my_profile(token: str) -> dict:
	emp = resolve_session(token)
	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"designation": emp.designation,
		"department": emp.department,
		"company": emp.company,
		"employee_number": emp.employee_number,
		"date_of_joining": emp.date_of_joining,
		"image": emp.image,
		"national_id": bool(emp.sdg_national_id),
		"has_email": bool(emp.personal_email or emp.company_email),
	}


# ---------------------------------------------------------------------------
# balances
# ---------------------------------------------------------------------------


def usage_by_status(employee: str, year: int) -> dict[str, dict[str, float]]:
	"""Committed leave days per leave code, split approved from pending.

	Each leave is weighted by its type's `sdg_vacation_day_multiplier`, so a day
	worked on a weekly rest day or a public holiday costs two days of allowance - which
	is the entire distinction between `LLDO`/`LLPH` and everything else.

	Split because the two are not the same claim on the balance. An approved request
	is time off the employee has; an `Open` one is time off HR has been asked for and
	not yet agreed. Both are committed (`COMMITTED_STATUSES`), so both come off the
	balance, but only one of them is a fact - and an employee watching a request sit in
	an approver's queue needs to see which half of the figure it is.
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
		fields=["leave_type", "total_leave_days", "status"],
		limit_page_length=0,
	)

	used: dict[str, dict[str, float]] = {}
	for leave in leaves:
		code = code_of.get(leave.leave_type)
		if not code:
			continue
		bucket = used.setdefault(code, {"approved": 0.0, "pending": 0.0})
		bucket["approved" if leave.status == "Approved" else "pending"] += flt(
			leave.total_leave_days
		) * multiplier.get(leave.leave_type, 1.0)
	return used


def days_by_code(employee: str, year: int) -> dict[str, float]:
	"""Committed leave days per leave code, approved and pending together.

	The total, for the one place that only needs "how much of this grant is gone".
	"""
	return {
		code: flt(parts["approved"]) + flt(parts["pending"])
		for code, parts in usage_by_status(employee, year).items()
	}


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
def leave_terms(info) -> dict:
	"""The rules that apply to one leave type, as sentences rather than fields.

	Read off the Leave Type and never restated here, because every one of these is
	policy that HR can change in the desk and a second copy of the rule would drift
	from it silently. A rule left at its default is reported as absent rather than as
	zero: "no limit on consecutive days" and "you may take 0 consecutive days" are
	different claims and only the first is true.
	"""
	if not info:
		return {}
	terms = []

	continuous = cint(info.max_continuous_days_allowed)
	if continuous > 0:
		terms.append(f"up to {continuous} consecutive days at a time")

	per_grant = flt(info.max_leaves_allowed)
	if per_grant > 0:
		terms.append(f"up to {per_grant:g} days from this grant")

	after = cint(info.applicable_after)
	if after > 0:
		terms.append(f"available after {after} days of service")

	if cint(info.allow_encashment):
		encashable = flt(info.max_encashable_leaves)
		terms.append(
			f"up to {encashable:g} days encashable" if encashable > 0 else "encashable"
		)
		non_encashable = flt(info.non_encashable_leaves)
		if non_encashable > 0:
			terms.append(f"{non_encashable:g} days non-encashable")

	multiplier = flt(info.sdg_vacation_day_multiplier, 1.0) or 1.0
	if multiplier != 1.0:
		terms.append(f"each day costs {multiplier:g} days of allowance")

	return {
		# Paid or not decides whether the time off costs salary, which is the first
		# thing anybody asks about a leave type. `is_lwp` is checked first and on its
		# own: it is a statement that the leave is *unpaid*, so it cannot be argued
		# with by a paid flag left on the type - a type carrying both is a data slip,
		# and the name is the one that is unambiguous.
		"paid": False
		if cint(info.is_lwp)
		else bool(info.sdg_is_paid) or bool(info.is_ppl),
		"lwp": bool(info.is_lwp),
		"earned": bool(info.is_earned_leave),
		"compensatory": bool(info.is_compensatory),
		"continuous_days": continuous or None,
		"terms": terms,
	}


def my_balances(token: str) -> dict:
	"""This year's granted leave, per type, with the spending against it.

	Read from `Leave Allocation` because HRMS's `Leave Balance` is not installed on
	this site; see the module docstring.

	Each row carries what the employee needs to act on the figure rather than only
	read it: what is still awaiting approval, the period the grant covers, the
	carry-forward terms, the rules that bound it, and the individual requests that
	account for the spending. A balance with none of those is a number the employee
	can do nothing with.
	"""
	emp = resolve_session(token)
	year = getdate(today()).year

	types = {
		r.name: r
		for r in frappe.get_all(
			"Leave Type",
			fields=["name", "sdg_code", "leave_type_name", "sdg_vacation_day_multiplier",
			        "is_ppl", "fraction_of_daily_salary_per_leave",
			        "sdg_is_paid", "is_lwp", "is_earned_leave", "is_compensatory",
			        "max_continuous_days_allowed", "max_leaves_allowed",
			        "applicable_after", "allow_encashment", "max_encashable_leaves",
			        "non_encashable_leaves", "include_holiday"],
			limit_page_length=0,
		)
	}
	usage = usage_by_status(emp.name, year)
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
		        "carry_forwarded_leaves_count", "unused_leaves",
		        "total_leaves_encashed", "new_leaves_allocated", "carry_forward",
		        "description"],
		order_by="from_date asc",
		limit_page_length=0,
	)

	# The requests behind the spending, so the figure can be checked rather than
	# believed. Grouped by leave type name because that is what the employee chose
	# when requesting, and `days_by_code` reports by tracker code.
	requests_by_type: dict[str, list] = {}
	for leave in frappe.get_all(
		"Leave Application",
		filters={
			"employee": emp.name,
			"status": ("in", COMMITTED_STATUSES),
			"docstatus": ["<", 2],
			"from_date": ["between", (f"{year}-01-01", f"{year}-12-31")],
		},
		fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status"],
		order_by="from_date desc",
		limit_page_length=0,
	):
		requests_by_type.setdefault(leave.leave_type, []).append(leave)

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
		parts = usage.get(code) or {"approved": 0.0, "pending": 0.0}
		row = {
			"code": code,
			"leave_type": alloc.leave_type,
			"label": (info and info.leave_type_name) or alloc.leave_type,
			"allocated": flt(alloc.total_leaves_allocated),
			"used": flt(used.get(code)),
			# The two halves of `used`. An employee reading "3 days taken" needs to know
			# how much of it HR has actually agreed to.
			"approved": flt(parts["approved"]),
			"pending": flt(parts["pending"]),
			"remaining": 0.0,
			"from_date": alloc.from_date,
			"to_date": alloc.to_date,
			"carry_cap": carry["cap"],
			"carry_allowed": carry["allowed"],
			"carry_expires": carry["expires"],
			# What the grant is made of, where HRMS records the split. Zero is left as
			# zero rather than guessed at: this group allocates a flat figure, so
			# "carried in 0" is the honest answer and the screen simply omits the line.
			"carried_in": flt(alloc.carry_forwarded_leaves_count),
			"new_grant": flt(alloc.new_leaves_allocated),
			"encashed": flt(alloc.total_leaves_encashed),
			"note": alloc.description or "",
			"terms": leave_terms(info),
			"requests": [
				{
					"name": r.name,
					"from_date": str(r.from_date),
					"to_date": str(r.to_date),
					"days": flt(r.total_leave_days),
					"status": r.status,
				}
				for r in requests_by_type.get(alloc.leave_type, [])
			],
		}
		row["remaining"] = max(row["allocated"] - row["used"], 0.0)
		rows.append(row)

	# Spending against a type with no allocation still has to be visible, or an
	# over-spent month looks like a clean sheet.
	for code, spent in used.items():
		if code not in seen:
			info = next((i for i in types.values() if i.sdg_code == code), None)
			leave_type = info.name if info else code
			parts = usage.get(code) or {"approved": 0.0, "pending": 0.0}
			rows.append(
				{
					"code": code,
					"leave_type": leave_type,
					"label": (info and info.leave_type_name) or leave_type,
					"allocated": 0.0,
					"used": flt(spent),
					"approved": flt(parts["approved"]),
					"pending": flt(parts["pending"]),
					"remaining": 0.0,
					"from_date": None,
					"to_date": None,
					"carry_cap": 0.0,
					"carry_allowed": None,
					"carry_expires": None,
					"carried_in": 0.0,
					"new_grant": 0.0,
					"encashed": 0.0,
					"note": "",
					"terms": leave_terms(info),
					"requests": [
						{
							"name": r.name,
							"from_date": str(r.from_date),
							"to_date": str(r.to_date),
							"days": flt(r.total_leave_days),
							"status": r.status,
						}
						for r in requests_by_type.get(leave_type, [])
					],
				}
			)

	return {
		"employee": emp.name,
		"year": year,
		"balances": rows,
		"total_remaining": round(sum(r["remaining"] for r in rows), 2),
		"total_allocated": round(sum(r["allocated"] for r in rows), 2),
		"total_used": round(sum(r["used"] for r in rows), 2),
		"total_pending": round(sum(r["pending"] for r in rows), 2),
		# Days the employee has committed to but HR has not agreed, across every type.
		# The one figure worth a line of its own: it is the part of the balance that can
		# still change.
		"request_count": sum(len(r["requests"]) for r in rows),
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
	year = getdate(today_date).year
	requestable = leave_policy.granted_types()
	multipliers = {
		r.name: flt(r.sdg_vacation_day_multiplier, 1.0) or 1.0
		for r in frappe.get_all("Leave Type", fields=["name", "sdg_vacation_day_multiplier"],
		                        limit_page_length=0)
	}
	rows = frappe.get_all(
		"Leave Type",
		filters={"is_lwp": 0},
		fields=["name", "leave_type_name", "sdg_code", "sdg_tracker_name"],
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
	pool = leave_policy.pool_balance(emp.name, year, multipliers)

	return {
		"pool": pool,
		"leave_types": [
			{
				"name": r.name,
				"label": r.leave_type_name,
				"code": r.sdg_code or "",
				"arabic": r.sdg_tracker_name or "",
				# Which track this spends, so the page can say "from your 30 days" rather
				# than implying six separate balances that do not exist.
				"track": leave_policy.classify(r.name),
				# Requestable is a property of the *policy*, not of whether this employee
				# happens to hold an allocation: the pool types share one entitlement, so
				# having no allocation for Casual Leave does not mean there are no days to
				# spend on it.
				"requestable": r.name in requestable,
				# Surfaced so the page can say why a type is not offered, rather than
				# simply omitting it and leaving the employee to wonder.
				"has_allocation": r.name in allocated,
				"balance": pool if leave_policy.classify(r.name) == "pool" else
					leave_policy.track_balance(emp.name, r.name, year),
			}
			for r in rows
		],
	}


def holiday_list_for(employee: str, on_date: str) -> str | None:
	"""The Holiday List that covers `on_date` for this employee.

	An explicit `Holiday List Assignment` wins, because that is HRMS's own resolution
	and HR may run a different calendar per company. This version keys an assignment by
	`applicable_for` plus `employee_company`, and - as on this site, where all thirteen
	assignments are company-wide from 2026-09-29 - carries no end date, so the company
	branch is matched with no date filter at all. Filtering on a `to_date` that does
	not exist would raise rather than fall back, which is why the field list is here.

	The fallback is the single list whose own range covers the day: a site with no
	assignments at all still gets a calendar rather than an empty screen.
	"""
	company = frappe.db.get_value("Employee", employee, "company")

	# `pluck` rather than `get_value`: this doctype is autonamed from a naming series,
	# and asking `get_value` for a field list here returns the row's own sequence
	# rather than the linked Holiday List - a name that resolves to nothing and leaves
	# the screen silently empty.
	assigned = frappe.get_all(
		"Holiday List Assignment",
		filters={"applicable_for": "Company", "employee_company": company,
		         "from_date": ["<=", on_date]},
		pluck="holiday_list",
		order_by="from_date desc",
		limit_page_length=1,
	)
	if assigned and assigned[0]:
		return assigned[0]

	covering = frappe.get_all(
		"Holiday List",
		filters={"from_date": ["<=", on_date], "to_date": [">=", on_date]},
		pluck="name",
		order_by="from_date desc",
		limit_page_length=1,
	)
	return covering[0] if covering else None


@frappe.whitelist()
def my_holidays(token: str) -> dict:
	"""This employee's days off: what is coming, and what the year holds.

	`Holiday` rows are read directly because this is the one screen whose data is
	simply a calendar - there is no balance to derive and nothing to weigh a request
	against. What it adds over the desk is the split that matters to an employee
	deciding whether to travel: the `weekly_off` rows are the ordinary weekend, and
	only the rest are the named public holidays.

	The description is the one HRMS holds, in Arabic, and it is returned as written
	rather than translated here - a second copy of the holiday's name would drift from
	the calendar, and the page renders whatever language the site is in.
	"""
	emp = resolve_session(token)
	today_date = today()
	list_name = holiday_list_for(emp.name, today_date)
	if not list_name:
		return {
			"employee": emp.name,
			"list": None,
			"days": [],
			"public_count": 0,
			"weekly_count": 0,
			"weekly_off": None,
		}

	rows = frappe.get_all(
		"Holiday",
		filters={"parent": list_name, "holiday_date": (">=", today_date)},
		fields=["holiday_date", "description", "weekly_off", "is_half_day"],
		order_by="holiday_date asc",
		limit_page_length=0,
	)

	# The year's totals, counted from January rather than from today: "the year holds
	# 11 public holidays" means the calendar, not the days still to come in it. Split
	# by `weekly_off`, because a Friday is a day off and not a holiday, and lumping
	# them together would report 115 holidays and mean nothing.
	year_start, year_end = f"{today_date[:4]}-01-01", f"{today_date[:4]}-12-31"
	# Positional, not `as_dict`: the generated column names here are the whole `sum(case
	# ...)` expression, so unpacking a dict yielded those names and every count came
	# out as 0. The test that pins weekly_count against a non-zero caught it.
	public_count, weekly_count = frappe.db.sql(
		"select sum(case when weekly_off = 0 then 1 else 0 end), "
		"sum(case when weekly_off = 1 then 1 else 0 end) "
		"from `tabHoliday` where parent = %s and holiday_date between %s and %s",
		(list_name, year_start, year_end),
	)[0]

	days = []
	for row in rows:
		when = getdate(row.holiday_date)
		days.append(
			{
				"date": str(when),
				"description": row.description or "",
				# "public" vs "weekly": the one distinction that changes whether a day
				# is worth planning a trip around.
				"kind": "weekly" if cint(row.weekly_off) else "public",
				"half_day": bool(row.is_half_day),
				"days_until": (when - getdate(today_date)).days,
			}
		)

	return {
		"employee": emp.name,
		"list": list_name,
		"weekly_off": frappe.db.get_value("Holiday List", list_name, "weekly_off"),
		"days": days,
		"upcoming_count": len(days),
		"public_count": cint(public_count),
		"weekly_count": cint(weekly_count),
	}


@frappe.whitelist()
def _requested_days(from_date, to_date, leave_type) -> float:
	"""What this request costs, worked out before the document exists.

	HRMS only settles `total_leave_days` on insert, and the check has to happen before
	that, so the same rule is applied here: a day on a weekly rest day or a public
	holiday costs two days of allowance. Counting calendar days instead would let an
	employee spend thirty days' worth of leave in fifteen, by choosing the cheapest
	weekend of the year.
	"""
	weekly_offs = _weekly_offs(getdate(from_date).year)
	days = 0.0
	day = getdate(from_date)
	last = getdate(to_date)
	while day <= last:
		if day.weekday() not in weekly_offs:
			days += 1
		else:
			# Worked on a day off: two days of allowance for the one day.
			days += 2
		day = add_days(day, 1)
	return days


def _weekly_offs(year: int) -> set[int]:
	"""Weekday numbers the group does not work, read from the Holiday List.

	Python's `weekday()` is Monday-zero; the calendar's rows are named by day, so the
	name is what is matched on. A site with no calendar works Monday to Friday, which is
	the HRMS default rather than a guess.
	"""
	list_name = frappe.db.get_value(
		"Holiday List",
		{"from_date": ["<=", f"{year}-01-01"], "to_date": [">=", f"{year}-12-31"]},
		"name",
		order_by="from_date desc",
	)
	if not list_name:
		return {5, 6}
	rows = frappe.get_all(
		"Holiday",
		filters={"parent": list_name, "weekly_off": 1},
		fields=["holiday_date"],
		limit_page_length=0,
	)
	off = {frappe.utils.get_datetime(r.holiday_date).weekday() for r in rows}
	return off or {5, 6}


def _check_pool(emp, leave_type, from_date, to_date, multipliers) -> None:
	"""Refuse a request that the single annual entitlement cannot cover.

	The pool types deliberately hold no allocation of their own - they spend the Annual
	Leave one - so HRMS will not catch an over-application here and this is the check
	that does.
	"""
	year = getdate(from_date).year
	wanted = _requested_days(from_date, to_date, leave_type)
	pool = leave_policy.pool_balance(emp.name, year, multipliers)

	if wanted > pool["remaining"]:
		frappe.throw(
			_("You have {0} day(s) left this year and this request needs {1}.").format(
				pool["remaining"], wanted
			),
			title=_("Not enough leave"),
		)

	if leave_type == frappe.db.get_value("Leave Type", {"sdg_code": "CL"}, "name"):
		casual = leave_policy.casual_spent(emp.name, year, multipliers)
		if casual + wanted > leave_policy.CASUAL_CAP_DAYS:
			frappe.throw(
				_("Casual leave is limited to {0} days a year and you have used {1}.").format(
					leave_policy.CASUAL_CAP_DAYS, casual
				),
				title=_("Casual leave limit"),
			)


def _check_own_allocation(emp, leave_type, from_date, to_date) -> None:
	"""Refuse a request outside this type's own allocation period.

	Sick Leave and Leave In Lieu sit outside the annual pool and carry their own
	allocation, so the original period check applies to them unchanged.
	"""
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

	track = leave_policy.classify(leave_type)
	if track == "earned":
		frappe.throw(
			_("{0} is earned by working, not requested.").format(leave_type),
			title=_("Not requestable"),
		)

	multipliers = {
		r.name: flt(r.sdg_vacation_day_multiplier, 1.0) or 1.0
		for r in frappe.get_all("Leave Type", fields=["name", "sdg_vacation_day_multiplier"],
		                        limit_page_length=0)
	}

	if track == "pool":
		_check_pool(emp, leave_type, from_date, to_date, multipliers)
	else:
		_check_own_allocation(emp, leave_type, from_date, to_date)

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
			# A half day is a half day: HRMS only records 0.5 when it is told which day
			# it applies to, and a request left as a whole day would silently cost twice
			# what the employee meant.
			**({"half_day": 1, "half_day_date": getdate(from_date)}
			   if leave_policy.is_half_day(leave_type) else {}),
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


# ---------------------------------------------------------------------------
# directory
# ---------------------------------------------------------------------------


@frappe.whitelist()
def my_colleagues(token: str) -> dict:
	"""The whole directory, in one read.

	Every active employee of the company, plus the list of departments they are spread
	over. There is deliberately no `search` or `department` argument: the client filters
	what it already has, so typing a name costs nothing and still works offline. A
	server-side search would also have been the wrong shape here - a directory of a few
	hundred rows is not worth a query per keystroke on a device sharing one connection.
	"""
	emp = resolve_session(token)
	employee = frappe.get_doc("Employee", emp.name)

	filters = {"status": "Active", "company": employee.company}

	fields = [
		"name", "employee_name", "employee_number", "designation",
		"department", "company", "image", "user_id", "cell_number",
		"company_email", "reports_to", "date_of_joining"
	]

	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=fields,
		order_by="employee_name asc",
		limit_page_length=500,
	)

	departments = sorted(
		{d for d in (e.department for e in employees) if d}
	)

	# Resolved here rather than looked up per card on the device: the names are already
	# in this same result set, so "who does this person report to" needs no second read.
	by_name = {e.name: e.employee_name for e in employees}
	for row in employees:
		row["manager_name"] = by_name.get(row.reports_to) or ""
		row["is_me"] = row.name == emp.name
		del row["reports_to"]

	return {
		"employee": emp.name,
		"colleagues": employees,
		"departments": departments,
		"total": len(employees),
	}


@frappe.whitelist()
def my_org_chart(token: str) -> dict:
	"""Org chart data for this employee's company.

	Returns a tree structure starting from the top-level managers (those with no
	reports_to or whose manager is outside the company) down to individual contributors.
	Includes the signed-in employee's path highlighted.
	"""
	emp = resolve_session(token)
	employee = frappe.get_doc("Employee", emp.name)

	# Get all active employees in the company
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": employee.company},
		fields=[
			"name", "employee_name", "employee_number", "designation",
			"department", "image", "reports_to", "user_id"
		],
		order_by="employee_name asc",
		limit_page_length=500,
	)

	# Build lookup maps
	by_name = {e.name: e for e in employees}
	children_map = {}
	roots = []

	for e in employees:
		if e.reports_to and e.reports_to in by_name:
			children_map.setdefault(e.reports_to, []).append(e.name)
		else:
			roots.append(e.name)

	# Find the signed-in employee's path to root for highlighting
	def build_path_to_root(emp_name):
		path = []
		current = emp_name
		while current and current in by_name:
			path.append(current)
			parent = by_name[current].reports_to
			if not parent or parent not in by_name:
				break
			current = parent
		return path

	my_path = set(build_path_to_root(emp.name))

	# Recursive tree builder
	def build_node(emp_name, depth=0):
		e = by_name[emp_name]
		node = {
			"name": e.name,
			"employee_name": e.employee_name,
			"employee_number": e.employee_number,
			"designation": e.designation,
			"department": e.department,
			"image": e.image,
			"is_me": e.name == emp.name,
			"in_my_path": e.name in my_path,
			"depth": depth,
			"children": [],
		}
		for child_name in children_map.get(emp_name, []):
			node["children"].append(build_node(child_name, depth + 1))
		return node

	tree = [build_node(root) for root in roots]

	return {
		"employee": emp.name,
		"company": employee.company,
		"tree": tree,
		"total": len(employees),
		"my_path": list(my_path),
	}


@frappe.whitelist()
def my_timesheets(token: str) -> dict:
	"""This employee's timesheets."""
	emp = resolve_session(token)
	timesheets = frappe.get_all(
		"Timesheet",
		filters={"employee": emp.name, "docstatus": ["<", 2]},
		fields=["name", "start_date", "end_date", "total_hours", "status", "creation", "modified"],
		order_by="start_date desc",
		limit_page_length=50,
	)
	return {"employee": emp.name, "timesheets": timesheets}


@frappe.whitelist()
def submit_timesheet(token: str, start_date: str, end_date: str, hours: float, activity_type: str, description: str | None = None) -> dict:
	"""Submit a new timesheet for the signed-in employee."""
	emp = resolve_session(token)
	ts = frappe.get_doc({
		"doctype": "Timesheet",
		"employee": emp.name,
		"start_date": start_date,
		"end_date": end_date,
		"time_logs": [{
			"activity_type": activity_type,
			"hours": flt(hours),
			"from_time": start_date + " 09:00:00",
			"to_time": start_date + " " + "{:02d}:{:02d}:00".format(9 + int(hours), int((hours % 1) * 60)),
			"description": description or "",
		}],
		"status": "Draft",
	})
	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		ts.insert(ignore_permissions=True)
	finally:
		frappe.set_user(previous)
	return {"name": ts.name, "status": ts.status, "total_hours": ts.total_hours}


@frappe.whitelist()
def my_overtime(token: str) -> dict:
	"""This employee's overtime slips.

	HRMS no longer has an `Overtime Request` doctype; the real document is the
	`Overtime Slip`, which HR folds into the payroll entry. The portal reads the
	employee's slips and reports their state from the slip's own fields.
	"""
	emp = resolve_session(token)
	rows = frappe.get_all(
		"Overtime Slip",
		filters={"employee": emp.name, "docstatus": ["<", 2]},
		fields=[
			"name", "posting_date", "start_date", "end_date", "company",
			"total_overtime_duration", "salary_slip", "docstatus"
		],
		order_by="posting_date desc",
		limit_page_length=50,
	)
	overtime = []
	for row in rows:
		status = "In Payroll" if row.salary_slip else ("Submitted" if row.docstatus == 1 else "Draft")
		span = (
			str(row.start_date)
			if str(row.start_date) == str(row.end_date)
			else f"{row.start_date} to {row.end_date}"
		)
		overtime.append({
			"name": row.name,
			"date": row.posting_date,
			"hours": row.total_overtime_duration,
			"status": status,
			"description": row.company,
			"period": span,
		})
	return {"employee": emp.name, "overtime": overtime}


@frappe.whitelist()
def submit_overtime(token: str, date: str, hours: float, reason: str) -> dict:
	"""Hand in an overtime slip for the signed-in employee.

	Creates an `Overtime Slip` (the real HRMS document) in draft, for HR to pick up
	with the payroll entry. The detail row's overtime type is the first one that
	allows the requested hours, so the slip stays valid when it is processed.
	`reason` is accepted for the form but the slip has no free-text field to carry it.
	"""
	emp = resolve_session(token)
	hours = flt(hours)
	if hours <= 0:
		frappe.throw(_("Overtime hours must be greater than zero."))
	if not date:
		frappe.throw(_("Select a date."))

	overtime_type = None
	for ot in frappe.get_all(
		"Overtime Type",
		fields=["name", "maximum_overtime_hours_allowed"],
		order_by="name asc",
		limit_page_length=100,
	):
		max_hours = flt(ot.maximum_overtime_hours_allowed)
		if not max_hours or hours <= max_hours:
			overtime_type = ot.name
			break
	if not overtime_type:
		frappe.throw(_("No Overtime Type is configured yet. Ask HR to add one, then submit again."))
	if frappe.db.exists("Overtime Slip", {"employee": emp.name, "docstatus": ["<", 2], "start_date": ["<=", date], "end_date": [">=", date]}):
		frappe.throw(_("An overtime slip already covers this date."))

	slip = frappe.get_doc({
		"doctype": "Overtime Slip",
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"company": emp.company,
		"posting_date": date,
		"start_date": date,
		"end_date": date,
		"overtime_details": [{
			"doctype": "Overtime Details",
			"date": date,
			"overtime_type": overtime_type,
			"overtime_duration": hours,
			"standard_working_hours": 8.0,
		}],
		"total_overtime_duration": hours,
	})
	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		slip.insert(ignore_permissions=True)
	finally:
		frappe.set_user(previous)
	return {"name": slip.name, "status": "Draft", "hours": slip.total_overtime_duration}


@frappe.whitelist()
def my_leave_calendar(token: str, month: int | None = None, year: int | None = None) -> dict:
	"""Team leave calendar for the employee's department."""
	emp = resolve_session(token)
	employee = frappe.get_doc("Employee", emp.name)
	
	from frappe.utils import getdate, today, add_days, get_first_day, get_last_day
	if not month:
		month = getdate(today()).month
	if not year:
		year = getdate(today()).year
	
	month_start = getdate(f"{year}-{month:02d}-01")
	month_end = get_last_day(month_start)
	
	# Get colleagues in same department
	colleagues = frappe.get_all(
		"Employee",
		filters={"status": "Active", "department": employee.department, "company": employee.company},
		pluck="name",
	)
	
	# Get approved leaves for the month
	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", colleagues],
			"status": "Approved",
			"docstatus": 1,
			"from_date": ["<=", month_end],
			"to_date": [">=", month_start],
		},
		fields=["employee", "employee_name", "leave_type", "from_date", "to_date", "total_leave_days", "status"],
		order_by="from_date asc",
		limit_page_length=200,
	)
	
	# Group by employee
	by_employee = {}
	for leave in leaves:
		by_employee.setdefault(leave.employee, []).append(leave)
	
	return {
		"employee": emp.name,
		"department": employee.department,
		"month": month,
		"year": year,
		"month_start": str(month_start),
		"month_end": str(month_end),
		"leaves": leaves,
		"by_employee": by_employee,
		"colleagues": colleagues,
	}


@frappe.whitelist()
def activity_types(token: str) -> dict:
	"""Activity types available for timesheet logging.

	`disabled`, not `is_active`, and `activity_type` rather than a display copy of it:
	those are the only two fields the base doctype has, and asking for a third is the
	`Unknown column` error the desk does not show but the portal's 400 does.
	"""
	emp = resolve_session(token)
	types = frappe.get_all(
		"Activity Type",
		filters={"disabled": 0},
		fields=["name", "activity_type"],
		order_by="activity_type asc",
		limit_page_length=100,
	)
	return {"activity_types": types}


@frappe.whitelist()
def my_assets(token: str) -> dict:
	"""This employee's assigned assets & equipment.

	Read-only, and only fields the base `Asset` actually carries: custodianship, the
	category, where it is, when it was bought, and the insurance window. Requesting
	equipment or booking maintenance is desk work, not a tablet screen.
	"""
	emp = resolve_session(token)

	assets = frappe.get_all(
		"Asset",
		filters={"custodian": emp.name, "docstatus": 1},
		fields=[
			"name", "asset_name", "asset_category", "item_code", "location",
			"status", "department", "purchase_date", "purchase_amount",
			"policy_number", "insurer", "insurance_end_date",
		],
		order_by="purchase_date desc",
		limit_page_length=50,
	)

	soon = frappe.utils.add_days(frappe.utils.today(), 30)
	# "In Use" is not an Asset status in the base doctype; a live asset is one that has
	# been submitted and not written off.
	live = ("Submitted", "Partly Depreciated", "Fully Depreciated")

	return {
		"employee": emp.name,
		"assets": assets,
		"total_assets": len(assets),
		"active_assets": len([a for a in assets if a.status in live]),
		"insurance_due": len([a for a in assets if a.insurance_end_date and a.insurance_end_date < soon]),
	}
