# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The leave policy this portal enforces, in one place.

Everything here is the group's own rule rather than a per-employee arrangement, and all
of it is recorded as data rather than as code: the Leave Type rows carry the flags, the
Leave Allocations carry the entitlement, and this module only says how they combine.

**Why there is a pool at all.** The group runs one entitlement - thirty days - and draws
several leave types against it. Annual Leave, Privilege Leave, Casual Leave, Paid
Absence and Half Day are *names for spending the same thirty days*, not separate buckets.
So they are checked together against the Annual Leave allocation, and an employee who
takes ten days of Casual Leave has ten fewer Annual days left. Splitting them into
per-type allocations would show a generous balance on six rows that no one can actually
use, which is worse than a smaller honest one.

**Two types sit outside it.** Sick Leave is legally mandated, has its own allocation, and
never touches the thirty days - it cannot, or a long illness would silently consume the
employee's annual leave. Leave In Lieu is a compensatory bank that starts at zero and is
filled only by working a public holiday or a weekly rest day; it is never granted.

The one number here that is not from HR is `SICK_ENTITLEMENT_DAYS`. It is a named
constant rather than a literal buried in a calculation so that correcting it is a
one-line change, and it is seeded by `seed_sick_leave_allocations`, which is idempotent:
re-running it after changing the number tops the entitlement up or draws it down rather
than doubling it.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, getdate, today

# Leave types spent against the single annual entitlement, by the tracker's own codes.
# `Privilege Leave` carries no code on this site, so it is named - it is the same thing
# as Annual Leave under a second name, and an employee choosing it must not be told the
# balance is empty.
POOL_CODES = ("AL", "CL", "PA", "HD")
POOL_TYPE_NAMES = ("Annual Leave", "Privilege Leave")

# The sub-limit inside the pool. This group's rule: Casual Leave is the same thirty days
# but a small annual allowance of its own, so a long illness cannot be paid for out of
# it. A cap of zero would mean "unlimited", not "none" - that is what HRMS means by it,
# and getting that backwards would silently forbid all casual leave.
CASUAL_CAP_DAYS = 6.0

# Sick Leave's own annual entitlement, on its own track.
SICK_LEAVE_DAYS = 30.0

# Codes for the two tracks outside the pool.
SICK_CODE = "SL"
LIEU_CODE = "LIW"

# The annual entitlement the pool is measured against. Named because the pool is
# expressed in one allocation and the type holding it is a data change, not a code one.
POOL_ANCHOR = "Annual Leave"


def pool_types() -> list[str]:
	"""Leave Type names that spend the annual entitlement, whatever code they carry."""
	names = list(POOL_TYPE_NAMES)
	for code in POOL_CODES:
		name = frappe.db.get_value("Leave Type", {"sdg_code": code}, "name")
		if name and name not in names:
			names.append(name)
	return names


def classify(name: str) -> str:
	"""Which track a leave type spends: `pool`, `sick`, `lieu`, or `earned`.

	`earned` covers the types paid by *working* a holiday or a rest day - the six
	compensatory ones plus Weekly Rest and Friday Leave. They are never granted and never
	requestable, so a balance row for each would read zero forever and imply a shortfall.
	"""
	code = frappe.db.get_value("Leave Type", name, "sdg_code")
	if name in POOL_TYPE_NAMES:
		return "pool"
	if code in POOL_CODES:
		return "pool"
	if code == SICK_CODE:
		return "sick"
	if code == LIEU_CODE:
		return "lieu"
	return "earned"


def granted_types() -> list[str]:
	"""Leave types an employee may actually request.

	Everything that is spent rather than earned, and not unpaid leave: Leave Without Pay
	carries no entitlement at all, and offering it next to the others would ask an
	employee to pick a bucket that has no balance in it.
	"""
	rows = frappe.get_all(
		"Leave Type",
		filters={"is_lwp": 0},
		fields=["name", "sdg_code"],
		limit_page_length=0,
	)
	out = []
	for row in rows:
		if classify(row.name) != "earned":
			out.append(row.name)
	return out


