# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Expenses: what this employee has claimed, and filing a new claim.

Separate from `portal.portal` because it is a different document family with its
own rules, and because one module per area keeps each readable. What it shares -
the session token, and therefore who is asking - is unchanged.

Three facts about this site shape everything here.

**`Expense Item` is not installed.** In older HRMS a claim line carried an
`Expense Item`, a stock-like master. In this version the line's `expense_type`
links to `Expense Claim Type` instead, and the account lives on a child row of the
claim type, one per company. So the employee picks a category - Travel, Food,
Medical, Calls, Others - not an item.

**The account comes from the claim type, per company.** Every one of the five types
has an `Expense Claims - <company>` account mapped for each of the group companies.
A type with no mapping for the employee's own company is therefore not merely
unconfigured, it would post to nothing, so `expense_claim_types` withholds it and
says why.

**Approval happens before submission, not after.** `ExpenseClaim.on_submit` throws
unless `approval_status` is Approved or Rejected - submitting an expense is what
moves money, and HRMS will not do it on an unapproved claim. HRMS also never sets
`expense_approver`: on a site with no Workflow on `Expense Claim` and no
`Employee Leave Approver`, nothing populates it. So a claim filed here stays a
*draft* with an approval request raised against it, and the approval action on
that request is what approves and submits. Nothing in this module submits a claim
that nobody has approved.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from sharm_dreams_group.portal.portal import resolve_session

__all__ = [
	"expense_claim_types",
	"file_expense_claim",
	"my_expense_claim",
	"my_expense_claims",
	"withdraw_expense_claim",
]

# The approval request type this area files against. Created by
# `seeder.ensure_approval_categories`, and named here rather than discovered so a
# missing category is a loud failure at file time rather than a claim that quietly
# has nobody to approve it.
EXPENSE_CATEGORY = "Expenses"

# A claim still in Draft belongs to the employee and can be withdrawn. Once approved
# and submitted it is a financial document.
# A draft is withdrawable. `Rejected` is deliberately *not* in this list even though
# the claim is never submitted: `approval_status` is set to Rejected by the approval
# action and the claim keeps `status` Draft, so keying on `status` alone would let an
# employee delete a claim HR has already turned down - destroying the record of the
# decision and the receipt that went with it.
WITHDRAWABLE_STATUSES = ("Draft",)


def resolve_cost_center(company: str) -> str | None:
	"""The cost centre this company's expenses are booked to.

	`validate_account_details` refuses to book an expense claim whose lines have no
	cost centre, so this is neither optional nor something to leave to the employee.

	Read from `Company.cost_center`, which is ERPNext's own answer to the question and
	is set on all 14 companies here. The single-leaf lookup that would otherwise be
	needed is deliberately not used: a company that later grows a second cost centre
	would silently start booking employee expenses to whichever sorted first, and the
	only symptom would be a cost report that does not add up.
	"""
	cost_center = frappe.db.get_value("Company", company, "cost_center")
	if not cost_center or not frappe.db.exists("Cost Center", cost_center):
		return None
	return cost_center


def resolve_payable_account(company: str) -> str | None:
	"""The account the reimbursement is owed on.

	HRMS fills this in only when a claim is raised *from* an Employee Advance
	(`get_expense_claim`), so a claim created any other way arrives with none, and
	`make_gl_entries` then fails with a bare "Account is required" naming no field.
	The company's own default is used, with the same payroll fallback the desk uses.
	"""
	account = frappe.db.get_value("Company", company, "default_expense_claim_payable_account")
	if not account:
		account = frappe.db.get_value("Company", company, "default_payroll_payable_account")
	if not account or not frappe.db.exists("Account", account):
		return None
	return account


