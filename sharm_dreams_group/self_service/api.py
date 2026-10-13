"""Public employee self-service API (token-session based).

Employees have no Frappe user, so the self-service page identifies them with their Egyptian
National ID (`Employee.sdg_national_id`) plus their Employee Number, and every
subsequent call carries the token minted at sign-in.  `resolve_session` turns that
token back into an Employee, which is the only identity any of this module uses.

`employee_number` is stock, mandatory and unique across the seeded staff, so it is
the employee code the self-service page asks for - no custom field, and the same number HR
already has on the letter.  It is a plain Data field rather than a unique index,
so a hand-created duplicate would make sign-in ambiguous; `self_service.test_self_service`
asserts the seeded data has none.

Two Frappe-isms make the bare paths here wrong, and both are load-bearing:

* `request_leave` runs the insert as the employee's own user.  HRMS's
  `validate_balance_leaves` rejects guests outright, so a self-service page request would fail
  with a message the employee cannot act on.
* `punch` writes `Employee Checkin` directly, and `self_service.geo` checks the
  coordinates against the employee's work location.  HRMS's own check only fires
  when `allow_geolocation_tracking` is on, which this app leaves off so every other
  punch channel keeps working; this is what stands between a punch from the car park
  and a punch at the office.
"""

from datetime import datetime

import frappe
from frappe.utils import getdate, now_datetime, nowdate

from sharm_dreams_group.self_service.geo import check_punch, status_label
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	create_session,
	resolve_session,
)

ALLOWED_LOG_TYPES = ("IN", "OUT")

