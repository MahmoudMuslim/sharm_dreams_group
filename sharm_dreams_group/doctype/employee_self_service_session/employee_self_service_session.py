# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The self-service's authorisation primitive.

A wall tablet is not a Frappe login and the employees using it have no user
account, so the whole session is one opaque token row.  Everything the self-service page can
do is derived from the Employee this row binds, which is why `resolve_session`
returns the Employee document rather than a user id.

`create_session` and `resolve_session` are the only two entry points and every
self-service API module goes through them, so the "is this token still good?" rule lives
in exactly one place.
"""

import secrets
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

# A shift, plus slack: a wall tablet is signed in once and then used all day.
SESSION_TTL_HOURS = 8


class EmployeeSelfServiceSession(Document):
	pass


def create_session(employee):
	"""Mint a short-lived self-service token for an employee."""
	session = frappe.get_doc(
		{
			"doctype": "Employee Self Service Session",
			"employee": employee,
			"access_token": secrets.token_urlsafe(32),
			"expiry": now_datetime() + timedelta(hours=SESSION_TTL_HOURS),
		}
	)
	session.insert(ignore_permissions=True)
	return session


def resolve_session(token):
	"""Return the Employee doc bound to a valid, un-expired token.

	Expiry is part of the filter rather than a comparison afterwards, so an
	expired token never loads a document at all.
	"""
	if not token:
		frappe.throw("Missing self-service session token.")

	session = frappe.db.get_value(
		"Employee Self Service Session",
		{"access_token": token, "expiry": (">", now_datetime())},
		["employee", "expiry"],
		as_dict=True,
	)
	if not session:
		frappe.throw("Self-service session expired or invalid. Please sign in again.")

	return frappe.get_doc("Employee", session.employee)