"""Vacation balances for the self-service page's Time Off screen.

Everything comes from `SharmDreamVacation Tracker.xlsx`, extracted by
`tools/build_vacation_spec.py` and read through `vacation_spec`. The workbook
models **one pooled entitlement**, not one bucket per leave type, so the screen
shows one balance rather than the six buckets this module used to show - those
six fields (`sdg_vacation_*_days`) are all zero on every employee, and
"Remote Areas" does not exist in the workbook at all.

Usage has two sources and they are not interchangeable:

  * the workbook's day grid, a hand-maintained snapshot as of `source.as_of`. It
    carries the leave already taken - H0236 has 52 vacation days in it.
  * Leave Applications approved in Frappe *after* that snapshot.

Taking entitlement from the workbook but usage from Frappe alone would report
every employee as owing their full entitlement unspent, which is simply wrong. So
the grid is the baseline and Frappe adds to it, counting only requests dated on or
after `as_of`: a Frappe request older than the snapshot is almost certainly
already in the grid, and counting it again would deduct it twice.

The balance is the workbook's own arithmetic, so the page agrees with the file HR
maintains:

    balance = (LL - PDO) + (carried + entitlement - half_days - CL - V)
              + (LLPH - PDOPH)

Leave in lieu is a net adjustment rather than a bucket, because working a rest day
*earns* a day back. It is reported in `adjustments` and is not a separate
allowance.
"""

from datetime import date

import frappe
from frappe.utils import flt, getdate

from sharm_dreams_group.self_service import vacation_spec

# Approved and still-open: a request in either state is committed time off, and an
# approved one can still be cancelled back to the employee.
VACATION_USED_STATUSES = ("Approved", "Open")

# Workbook day-grid code -> the `Leave Type.sdg_code` that draws on the allowance.
#
# The two vocabularies barely overlap, so only unambiguous mappings appear here.
# `V` is the workbook's main entitlement code and maps to Annual Leave; `CL` and
# `HD` (half day) are the only other codes that reduce the balance.
#
# `DO`, `PDO`, `UL` and `PH` are deliberately absent. The app has three unrelated
# types that could each claim `DO`, `PDO` appears in the workbook as part of a net
# adjustment rather than as a leave in its own right, and `UL` has no Leave Type at
# all. Guessing would move real balances, so those days are reported in
# `other_days` without affecting the entitlement.
ENTITLEMENT_CODE_MAP = {
	"V": "AL",
	"CL": "CL",
	"0.5": "HD",
}

# Workbook day-grid code -> Leave Application leave type, for the leave-in-lieu
# adjustment. Working a rest day or public holiday *earns* a day back, so these net
# against the days worked rather than being spent.
#
# `LL` maps onto the app's `WD` (Worked Rest Day) rather than its `LIW`: the
# workbook's leave-in-lieu days are the ones earned by working a rest day, and `WD`
# is also the code `setup.SELF_SERVICE_DOUBLE_DAY_LEAVE_CODES` charges two days
# for. `LIW` on the other hand is what an employee *takes*, which is the opposite
# movement, so mapping it here would credit days for leave being spent.
LIEU_CODE_MAP = {
	"LL": "WD",
	"PDO": "PDODO",
}

# Codes the workbook nets as leave in lieu rather than spending. They are handled,
# but not deducted, so they are named separately from `NON_DEDUCTED_CODES` - which
# is "recorded but irrelevant to the balance", whereas these do change it.
LIEU_NET_CODES = ("LLPH",)

# Days the workbook records that do not change the entitlement at all. Listed so
# they are reported rather than dropped: sick leave, weekly rest days, public
# holidays, unpaid leave, and a public holiday worked. `LL` and `PDO` are absent
# because they net rather than sit idle - see `LIEU_CODE_MAP` - so listing them
# here as well would show the same days twice.
NON_DEDUCTED_CODES = ("SL", "DO", "PH", "UL", "PDOPH")
UNCONFIRMED_CODES = ("DE-L", "DI.L", "D.L")

# Codes that reduce the pooled allowance, and whether a half day counts as one.
DEDUCTED_CODES = ("V", "CL", "0.5")
HALF_DAY = "0.5"

UNKNOWN_LABEL = "Other"


def _day_counts(person):
	"""The workbook's per-code day counts for one person, as floats."""
	days = (person or {}).get("days") or {}
	return {code: flt(count) for code, count in days.items()}


