# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Turns an employee's recorded penalties into a level HR can act on.

The model, and why it is this shape:

*   Each `SDG Penalty` row carries **points**, not just an existence.  A verbal
    warning is 1, a written warning 3, an HR escalation 5.  Levels are then
    thresholds on the total rather than "exactly two penalties", which means
    escalating somebody never means editing the rows already recorded against
    them - it means adding one more.

*   A penalty stops counting when it is waived, withdrawn or past its
    `expires_on`.  Without that an employee with six warnings from five years ago
    is permanently at the top band and the scale stops meaning anything.

*   The bands live in `SDG Penalty Level` as data, so HR can move a threshold or
    change a colour without a deployment.

Nothing here touches salary or any payslip field.  A penalty is recorded and
escalated; it is never deducted.

Run with:  bench --site <site> execute sharm_dreams_group.hr.penalty.verify
"""

from __future__ import annotations

import json
from itertools import pairwise

import frappe
from frappe.utils import getdate, nowdate

# Statuses that keep counting.  Anything else is kept for the record and ignored
# by the total, so waiving a penalty never means deleting it.
COUNTING_STATUSES = ("Open", "Acknowledged", "Appealed")

LEVEL_FIELD = "sdg_penalty_level"
POINTS_FIELD = "sdg_penalty_points"
COUNT_FIELD = "sdg_penalty_count"
COLOUR_FIELD = "sdg_penalty_colour"
SUMMARY_FIELD = "sdg_penalty_summary"
INDICATOR_FIELD = "sdg_penalty_indicator"
TABLE_FIELD = "sdg_penalty"

# Everything the hook owns, in the order it is derived.
DERIVED_FIELDS = (POINTS_FIELD, COUNT_FIELD, LEVEL_FIELD, COLOUR_FIELD, INDICATOR_FIELD, SUMMARY_FIELD)


# ---------------------------------------------------------------------------
# counting
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# who may see penalties
# ---------------------------------------------------------------------------
# Who may see penalties is expressed where Frappe already enforces it: the read
# permission on `Employee`, which is where the penalty rows live.
#
# `SDG Penalty` is a child table of Employee, so it has no DocPerm of its own to
# gate - a permission on a child table is not a thing Frappe enforces.  A
# settings table of our own was duplicating that policy in a place Frappe does not
# read, so adding a role in the UI did nothing for the guards below while appearing
# to work.  `SDG Penalty Settings` is gone for that reason.
#
# Consequence worth knowing: a role granted read on Employee immediately sees
# penalties. That is deliberate - the alternative is a second permission model
# nobody remembers to update.
PENALTY_PARENT = "Employee"


def configured_roles() -> list[str]:
	"""The roles that can read the employees the penalties hang off.

	Read from the parent's DocType permission, so the answer is whatever the desk
	already enforces. Falls back to HR Manager when the permission cannot be read at
	all - a site whose DocType was never synced, say. Defaulting open would leak
	every employee's disciplinary record; defaulting closed would hide the feature
	from the staff who installed it.
	"""
	try:
		perms = frappe.get_meta(PENALTY_PARENT).permissions or []
		roles = sorted({p.role for p in perms if p.read})
	except Exception:
		return ["HR Manager"]
	return roles or ["HR Manager"]


def can_view_penalties(user: str | None = None) -> bool:
	"""Whether `user` may see the penalty feature at all.

	Frappe's own permission check, plus a role the parent DocType actually names -
	not a role list compared here.  `has_permission` already accounts for user
	permissions, role profiles and owner/assignee rules, and it is the same answer
	the desk gives.
	"""
	if not user:
		user = frappe.session.user
	if user == "Administrator":
		return True
	try:
		if not frappe.has_permission(PENALTY_PARENT, ptype="read", user=user):
			return False
	except Exception:
		# no permission row at all means nobody but the administrator
		return False
	return bool(set(frappe.get_roles(user)).intersection(configured_roles()))


def require_penalty_access() -> None:
	"""Guard for the whitelisted entry points.

	Client-side hiding is only a courtesy: the Number Cards, the colour lookup
	and the report are all reachable by URL with the session cookie alone, so the
	rows themselves are refused here.
	"""
	if not can_view_penalties():
		frappe.throw(
			frappe._("Not permitted to view employee penalties."),
			frappe.PermissionError,
		)


def counts(penalty, today=None) -> bool:
	"""Whether one penalty row contributes to the total.

	Exposed separately so a test can pin each condition: a future-dated expiry is
	treated as not yet expired rather than as an error, because that is what a
	half-filled row in the grid means.
	"""
	today = getdate(today or nowdate())
	if penalty.status not in COUNTING_STATUSES:
		return False
	if penalty.expires_on and getdate(penalty.expires_on) < today:
		return False
	return True


def totals(penalties, today=None) -> tuple[int, int]:
	"""`(points, count)` over the penalties that still count."""
	points = count = 0
	for penalty in penalties or []:
		if counts(penalty, today):
			points += cint(penalty.points)
			count += 1
	return points, count


def cint(value) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


# ---------------------------------------------------------------------------
# banding
# ---------------------------------------------------------------------------
def bands() -> list:
	"""The configured levels, highest band first.

	Highest-first is what makes the banding unambiguous: an employee with 7 points
	and bands from 1, 3 and 6 matches the 6 band, and the order the bands happen to
	be listed in the UI cannot change the answer.
	"""
	return frappe.get_all(
		"SDG Penalty Level",
		fields=["name", "level_name", "from_points", "to_points", "indicator", "colour", "action_required"],
		order_by="from_points desc",
		limit_page_length=0,
	)


def upper_bound(level) -> int | None:
	"""The band's upper bound, or None when it is open-ended.

	An Int field is stored as 0 when it is left blank - Frappe defaults it on
	insert - so 0 and None both mean "no upper bound" here.  That is not a
	fudge: a band ending at zero points is meaningless, and reading it as a real
	bound would leave the top band of every scale matching nothing, which is
	exactly what happened before this was handled.
	"""
	bound = level.to_points
	return bound if bound else None


def level_for(points: int, levels=None) -> dict | None:
	"""The band `points` falls into, or None when no bands are configured.

	Returns None rather than guessing a default: with no levels set there is no
	rule to apply, and inventing a colour would put a meaning on the employee that
	HR never asked for.

	Sorts by `from_points` descending itself rather than trusting the caller.
	`bands()` already returns them in that order, but an open-ended lowest band
	matches every score, so an ascending list would answer "Clear" for an employee
	who should be Critical - a wrong answer with nothing to notice it.
	"""
	candidates = list(levels if levels is not None else bands())
	candidates.sort(key=lambda lv: lv.from_points or 0, reverse=True)
	for level in candidates:
		if points < (level.from_points or 0):
			continue
		bound = upper_bound(level)
		if bound is not None and points > bound:
			continue
		return level
	return None


def summary_for(level, points: int, count: int) -> str:
	"""The one-line description shown on the Employee form and in the list."""
	if not level:
		return "" if not count else f"{count} penalty record(s), no levels configured"
	action = " ".join((level.action_required or "").split())
	noun = "penalty" if count == 1 else "penalties"
	base = f"{level.level_name} - {points} pts from {count} {noun}"
	return f"{base}. {action}" if action else base


# ---------------------------------------------------------------------------
# the Employee hook
# ---------------------------------------------------------------------------
def apply_to_employee(doc, today=None) -> None:
	"""Recompute the derived penalty fields on an Employee.

	Guarded on the fields existing so it is safe on a site where the custom fields
	have not been created yet, which is the same guard the tenure fields use.
	"""
	fields = {f.fieldname for f in doc.meta.fields}
	penalties = doc.get(TABLE_FIELD) or []

	points, count = totals(penalties, today)
	level = level_for(points)

	if POINTS_FIELD in fields:
		doc.set(POINTS_FIELD, points)
	if COUNT_FIELD in fields:
		doc.set(COUNT_FIELD, count)
	if LEVEL_FIELD in fields:
		doc.set(LEVEL_FIELD, level.name if level else None)
	if COLOUR_FIELD in fields:
		doc.set(COLOUR_FIELD, level.colour if level else None)
	if INDICATOR_FIELD in fields:
		doc.set(INDICATOR_FIELD, level.indicator if level else None)
	if SUMMARY_FIELD in fields:
		doc.set(SUMMARY_FIELD, summary_for(level, points, count))


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------
def backfill(batch: int = 200) -> int:
	"""Bring every employee's derived penalty fields up to date.

	The fields are only computed on save, so without this an employee who has
	never been edited since the feature was installed would show no level and no
	colour - the list would look like the feature had not been switched on.

	Deliberately loads and saves the documents rather than writing the columns
	directly: the hook is what decides the values, so going around it would let
	the backfill and the hook disagree.  Batched, and skips anyone already
	correct, so it is cheap to re-run.

	Returns the number of employees updated.
	"""
	names = frappe.get_all(
		"Employee",
		filters={"status": ("!=", "Disabled")},
		pluck="name",
		limit_page_length=0,
	)
	updated = 0
	for start in range(0, len(names), batch):
		for name in names[start : start + batch]:
			try:
				doc = frappe.get_doc("Employee", name)
			except frappe.DoesNotExistError:
				continue
			before = (doc.get(POINTS_FIELD), doc.get(COUNT_FIELD), doc.get(LEVEL_FIELD))
			apply_to_employee(doc)
			after = (doc.get(POINTS_FIELD), doc.get(COUNT_FIELD), doc.get(LEVEL_FIELD))
			if before == after:
				continue
			try:
				doc.save(ignore_permissions=True)
			except (frappe.ValidationError, frappe.MandatoryError, frappe.PermissionError):
				# Some employees predate the feature and already carry a problem of
				# their own - a missing gender, say - so a full save trips mandatory
				# validation on data that has nothing to do with penalties.  The
				# values are already computed by the hook above, so write just those
				# six columns and leave the rest of the record alone; fixing the
				# underlying record is HR's call, not something a backfill should
				# do silently.
				for field in DERIVED_FIELDS:
					frappe.db.set_value("Employee", name, field, doc.get(field))
			updated += 1
		frappe.db.commit()
	return updated


def validate_rows(penalties) -> None:
	"""The row rules, applied from the Employee hook.

	The same two rules `SDGPenalty.validate` states, repeated here on purpose.
	A child doctype's own `validate` does **not** run when its parent is saved:
	`Document.update_child_table` calls `db_update()` on every row, and that path
	never fires `validate`. So the guard on the child controller is only reached
	if the row is opened on its own, which is not how anyone records a penalty.

	Checking here is what actually enforces it on the form that matters.
	"""
	for penalty in penalties or []:
		if penalty.penalty_date and getdate(penalty.penalty_date) > getdate(nowdate()):
			frappe.throw(
				frappe._("A penalty cannot be dated in the future."),
				title=frappe._("Check the penalty date"),
			)
		if penalty.points is None:
			penalty.points = 1
		if cint(penalty.points) < 0:
			frappe.throw(frappe._("Points cannot be negative."))


def validate_employee(doc) -> None:
	"""Employee `validate` hook, registered in hooks.py.

	Called on every save, so adding a penalty row and removing one are the same
	operation: the level is always derived, never stored by hand.
	"""
	validate_rows(doc.get(TABLE_FIELD) or [])
	apply_to_employee(doc)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def employees_by_level() -> list[dict]:
	"""Headcount per level, for the dashboard.

	Counts employees whose derived level matches, plus the ones with no band
	configured, so the total always reconciles with the employee count rather than
	silently omitting the unbanded.
	"""
	out = []
	headcount = frappe.db.count("Employee")
	banded = 0
	for level in reversed(bands()):
		n = frappe.db.count("Employee", {LEVEL_FIELD: level.name})
		banded += n
		out.append(
			{
				"level": level.level_name,
				"indicator": level.indicator,
				"colour": level.colour,
				"action_required": level.action_required,
				"employees": n,
			}
		)
	if headcount - banded:
		out.append(
			{
				"level": "No level",
				"indicator": "Grey",
				"colour": None,
				"action_required": "",
				"employees": headcount - banded,
			}
		)
	return out


def report() -> None:
	"""Print the configured bands and the current distribution."""
	levels = bands()
	print(f"=== penalty levels ({len(levels)}) ===")
	for level in reversed(levels):
		bound = upper_bound(level)
		span = f"{level.from_points}+" if bound is None else f"{level.from_points}-{bound}"
		print(
			f"  {span:<10} {level.level_name:<14} {level.indicator or '-':<8} {level.action_required or ''}"
		)
	print()
	print(f"=== employees ({frappe.db.count('Employee')}) ===")
	rows = employees_by_level()
	for row in rows:
		print(f"  {row['level']:<14} {row['indicator']:<8} {row['employees']:>5}")
	print(f"  {'TOTAL':<14} {'':<8} {sum(r['employees'] for r in rows):>5}")


def verify() -> None:
	"""Check the banding is coherent, without touching any record."""
	levels = bands()
	if not levels:
		print("  WARNING: no SDG Penalty Level records exist, so no employee can be banded")
		return
	ordered = sorted(levels, key=lambda x: x.from_points or 0)
	for previous, current in pairwise(ordered):
		bound = upper_bound(previous)
		if bound is not None and current.from_points is not None:
			if bound >= current.from_points:
				print(
					f"  OVERLAP: {previous.level_name} ends at {previous.to_points} but "
					f"{current.level_name} starts at {current.from_points}"
				)
	print(f"  levels configured: {len(levels)}")
	for points in (0, 1, 3, 6, 10, 99):
		level = level_for(points, levels)
		print(f"  {points:>3} points -> {level.level_name if level else '(no band)'}")


# ---------------------------------------------------------------------------
# the desk
# ---------------------------------------------------------------------------
@frappe.whitelist()
def headcount(filters: str | dict | None = None) -> int:
	"""Headcount at one penalty level, for a Number Card.

	The level arrives in `filters` rather than as an argument because that is what
	a `Custom` Number Card sends - it forwards its own `filters_json` verbatim - so
	one method serves a card per level and adding a level means adding a card, not
	another function.

	Guarded: the cards are the most obvious leak, since one URL with no
	`filters` answers "how many people are at Critical" for the whole company.

	With no level, the count is everyone carrying at least one penalty, which is
	the "is this actually a problem" card rather than any single band.
	"""
	require_penalty_access()

	filters = frappe.parse_json(filters) or {}
	level = filters.get("level")

	if not level:
		return frappe.db.count("Employee", {POINTS_FIELD: (">", 0)})
	if not frappe.db.exists("SDG Penalty Level", level):
		# A card left behind after its level was renamed would otherwise show 0
		# and read as "nobody is at this level", which is a different and wrong
		# statement.
		frappe.throw(frappe._("Penalty level {0} no longer exists.").format(level))
	return frappe.db.count("Employee", {LEVEL_FIELD: level})


def level_colour(level: str | None) -> str | None:
	"""The colour configured for a level, for the Number Card."""
	if not level or not frappe.db.exists("SDG Penalty Level", level):
		return None
	return frappe.get_cached_value("SDG Penalty Level", level, "colour")


def apply_list_view_columns() -> bool:
	"""Show the penalty level as a column on the Employee list.

	The coloured strip from `get_indicator` says *that* somebody is banded; a
	column says *which* band, and is sortable and filterable, which a colour is
	not.  The existing columns are kept - this appends, it does not replace, so a
	view HR has arranged themselves is left alone.
	"""
	columns = [
		{"fieldname": LEVEL_FIELD, "label": "Penalty Level", "width": "130"},
		{"fieldname": POINTS_FIELD, "label": "Penalty Points", "width": "110"},
	]
	required = {c["fieldname"] for c in columns}

	# `fields` on List View Settings is a Code field holding JSON, not a table,
	# so it is written as a string in both branches.
	if frappe.db.exists("List View Settings", "Employee"):
		doc = frappe.get_doc("List View Settings", "Employee")
		existing = frappe.parse_json(doc.fields) or []
		if required <= {c.get("fieldname") for c in existing}:
			return False
		# existing first, then the new ones, so a view HR has arranged keeps its
		# column order and the additions land at the end
		doc.fields = json.dumps(_merge(existing, columns))
		doc.save()
		return True

	# Nothing to append to, so this creates the record - and a List View Settings
	# row *replaces* the doctype's default column set rather than adding to it.
	# Creating one holding only the two penalty columns therefore wiped HR's list
	# down to a level and a number.  So the desk's own default columns are written
	# first, in their own order.
	#
	# The two penalty fields carry `in_list_view` on the Custom Field, so they are
	# already in that default set - which is what puts them in the image view's
	# tiles.  Deduplicated here so they are not listed twice.
	frappe.get_doc(
		{
			"doctype": "List View Settings",
			"name": "Employee",
			"fields": json.dumps(_merge(_default_list_columns(), columns)),
		}
	).insert(ignore_permissions=True)
	return True


def _merge(existing: list, extra: list) -> list:
	"""`existing` then whatever in `extra` is not already there, in order."""
	seen = {c.get("fieldname") for c in existing}
	return existing + [c for c in extra if c.get("fieldname") not in seen]


def _default_list_columns() -> list[dict]:
	"""The columns the desk shows for this doctype with no List View Settings.

	Read from `in_list_view` on the doctype, which is what the desk itself uses, so
	creating the settings row does not quietly change the list a manager is used
	to.
	"""
	out = []
	for field in frappe.get_meta("Employee").fields:
		if field.in_list_view and not getattr(field, "is_custom", False):
			out.append({"fieldname": field.fieldname, "label": field.label})
	return out


@frappe.whitelist()
def level_colours() -> dict:
	"""`{level_name: colour}` for the configured bands.

	Served to the desk so the image view can colour a tile without fetching the
	colour as a column - a hex string in the detail bullets would be noise, and
	adding a hidden colour field to the query is more coupling than a lookup.
	"""
	require_penalty_access()

	return {
		level.name: level.colour or level.indicator
		for level in bands()
		if level.name and (level.colour or level.indicator)
	}
