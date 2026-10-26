# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Attendance: the employee's own shifts, and tapping to record today's hours.

Tap-to-check-in, geofenced. The browser sends its coordinates and `portal.geo`
verifies them against the `Shift Location` on the employee's current shift assignment -
the same work-location zone HRMS's own check reads - before the checkin is written.
Whether a punch outside the zone is refused or merely flagged is `geo_punch_enforce` in
Employee Self Service Settings. The coordinates and the verdict are recorded on the
checkin either way, so a punch that was refused can still be audited.

`HR Settings.allow_geolocation_tracking` is off on this site, so HRMS's
`validate_distance_from_shift_location` returns immediately and the check happens here
instead - which is what lets the verdict be stored and a sentence returned rather than a
generic validation error. WebAuthn is retired - the punch is a single tap.

Three facts about this site's HRMS shape the whole module.

**A shift is only found on a *submitted* `Shift Assignment`.** `get_shifts_for_date`
filters on `docstatus == 1`, so a draft assignment resolves to nothing and every punch
lands as `offshift` with no shift on it. The 177 assignments on this site are
submitted by `seeder.assign_shifts_by_gender`.

**HRMS works out IN and OUT itself.** Both shift types here have
`determine_check_in_and_check_out` set to "Alternating entries as IN and OUT during the
same shift", so no `log_type` is sent. That is what makes a single button correct: the
first tap of the day is the IN, the next is the OUT, and HRMS decides which. Under
"Strictly based on Log Type" it would refuse a check-in with no log type.

**Attendance is created from the day's logs, not by the tap.** `Employee Checkin` and
`Attendance` are separate documents, and `mark_attendance_and_link_log` is what turns a
pair of logs into a day. So the first tap of the day creates a checkin and *attempts* the
attendance; HRMS creates nothing until a second log exists, and that is correct - a
half-recorded day is not a day.

That function also does not read the times off the logs it is handed. `in_time`,
`out_time`, `working_hours` and `shift` all default to None, and it swallows any
`ValidationError` and returns None. Called with only the logs and a status - which reads
like a complete call - it writes an attendance with nothing on it and reports nothing
back. Both halves of that are handled here: the times are supplied, and a None return
after a check-out is raised as an error rather than shown as a successful punch.

**A tap outside the punch window is recorded, not refused.** HRMS files it offshift,
which is what that state is for. Refusing was the first attempt and it was wrong: an
employee arriving late would have been left with no record at all, so in a payroll
dispute HR could not see that they had turned up. The window is still *told* to the
employee before they tap (`check_in_open`), because a warning in advance is worth more
than a flat refusal afterwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, getdate, now_datetime, today

from sharm_dreams_group.portal.portal import resolve_session
from sharm_dreams_group.portal.geo import check_punch, status_label

__all__ = [
	"my_attendance",
	"my_shifts",
	"punch",
]

# How many days back the attendance list reaches. A month on a wall tablet: enough to
# answer "how many days have I come in this month" without paging through a year, and
# bounded so the screen cannot grow without limit.
ATTENDANCE_DAYS = 31

# The Shift Type a shift assignment names, for the "today" summary. Read rather than
# derived from the employee's gender: the assignment is the record of who works when, and
# deriving it from `gender` would be a second, silently divergent copy of the policy.
PUNCH_DEVICE = "employee-portal"

# Two taps closer together than this are one tap. HRMS refuses a second log at the same
# timestamp, and on a wall tablet a double-tap is a single gesture - so it is collapsed
# here rather than reaching the employee as "This employee already has a log with the
# same timestamp", which is both true and useless at a door. Five seconds is long enough
# to absorb a double-tap and short enough that nobody thinks their IN went missing.
PUNCH_DEBOUNCE_SECONDS = 5


def _to_float(value):
	"""A coordinate the browser sent, or None.

	The tablet posts JSON numbers, but a form-encoded fallback arrives as strings and a
	`null` arrives as None, so this is the one place that decides what counts as a
	usable coordinate. Anything unparseable is None rather than a guess: a coordinate
	that is not a number is not a location.
	"""
	if value is None or value == "":
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