def _weighted_leave_days(employee, code_map, since):
	"""Days approved in Frappe, keyed by *workbook* code.

	Weighted by `Leave Type.sdg_vacation_day_multiplier`: working a weekly rest day
	burns two days of allowance, so summing raw `total_leave_days` would halve it.

	`since` is the workbook snapshot date, and is what keeps the grid and Frappe
	from counting the same day twice.
	"""
	if not employee or not code_map:
		return {}

	leave_types = frappe.get_all(
		"Leave Type",
		fields=["name", "sdg_code", "sdg_vacation_day_multiplier"],
		limit_page_length=0,
	)
	type_by_app_code = {}
	for row in leave_types:
		if row.sdg_code:
			type_by_app_code.setdefault(row.sdg_code, {})[row.name] = (
				flt(row.sdg_vacation_day_multiplier, 1.0) or 1.0
			)
	if not type_by_app_code:
		return {}

	# Leave Type names we care about, and the workbook code each maps back to.
	wanted = {}
	for workbook_code, app_code in code_map.items():
		for name, multiplier in type_by_app_code.get(app_code, {}).items():
			wanted[name] = (workbook_code, multiplier)
	if not wanted:
		return {}

	since = getdate(since) if since else None
	if since:
		from_date = ["between", (since.isoformat(), date.today().isoformat())]
	else:
		from_date = ["between", (f"{date.today().year}-01-01", f"{date.today().year}-12-31")]

	leaves = frappe.get_all(
		"Leave Application",
		filters={
			"employee": employee,
			"status": ("in", VACATION_USED_STATUSES),
			"leave_type": ("in", list(wanted)),
			"from_date": from_date,
		},
		fields=["leave_type", "total_leave_days"],
		limit_page_length=0,
	)

	used = {}
	for leave in leaves:
		code, multiplier = wanted.get(leave.leave_type, (None, 1.0))
		if not code:
			continue
		used[code] = used.get(code, 0.0) + flt(leave.total_leave_days) * multiplier
	return used


def get_vacation_balance(employee, year=None):
	"""One employee's balance, as the workbook computes it.

	`employee` may be a name or an Employee document, so callers holding either need
	not know which. Raises `VacationSpecUnavailable` when the spec has not been
	built - deliberately not a zero balance, which would be indistinguishable from a
	real answer.
	"""
	employee_doc = (
		frappe.get_doc("Employee", employee) if isinstance(employee, str) else employee
	)
	as_of = vacation_spec.get_spec().get("source", {}).get("as_of")
	person = vacation_spec.get_person(employee_doc)

	if person is None:
		return _balance_for_untracked(employee_doc, as_of, year)

	days = _day_counts(person)
	entitlement = person.get("entitlement")
	carried = flt(person.get("carried_forward"))
	base = {
		"employee": employee_doc.name,
		"year": year or date.today().year,
		"in_workbook": True,
		"is_derived": False,
		"as_of": as_of,
		"unmapped_days": vacation_spec.unmapped_days(employee_doc),
	}

	if entitlement is None:
		# Not eligible: the workbook leaves its Opening Balance blank, so there is no
		# balance to report. Reporting zero here would say they are owed nothing,
		# which is a different claim.
		base.update(
			{
				"eligible": False,
				"entitlement": None,
				"carried_forward": carried,
				"used": 0.0,
				"used_by_code": {},
				"other_days": {},
				"adjustments": 0.0,
				"balance": None,
			}
		)
		return base

	# Baseline usage from the grid, plus what Frappe has approved since.
	since = getdate(as_of) if as_of else None
	live = _weighted_leave_days(employee_doc.name, ENTITLEMENT_CODE_MAP, since)
	live_lieu = _weighted_leave_days(employee_doc.name, LIEU_CODE_MAP, since)

	used_by_code = {}
	for code in DEDUCTED_CODES:
		baseline = days.get(code, 0.0)
		if code == HALF_DAY:
			baseline /= 2.0
		total = baseline + flt(live.get(code))
		if total:
			used_by_code[code] = total
	used = sum(used_by_code.values())

	# Leave in lieu, netted exactly as the workbook nets it.
	lieu_day_off = days.get("LL", 0.0) - days.get("PDO", 0.0) + flt(live_lieu.get("LL")) - flt(live_lieu.get("PDO"))
	lieu_public_holiday = days.get("LLPH", 0.0) - days.get("PDOPH", 0.0)
	adjustments = lieu_day_off + lieu_public_holiday

	other_days = {}
	for code in NON_DEDUCTED_CODES + UNCONFIRMED_CODES:
		if days.get(code):
			other_days[code] = days[code]

	base.update(
		{
			"eligible": True,
			"entitlement": flt(entitlement),
			"carried_forward": carried,
			"used": used,
			"used_by_code": used_by_code,
			"other_days": other_days,
			"lieu_day_off": lieu_day_off,
			"lieu_public_holiday": lieu_public_holiday,
			"adjustments": adjustments,
			"balance": round(
				lieu_day_off + (carried + flt(entitlement) - used) + lieu_public_holiday, 4
			),
		}
	)
	return base


