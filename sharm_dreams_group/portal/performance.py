# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Performance: the employee's own appraisals, and the self-assessment they submit.

The last of the five areas, and the only one the employee cannot start themselves.
That is HRMS's shape, not this app's choice, and the difference matters for what the
portal is allowed to do:

**HR creates the `Appraisal`, not the employee.** `AppraisalCycle.create_appraisals`
walks the cycle's `appraisees` and writes one `Appraisal` per person, filling its goals
from the `Appraisal Template`. There is no employee-facing path that creates one, so
there is none here either. Inventing an appraisal would mean inventing the goals and
weightages in it, and those are HR's policy.

So the employee's part is narrower than in the other four areas: fill in the
self-assessment HRMS laid out, then submit it. That is still a real submission -
`Appraisal` is submittable, `validate` refuses a cycle whose status is `Completed`,
and `validate_total_weightage` refuses self-ratings that do not add up to 100.

**Every Performance doctype on this site is installed and every table is empty.** No
Appraisal Cycle, no Appraisal Template, no Appraisal, no Goal, no KRA, and no
`Employee Feedback Criteria`. That is not an error state and not something this module
can fix - it needs HR to create a cycle. So rather than rendering an empty box and
letting the employee conclude the app is broken, `my_performance` reports *blockers*:
plain sentences naming what is missing. An empty screen that says why it is empty is
the opposite of a dead click.

**The employee may only fill in rows HRMS already made.** `self_ratings` rows carry
`criteria`, `per_weightage` and `rating`, and `validate_total_weightage` requires the
weightages to total 100. Adding a row here would change that total, so the portal only
writes the `rating` onto rows that already exist. An employee cannot invent a criterion
or change how much of their score it carries.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from sharm_dreams_group.portal.portal import resolve_session

__all__ = [
	"my_performance",
	"save_self_appraisal",
	"submit_appraisal",
]

# The approval category this area files into. One row per area in
# `Employee Self Service Approval Category`, so an approver sees performance requests
# separately from expenses.
PERFORMANCE_CATEGORY = "Performance"

# The scale the employee picks on, and the one the field actually stores. They are not
# the same number, and getting this wrong silently halves every rating.
#
# `Employee Feedback Rating.rating` is a Frappe `Rating` field, and
# `Document._fix_rating_value` clamps it to **0-1** on every save. The stars are
# `value * 5`, which is how `calculate_self_appraisal_score` reads it:
# `rating * number_of_stars * weightage`, with `number_of_stars` defaulting to 5 when
# the field carries no options (and this one does not).
#
# So 1.0 is five stars and 0.4 is two. Anything that writes 4 straight into the column
# is clamped to 1 on save and the employee's rating silently becomes full marks - which
# is how a self-assessment quietly records everyone as perfect.
RATING_STARS = 5


def _stars_to_stored(stars) -> float:
	"""What the employee picked, in the field's own 0-1 scale."""
	return max(0.0, min(flt(stars), RATING_STARS)) / RATING_STARS


def _stored_to_stars(value) -> float:
	"""What the field holds, back on the 0-5 scale the employee sees."""
	return flt(value) * RATING_STARS