def is_half_day(leave_type: str) -> bool:
	"""Whether this type is taken in half days.

	`Half Day` is the whole reason this exists: HRMS will record `0.5` on a request only
	when `half_day` and `half_day_date` are set, so a request left as a whole day costs
	twice what the employee meant. Read from the tracker code so adding another
	half-day type is a data change.
	"""
	return frappe.db.get_value("Leave Type", leave_type, "sdg_code") == "HD"


def pool_allocation(employee: str, year: int) -> float:
	"""The annual entitlement the pool is measured against, in days."""
	return flt(
		frappe.db.get_value(
			"Leave Allocation",
			{
				"employee": employee,
				"leave_type": POOL_ANCHOR,
				"docstatus": 1,
				"from_date": ["<=", f"{year}-12-31"],
				"to_date": [">=", f"{year}-01-01"],
			},
			"total_leaves_allocated",
		)
	)


def pool_spent(employee: str, year: int, multiplier_of=None) -> float:
	"""Days already spent against the pool, across every type that draws on it.

	`multiplier_of` maps a leave type name to its day multiplier, so this is weighted the
	same way `portal.days_by_code` weights it - a day worked on a weekly rest day costs two
	days of allowance whichever bucket is paying for it.

	Passing the map rather than importing it keeps this module free of a dependency on
	`portal.portal`, which imports this one.
	"""
	codes = set(POOL_CODES)
	names = set(pool_types())
	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"status": ("in", ("Approved", "Open")),
			"docstatus": ["<", 2],
			"from_date": ["between", (f"{year}-01-01", f"{year}-12-31")],
		},
		fields=["leave_type", "total_leave_days"],
		limit_page_length=0,
	)
	spent = 0.0
	for leave in leaves:
		if leave.leave_type not in names:
			continue
		code = frappe.db.get_value("Leave Type", leave.leave_type, "sdg_code")
		if code and code not in codes:
			continue
		weight = 1.0
		if multiplier_of:
			weight = flt(multiplier_of.get(leave.leave_type), 1.0) or 1.0
		spent += flt(leave.total_leave_days) * weight
	return spent


def pool_balance(employee: str, year: int, multiplier_of=None) -> dict:
	"""The one balance every pool type spends against."""
	allocated = pool_allocation(employee, year)
	spent = pool_spent(employee, year, multiplier_of)
	return {
		"allocated": allocated,
		"spent": spent,
		"remaining": max(allocated - spent, 0.0),
		"casual_cap": CASUAL_CAP_DAYS,
	}


def casual_spent(employee: str, year: int, multiplier_of=None) -> float:
	"""Casual Leave days taken this year, for the sub-limit inside the pool."""
	code = frappe.db.get_value("Leave Type", {"sdg_code": "CL"}, "name")
	if not code:
		return 0.0
	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"leave_type": code,
			"status": ("in", ("Approved", "Open")),
			"docstatus": ["<", 2],
			"from_date": ["between", (f"{year}-01-01", f"{year}-12-31")],
		},
		fields=["total_leave_days"],
		limit_page_length=0,
	)
	total = 0.0
	for leave in leaves:
		weight = 1.0
		if multiplier_of:
			weight = flt(multiplier_of.get(code), 1.0) or 1.0
		total += flt(leave.total_leave_days) * weight
	return total


def track_balance(employee: str, leave_type: str, year: int) -> dict:
	"""Balance for a type that has its own allocation: Sick Leave, Leave In Lieu."""
	allocated = flt(
		frappe.db.sql(
			"select sum(total_leaves_allocated) from `tabLeave Allocation` "
			"where employee = %s and leave_type = %s and docstatus = 1 "
			"and from_date <= %s and to_date >= %s",
			(employee, leave_type, f"{year}-12-31", f"{year}-01-01"),
		)[0][0]
	)
	spent = flt(
		frappe.db.sql(
			"select sum(total_leave_days) from `tabLeave Application` "
			"where employee = %s and leave_type = %s "
			"and status in ('Approved', 'Open') and docstatus < 2 "
			"and from_date between %s and %s",
			(employee, leave_type, f"{year}-01-01", f"{year}-12-31"),
		)[0][0]
	)
	return {
		"allocated": allocated,
		"spent": spent,
		"remaining": max(allocated - spent, 0.0),
	}