def _balance_for_untracked(employee_doc, as_of, year):
	"""A balance for one of the 68 active employees the workbook omits.

	The entitlement is the workbook's own rule applied to their dates, so it is the
	same policy rather than an invention - but it is computed, not entered by HR,
	and `is_derived` says so. Carried-forward is left `None` rather than 0: the
	workbook has no row, so there is no figure, and a 0 would read as a recorded
	answer.
	"""
	entitlement = vacation_spec.derived_entitlement(employee_doc, as_of=as_of)
	return {
		"employee": employee_doc.name,
		"year": year or date.today().year,
		"in_workbook": False,
		"is_derived": entitlement is not None,
		"eligible": entitlement is not None,
		"entitlement": flt(entitlement) if entitlement is not None else None,
		"carried_forward": None,
		"used": 0.0,
		"used_by_code": {},
		"other_days": {},
		"adjustments": 0.0,
		"balance": flt(entitlement) if entitlement is not None else None,
		"as_of": as_of,
		"unmapped_days": {},
	}


def get_vacation_balance_rows(employee, year=None):
	"""``(year, rows)`` for the balances screen - entitlement, usage, balance.

	The row shape keeps `label`/`due`/`used`/`remaining` from the six-bucket era so
	the client's progress bar keeps working, but the numbers now describe one
	pooled entitlement rather than a bucket.
	"""
	employee_doc = (
		frappe.get_doc("Employee", employee) if isinstance(employee, str) else employee
	)
	balance = get_vacation_balance(employee_doc, year=year)
	year = balance["year"]

	if not balance["eligible"]:
		return year, []

	rows = [
		{
			"label": "Entitlement",
			"code": "ENT",
			"due": flt(balance["entitlement"]),
			"used": 0.0,
			"remaining": flt(balance["entitlement"]),
			"derived": balance["is_derived"],
		}
	]

	if balance["carried_forward"]:
		carried = flt(balance["carried_forward"])
		source = vacation_spec.spec_source()
		rows.append(
			{
				"label": "Carried forward",
				"code": "CF",
				"due": carried,
				"used": 0.0,
				"remaining": carried,
				"expires": source.get("carry_forward_expires"),
				"above_documented_cap": carried > (source.get("carry_forward_cap_days") or 0),
			}
		)

	used_rows = []
	codes = vacation_spec.spec_codes()
	for code, days in sorted((balance.get("used_by_code") or {}).items()):
		used_rows.append(
			{
				"label": codes.get(code) or UNKNOWN_LABEL,
				"code": code,
				"days": round(flt(days), 2),
			}
		)
	for code, days in sorted((balance.get("other_days") or {}).items()):
		used_rows.append(
			{
				"label": codes.get(code) or UNKNOWN_LABEL,
				"code": code,
				"days": round(flt(days), 2),
			}
		)
	rows.append(
		{
			"label": "Taken",
			"code": "USED",
			"due": flt(balance["entitlement"]) + flt(balance["carried_forward"]),
			"used": flt(balance["used"]),
			"remaining": flt(balance["balance"]),
			"breakdown": used_rows,
		}
	)

	if balance["adjustments"]:
		rows.append(
			{
				"label": "Leave in lieu",
				"code": "LIEU",
				"due": 0.0,
				"used": 0.0,
				"remaining": flt(balance["adjustments"]),
				"breakdown": [
					{"label": "Rest day worked", "code": "LL", "days": flt(balance.get("lieu_day_off"))},
					{"label": "Public holiday worked", "code": "LLPH", "days": flt(balance.get("lieu_public_holiday"))},
				],
			}
		)

	return year, rows


def get_balance_context(employee=None):
	"""Provenance for the screen: where the figures came from, and their caveats."""
	source = vacation_spec.spec_source()
	context = dict(source)
	context["stale_codes"] = vacation_spec.stale_codes()
	context["unconfirmed_codes"] = list(vacation_spec.UNMAPPED_CODES)
	return context