@frappe.whitelist()
def my_performance(token: str) -> dict:
	"""This employee's appraisals, goals and rating criteria, and what is missing.

	The counts are returned beside the lists they describe rather than the client
	counting them, so a figure on a card and the length of the list it opens cannot
	drift apart.
	"""
	emp = resolve_session(token)

	appraisals = frappe.get_all(
		"Appraisal",
		filters={"employee": emp.name, "docstatus": ("!=", 2)},
		fields=[
			"name",
			"appraisal_cycle",
			"start_date",
			"end_date",
			"docstatus",
			"total_score",
			"self_score",
			"final_score",
			"reflections",
			"amended_from",
		],
		order_by="creation desc",
		limit_page_length=0,
	)

	out_appraisals = []
	for row in appraisals:
		out_appraisals.append(_appraisal_view(emp, row))

	goals = frappe.get_all(
		"Goal",
		filters={"employee": emp.name, "is_group": 0},
		fields=[
			"name",
			"goal_name",
			"progress",
			"status",
			"start_date",
			"end_date",
			"kra",
			"appraisal_cycle",
		],
		order_by="creation desc",
		limit_page_length=0,
	)

	out_goals = [
		{
			"name": goal.name,
			"goal_name": goal.goal_name,
			"progress": flt(goal.progress),
			"status": goal.status,
			"start_date": str(goal.start_date) if goal.start_date else None,
			"end_date": str(goal.end_date) if goal.end_date else None,
			"kra": goal.kra,
			"appraisal_cycle": goal.appraisal_cycle,
		}
		for goal in goals
	]

	criteria = frappe.get_all(
		"Employee Feedback Criteria",
		fields=["name"],
		order_by="name asc",
		limit_page_length=0,
	)

	cycles = frappe.get_all(
		"Appraisal Cycle",
		filters={"status": ("!=", "Completed")},
		fields=["name", "cycle_name", "status", "start_date", "end_date"],
		order_by="start_date desc",
		limit_page_length=0,
	)

	awaiting_me = [a for a in out_appraisals if a["can_submit"]]

	return {
		"employee": emp.name,
		"appraisals": out_appraisals,
		"goals": out_goals,
		"criteria": [c.name for c in criteria],
		"cycles": [
			{
				"name": cycle.name,
				"cycle_name": cycle.cycle_name,
				"status": cycle.status,
				"start_date": str(cycle.start_date) if cycle.start_date else None,
				"end_date": str(cycle.end_date) if cycle.end_date else None,
			}
			for cycle in cycles
		],
		# Every figure has its list beside it, in the same response, so the screen
		# cannot show a count that no list backs.
		"counts": {
			"appraisals": len(out_appraisals),
			"goals": len(out_goals),
			"awaiting_me": len(awaiting_me),
			"criteria": len(criteria),
		},
		"blockers": _blockers(out_appraisals, criteria, bool(cycles)),
	}


def _blockers(appraisals: list, criteria: list, has_cycle: bool) -> list[str]:
	"""What HR has to create before this screen has anything in it.

	Plain sentences, and only the ones that actually apply. Shown instead of an empty
	frame, because "no appraisal cycle has started" is an answer and a blank list is
	not.

	All three conditions are arguments rather than two arguments and a database read.
	Reading the cycle count here would have made this untestable in isolation - the only
	way to exercise the no-cycle branch would be to empty the site, which is exactly
	what made these tests break when the seeder filled it.
	"""
	missing = []
	if not has_cycle:
		missing.append(
			"No appraisal cycle has been set up yet, so there is nothing to appraise "
			"against. Your HR team needs to create one."
		)
	if not criteria:
		missing.append(
			"No rating criteria have been defined, so a self-assessment cannot be "
			"filled in yet. Your HR team needs to define them."
		)
	if not appraisals:
		missing.append(
			"You have no appraisal yet. Your HR team creates one for you when a cycle "
			"starts."
		)
	return missing


def _appraisal_view(emp, row) -> dict:
	"""One appraisal, with its goals, its rating rows and its approval request."""
	doc = frappe.get_doc("Appraisal", row.name)

	self_ratings = [
		{
			"criteria": rating.criteria,
			"per_weightage": flt(rating.per_weightage),
			# Reported in stars, not in the field's 0-1, because that is what the
			# employee is rating on and what they will read back.
			"rating": round(_stored_to_stars(rating.rating), 2),
		}
		for rating in (doc.self_ratings or [])
	]
	rated = [r for r in self_ratings if r["rating"]]
	complete = bool(self_ratings) and len(rated) == len(self_ratings)

	approval = frappe.db.get_value(
		"Employee Self Service Approval Request",
		{"reference": ("like", f"%{row.name}%"), "request_type": PERFORMANCE_CATEGORY},
		["name", "status"],
		# `as_dict` is required, not decorative: asking for two fields without it
		# returns a plain tuple, and `approval.name` on a tuple is an AttributeError
		# raised the first time an employee has an approval request - which is the only
		# time this line matters.
		as_dict=True,
		order_by="creation desc",
	)

	return {
		"name": row.name,
		"appraisal_cycle": row.appraisal_cycle,
		# `docstatus` is 0/1 here; cancelled ones are filtered out above, but named
		# rather than turned into a bool so the client cannot mistake Draft for
		# Submitted.
		"docstatus": row.docstatus,
		"status_label": {0: "Draft", 1: "Submitted"}.get(row.docstatus, "Unknown"),
		"start_date": str(row.start_date) if row.start_date else None,
		"end_date": str(row.end_date) if row.end_date else None,
		"total_score": flt(row.total_score),
		"self_score": flt(row.self_score),
		"final_score": flt(row.final_score),
		"goals": [
			{
				"kra": goal.kra,
				"per_weightage": flt(goal.per_weightage),
				"score": flt(goal.score),
				"score_earned": flt(goal.score_earned),
			}
			for goal in (doc.goals or [])
		],
		"self_ratings": self_ratings,
		"rated_count": len(rated),
		"rating_count": len(self_ratings),
		"reflections": doc.reflections or "",
		# Set when this document is a rejected one reopened for revision, which is how
		# an approver sends a self-assessment back without stranding the employee on a
		# document they can no longer edit.
		"amended_from": row.amended_from,
		# Only a draft is editable, and only a complete draft may be submitted. Both
		# decided here so the button and the refusal agree.
		"can_edit": row.docstatus == 0,
		"can_submit": row.docstatus == 0 and complete,
		"approval": (
			{"name": approval.name, "status": approval.status} if approval else None
		),
	}


