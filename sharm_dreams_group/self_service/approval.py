"""Self-service approval requests: the request-type picker, submission and tracking.

The self-service token is the only authorisation. Employees have no Frappe user, so
everything runs against the Employee bound to the session, and every document is
written with `ignore_permissions`.

The category is a specification, not a template: it says which optional values to
collect and whether each is required, and `_apply_category_fields` is what turns
that into validation.  Doing it here rather than in the doctype means the self-service page
and the desk raise the same messages.
"""

import frappe
from frappe.utils import get_datetime, now_datetime

from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	resolve_session,
)

# The approver chain itself - who may approve, and the fallback when nobody is
# configured - lives on the doctype as
# `EmployeeSelfServiceApprovalRequest.compute_approvers`, so there is exactly one place that
# decides it and it cannot be bypassed by forgetting a second call here.


def _employee(token):
	emp = resolve_session(token)
	return emp, emp.name


def _category_payload(category):
	"""Payload for the self-service page's request-type picker."""
	return {
		"name": category.name,
		"description": category.description or "",
		"has_date": category.has_date,
		"has_period": category.has_period,
		"has_quantity": category.has_quantity,
		"has_amount": category.has_amount,
		"has_reference": category.has_reference,
		"has_partner": category.has_partner,
		"has_location": category.has_location,
		"requirer_document": category.requirer_document,
		"approvers": [
			row.approver_user
			for row in frappe.get_all(
				"Employee Self Service Approval Category Approver",
				filters={"parent": category.name},
				fields=["approver_user", "sequence"],
				order_by="sequence asc",
			)
			if row.approver_user
		],
		"manager_approval": category.manager_approval or "No",
		"minimum_approvals": category.approval_minimum or 0,
	}


def _request_payload(doc):
	return {
		"name": doc.name,
		"subject": doc.subject or "",
		"category": doc.request_type,
		"status": doc.status,
		"from_self_service": bool(doc.from_self_service),
		"create_date": doc.creation,
		"date_confirmed": doc.date_confirmed,
		"date_start": doc.date_start,
		"date_end": doc.date_end,
		"date": doc.date,
		"amount": doc.amount,
		"quantity": doc.quantity,
		"reference": doc.reference,
		"location": doc.location,
		"contact": doc.contact,
		"reason": doc.reason or "",
		"approvers": [
			{
				"user": row.approver_user,
				"status": row.status,
				"required": bool(row.required),
				"sequence": row.sequence,
			}
			for row in doc.approvers
		],
	}


def _parse_datetime(value):
	"""Parse a ``datetime-local`` value (ISO ``T`` separator, optional seconds)."""
	if not value:
		return None
	text = str(value).strip().replace("T", " ")
	try:
		return get_datetime(text)
	except ValueError:
		if text.count(":") == 2:
			return get_datetime(text[:-3])
		raise


@frappe.whitelist()
def approval_categories(token):
	"""Active request types available to the signed-in employee."""
	emp, _name = _employee(token)

	filters = {"active": 1}
	if emp.company:
		filters["company"] = emp.company
	categories = frappe.get_all(
		"Employee Self Service Approval Category",
		filters=filters,
		fields=["name"],
		order_by="sequence asc, name asc",
	)

	# Categories are commonly left company-less (they apply everywhere), and a
	# company-specific filter must not leave an employee with an empty picker.
	if not categories:
		categories = frappe.get_all(
			"Employee Self Service Approval Category",
			filters={"active": 1},
			fields=["name"],
			order_by="sequence asc, name asc",
		)

	docs = [frappe.get_doc("Employee Self Service Approval Category", c.name) for c in categories]
	return {"categories": [_category_payload(c) for c in docs]}


@frappe.whitelist()
def approval_contact_search(token, search_term=None):
	"""Contact autocomplete, for request types with the "Has Contact" flag."""
	_employee(token)
	term = str(search_term or "").strip()
	if not term:
		return {"contacts": []}
	rows = frappe.get_all(
		"Contact",
		filters=[["full_name", "like", f"%{term}%"]],
		fields=["name", "full_name", "email_id"],
		limit_page_length=10,
	)
	return {
		"contacts": [
			{"name": r.name, "full_name": r.full_name or "", "email": r.email_id or ""}
			for r in rows
		]
	}