def _claims_for(employee: str) -> list:
	return frappe.get_all(
		"Expense Claim",
		filters={"employee": employee, "docstatus": ["<", 2]},
		fields=[
			"name",
			"employee_name",
			"posting_date",
			"status",
			"approval_status",
			"total_claimed_amount",
			"total_sanctioned_amount",
			"total_amount_reimbursed",
			"currency",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=50,
	)


@frappe.whitelist()
def my_expense_claims(token: str) -> dict:
	"""This employee's expense claims, newest first."""
	emp = resolve_session(token)
	return {"claims": _claims_for(emp.name)}


@frappe.whitelist()
def expense_claim_types(token: str) -> dict:
	"""Categories this employee may claim against, and why any are withheld.

	The account is resolved per company here rather than in the client, because it is
	the server's job to decide whether a category is usable: a claim type with no
	account for this employee's company would be accepted on submit and post nothing.
	"""
	emp = resolve_session(token)
	rows = frappe.get_all(
		"Expense Claim Type",
		fields=["name", "expense_type"],
		order_by="expense_type asc",
		limit_page_length=0,
	)

	# One read for every mapping rather than one per type.
	accounts = {}
	for row in frappe.get_all(
		"Expense Claim Account",
		filters={"company": emp.company},
		fields=["parent", "default_account"],
		limit_page_length=0,
	):
		accounts.setdefault(row.parent, row.default_account)

	out = []
	for row in rows:
		account = accounts.get(row.name)
		out.append(
			{
				"name": row.name,
				"label": row.expense_type or row.name,
				# Sent so the claim can be written without a second lookup, and so the
				# employee can see where the money is booked rather than only how much.
				"account": account,
				# Withheld from the client's picker, and reported here so the screen can
				# explain the gap instead of leaving the employee to wonder why a
				# category they saw on another device is missing.
				"usable": bool(account),
			}
		)
	return {"company": emp.company, "expense_types": out}


@frappe.whitelist()
def my_expense_claim(token: str, name: str) -> dict:
	"""One claim with its line items and any attached receipts."""
	emp = resolve_session(token)
	# Existence checked before the document is loaded, so a name that is not a claim is
	# refused by one sentence rather than by whatever Frappe's link validation says -
	# and so the two refusals below have a single shape.
	if not frappe.db.exists("Expense Claim", name):
		frappe.throw(_("Expense claim not found."))
	claim = frappe.get_doc("Expense Claim", name)
	if claim.employee != emp.name:
		frappe.throw(_("Expense claim not found."))

	items = []
	for row in claim.expenses or []:
		items.append(
			{
				"expense_type": row.expense_type,
				"expense_date": row.expense_date,
				"description": row.description,
				"amount": flt(row.amount),
				"sanctioned_amount": flt(row.sanctioned_amount),
				"account": row.default_account,
			}
		)

	# Receipts are the evidence for the claim, so their absence or presence changes
	# what an approver can do; it is reported rather than left to be discovered.
	receipts = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Expense Claim", "attached_to_name": claim.name},
		fields=["name", "file_name", "file_url", "file_size"],
		limit_page_length=20,
	)

	return {
		"name": claim.name,
		# `Expense Claim` has no `title` field in this version; `employee_name` is the
		# only human label it carries. Read rather than invented, so nothing here
		# claims a description the document does not have.
		"employee_name": claim.employee_name,
		"posting_date": claim.posting_date,
		"status": claim.status,
		"approval_status": claim.approval_status,
		"currency": claim.currency,
		"total_claimed_amount": flt(claim.total_claimed_amount),
		"total_sanctioned_amount": flt(claim.total_sanctioned_amount),
		"total_amount_reimbursed": flt(claim.total_amount_reimbursed),
		"docstatus": claim.docstatus,
		# Both conditions, not just the status: `approval_status` is what records that
		# a person has decided. Keying on `status` alone would let the employee delete
		# a claim HR has already turned down - which keeps `status` Draft - taking the
		# decision and its receipts with it.
		"withdrawable": _is_withdrawable(claim),
		"items": items,
		"receipts": receipts,
	}


def _is_withdrawable(claim) -> bool:
	"""Whether this employee may still withdraw the claim.

	Three conditions, all of which have to hold. `docstatus` because a submitted claim
	is a financial document. `status` for the same reason in the weaker form.
	`approval_status` because that, not `status`, is what records that a person has
	decided: a rejected claim still reads `status` Draft, so a check on `status`
	alone would let the employee delete HR's decision and its evidence.
	"""
	return (
		claim.docstatus == 0
		and claim.status in WITHDRAWABLE_STATUSES
		and claim.approval_status == "Draft"
	)