def _my_appraisal(emp, name: str):
	"""The named appraisal, if it is this employee's and still a draft.

	Refused rather than returned for someone else: an appraisal is somebody's
	self-assessment, and reading or writing another person's is not a thing the portal
	does under any token.
	"""
	if not name or not frappe.db.exists("Appraisal", name):
		frappe.throw(_("That appraisal does not exist."))
	doc = frappe.get_doc("Appraisal", name)
	if doc.employee != emp.name:
		frappe.throw(_("That appraisal belongs to a different employee."))
	if doc.docstatus != 0:
		frappe.throw(
			_("That appraisal has already been submitted, so it cannot be changed.")
		)
	return doc


@frappe.whitelist()
def save_self_appraisal(token: str, name: str, ratings: list | str | None = None,
						reflections: str | None = None) -> dict:
	"""Write the employee's own self-ratings and reflections onto their draft.

	`ratings` maps a criterion to a rating. Only rows HRMS already created are
	written: `validate_total_weightage` requires the self-rating weightages to total
	100, so a row added here would change a total HR set deliberately. An unknown
	criterion, or one that is not on this appraisal, is refused rather than ignored -
	silently dropping one would let an employee believe they had rated it.
	"""
	emp = resolve_session(token)
	doc = _my_appraisal(emp, name)

	if ratings is None:
		ratings = []
	if isinstance(ratings, str):
		ratings = frappe.parse_json(ratings)
	incoming = {
		str(row.get("criteria")): row
		for row in (ratings or [])
		if isinstance(row, dict) and row.get("criteria")
	}

	existing = {row.criteria: row for row in (doc.self_ratings or [])}
	unknown = sorted(set(incoming) - set(existing))
	if unknown:
		frappe.throw(
			_("These rating criteria are not part of your appraisal: {0}").format(
				", ".join(unknown)
			)
		)

	for criterion, row in incoming.items():
		stars = flt(row.get("rating"))
		if stars < 0 or stars > RATING_STARS:
			frappe.throw(
				_("A rating must be between 0 and {0}.").format(RATING_STARS)
			)
		existing[criterion].rating = _stars_to_stored(stars)

	if reflections is not None:
		# Trimmed but not emptied by accident: a blank reflections field is a legitimate
		# thing to save, and `None` is how the client says "leave it alone".
		doc.reflections = (reflections or "").strip()

	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "saved": True, "rated_count": sum(
		1 for row in (doc.self_ratings or []) if row.rating
	)}