# The self-service deliberately offers only the languages it has strings for, rather than
# every installed language - offering one that falls back to English is worse than
# not offering it. Mirrored in `www/self-service.py`'s SELF_SERVICE_LANGUAGES.
SELF_SERVICE_LANGS = (
	{"code": "en", "name": "English"},
	{"code": "fr", "name": "Fran\u00e7ais"},
	{"code": "ar", "name": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629"},
)


def _to_float(value):
	"""Coerce a browser-sent coordinate to float, or None when unusable."""
	if value in (None, "", False):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _available_langs():
	return [dict(x) for x in SELF_SERVICE_LANGS]


@frappe.whitelist(allow_guest=True)
def sign_in(national_id, employee_code):
	"""Authenticate against National ID + Employee Number and mint a self-service page token.

	`employee_code` is the wire name the self-service page client posts; it carries the
	Employee Number.  This plain path is only for a device without a platform
	authenticator, or an employee's first time on it.  Once a biometric is enrolled
	the account is locked to it, and `webauthn_login` is the way back in.
	"""
	national_id = (national_id or "").strip()
	employee_code = (employee_code or "").strip()
	if not national_id or not employee_code:
		frappe.throw("National ID and Employee Number are required.")

	employee = frappe.db.get_value(
		"Employee",
		{"sdg_national_id": national_id, "employee_number": employee_code},
		"name",
	)
	if not employee:
		frappe.throw("No employee matches the National ID + Employee Number. Please ask HR.")

	enrolled = frappe.db.count(
		"Employee Self Service Biometric Credential", {"employee": employee, "active": 1}
	)
	if enrolled:
		frappe.throw(
			"This account is protected by biometric sign-in. "
			"Please sign in with your fingerprint or face ID.",
			exc=frappe.PermissionError,
		)

	emp = frappe.get_doc("Employee", employee)
	session = create_session(employee)
	return {
		"token": session.access_token,
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"company": emp.company,
	}


@frappe.whitelist()
def sign_out(token):
	"""Expire the session rather than delete it.

	A deleted row would leave nothing to audit, and `sign_out` arrives from a
	browser that may be about to send one more request, so expiring is both safer
	and idempotent.
	"""
	frappe.db.set_value("Employee Self Service Session", {"access_token": token}, "expiry", now_datetime())
	frappe.db.commit()
	return {"ok": True}


# How long the tablet may sit untouched before the session is considered stale.
# The client re-arms this on every interaction.
SELF_SERVICE_INACTIVITY_SECONDS = 30


@frappe.whitelist()
def my_profile(token):
	"""Attendance snapshot for the self-service page dashboard and greet screens.

	The greeting screen has to know whether the punch it is about to record is a
	check-in or a check-out, and the dashboard's three stat cards need today's
	worked hours plus the previous session's.
	"""
	emp = resolve_session(token)

	today = frappe.utils.today()
	logs = frappe.db.sql(
		"""select log_type, time from `tabEmployee Checkin`
		   where employee=%s and date(time)=%s
		   order by time asc, name asc""",
		(emp.name, today),
		as_dict=True,
	)

	last_in = None
	last_out = None
	for row in logs:
		if row.log_type == "IN":
			last_in = row.time
		elif row.log_type == "OUT":
			last_out = row.time

	last_in_dt = _as_datetime(last_in)
	last_out_dt = _as_datetime(last_out)
	# A trailing IN with no matching OUT means still clocked in. Compared as
	# datetimes, not as whatever the driver handed back - see `_as_datetime`.
	attendance_state = (
		"checked_in"
		if (last_in_dt and not last_out_dt) or (last_in_dt and last_in_dt > last_out_dt)
		else "checked_out"
	)

	# Today's hours: each completed IN->OUT pair, plus the live session so far.
	total_seconds = 0
	open_in = None
	for row in logs:
		if row.log_type == "IN":
			open_in = _as_datetime(row.time)
		elif row.log_type == "OUT" and open_in:
			total_seconds += (_as_datetime(row.time) - open_in).total_seconds()
			open_in = None
	if open_in:
		# now_datetime(), not now(): `now()` returns a *string*, and subtracting it
		# from a datetime raises for every employee who is still clocked in.
		total_seconds += (now_datetime() - open_in).total_seconds()
	hours_today = round(max(0.0, total_seconds) / 3600.0, 2)

	# The last completed session, for the "Session" stat card. Scanned backwards
	# and stops at the first completed pair, which is the most recent one.
	last_session = None
	prior = frappe.db.sql(
		"""select log_type, time from `tabEmployee Checkin`
		   where employee=%s and date(time) < %s
		   order by time desc, name desc limit 60""",
		(emp.name, today),
		as_dict=True,
	)
	seen_in = None
	for row in reversed(prior):
		if row.log_type == "IN":
			seen_in = _as_datetime(row.time)
		elif row.log_type == "OUT" and seen_in:
			last_session = round((_as_datetime(row.time) - seen_in).total_seconds() / 3600.0, 2)
			break

	return {
		"employee": emp.name,
		"employee_name": emp.employee_name,
		"employee_code": emp.employee_number,
		"company": emp.company,
		"company_name": emp.company,
		"avatar": _avatar_data_uri(emp.name),
		"date_of_joining": emp.date_of_joining,
		"attendance_state": attendance_state,
		"last_check_in": last_in,
		"last_check_out": last_out,
		"hours_today": hours_today,
		"last_attendance_worked_hours": last_session or 0.0,
		"self_service_delay": SELF_SERVICE_INACTIVITY_SECONDS * 1000,
		"available_langs": _available_langs(),
		"last_checkin": logs[-1] if logs else None,
	}


def _as_datetime(value):
	"""Coerce a stored timestamp to a `datetime` for arithmetic.

	`frappe.db.sql` hands back the raw driver value for a Datetime column. That is
	a `datetime` here but a `str` on some other backends, and every duration
	calculation above subtracts two of them - so they all go through here rather
	than each checking its own types.
	"""
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	return frappe.utils.get_datetime(str(value))


def _avatar_data_uri(employee):
	"""Inline the employee photo as a data URI.

	The self-service holds an opaque token, not a Frappe login, so a `/private/files` URL
	would 403.
	"""
	try:
		image = frappe.db.get_value("Employee", employee, "image")
		if not image:
			return None
		row = frappe.db.get_value(
			"File", {"file_url": image}, ["file_name", "is_private"], as_dict=True
		)
		if not row:
			return None
		import base64
		import io
		import mimetypes

		data = frappe.get_doc("File", {"file_url": image}).get_content()
		if not data:
			return None

		# A raw PNG attach is ~65KB of base64 on every dashboard load, so downscale
		# to a 256px thumbnail - the size HRMS itself serves in the desk.
		try:
			from PIL import Image

			buf = io.BytesIO()
			thumb = Image.open(io.BytesIO(data)).convert("RGB")
			thumb.thumbnail((256, 256))
			buf = io.BytesIO()
			thumb.save(buf, format="JPEG", quality=82, optimize=True)
			return "data:image/jpeg;base64,{}".format(
				base64.b64encode(buf.getvalue()).decode()
			)
		except Exception:
			mime = mimetypes.guess_type(row.file_name or "")[0] or "image/png"
			return "data:{};base64,{}".format(mime, base64.b64encode(data).decode())
	except Exception:
		frappe.log_error(title="Self-service avatar read failed")
		return None


@frappe.whitelist()
def punch(token, log_type, latitude=None, longitude=None):
	"""Record an Employee Checkin (IN / OUT) from the self-service page.

	When geofencing is enforced the browser coordinates must fall inside the
	radius of the `Shift Location` on the employee's current shift assignment.
	Coordinates are always stored on the checkin for audit, whatever the outcome.
	"""
	log_type = (log_type or "").upper()
	if log_type not in ALLOWED_LOG_TYPES:
		frappe.throw("log_type must be IN or OUT.")

	emp = resolve_session(token)

	latitude = _to_float(latitude)
	longitude = _to_float(longitude)
	geo_ok, geo_status, geo_message = check_punch(emp.name, latitude, longitude)
	if not geo_ok:
		return {"ok": False, "error": geo_message, "geo": {"status": geo_status}}

	checkin = frappe.get_doc(
		{
			"doctype": "Employee Checkin",
			"employee": emp.name,
			"log_type": log_type,
			"time": now_datetime(),
			"device_id": "self-service",
			"latitude": latitude,
			"longitude": longitude,
			"sdg_self_service_geo_status": status_label(geo_status),
			"sdg_self_service_geo_message": geo_message,
		}
	)
	checkin.insert(ignore_permissions=True)

	last = frappe.db.sql(
		"""select log_type, time from `tabEmployee Checkin`
		   where employee=%s order by time desc, name desc limit 1""",
		emp.name,
		as_dict=True,
	)
	return {
		"ok": True,
		"checkin": checkin.name,
		# the greet screen shows the punch time, so it is returned flat as well
		"time": checkin.time,
		"log_type": log_type,
		"last": last[0] if last else None,
		"geo": {"status": geo_status},
	}


@frappe.whitelist()
def payslips(token):
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
def payslip(token, name):
	"""One payslip, for this employee only.

	Employer contributions are included: HRMS writes them to the separate
	`employer_contributions` table on the slip, and they are what an employee
	checking their insurance or pension deduction actually wants to see. The key
	is `employer` because that is what `renderPayslipDetail` in self-service.js reads.
	"""
	emp = resolve_session(token)
	slip = frappe.get_doc("Salary Slip", name)
	if slip.employee != emp.name:
		frappe.throw("Not allowed to view this payslip.")

	earnings = [{"component": r.salary_component, "amount": r.amount} for r in slip.earnings or []]
	deductions = [{"component": r.salary_component, "amount": r.amount} for r in slip.deductions or []]
	employer = [
		{"component": r.salary_component, "amount": r.amount}
		for r in slip.employer_contributions or []
	]

	return {
		"name": slip.name,
		"status": slip.status,
		"start_date": slip.start_date,
		"end_date": slip.end_date,
		"currency": slip.currency,
		"gross_pay": slip.gross_pay,
		"total_deduction": slip.total_deduction,
		"net_pay": slip.net_pay,
		"earnings": earnings,
		"deductions": deductions,
		"employer": employer,
	}


@frappe.whitelist()
def leave_types(token):
	"""The leave types an employee can actually ask for on the self-service page.

	Only those with a Sharm code: the self-service page keys the Time Off screen on that code,
	so an uncoded type would appear with no category and no place in the balance.
	"""
	resolve_session(token)
	rows = frappe.get_all(
		"Leave Type",
		filters={"sdg_code": ["!=", ""]},
		fields=["name", "sdg_code", "sdg_vacation_day_multiplier"],
		order_by="name asc",
		limit_page_length=30,
	)
	return {"leave_types": rows}


@frappe.whitelist()
def request_leave(token, leave_type, from_date, to_date, reason=None):
	emp = resolve_session(token)
	if not leave_type:
		frappe.throw("Select a leave type.")
	if not (from_date and to_date):
		frappe.throw("From and To dates are required.")
	if getdate(from_date) > getdate(to_date):
		frappe.throw("From date cannot be after To date.")

	# HRMS rejects the insert with "Application period cannot be outside leave
	# allocation period", which is not actionable for a self-service page employee.
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
		frappe.throw(
			"You do not have a leave allocation covering the selected dates. "
			"Please contact HR."
		)

	approver_user = _resolve_leave_approver(emp)
	if not approver_user:
		frappe.throw(
			"No leave approver could be resolved for you. Please contact HR.",
			exc=frappe.PermissionError,
		)

	leave_app = frappe.get_doc(
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
			"leave_approver": approver_user,
		}
	)
	# HRMS's validate_balance_leaves() -> validate_leave_access() rejects guests,
	# so run the insert as the employee's own user (or Administrator when the
	# employee has no user account). The self-service token already authenticated them.
	as_employee_user(emp, lambda: leave_app.insert(ignore_permissions=True))

	return {
		"name": leave_app.name,
		"total_leave_days": leave_app.total_leave_days,
		"status": leave_app.status,
	}


