"""The vacation tracker workbook, as a read-only data source.

`SharmDreamVacation Tracker.xlsx` is where every vacation balance comes from. It
is not read at request time - the file lives outside the app and is rewritten by
hand - so `tools/build_vacation_spec.py` extracts it once into
`seed_data/vacation_spec.json` and this module reads that.

The extraction is deliberately a *derivation*, not a copy. The workbook computes
its balances in formulas, its day grid is edited by hand without a
recalculation, and three of its columns call `TODAY()`. Reading the cached values
would mean importing stale figures that no longer match their own row, so the
builder recomputes the arithmetic and records what the workbook's own cached
figures say, in `spec["reconciliation"]`, so the divergence stays visible.

What the workbook actually encodes, recovered from its formulas:

    eligible     service >= 6 months
    entitlement  30 days if age >= 50 or service >= 120 months, else 21
                 20 employees instead carry a hand-entered sum such as
                 `30+7+52+15` - staff who do not work a 5-day week
    carry forward  hand typed per row, uncapped in practice
    balance      (LL - PDO) + (carried + entitlement - half_days - CL - V)
                 + (LLPH - PDOPH)
"""

from __future__ import annotations

import functools
import json
import pathlib

import frappe

SPEC_PATH = pathlib.Path(__file__).resolve().parents[2] / "seed_data" / "vacation_spec.json"

# Leave types the workbook has no code for. The day grid records them, nothing
# references them in the workbook's own arithmetic, and 4 days across the group
# carry them. They are named here so they are reported rather than dropped.
UNMAPPED_CODES = ("DE-L", "DI.L", "D.L")


class VacationSpecUnavailable(frappe.ValidationError):
	"""The extracted workbook spec is missing or unreadable.

	Raised rather than falling back to zeros. A balances screen that silently
	shows every employee owing nothing looks like a real answer, and it would be
	indistinguishable from one.
	"""


@functools.lru_cache(maxsize=1)
def get_spec():
	"""The whole spec, read once per process."""
	if not SPEC_PATH.exists():
		raise VacationSpecUnavailable(
			f"Vacation balances are unavailable: {SPEC_PATH.name} has not been "
			"generated. Run tools/build_vacation_spec.py against "
			"SharmDreamVacation Tracker.xlsx."
		)
	try:
		return json.loads(SPEC_PATH.read_text(encoding="utf-8"))
	except (OSError, ValueError) as exc:
		raise VacationSpecUnavailable(
			f"Vacation balances are unavailable: {SPEC_PATH.name} could not be "
			f"read ({exc})."
		) from exc


def spec_source():
	"""Provenance for the screen: where these numbers came from and as of when."""
	source = get_spec().get("source") or {}
	return {
		"workbook": source.get("workbook"),
		"sheet": source.get("sheet"),
		"as_of": source.get("as_of"),
		"carry_forward_expires": source.get("carry_forward_expires"),
		"carry_forward_cap_days": source.get("carry_forward_cap_days"),
		"carry_forward_cap_enforced": source.get("carry_forward_cap_enforced", False),
	}


@functools.lru_cache(maxsize=1)
def _by_code():
	rows = {}
	for person in get_spec().get("people") or []:
		code = (person.get("code") or "").strip()
		if code:
			rows[code] = person
	return rows


def get_person(employee):
	"""The spec row for one employee, or None when the workbook has no row.

	`employee` may be a name or an Employee document; 68 of the group's active
	employees have no row, and their absence has to be distinguishable from a
	zero balance - see `derived_entitlement` for what happens in that case.
	"""
	name = getattr(employee, "name", employee)
	person = _by_code().get(name)
	return dict(person) if person else None


def in_workbook(employee):
	return get_person(employee) is not None


def stale_codes():
	"""Employee codes whose workbook row no longer agrees with its own formula.

	The workbook's day grid is maintained by hand and edited without a
	recalculation, so on these rows the cached balance and the arithmetic in the
	same row disagree. The spec follows the arithmetic. Surfaced so HR can see
	exactly which rows need a resave in Excel rather than being corrected here.
	"""
	return sorted(
		row["code"] for row in (get_spec().get("reconciliation") or {}).get("stale") or []
	)


def unmapped_days(employee):
	"""Day-grid tokens with no established meaning, for one employee."""
	person = get_person(employee)
	if not person:
		return {}
	return dict(person.get("unmapped_days") or {})


def derived_entitlement(employee, as_of=None):
	"""The workbook's entitlement rule applied to an employee with no workbook row.

	Only used for employees the workbook omits. The rule is the workbook's own
	(`30 if age >= 50 or service >= 120 months, else 21`, needing 6 months of
	service), so it is the same policy rather than a guess - but it is still
	computed rather than entered by HR, and `is_derived` marks it as such on the
	balance row so nobody mistakes it for a recorded figure.
	"""
	person = get_person(employee)
	if person:
		return person.get("entitlement")

	if isinstance(employee, str):
		employee = frappe.get_doc("Employee", employee)
	rules = get_spec().get("rules") or {}
	as_of = as_of or get_spec().get("source", {}).get("as_of")

	# This HRMS build names the hire date `date_of_joining`; there is no
	# `joining_date` column, so asking for it silently yields nothing and every
	# employee looks like they have no service.
	service = _months_between(employee.get("date_of_joining"), as_of)
	if service is None or service < (rules.get("min_service_months") or 0):
		return None
	age = _years_between(employee.get("date_of_birth"), as_of)
	senior = (age is not None and age >= (rules.get("senior_age") or 50)) or \
		service >= (rules.get("senior_service_months") or 120)
	return rules.get("senior_entitlement") if senior else rules.get("base_entitlement")


def _parse_date(value):
	from frappe.utils import getdate

	if not value:
		return None
	try:
		return getdate(value)
	except (ValueError, TypeError):
		return None


def _months_between(start, end):
	"""Whole months between two dates, matching Excel's DATEDIF(..., "m")."""
	start, end = _parse_date(start), _parse_date(end)
	if not start or not end:
		return None
	months = (end.year - start.year) * 12 + (end.month - start.month)
	if end.day < start.day:
		months -= 1
	return max(months, 0)


def _years_between(start, end):
	start, end = _parse_date(start), _parse_date(end)
	if not start or not end:
		return None
	years = end.year - start.year
	if (end.month, end.day) < (start.month, start.day):
		years -= 1
	return max(years, 0)


def spec_codes():
	"""The workbook's day-grid vocabulary, for tests and for the request form."""
	return dict(get_spec().get("codes") or {})