@frappe.whitelist()
def my_shifts(token: str) -> dict:
	"""This employee's shift assignments, and which one covers today.

	Only submitted assignments, matching what HRMS will actually resolve a punch
	against - a draft one is on file but HRMS ignores it, so listing it would offer a
	shift that cannot be worked.
	"""
	emp = resolve_session(token)
	today_date = getdate(today())

	rows = frappe.get_all(
		"Shift Assignment",
		filters={"employee": emp.name, "docstatus": 1, "status": "Active"},
		fields=["name", "shift_type", "start_date", "end_date"],
		order_by="start_date asc",
		limit_page_length=0,
	)

	out = []
	for row in rows:
		shift = _shift_details(row.shift_type)
		covers_today = (
			getdate(row.start_date) <= today_date
			and (not row.end_date or today_date <= getdate(row.end_date))
		)
		out.append(
			{
				"shift_type": row.shift_type,
				"start_time": shift.get("start_time"),
				"end_time": shift.get("end_time"),
				# The window a punch is accepted in: the shift plus the grace periods,
				# which is what HRMS records as `shift_actual_start`/`_end`.
				"check_in_from": shift.get("actual_start"),
				"check_in_until": shift.get("actual_end"),
				"start_date": row.start_date,
				"end_date": row.end_date,
				"covers_today": covers_today,
				# Whether a tap right now would fall inside the window. Said up front so
				# the tablet can warn before the employee presses, instead of letting them
				# find out from the result - the tap is recorded either way, but it is
				# filed offshift and HR will see it as an exception.
				"check_in_open": _within_window(shift) if covers_today else None,
			}
		)

	return {
		"employee": emp.name,
		"shifts": out,
		# None rather than False: "no shift today" and "we could not tell" are different
		# answers, and a wall tablet must not say "you are not working today" on the
		# strength of a query that failed.
		"today_shift": next((s for s in out if s["covers_today"]), None),
	}


def _shift_details(shift_type: str) -> dict:
	"""A shift's times, with its grace periods applied.

	`begin_check_in_before_shift_start_time` and `allow_check_out_after_shift_end_time`
	are read from the Shift Type rather than assumed, because they are what HRMS uses to
	decide `shift_actual_start`/`shift_actual_end` - and a punch window shown to an
	employee that disagrees with the one enforced on insert is a punch refused for a
	reason nobody can see.
	"""
	if not shift_type:
		return {}
	shift = frappe.db.get_value(
		"Shift Type",
		shift_type,
		["start_time", "end_time", "begin_check_in_before_shift_start_time",
		 "allow_check_out_after_shift_end_time"],
		as_dict=True,
	)
	if not shift:
		return {}
	start = _as_hms(shift.start_time)
	end = _as_hms(shift.end_time)
	if start is None or end is None:
		return {}
	before = int(flt(shift.begin_check_in_before_shift_start_time))
	after = int(flt(shift.allow_check_out_after_shift_end_time))
	return {
		"start_time": start.strftime("%H:%M"),
		"end_time": end.strftime("%H:%M"),
		# `timedelta`, not seconds: `_as_hms` returns a datetime, so subtracting a number
		# of seconds off it is not arithmetic on a clock and raises. This is the window
		# HRMS enforces as `shift_actual_start` / `shift_actual_end`.
		"actual_start": (start - timedelta(minutes=before)).strftime("%H:%M"),
		"actual_end": (end + timedelta(minutes=after)).strftime("%H:%M"),
	}


def _within_window(shift: dict) -> bool:
	"""Is now inside this shift's punch window?

	Uses the same grace periods HRMS applies on insert, read from the Shift Type rather
	than reimplemented. Returns False rather than raising when the window cannot be
	worked out: an unknown window is not an open one, and a wall tablet that thought it
	was open when it is not would tell an employee their tap was ordinary.
	"""
	if not shift.get("actual_start") or not shift.get("actual_end"):
		return False
	now = now_datetime()
	stamp = now.strftime("%H:%M")
	return shift["actual_start"] <= stamp <= shift["actual_end"]