def _resolve_leave_approver(emp):
	"""The user who will have to approve this employee's leave, or None.

	Reporting manager, then the department's default approver, then the self-service page-wide
	default in Employee Self Service Settings.  HRMS makes `Leave Application.leave_approver`
	mandatory, so without that last step a self-service page employee with no manager and no
	department approver simply could not ask for leave.

	There is deliberately no "employee's own approver" step ahead of the manager.
	HRMS keeps that on the Employee as an `Employee Leave Approver` child table,
	which this version of HRMS does not have - so reading it would raise rather
	fall through, and the self-service page would fail for every employee.
	"""
	reports_to = frappe.db.get_value("Employee", emp.name, "reports_to")
	approver_user = (
		frappe.db.get_value("Employee", reports_to, "user_id") if reports_to else None
	)

	if not approver_user:
		department = frappe.db.get_value("Employee", emp.name, "department")
		if department:
			approver_user = frappe.db.get_value(
				"Department Approver",
				{"parent": department, "parentfield": "leave_approvers", "idx": 1},
				"approver",
			)

	if not approver_user:
		approver_user = frappe.db.get_single_value("Employee Self Service Settings", "default_leave_approver")

	# Normalised to None, not left as whatever the last lookup produced. A Single's
	# unset Link field reads back as "" and a manager with no user account reads
	# back as "", so this function would otherwise answer "" for "no approver" -
	# which is false for a caller testing `is None`.
	return approver_user or None