@frappe.whitelist()
def approval_requests(token):
	"""The signed-in employee's requests submitted through the self-service page."""
	_emp, name = _employee(token)
	docs = frappe.get_all(
		"Employee Self Service Approval Request",
		filters={"employee": name, "from_self_service": 1},
		fields=["name"],
		order_by="creation desc",
		limit_page_length=100,
	)
	return {
		"requests": [
			_request_payload(frappe.get_doc("Employee Self Service Approval Request", d.name)) for d in docs
		]
	}


@frappe.whitelist()
def approval_request_submit(
	token,
	category,
	subject=None,
	reason=None,
	date=None,
	date_start=None,
	date_end=None,
	quantity=None,
	amount=None,
	reference=None,
	location=None,
	contact=None,
):
	"""Create an approval request, resolve its approvers and confirm it."""
	emp, name = _employee(token)

	if not frappe.db.exists("Employee Self Service Approval Category", category):
		frappe.throw("This request type is no longer available.")
	category_doc = frappe.get_doc("Employee Self Service Approval Category", category)

	if not category_doc.active:
		frappe.throw("This request type is no longer available.")

	doc = frappe.get_doc(
		{
			"doctype": "Employee Self Service Approval Request",
			"request_type": category_doc.name,
			"subject": str(subject or "").strip() or category_doc.name,
			"reason": str(reason or "").strip(),
			"employee": name,
			"request_owner": emp.user_id or None,
			"from_self_service": 1,
		}
	)

	_apply_category_fields(
		doc,
		category_doc,
		{
			"date": date,
			"date_start": date_start,
			"date_end": date_end,
			"quantity": quantity,
			"amount": amount,
			"reference": reference,
			"location": location,
			"contact": contact,
		},
	)

	doc.insert(ignore_permissions=True)
	# `compute_approvers` builds the whole chain, fallback included, so there is no
	# second step here to forget.
	doc.compute_approvers()

	# `compute_approvers` only mutates the document in memory; `action_confirm` is
	# what saves the chain.  No commit in between, deliberately: `action_confirm`
	# throws when the category demands a manager or N approvers that do not exist,
	# and committing the insert first would leave an orphaned Draft behind every
	# time an employee's request was rejected for a reason HR has to fix.
	doc.action_confirm()

	# One commit, at the end.  The tablet may lose the network the instant it gets
	# its answer, so the request has to be durable before the response is built
	# rather than merely when the request handler finishes.
	frappe.db.commit()

	return {
		"message": "Your request has been submitted for approval.",
		"request": _request_payload(doc),
	}


def _apply_category_fields(doc, category, values):
	"""Validate and set the optional values the request type asks for.

	Each block is skipped entirely when the request type sets that flag to "No", so
	a request never carries a value the type did not ask for. "Required" is the
	only strictness: "Optional" accepts blank and validates the format if given.
	"""
	if category.has_period != "No":
		start = _parse_datetime(values["date_start"])
		end = _parse_datetime(values["date_end"])
		if category.has_period == "Required" and not (start and end):
			frappe.throw("Please provide the start and end date.")
		if start and end and start > end:
			frappe.throw("The start date must precede the end date.")
		doc.date_start = start
		doc.date_end = end
	elif category.has_date != "No":
		value = _parse_datetime(values["date"])
		if category.has_date == "Required" and not value:
			frappe.throw("Please provide a date.")
		doc.date = value

	if category.has_quantity != "No":
		value = frappe.utils.flt(values["quantity"])
		if category.has_quantity == "Required" and not value:
			frappe.throw("Please provide a quantity.")
		doc.quantity = value

	if category.has_amount != "No":
		value = frappe.utils.flt(values["amount"])
		if category.has_amount == "Required" and not value:
			frappe.throw("Please provide an amount.")
		doc.amount = value

	if category.has_reference != "No":
		value = str(values["reference"] or "").strip()
		if category.has_reference == "Required" and not value:
			frappe.throw("Please provide a reference.")
		doc.reference = value

	if category.has_location != "No":
		value = str(values["location"] or "").strip()
		if category.has_location == "Required" and not value:
			frappe.throw("Please provide a location.")
		doc.location = value

	if category.has_partner != "No":
		value = values["contact"]
		if category.has_partner == "Required" and not value:
			frappe.throw("Please select a contact.")
		if value:
			doc.contact = value

	# The self-service cannot upload files, so this is only ever satisfied by a request
	# raised in the desk - which is the point of checking it here rather than
	# trusting the category's flag.
	if category.requirer_document == "Required" and not doc.get("attachments"):
		frappe.throw("Please attach the requested document.")