def _as_hms(value) -> datetime | None:
	"""A `Time` field as a `datetime` on an arbitrary day, or None.

	Frappe hands a `Time` back as a `timedelta` - a *duration since midnight*, with the
	hours in `.seconds`. Arithmetic on that is arithmetic on a duration, so
	`start - timedelta(minutes=60)` would subtract 60 from a number of seconds rather than
	from a clock, and raise. Converting to a datetime first is what makes the grace-period
	window below a clock window.

	The day is arbitrary and never used: only the time of day is read back out, because
	`get_actual_start_end_datetime_of_shift` applies the same grace periods itself and a
	comparison against the real date would be against the wrong one.
	"""
	if value is None:
		return None
	if hasattr(value, "seconds") and not hasattr(value, "hour"):
		seconds = int(value.total_seconds())
		return datetime(2000, 1, 1) + timedelta(seconds=seconds)
	if hasattr(value, "hour"):
		return datetime(2000, 1, 1, value.hour, value.minute, value.second)
	# A string, from a `get_all` rather than `get_value`.
	try:
		parsed = get_datetime(f"2000-01-01 {value}")
	except Exception:
		return None
	return datetime(2000, 1, 1, parsed.hour, parsed.minute, parsed.second)


def _today_logs(employee: str) -> list:
	"""This employee's checkins for today, oldest first.

	Shared by `punch` and `my_attendance` because the tablet has to be told what the
	next tap *will* do, and that is decided by how many logs exist so far - not by
	whether an `Attendance` row has appeared, since the first tap of a day creates none.
	"""
	return frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", (f"{today()} 00:00:00", f"{today()} 23:59:59")],
		},
		fields=["name", "time", "log_type", "shift", "offshift", "attendance",
		        "sdg_self_service_geo_status"],
		order_by="time asc",
		limit_page_length=0,
	)


@frappe.whitelist()
def my_attendance(token: str) -> dict:
	"""This employee's attendance for the last `ATTENDANCE_DAYS` days, newest first."""
	emp = resolve_session(token)
	from_date = add_to_date(today(), days=-ATTENDANCE_DAYS + 1)

	rows = frappe.get_all(
		"Attendance",
		filters={"employee": emp.name, "attendance_date": ["between", (from_date, today())]},
		fields=[
			"name",
			"attendance_date",
			"status",
			"shift",
			"in_time",
			"out_time",
			"working_hours",
			"late_entry",
			"early_exit",
		],
		order_by="attendance_date desc",
		limit_page_length=ATTENDANCE_DAYS,
	)

	days = [
		{
			# `attendance_date` comes back from `get_all` as a `datetime.date` while
	# `from_date`/`to_date` below are strings. Left alone that means the caller has to
	# know which of the three dates it is holding, and every consumer ends up
	# stringifying some of them. All dates out of this function are ISO strings.
	# `getdate` is not enough here: it returns the same object it was given.
	"date": row.attendance_date.isoformat(),
			"status": row.status,
			"shift": row.shift,
			"in_time": str(row.in_time) if row.in_time else None,
			"out_time": str(row.out_time) if row.out_time else None,
			"working_hours": flt(row.working_hours),
			"late_entry": bool(row.late_entry),
			"early_exit": bool(row.early_exit),
		}
		for row in rows
	]

	present = sum(1 for day in days if day["status"] == "Present")
	hours = sum(day["working_hours"] for day in days)

	# Where today stands, so the tablet can label the button before it is pressed rather
	# than after. Counted from the checkins, not from `days`: a half-recorded day has no
	# `Attendance` row yet, and reading the button's state off the absence of a row would
	# invite a third tap.
	logged = _today_logs(emp.name)
	today_row = next((d for d in days if d["date"] == today()), None)
	return {
		"employee": emp.name,
		"from_date": from_date,
		"to_date": today(),
		"days": days,
		"present_days": present,
		"total_hours": round(hours, 2),
		"today": {
			"punches": [
				{
					"time": str(row["time"]) if row["time"] else None,
					"shift": row["shift"],
					"offshift": bool(row["offshift"]),
					"geo_status": row.get("sdg_self_service_geo_status") or "",
				}
				for row in logged
			],
			"logged": len(logged),
			# What a tap now would do. "none" once the day cannot take another log -
			# `punch` refuses a third, and a button that invites a refusal is a dead
			# click.
			"next": "in" if not logged else ("out" if len(logged) == 1 else "none"),
			"complete": bool(today_row and today_row["out_time"]),
		},
	}


