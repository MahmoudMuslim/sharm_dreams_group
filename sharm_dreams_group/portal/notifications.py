# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""What needs this employee's attention, on their dashboard.

Every item here is derived from data that already exists - there is no "seen" flag, no
notification table and no read/unread state. That is a deliberate constraint: a badge
that has to be dismissed, or a bell that has to be marked read, is a thing to maintain
and a thing that drifts the moment nobody clears it. Each item answers "is there work
outstanding?", so it goes away by itself the moment the work is done, and cannot go
stale the way a stored flag can.

The corollary is that nothing here is *novelty*. "You have a new payslip" needs to
remember what you last looked at, and a portal that is wrong about novelty is worse
than one that never claims it. What it can say honestly is "payroll for September is
available", and that is what it says.

Each item carries the screen it belongs to, so tapping it lands on the thing that needs
doing rather than on the screen the count came from.
"""

from __future__ import annotations

import frappe

from sharm_dreams_group.portal.performance import _appraisal_view
from sharm_dreams_group.portal.portal import resolve_session

# How far back the attendance scan looks. A month: long enough that someone on leave
# finds it before HR raises it, short enough that a stale punch from last year is not
# still shouting at them.
ATTENTION_DAYS = 30


def _item(key, icon, tone, headline, detail, screen):
	return {
		"key": key,
		"icon": icon,
		"tone": tone,
		"headline": headline,
		"detail": detail,
		"screen": screen,
	}


def _leave_pending(emp) -> list[dict]:
	"""Requests sitting with an approver.

	`Open` is the only state that is genuinely outstanding: an approved one has been
	actioned and a rejected or cancelled one is over. Counting all three would put a
	number on the dashboard that the employee cannot act on.
	"""
	rows = frappe.get_all(
		"Leave Application",
		filters={"employee": emp.name, "status": "Open", "docstatus": ["<", 2]},
		fields=["name", "leave_type", "from_date", "to_date", "total_leave_days"],
		order_by="from_date asc",
		limit_page_length=0,
	)
	items = []
	for row in rows:
		span = (
			str(row.from_date)
			if str(row.from_date) == str(row.to_date)
			else f"{row.from_date} to {row.to_date}"
		)
		items.append(
			_item(
				f"leave:{row.name}",
				"fa-clock-o",
				"warn",
				"Your leave request is awaiting approval",
				f"{row.leave_type} · {span}",
				"time_off",
			)
		)
	return items


def _appraisal_outstanding(emp) -> list[dict]:
	"""Draft appraisals this employee has not finished rating.

	Reads completeness through `performance._appraisal_view` rather than repeating it:
	"complete" is the same rule the submit button enforces, and a second copy of it
	would be free to disagree - telling someone their appraisal is done on the dashboard
	while the form refuses to submit it.

	An appraisal with no rating criteria at all is skipped. HRMS creates them before the
	criteria exist, and an employee cannot rate criteria that are not there; showing
	"0 of 0" as outstanding would be a task they cannot perform.
	"""
	rows = frappe.get_all(
		"Appraisal",
		filters={"employee": emp.name, "docstatus": 0},
		fields=["name", "appraisal_cycle"],
		limit_page_length=0,
	)
	items = []
	for row in rows:
		view = _appraisal_view(emp, row)
		# `can_submit` is the same flag the submit button is gated on, so the badge
		# cannot say "done" on an appraisal the form still refuses.
		if not view["self_ratings"] or view["can_submit"]:
			continue
		items.append(
			_item(
				f"appraisal:{row.name}",
				"fa-star-half-o",
				"warn",
				"Your self-assessment is not finished",
				f"{view['rated_count']} of {view['rating_count']} criteria rated",
				"performance",
			)
		)
	return items


def _no_shift_today(token) -> list[dict]:
	"""No shift today, which means the punch would be refused.

	Worth saying in advance rather than letting someone walk to a tablet, tap, and be
	told. Read through `attendance.my_shifts` rather than re-deriving it: the punch
	refuses on this exact rule, and a second copy of "covers today" could disagree with
	it - telling someone they are working when the punch is about to refuse, or the
	reverse, which is the sort of small contradiction a wall tablet makes look broken.
	"""
	from sharm_dreams_group.portal import attendance

	today_shift = attendance.my_shifts(token)["today_shift"]
	if today_shift:
		return []
	return [
		_item(
			"no-shift-today",
			"fa-calendar-times-o",
			"warn",
			"No shift covers today",
			"You will not be able to punch until HR assigns one",
			"attendance",
		)
	]


def _recent_offshift(emp) -> list[dict]:
	"""Punches HRMS filed outside the shift, for the employee to see first.

	Recorded rather than refused, because a late arrival still has to leave a record -
	but the employee should learn about it from their own portal rather than from a
	question in a payroll dispute. Counted, not listed: one line naming the count is the
	useful thing, and the detail screen already has the days.
	"""
	cutoff = frappe.utils.add_days(frappe.utils.today(), -ATTENTION_DAYS)
	count = frappe.db.count(
		"Employee Checkin",
		{
			"employee": emp.name,
			"offshift": 1,
			"time": [">=", cutoff],
		},
	)
	if not count:
		return []
	return [
		_item(
			"offshift",
			"fa-exclamation-triangle",
			"bad",
			"Your hours were recorded outside your shift",
			f"{count} punch{'es' if count != 1 else ''} in the last {ATTENTION_DAYS} days",
			"attendance",
		)
	]


@frappe.whitelist()
def my_notifications(token: str) -> dict:
	"""This employee's outstanding items, most urgent first.

	Scoped to the signed-in employee exactly as every other portal read is: a
	notification list is the easiest place in the app to leak somebody else's leave or
	appraisal, because it is small, always fetched, and shown before anyone signs in.
	"""
	emp = resolve_session(token)

	items = []
	items += _leave_pending(emp)
	items += _appraisal_outstanding(emp)
	items += _no_shift_today(token)
	items += _recent_offshift(emp)

	return {"items": items, "count": len(items)}