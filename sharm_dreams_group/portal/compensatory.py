# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Compensatory Leave (Leave In Lieu) earning.

When an employee works on a public holiday or their weekly rest day, they earn
Leave In Lieu (LIW) credits. This module runs nightly, finds check-ins that fall
on holidays or weekly rest days, and creates *proposed* credits for HR/manager
approval rather than crediting automatically - so the balance only moves when
HR confirms the work was legitimate.

The credit amount follows the existing `sdg_vacation_day_multiplier` on the
Leave Type: `Worked Public Holiday` (2.0) and `Worked Rest Day` (2.0) are the
two types that pay double. The employee's check-in records the actual hours, and
the credit is `hours_worked / 8.0 * multiplier`, capped at 1.0 day per calendar
day (you cannot earn more than one day of compensatory leave from a single day
of work, no matter how many hours you logged).
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, get_datetime, add_days, time_diff_in_hours

# Multiplier codes that represent earned compensatory leave
EARNABLE_CODES = ("LLDO", "LLPH", "WD")
LIW_CODE = "LIW"


def get_liw_type_name() -> str | None:
	"""The Leave In Lieu type name, looked up by tracker code."""
	return frappe.db.get_value("Leave Type", {"sdg_code": "LIW"}, "name")


def get_earnable_type_multipliers() -> dict[str, float]:
	"""Map earnable leave type names to their `sdg_vacation_day_multiplier`."""
	rows = frappe.get_all(
		"Leave Type",
		filters={"sdg_code": ("in", EARNABLE_CODES)},
		fields=["name", "sdg_vacation_day_multiplier"],
		limit_page_length=0,
	)
	return {r.name: flt(r.sdg_vacation_day_multiplier, 2.0) for r in rows}


def _employee_workday_shift(employee: str, on_date) -> dict | None:
	"""The shift the employee was assigned on `on_date`, if any.

	Returns a dict with `shift_type`, `start_time`, `end_time`, or None if the
	employee had no submitted, active assignment covering that day.
	"""
	today_date = getdate(on_date)
	SA = frappe.qb.DocType("Shift Assignment")
	rows = (
		frappe.qb.from_(SA)
		.select("name", "shift_type", "start_date", "end_date", "overtime_type")
		.where(
			(SA.employee == employee)
			& (SA.docstatus == 1)
			& (SA.status == "Active")
			& (SA.start_date <= today_date)
			& (
				(SA.end_date.isnull())
				| (SA.end_date >= today_date)
			)
		)
		.orderby("start_date", order=frappe.qb.desc)
		.limit(1)
		.run(as_dict=True)
	)

	if not rows:
		return None

	row = rows[0]
	shift_details = frappe.db.get_value(
		"Shift Type",
		row.shift_type,
		["start_time", "end_time", "begin_check_in_before_shift_start_time", "allow_check_out_after_shift_end_time"],
		as_dict=True,
	)
	if not shift_details:
		return None

	return {
		"shift_type": row.shift_type,
		"start_time": shift_details.start_time,
		"end_time": shift_details.end_time,
		"begin_check_in_before_shift_start_time": shift_details.begin_check_in_before_shift_start_time,
		"allow_check_out_after_shift_end_time": shift_details.allow_check_out_after_shift_end_time,
	}
	if not shifts:
		return None
	shift_type = shifts[0]
	details = frappe.db.get_value(
		"Shift Type",
		shift_type,
		["start_time", "end_time"],
		as_dict=True,
	)
	if not details:
		return None
	return {
		"shift_type": shift_type,
		"start_time": details.start_time,
		"end_time": details.end_time,
	}


def _holiday_on(date) -> bool:
	"""Whether `date` is a public holiday in the employee's holiday list."""
	from sharm_dreams_group.portal.portal import holiday_list_for

	employee = frappe.local.form_dict.get("employee")  # not available here
	# We need the employee's holiday list; this helper will be called from a
	# context that already knows the employee, so we accept the list name.
	# For the scheduler, we'll pass it explicitly.
	pass


def _weekly_off_days(holiday_list: str) -> set[int]:
	"""Weekday numbers (Mon=0) that are weekly off, read from the Holiday List."""
	rows = frappe.get_all(
		"Holiday",
		filters={"parent": holiday_list, "weekly_off": 1},
		fields=["holiday_date"],
		limit_page_length=0,
	)
	off = {frappe.utils.get_datetime(r.holiday_date).weekday() for r in rows}
	return off or {5, 6}  # default Sat/Sun


def _is_holiday(date, holiday_list: str) -> bool:
	"""Whether `date` is a public holiday in the given holiday list."""
	return bool(
		frappe.db.exists(
			"Holiday",
			{"parent": holiday_list, "holiday_date": getdate(date)},
		)
	)


def _employee_holiday_list(employee: str, on_date) -> str | None:
	"""The holiday list name for an employee on a given date."""
	from sharm_dreams_group.portal.portal import holiday_list_for

	return holiday_list_for(employee, on_date)


def _checkins_on(date, employee: str) -> list[dict]:
	"""All check-ins for `employee` on `date`, ordered by time."""
	return frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", (f"{date} 00:00:00", f"{date} 23:59:59")],
		},
		fields=["name", "time", "log_type", "latitude", "longitude"],
		order_by="time asc",
	)


