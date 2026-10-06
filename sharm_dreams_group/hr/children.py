# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Children and the company school fee each one attracts.

Ported from the Odoo `hr.employment.children` / `hr.employment.school.fee` pair
in `/home/mahmoud/hr_managing/models`. The shape is kept deliberately:

*   one child per row, because a child in primary and one at university are not
    the same benefit, and a single "children count x fee" figure cannot say which
    is which;
*   the fee itself is a master record rather than a number on the row, so
    changing the company's fee is one edit rather than one per child.

The one thing deliberately *not* ported is Odoo's constraint that the number of
child rows must exactly equal Employee's `children` count. The equivalent field
here is `sdg_dependants_count`, and dependants there includes the spouse - so the
rule would reject a perfectly correct record. It is left out rather than ported
wrong, and is the one thing worth confirming with HR before it is added.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

TABLE_FIELD = "sdg_children"
COUNT_FIELD = "sdg_children_count"
TOTAL_FIELD = "sdg_school_fee_total"
ELIGIBLE_FIELD = "sdg_school_fee_children"

DERIVED_FIELDS = (COUNT_FIELD, TOTAL_FIELD, ELIGIBLE_FIELD)


def eligible_children(children) -> list:
	"""The child rows that actually attract a fee.

	Eligibility is the employee's answer and the fee is the company's, so a row
	with no fee is not costed - otherwise an eligible child awaiting a fee record
	would report a total that is quietly wrong rather than visibly incomplete.
	"""
	return [c for c in children or [] if c.eligible_for_school_fee and c.school_fee]


def totals(children) -> tuple[int, int, float]:
	"""`(children, eligible, monthly total)` for one employee.

	The fee amount is read from the master rather than taken from the row,
	because the row holds only a link. A child pointing at a fee that has since
	been deleted contributes nothing rather than raising on every Employee save.
	"""
	children = list(children or [])
	eligible = eligible_children(children)

	fees: dict[str, float] = {}
	if eligible:
		names = list({c.school_fee for c in eligible})
		# Read row.name explicitly: `dict(list_of_dicts)` keys by list index, so it
		# would build {0: {...}, 1: {...}} and every lookup would miss.
		fees = {
			row.name: row.amount
			for row in frappe.get_all(
				"SDG School Fee",
				filters={"name": ("in", names)},
				fields=["name", "amount"],
				limit_page_length=0,
			)
		}

	total = sum(flt(fees.get(c.school_fee)) for c in eligible)
	return len(children), len(eligible), total


def apply_to_employee(doc, today=None) -> None:
	"""Recompute the derived children fields on an Employee.

	Guarded on the fields existing, matching the penalty hook, so the module is
	safe on a site where the custom fields have not been created yet.
	"""
	fields = {f.fieldname for f in doc.meta.fields}
	children = doc.get(TABLE_FIELD) or []

	count, eligible, total = totals(children)

	if COUNT_FIELD in fields:
		doc.set(COUNT_FIELD, count)
	if ELIGIBLE_FIELD in fields:
		doc.set(ELIGIBLE_FIELD, eligible)
	if TOTAL_FIELD in fields:
		doc.set(TOTAL_FIELD, total)


def validate_rows(children) -> None:
	"""The row rules, applied from the Employee hook.

	`SDGChildren.validate` states the same thing and is not enough on its own:
	a child doctype's `validate` does not run when the parent is saved, because
	`Document.update_child_table` writes the rows with `db_update()`. Checking
	here is what enforces it on the form people actually use.
	"""
	for child in children or []:
		if child.age is not None and child.age < 0:
			frappe.throw(
				frappe._("{0} is not a valid age for {1}.").format(child.age, child.child_name),
				title=frappe._("Check the age"),
			)
		if child.age is not None and child.age > 100:
			frappe.throw(
				frappe._("An age of {0} looks like a typo for {1}.").format(child.age, child.child_name),
				title=frappe._("Check the age"),
			)


def validate_employee(doc, method=None) -> None:
	"""The Employee hook."""
	validate_rows(doc.get(TABLE_FIELD) or [])
	apply_to_employee(doc)


def refresh_fee_counts() -> None:
	"""Recount every school fee.

	Run after the employee changes rather than on the fee's own save, because the
	rows that decide the counts live on the Employee - editing a child from the
	employee form never touches the fee record, so a count maintained only by the
	fee would be stale from the moment it was first right.
	"""
	for name in frappe.get_all("SDG School Fee", pluck="name", limit_page_length=0):
		frappe.get_doc("SDG School Fee", name).refresh_counts()
