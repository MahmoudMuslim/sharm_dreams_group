# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Routes for the employee portal.

`/employee-portal` is the page and `/employee-portal-api` is the JSON endpoint it
talks to. Both are served by `page_renderer` entries in `hooks.py` rather than by
Frappe's `/api/method`, for the reason the old page documents: on a bench running
`press` the API host is separated from the site host, and a cross-origin POST from a
wall tablet is refused. A same-origin page renderer has no such problem, and needs no
CORS headers and no CSRF exemption.

POST only, and every action is looked up in `ACTIONS` - a whitelist, so an action
missing from this map is unreachable over HTTP however it was reached in Python.
"""

from __future__ import annotations

import inspect
import re

import frappe
from frappe import _
from frappe.utils.response import build_response

from sharm_dreams_group.portal import (
	attendance,
	expenses,
	notifications,
	performance,
	portal,
)
from sharm_dreams_group.self_service import webauthn

PAGE_ROUTE = "employee-portal"
API_ROUTE = f"{PAGE_ROUTE}-api"

# Frappe's own parameters, which are not action arguments.
RESERVED_KEYS = ("action", "csrf_token", "cmd")

ACTIONS = {
	# session
	"sign_in": portal.sign_in,
	"sign_out": portal.sign_out,
	"my_profile": portal.my_profile,
	# details
	"my_details": portal.my_details,
	"update_my_details": portal.update_my_details,
	# documents
	"my_documents": portal.my_documents,
	# announcements
	"my_announcements": portal.my_announcements,
	# directory
	"my_colleagues": portal.my_colleagues,
	# org chart
	"my_org_chart": portal.my_org_chart,
	# leave calendar
	"my_leave_calendar": portal.my_leave_calendar,
	# timesheets & overtime
	"my_timesheets": portal.my_timesheets,
	"submit_timesheet": portal.submit_timesheet,
	"activity_types": portal.activity_types,
	"my_overtime": portal.my_overtime,
	"submit_overtime": portal.submit_overtime,
	# shift swaps
	"my_shift_swaps": portal.my_shift_swaps,
	"request_shift_swap": portal.request_shift_swap,
	"approve_shift_swap": portal.approve_shift_swap,
	"reject_shift_swap": portal.reject_shift_swap,
	# benefits
	"my_benefits": portal.my_benefits,
	# learning
	"my_learning": portal.my_learning,
	# assets
	"my_assets": portal.my_assets,
	"request_asset": portal.request_asset,
	"asset_categories": portal.asset_categories,
	# travel
	"my_travel": portal.my_travel,
	"request_travel": portal.request_travel,
	"travel_modes": portal.travel_modes,
	# loans
	"my_loans": portal.my_loans,
	"request_loan": portal.request_loan,
	"loan_types": portal.loan_types,
	# wellness
	"my_wellness": portal.my_wellness,
	# recognition
	"my_recognition": portal.my_recognition,
	"send_recognition": portal.send_recognition,
	"recognition_types": portal.recognition_types,
	# support
	"my_support": portal.my_support,
	# biometric sign-in
	#
	# The self-service page's tested two-step WebAuthn flow, reused as-is rather than
	# reimplemented beside it. `webauthn_login` decides between first-time enrolment
	# (attest) and an existing device (assert) once National ID and Employee Number
	# match, and `webauthn_verify` is the only thing that mints a session. Shared on
	# purpose: the credentials and pre-auth rows are the same DocTypes the self-service
	# page uses, so a device enrolled on one page signs in on the other.
	"webauthn_login": webauthn.webauthn_login,
	"webauthn_verify": webauthn.webauthn_verify,
	# balances and time off
	"my_balances": portal.my_balances,
	"leave_types": portal.leave_types,
	"my_leaves": portal.my_leaves,
	"my_holidays": portal.my_holidays,
	"my_notifications": notifications.my_notifications,
	"request_leave": portal.request_leave,
	"cancel_leave": portal.cancel_leave,
	# payroll
	"my_payslips": portal.my_payslips,
	"my_payslip": portal.my_payslip,
	"payslip_pdf": portal.payslip_pdf,
	# attendance
	"my_shifts": attendance.my_shifts,
	"my_attendance": attendance.my_attendance,
	"punch": attendance.punch,
	# compensatory approvals (HR/manager only)
	"my_compensatory_proposals": portal.my_compensatory_proposals,
	"approve_compensatory_proposal": portal.approve_compensatory_proposal,
	"reject_compensatory_proposal": portal.reject_compensatory_proposal,
	# performance
	"my_performance": performance.my_performance,
	"save_self_appraisal": performance.save_self_appraisal,
	"submit_appraisal": performance.submit_appraisal,
	# expenses
	"my_expense_claims": expenses.my_expense_claims,
	"expense_claim_types": expenses.expense_claim_types,
	"my_expense_claim": expenses.my_expense_claim,
	"file_expense_claim": expenses.file_expense_claim,
	"withdraw_expense_claim": expenses.withdraw_expense_claim,
}


def _error_message(exc: Exception) -> str:
	"""The most recent message Frappe recorded for this request.

	`frappe.throw` puts a human sentence in the log and the exception carries only a
	code, so reading the log back is the only way to show the employee *why* a request
	was refused rather than a bare "Unknown error".
	"""
	for entry in reversed(frappe.local.message_log or []):
		message = entry.get("message") if isinstance(entry, dict) else entry
		if message:
			return re.sub(r"<[^>]+>", "", str(message)).strip()
	return str(exc)


def _typed_args(handler, args: dict):
	"""Coerce JSON values to the parameter types the handler declared.

	Returns the coerced dict, or an `{"error": ...}` dict. Without the coercion a
	numeric field arrives as a string and `from_date > to_date` compares lexically -
	a bug that passes right up until someone tries a particular pair of dates.
	"""
	try:
		signature = inspect.signature(handler)
	except (TypeError, ValueError):
		return dict(args)

	coerced = {}
	for key, value in args.items():
		param = signature.parameters.get(key)
		if param is None:
			# Not a parameter of this action, refused rather than passed on, so a
			# caller cannot smuggle an extra argument past the signature.
			return {"error": _("Unexpected argument: {0}").format(key)}
		annotation = str(param.annotation)
		if annotation.startswith("bool"):
			coerced[key] = bool(value)
		elif annotation.startswith("int") or annotation.startswith("float"):
			try:
				coerced[key] = float(value)
			except (TypeError, ValueError):
				return {"error": _("{0} must be a number").format(key)}
		elif annotation.startswith("str"):
			coerced[key] = "" if value is None else str(value)
		else:
			coerced[key] = value
	return coerced


class PortalApiRenderer:
	"""The JSON endpoint behind the portal page."""

	def __init__(self, endpoint=None, http_status_code=None):
		# Frappe's `path_resolver` constructs every renderer as
		# `renderer(endpoint, self.http_status_code)`, so the signature has to take
		# both positionally. A renderer written as `__init__(self, path=None)` raises
		# `TypeError: takes from 1 to 2 positional arguments but 3 were given` on
		# *every* page, not just this one - which is how it showed up as a bare
		# `500: There was an error building this page` at the route.
		#
		# `frappe.local.request` *raises* `AttributeError` when there is no request
		# rather than returning None - `frappe.local` overrides `__getattribute__` to
		# refuse unset keys - so it is reached through `getattr`, never through a
		# truthiness test. Reading it unguarded breaks
		# `frappe.get_print(..., as_pdf=True)`, which resolves the `printview`
		# endpoint through this same resolver with no request in scope; that is every
		# PDF the site generates, from a background job or a bench command as well
		# as from a browser.
		#
		# `endpoint` is preferred because it is the one the resolver always passes.
		request = getattr(frappe.local, "request", None)
		self.endpoint = endpoint
		self.http_status_code = http_status_code
		self.path = endpoint or (request.path if request else "")

	def can_render(self):
		return bool(self.path) and self.path.endswith(API_ROUTE)

	def render(self):
		frappe.local.response["type"] = "json"
		frappe.local.response["allow_cache"] = False

		if not getattr(frappe.local, "request", None):
			# Reached only if something resolves this endpoint directly rather than
			# through the path resolver. Answering with a sentence beats raising
			# `AttributeError: request` at a caller that is trying to render a PDF.
			return self._respond(_("This endpoint needs an HTTP request."), 400)

		if frappe.request.method != "POST":
			return self._respond(_("This endpoint accepts POST only."), 405)

		payload = frappe.request.get_json(silent=True) or frappe.form_dict
		action = payload.get("action")
		handler = ACTIONS.get(action)
		if handler is None:
			# Unknown action refused rather than guessed at, and the list of real ones
			# is not echoed: that helps a developer and an attacker equally.
			return self._respond(_("Unknown portal action: {0}").format(action), 400)

		typed = _typed_args(handler, {k: v for k, v in payload.items() if k not in RESERVED_KEYS})
		if len(typed) == 1 and "error" in typed:
			return self._respond(typed["error"], 400)

		try:
			message = handler(**typed)
		except Exception as exc:
			# Rolled back rather than committed: a refused request must not leave half
			# a leave application behind for the employee to discover as a duplicate.
			frappe.db.rollback()
			return self._respond(_error_message(exc), 400)

		frappe.local.response["message"] = message
		return build_response("json")

	def _respond(self, error, status_code):
		"""Answer with `{"error": ...}` at `status_code`.

		A renderer must return a `Response`, not `None`: `serve.get_response` hands
		the return value straight to werkzeug, which calls it. Returning `None`
		raises `TypeError: 'NoneType' object is not callable` from inside werkzeug's
		request wrapper - so the traceback points at werkzeug and not at the line that
		caused it.
		"""
		frappe.local.response["http_status_code"] = status_code
		frappe.local.response["message"] = {"error": error}
		return build_response("json")


class PortalLangRenderer:
	"""The language switcher endpoint behind the portal page."""

	def __init__(self, endpoint=None, http_status_code=None):
		request = getattr(frappe.local, "request", None)
		self.endpoint = endpoint
		self.http_status_code = http_status_code
		self.path = endpoint or (request.path if request else "")

	def can_render(self):
		return bool(self.path) and self.path.endswith(f"{PAGE_ROUTE}/setlang")

	def render(self):
		frappe.local.response["type"] = "json"
		frappe.local.response["allow_cache"] = False

		if not getattr(frappe.local, "request", None):
			return self._respond(_("This endpoint needs an HTTP request."), 400)

		if frappe.request.method != "POST":
			return self._respond(_("This endpoint accepts POST only."), 405)

		payload = frappe.request.get_json(silent=True) or frappe.form_dict
		lang = payload.get("lang")
		if not lang:
			return self._respond(_("Language not specified."), 400)

		# Set the language cookie
		frappe.local.response["set_cookie"] = {
			"key": "employee_portal_lang",
			"value": lang,
			"max_age": 365 * 24 * 60 * 60,
			"httponly": True,
			"secure": frappe.conf.get("force_ssl", False),
			"samesite": "Lax",
		}

		frappe.local.response["message"] = {"ok": True, "lang": lang}
		return build_response("json")

	def _respond(self, error, status_code):
		frappe.local.response["http_status_code"] = status_code
		frappe.local.response["message"] = {"error": error}
		return build_response("json")