@frappe.whitelist()
def submit_appraisal(token: str, name: str) -> dict:
	"""Submit the employee's completed self-assessment and ask for it to be approved.

	Submitting an `Appraisal` *is* the act here - unlike an expense claim, there is no
	ledger posting waiting behind it - so this submits and then raises the request. The
	approver's rejection cancels the appraisal and leaves it as a draft again, so the
	employee can revise and resubmit rather than being stuck with a rejected document
	they cannot touch.
	"""
	emp = resolve_session(token)
	doc = _my_appraisal(emp, name)

	self_ratings = doc.self_ratings or []
	if not self_ratings:
		frappe.throw(
			_("There are no rating criteria on this appraisal yet, so it cannot be "
			  "submitted. Please contact HR.")
		)
	missing = [row.criteria for row in self_ratings if not row.rating]
	if missing:
		frappe.throw(
			_("Please give a rating for every criterion before submitting. Still to "
			  "do: {0}").format(", ".join(sorted(missing)))
		)

	# `submit()` runs HRMS's own permission checks, and under a portal token the session
	# user is Guest - who has no role granting access to Appraisal at all. `save` above
	# passes `ignore_permissions`; `submit` takes the same flag, and it is not optional
	# here: without it the submission fails with "User Guest does not have doctype
	# access", which reads to the employee as their appraisal being broken.
	#
	# The user is switched as well, so `owner` and the audit trail belong to the punch
	# rather than to an Administrator. 179 of the 180 active employees have no Frappe
	# User, so the fallback is the normal case rather than an edge one - refusing their
	# submission over it would leave the area unusable.
	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		doc.submit()
	finally:
		frappe.set_user(previous)

	request = _raise_approval_request(emp, doc)
	frappe.db.commit()
	return {
		"name": doc.name,
		"submitted": True,
		"approval": request,
	}


def _raise_approval_request(emp, appraisal: dict) -> str | None:
	"""The `Employee Self Service Approval Request` this appraisal waits on.

	None if the category is missing. The appraisal is still submitted: it is the
	employee's own self-assessment and it stands on its own. What a missing category
	prevents is it appearing in an approval queue, and pretending otherwise by raising
	a request with no approvers would be worse than saying so.
	"""
	if not frappe.db.exists(
		"Employee Self Service Approval Category", PERFORMANCE_CATEGORY
	):
		return None

	request = frappe.get_doc(
		{
			"doctype": "Employee Self Service Approval Request",
			"request_type": PERFORMANCE_CATEGORY,
			"employee": emp.name,
			"from_self_service": 1,
			"date": today(),
			"subject": f"Self-assessment for {appraisal.appraisal_cycle}",
			"reference": f"Appraisal {appraisal.name}",
			"reason": (appraisal.reflections or "").strip()[:500],
		}
	)
	request.insert(ignore_permissions=True)
	# `compute_approvers` then `action_confirm`, in that order: the first only mutates
	# the document in memory, and the second persists the chain and moves the request
	# out of Draft. A request inserted without them has no approver rows at all, so
	# `may_act` is False for everyone and approving is refused.
	request.compute_approvers()
	request.action_confirm()
	return request.name


@frappe.whitelist()
def act_on_appraisal(request, approve: bool) -> str | None:
	"""Approve or reject the `Appraisal` behind an approval request.

	Approving leaves the submitted appraisal alone: it is already submitted, and
	there is nothing further to post.

	Rejecting **amends** rather than merely cancelling, and the distinction is the whole
	point. `cancel()` alone leaves the document at `docstatus == 2`, which is not an
	editable draft - an employee told to revise something they cannot open is stuck.
	`amend()` cancels the submitted one and returns a fresh draft carrying the same
	goals and the ratings already given, which is what they can revise and resubmit.

	`reference` is a plain Data field, so the name is read back out of it and the
	appraisal must belong to the request's own employee. A reference edited to name
	somebody else's appraisal is refused, not quietly corrected.
	"""
	if request.request_type != PERFORMANCE_CATEGORY:
		return None

	name = (request.reference or "").removeprefix("Appraisal ").strip()
	if not name or not frappe.db.exists("Appraisal", name):
		return None

	appraisal = frappe.get_doc("Appraisal", name)
	if request.employee and appraisal.employee != request.employee:
		frappe.throw(
			"The referenced Appraisal belongs to a different employee than this request."
		)
	if appraisal.docstatus != 1:
		# Already a draft or already cancelled, so it was decided by other means.
		return name

	if approve:
		appraisal.add_comment(
			text=_("Self-assessment approved. No further action is needed on this document.")
		)
		return name

	appraisal.cancel()

	# Frappe has no `Document.amend()`; a copy plus an explicit `amended_from` is what
	# one amounts to. Both are set by hand rather than left to `copy_doc`, which clears
	# `amended_from` and - only outside tests - `docstatus`. Relying on it means the
	# behaviour differs between the suite and the site, which is the worst place for a
	# difference to hide.
	reopened = frappe.copy_doc(appraisal)
	reopened.docstatus = 0
	reopened.amended_from = appraisal.name
	reopened.insert(ignore_permissions=True)
	reopened.add_comment(
		text=_(
			"Self-assessment rejected. This is a new draft you can revise and send again."
		)
	)
	return name