@frappe.whitelist()
def file_expense_claim(
	token: str,
	items: list,
	title: str | None = None,
	posting_date: str | None = None,
) -> dict:
	"""Raise a claim as a draft, with an approval request against it.

	The claim is deliberately left unsubmitted. HRMS refuses to submit a claim whose
	`approval_status` is Draft, and submitting is what posts the expense to the
	accounts - so the claim waits for the approval action on the request raised
	here.

	`items` is a list of dicts: `expense_type`, `amount`, `expense_date`,
	`description`. Validated here rather than trusted, because the account for each
	line is the server's to resolve.
	"""
	emp = resolve_session(token)
	if not items:
		frappe.throw(_("Add at least one expense line."))

	posting_date = posting_date or today()
	if getdate(posting_date) > getdate():
		frappe.throw(_("The posting date cannot be in the future."))

	accounts = {
		row.parent: row.default_account
		for row in frappe.get_all(
			"Expense Claim Account",
			filters={"company": emp.company},
			fields=["parent", "default_account"],
			limit_page_length=0,
		)
	}

	# Both resolved before the lines are built, because without them the claim is
	# accepted here and then fails at submit with messages that name no field the
	# employee can see: "Row 1: Cost Center is required..." and a bare "Account is
	# required". Refused here instead, with the reason and who to ask.
	cost_center = resolve_cost_center(emp.company)
	if not cost_center:
		frappe.throw(
			_("No cost centre is set up for {0}, so expenses cannot be booked. "
			  "Please contact HR.").format(emp.company)
		)
	payable_account = resolve_payable_account(emp.company)
	if not payable_account:
		frappe.throw(
			_("No expense payable account is set up for {0}. Please contact HR.").format(
				emp.company
			)
		)

	expenses = []
	for index, raw in enumerate(items, start=1):
		expense_type = (raw or {}).get("expense_type") or ""
		amount = flt((raw or {}).get("amount"))
		if not expense_type:
			frappe.throw(_("Line {0}: choose an expense category.").format(index))
		if amount <= 0:
			frappe.throw(_("Line {0}: the amount must be more than zero.").format(index))
		account = accounts.get(expense_type)
		if not account:
			# Not "unavailable at the moment" but genuinely wrong: this category has no
			# expense account for this employee's company, so the claim would post to
			# nothing and the money would be neither paid nor accounted for.
			frappe.throw(
				_("Line {0}: {1} has no expense account for {2}.").format(
					index, expense_type, emp.company
				)
			)
		expense_date = (raw or {}).get("expense_date") or posting_date
		if getdate(expense_date) > getdate(posting_date):
			frappe.throw(
				_("Line {0}: the expense date cannot be after the posting date.").format(index)
			)
		expenses.append(
			{
				"expense_type": expense_type,
				"default_account": account,
				"cost_center": cost_center,
				"amount": amount,
				"expense_date": expense_date,
				"description": (raw or {}).get("description") or "",
			}
		)

	previous = frappe.session.user
	# As with a leave application, the insert runs as the employee's own user where
	# they have one. The token already established who is asking.
	frappe.set_user(emp.user_id or "Administrator")
	try:
		claim = frappe.get_doc(
			{
				"doctype": "Expense Claim",
				"employee": emp.name,
				"company": emp.company,
				"posting_date": posting_date,
				# `title` is not a field on `Expense Claim` in this version, so the
				# employee's wording lives on the approval request's `subject`, where
				# the approver actually reads it, rather than being written to a field
				# that does not exist and silently dropped.
				"expenses": expenses,
				# Both of these are set here because nothing else does. HRMS fills them
				# in only when a claim is raised from an Employee Advance; a claim the
				# portal creates has neither, and `make_gl_entries` fails on submit with
				# "Account is required" - a message that names no field the employee can
				# see and means nothing on a tablet.
				"payable_account": payable_account,
				"cost_center": cost_center,
				# Nothing in HRMS sets this on a site with no Workflow and no
				# `Employee Leave Approver`; the approval request below is the record of
				# who is deciding, and the approve action fills this in from it.
				"expense_approver": _expense_approver_user(),
			}
		)
		claim.insert(ignore_permissions=True)
		request = _raise_approval_request(emp, claim, title)
	finally:
		frappe.set_user(previous)

	return {
		"name": claim.name,
		"total_claimed_amount": flt(claim.total_claimed_amount),
		"currency": claim.currency,
		"status": claim.status,
		"approval_request": request,
	}