@frappe.whitelist()
def punch(token: str, latitude: float | None = None, longitude: float | None = None) -> dict:
	"""Record one tap: an IN if this is the first of the day, otherwise an OUT.

	Returns what the day now looks like, so the tablet can say it plainly rather than
	making the employee come back to check. A refusal is a refusal with a sentence, not
	a silent no-op - this is the one action on the portal that a person stands in front
	of at the start of a shift.

	The browser's coordinates are checked against the employee's company geofence
	before anything is written. When enforcement is on and the point falls outside,
	this refuses with the fence's own sentence; when it is off, the punch is recorded
	and the verdict is stored on the checkin so the location history is still complete.
	"""
	emp = resolve_session(token)
	today_date = today()

	# No shift *scheduled* today: refused. This is different from being outside the
	# punch window, which is recorded (see below). Someone with no assignment on the
	# roster is not at work at all, and there is nothing to time.
	shift_assignment = frappe.db.get_value(
		"Shift Assignment",
		{"employee": emp.name, "docstatus": 1, "status": "Active",
		 "start_date": ["<=", today_date]},
		["shift_type", "end_date"],
		as_dict=True,
	)
	if not shift_assignment or (
		shift_assignment.end_date and getdate(shift_assignment.end_date) < getdate(today_date)
	):
		frappe.throw(
			_("You have no shift assigned today, so nothing can be recorded. Please contact HR.")
		)

	logs_today = _today_logs(emp.name)

	# Already complete: refused with the reason, because a third tap would otherwise
	# create a third log that HRMS cannot pair.
	if len(logs_today) >= 2:
		frappe.throw(_("You have already recorded today. Please contact HR if this is wrong."))

	# Two taps within the same second are one tap. HRMS's `validate_duplicate_log`
	# rejects a second log at the same timestamp, and on a wall tablet a double-tap is a
	# single gesture - so it is collapsed here rather than reaching the employee as
	# "This employee already has a log with the same timestamp", which is both true and
	# useless at a door. The last log of the day is returned unchanged: the day is already
	# recorded, and the caller is told so by the same `complete` flag.
	moment = frappe.utils.now_datetime()
	recent = frappe.db.get_value(
		"Employee Checkin",
		{"employee": emp.name, "log_type": logs_today[0]["log_type"] if logs_today else ""},
		["name", "time", "shift", "offshift"],
		order_by="time desc",
		as_dict=True,
	)
	if recent and recent.time and abs(
		(moment - recent.time).total_seconds()
	) < PUNCH_DEBOUNCE_SECONDS:
		return {
			"recorded": recent.name,
			"kind": "IN" if not logs_today else "OUT",
			"time": recent.time,
			"shift": recent.shift,
			"offshift": bool(recent.offshift),
			"attendance": None,
			# Nothing new, and the day is not complete because this tap did not change it.
			"complete": False,
			"next": "out" if not logs_today else "out",
			"debounced": True,
		}

	# Where the tap happened. `check_punch` refuses only when enforcement is on and
	# the point is unusable or outside; with enforcement off it always passes and
	# carries the verdict in `geo_status`, so the checkin records where the punch came
	# from whether or not it was allowed.
	latitude = _to_float(latitude)
	longitude = _to_float(longitude)
	geo_ok, geo_status, geo_message = check_punch(emp.name, latitude, longitude)
	if not geo_ok:
		frappe.throw(geo_message)

	from hrms.hr.doctype.employee_checkin.employee_checkin import (
		add_log_based_on_employee_field,
		calculate_time_difference,
		mark_attendance_and_link_log,
	)

	previous = frappe.session.user
	# The employee's own user if they have one, so `docstatus`/`owner` on the checkin
	# and the attendance belong to the punch rather than to an Administrator. Falling
	# back to Administrator is deliberate: 179 of the 180 active employees have no
	# Frappe User, and refusing their punch over that would leave the portal unusable
	# for all of them.
	frappe.set_user(emp.user_id or "Administrator")
	try:
		checkin = add_log_based_on_employee_field(
			employee_field_value=emp.name,
			timestamp=moment,
			employee_fieldname="name",
			device_id=PUNCH_DEVICE,
			latitude=latitude,
			longitude=longitude,
		)
		# HRMS stores the coordinates but knows nothing about this app's zones, so the
		# verdict is written beside them. Done after insert because
		# `add_log_based_on_employee_field` takes no such argument - it is HRMS's own
		# helper, and the two fields are custom ones this app owns.
		frappe.db.set_value(
			"Employee Checkin",
			checkin.name,
			{
				"sdg_self_service_geo_status": status_label(geo_status),
				"sdg_self_service_geo_message": geo_message,
			},
			update_modified=False,
		)
		# No `log_type`: both shift types here alternate IN/OUT themselves, and sending
		# one would override what HRMS decided.
		#
		# A tap outside the punch window is *recorded*, as HRMS's own offshift state, not
		# refused. The employee arriving two hours late still needs a record: refusing
		# would leave them with nothing at all, so in a payroll dispute HR would not even
		# see that they turned up. `offshift` carries that back to the tablet so the
		# screen can say "recorded outside your shift" rather than a flat refusal. This
		# was a refusal in an earlier draft and was wrong.

		# A single log is not a day. HRMS creates the Attendance only when there is an
		# OUT to pair the IN with, and `mark_attendance_and_link_log` returns None for a
		# one-log day rather than guessing a finish time.
		attendance = None
		if len(logs_today) + 1 >= 2:
			all_logs = [
				frappe.get_doc("Employee Checkin", name)
				for name in [row["name"] for row in logs_today] + [checkin.name]
			]
			# `mark_attendance_and_link_log` does NOT read the times off the logs it is
			# given - every one of these defaults to None. Called with only `logs` and a
			# status, it writes an Attendance with `in_time`, `out_time`,
			# `working_hours` and `shift` all empty, and the employee's day shows up on
			# their own screen with no times on it. That is why it swallows the
			# `ValidationError` too and returns None on some inputs: a caller that never
			# noticed would see a silent no-op.
			stamps = sorted(x.time for x in all_logs if x.time)
			attendance = mark_attendance_and_link_log(
				logs=all_logs,
				attendance_status="Present",
				attendance_date=today_date,
				working_hours=(
					calculate_time_difference(stamps[0], stamps[-1])
					if len(stamps) >= 2
					else None
				),
				shift=next((x.shift for x in all_logs if x.shift), None),
				in_time=stamps[0] if stamps else None,
				out_time=stamps[-1] if stamps else None,
			)
			if attendance is None:
				# It returns None both when it swallowed an error and when it decided
				# the day was already handled. Neither is worth reporting as a completed
				# punch, and neither is worth hiding behind "recorded" either.
				frappe.throw(
					_("Your check-out was recorded, but the day's hours could not be "
					  "saved. Please contact HR.")
				)
	finally:
		frappe.set_user(previous)

	# Committed here rather than at the end of the request: a tablet may lose the
	# network the instant it gets its answer, and an employee who saw "recorded" must
	# not find it missing.
	frappe.db.commit()

	return {
		"recorded": checkin.name,
		"kind": "IN" if not logs_today else "OUT",
		"time": checkin.time,
		"shift": checkin.shift,
		# True when HRMS filed the tap outside the punch window. Recorded, flagged, and
		# for HR to adjudicate - the tablet must not pretend the timing was normal.
		"offshift": bool(checkin.offshift),
		# None until the OUT lands, which is the honest answer rather than a half day
		# invented to fill the space.
		"attendance": attendance.name if attendance else None,
		"complete": bool(attendance),
		"next": "out" if not logs_today else "none",
		# The zone verdict, so the caller can say "location verified" rather than
		# leaving the employee to assume a tap from anywhere was accepted.
		"geo_status": geo_status,
	}