def as_employee_user(emp, work):
	"""Run `work` as the employee's own user, then restore the previous one.

	`frappe.set_user` is process-global, so the restore is in a `finally` - an
	exception out of `work` would otherwise leave the rest of the request running
	as somebody else. Administrator is the fallback: a self-service page employee with no user
	account still has to be able to file a request.

	`work` is a callable rather than a document because the caller has to keep
	using the document *after* the user has been restored - `request_leave` reads
	`total_leave_days` off it, which HRMS computes during the insert.
	"""
	acting_user = frappe.db.get_value("Employee", emp.name, "user_id") or "Administrator"
	previous_user = frappe.session.user
	frappe.set_user(acting_user)
	try:
		return work()
	finally:
		frappe.set_user(previous_user)


@frappe.whitelist()
def my_leaves(token):
	emp = resolve_session(token)
	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": emp.name},
		fields=["name", "leave_type", "from_date", "to_date", "total_leave_days", "status", "posting_date", "docstatus"],
		order_by="posting_date desc",
		limit_page_length=20,
	)
	today = nowdate()
	for row in rows:
		row["can_cancel"] = bool(
			row.status == "Open" and row.docstatus == 0 and row.from_date
		) and str(row.from_date) > today
	return {"leaves": rows}


@frappe.whitelist()
def approvals(token):
	"""Leave applications waiting on this employee's approval, as a manager."""
	emp = resolve_session(token)
	approver_user = frappe.db.get_value("Employee", emp.name, "user_id")
	rows = []
	if approver_user:
		rows = frappe.get_all(
			"Leave Application",
			filters={"leave_approver": approver_user, "status": "Open", "docstatus": 0},
			fields=["name", "employee", "employee_name", "leave_type", "from_date", "to_date", "total_leave_days"],
			order_by="posting_date asc",
			limit_page_length=20,
		)
	return {"approvals": rows}