def _hours_worked(checkins: list[dict], shift_start, shift_end) -> float:
	"""Total hours worked between first IN and last OUT, capped to shift hours.

	If the employee has no valid IN/OUT pair, returns 0.
	"""
	if not checkins:
		return 0.0

	# Find first IN and last OUT
	first_in = next((c for c in checkins if c.log_type == "IN"), None)
	last_out = next((c for c in reversed(checkins) if c.log_type == "OUT"), None)

	if not first_in or not last_out:
		return 0.0

	in_dt = get_datetime(first_in.time)
	out_dt = get_datetime(last_out.time)

	if out_dt <= in_dt:
		return 0.0

	# Cap at shift duration if shift times are available
	hours = time_diff_in_hours(out_dt, in_dt)
	if shift_start and shift_end:
		shift_hours = time_diff_in_hours(
			frappe.utils.get_datetime(f"2000-01-01 {shift_end}"),
			frappe.utils.get_datetime(f"2000-01-01 {shift_start}"),
		)
		if shift_hours > 0:
			hours = min(hours, shift_hours)

	return max(0.0, hours)


def _compute_credit(
	hours_worked: float,
	multiplier: float,
) -> float:
	"""Credit = hours_worked / 8.0 * multiplier, capped at 1.0 day."""
	if hours_worked <= 0:
		return 0.0
	credit = (hours_worked / 8.0) * multiplier
	return min(credit, 1.0)


def _create_proposal(
	employee: str,
	on_date,
	leave_type: str,
	credit_days: float,
	reason: str,
) -> str:
	"""Create a Leave Allocation proposal for HR/manager approval.

	The proposal is a Leave Allocation in Draft status; HR approves by
	submitting it. The `reason` explains why the credit is proposed.

	Note: `to_date` must be strictly after `from_date` per HRMS validation,
	so we set `to_date` to the next day for single-day allocations.
	"""
	on_date_obj = getdate(on_date)
	to_date_obj = add_days(on_date_obj, 1)
	doc = frappe.get_doc({
		"doctype": "Leave Allocation",
		"employee": employee,
		"leave_type": leave_type,
		"from_date": on_date_obj,
		"to_date": to_date_obj,
		"new_leaves_allocated": credit_days,
		"description": f"Auto-proposed: {reason}",
		"docstatus": 0,  # Draft - requires approval
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def process_daily_compensatory_earnings(on_date=None) -> dict:
	"""Main scheduler entry point.

	For each active employee with a shift assignment and holiday list:
	  1. Determine if `on_date` was a public holiday or weekly rest day.
	  2. If so, collect their check-ins and compute hours worked.
	  3. If hours > 0, compute the compensatory credit.
	  4. Create a Draft Leave Allocation for LIW with the credit.

	Returns a summary dict for logging.
	"""
	on_date = getdate(on_date or frappe.utils.today())
	report = {"processed": 0, "proposals": 0, "skipped": 0, "errors": []}

	liw_type = get_liw_type_name()
	if not liw_type:
		report["errors"].append("Leave In Lieu type (LIW) not found")
		return report

	multipliers = get_earnable_type_multipliers()

	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": ("is", "set")},
		fields=["name", "company", "branch"],
		limit_page_length=0,
	)

	for emp in employees:
		try:
			holiday_list = _employee_holiday_list(emp.name, on_date)
			if not holiday_list:
				report["skipped"] += 1
				continue

			is_holiday = _is_holiday(on_date, holiday_list)
			weekly_off_days = _weekly_off_days(holiday_list)
			is_weekly_off = getdate(on_date).weekday() in weekly_off_days

			if not (is_holiday or is_weekly_off):
				report["skipped"] += 1
				continue

			shift = _employee_workday_shift(emp.name, on_date)
			if not shift:
				report["skipped"] += 1
				continue

			checkins = _checkins_on(on_date, emp.name)
			if not checkins:
				report["skipped"] += 1
				continue

			hours = _hours_worked(checkins, shift["start_time"], shift["end_time"])
			if hours <= 0:
				report["skipped"] += 1
				continue

			# Determine which earnable type applies
			earnable_type = None
			if is_holiday:
				earnable_type = next(
					(n for n, m in multipliers.items() if m == 2.0 and "Public Holiday" in n),
					None,
				)
			if not earnable_type and is_weekly_off:
				earnable_type = next(
					(n for n, m in multipliers.items() if m == 2.0 and "Rest Day" in n),
					None,
				)

			if not earnable_type:
				report["skipped"] += 1
				continue

			multiplier = multipliers.get(earnable_type, 2.0)
			credit = _compute_credit(hours_worked=hours, multiplier=multiplier)

			if credit <= 0:
				report["skipped"] += 1
				continue

			reason = (
				f"Worked on {'public holiday' if is_holiday else 'weekly rest day'} "
				f"({on_date}); {hours:.2f}h @ {multiplier}x = {credit:.2f}d LIW"
			)
			proposal = _create_proposal(
				employee=emp.name,
				on_date=on_date,
				leave_type="Leave In Lieu",
				credit_days=credit,
				reason=reason,
			)
			report["proposals"] += 1
			frappe.logger().info(f"Compensatory proposal {proposal} for {emp.name} on {on_date}")

		except Exception as e:
			report["errors"].append(f"{emp.name}: {e}")
			frappe.log_error(f"Compensatory earnings failed for {emp.name}", str(e))

		report["processed"] += 1

	frappe.db.commit()
	return report