def enable_pool_types() -> dict:
	"""Let HRMS accept a request for a type that spends the pool. Idempotent.

	Annual, Casual, Privilege, Paid Absence and Half Day hold **no allocation of their
	own** - one entitlement, five names for spending it. HRMS, however, refuses a leave
	application whose type has no allocation covering the dates, in
	`validate_dates_across_allocation`, and the only way past that is the type's own
	`allow_negative` flag. That flag is set here *only* on the pool types that have no
	allocation, and never on Annual Leave, which has real allocations and keeps HRMS's
	own balance check intact.

	The check itself is ours - `_check_pool` in `portal.portal` - so `allow_negative` is
	only removing an obstacle, not granting permission: a request the pool cannot cover
	still comes back with a sentence.
	"""
	report = {"enabled": [], "already": [], "skipped_anchor": []}
	for name in pool_types():
		if name == POOL_ANCHOR:
			# Has real allocations; leaving `allow_negative` off keeps HRMS's own
			# balance validation in force for the type the entitlement is held against.
			report["skipped_anchor"].append(name)
			continue
		if cint(frappe.db.get_value("Leave Type", name, "allow_negative")):
			report["already"].append(name)
			continue
		frappe.db.set_value("Leave Type", name, "allow_negative", 1)
		report["enabled"].append(name)
	return report


def seed_sick_leave_allocations(year: int | None = None) -> dict:
	"""Give every active employee this year's Sick Leave entitlement. Idempotent.

	Sick Leave is on its own track, so it needs its own `Leave Allocation` - unlike the
	pool types, which deliberately have none and are checked against the annual
	allocation instead. HRMS has no way to record an entitlement that is not an
	allocation, so this is the only way to give Sick Leave a balance at all.

	Idempotent by reading what is already there rather than by assuming: an employee who
	has already been given Sick Leave is left alone, so re-running after changing
	`SICK_LEAVE_DAYS` tops the existing allocation up rather than adding a second one.
	Re-running never reduces an allocation - drawing someone's entitlement back down
	because a constant changed is not something a seeder should do to a live record.
	"""
	year = year or getdate(today()).year
	report = {"created": 0, "kept": 0, "skipped_no_company": 0, "skipped_no_type": 0}

	if not frappe.db.exists("Leave Type", {"sdg_code": SICK_CODE}):
		report["skipped_no_type"] = 1
		return report

	sick = frappe.db.get_value("Leave Type", {"sdg_code": SICK_CODE}, "name")
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": ("is", "set")},
		fields=["name", "company"],
		limit_page_length=0,
	)

	for emp in employees:
		existing = frappe.get_all(
			"Leave Allocation",
			filters={
				"employee": emp.name,
				"leave_type": sick,
				"docstatus": 1,
				"from_date": ["<=", f"{year}-12-31"],
				"to_date": [">=", f"{year}-01-01"],
			},
			pluck="name",
			limit_page_length=1,
		)
		if existing:
			if flt(frappe.db.get_value("Leave Allocation", existing[0], "total_leaves_allocated")) >= SICK_LEAVE_DAYS:
				report["kept"] += 1
				continue
			# Short of the entitlement: top the existing one up rather than adding a
			# second, because two allocations for one entitlement is two balances and an
			# employee cannot tell which one they are spending.
			frappe.db.set_value(
				"Leave Allocation",
				existing[0],
				"new_leaves_allocated",
				SICK_LEAVE_DAYS,
				update_modified=False,
			)
			report["kept"] += 1
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Leave Allocation",
				"employee": emp.name,
				"leave_type": sick,
				"from_date": f"{year}-01-01",
				"to_date": f"{year}-12-31",
				# `new_leaves_allocated`, not `total_leaves_allocated`: HRMS's validator
				# recomputes the total as `unused_leaves + new_leaves_allocated` and
				# refuses the insert when it lands on zero, so writing the total is
				# silently discarded and the entitlement never exists.
				"new_leaves_allocated": SICK_LEAVE_DAYS,
				"description": "Sick leave entitlement - separate from the annual leave pool",
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		report["created"] += 1

	return report