def _expense_approver_user() -> str | None:
	"""The user expenses go to, or None.

	Read from the approval category rather than assumed, so the person who approves
	is the one HR configured. None is a legitimate answer: the approval request
	falls back to an HR Manager or System Manager, and that is recorded rather than
	guessed at here.
	"""
	if not frappe.db.exists("Employee Self Service Approval Category", EXPENSE_CATEGORY):
		return None
	approvers = frappe.get_all(
		"Employee Self Service Approval Category Approver",
		filters={"parent": EXPENSE_CATEGORY},
		pluck="approver_user",
		limit_page_length=1,
	)
	return approvers[0] if approvers else None


def _raise_approval_request(emp, claim, title: str | None = None) -> str | None:
	"""The `Employee Self Service Approval Request` this claim waits on.

	None if the category is missing. The claim is still created: it is a draft
	nobody has approved, it appears in the employee's own list, and it can be
	withdrawn. What it does not do is pretend to be in a queue - which is exactly
	what a request with no approvers would look like.
	"""
	if not frappe.db.exists("Employee Self Service Approval Category", EXPENSE_CATEGORY):
		return None
	request = frappe.get_doc(
		{
			"doctype": "Employee Self Service Approval Request",
			"request_type": EXPENSE_CATEGORY,
			"employee": emp.name,
			"from_self_service": 1,
			"date": today(),
			# The employee's own wording, or a date an approver can act on. The
			# posting date is interpolated rather than passed through `format_date`,
			# because this file quotes the string as `claim.name`-style text and a
			# second opinion about its format helps nobody.
			"subject": (title or "").strip() or ("Expenses for " + claim.posting_date),
			# `reference` is a plain Data field on this doctype, not a Link, so the
			# claim is named rather than validated. `act_on_expense_claim` re-reads
			# the reference and refuses if it is not a claim this employee owns.
			"reference": "Expense Claim " + claim.name,
			"amount": flt(claim.total_claimed_amount),
			"reason": " ".join(
				f"{row.expense_type}: {flt(row.amount):.2f}"
				for row in (claim.expenses or [])
			),
		}
	)
	request.insert(ignore_permissions=True)

	# Two steps, both required, in this order.
	#
	# `compute_approvers` builds the chain from the category. It only mutates the
	# document in memory - a request inserted without it has *no approver rows at
	# all*, so `may_act` returns False for everyone including the person the category
	# names, and approving is refused with "You are not an approver on this request."
	# That is the state a plain insert leaves behind.
	#
	# `action_confirm` is what persists the chain and moves the request out of Draft.
	# It also throws when the category demands approvers that do not exist, which is
	# why the claim insert and the request are not committed separately: a commit
	# between them would leave a claim nobody can approve.
	request.compute_approvers()
	request.action_confirm()
	return request.name


@frappe.whitelist()
def withdraw_expense_claim(token: str, name: str) -> dict:
	"""Withdraw a claim that has not been approved.

	Deleted rather than cancelled where it is still a draft: nothing has been posted
	and nothing has been decided, so keeping the row would only make the employee's
	own list longer with a claim they already withdrew.
	"""
	emp = resolve_session(token)
	if not name or not frappe.db.exists("Expense Claim", name):
		frappe.throw(_("Expense claim not found."))
	claim = frappe.get_doc("Expense Claim", name)
	# The same sentence as for "not found", so the answer does not confirm that
	# somebody else's claim exists.
	if claim.employee != emp.name:
		frappe.throw(_("Expense claim not found."))
	if not _is_withdrawable(claim):
		frappe.throw(
			_("Only a claim that has not been approved can be withdrawn. Please contact HR.")
		)

	previous = frappe.session.user
	frappe.set_user(emp.user_id or "Administrator")
	try:
		# The approval request goes with the claim. Leaving it behind would leave a
		# Pending request naming a claim that no longer exists, which is precisely the
		# state that looks like work in progress and is not - and the approver would
		# then be asked to approve something invisible.
		for request in frappe.get_all(
			"Employee Self Service Approval Request",
			filters={"reference": "Expense Claim " + claim.name},
			pluck="name",
			limit_page_length=0,
		):
			frappe.delete_doc(
				"Employee Self Service Approval Request", request,
				force=True, ignore_permissions=True,
			)
		frappe.delete_doc("Expense Claim", claim.name, force=True, ignore_permissions=True)
	finally:
		frappe.set_user(previous)
	return {"ok": True}
