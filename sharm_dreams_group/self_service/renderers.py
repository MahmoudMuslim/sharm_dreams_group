"""The website routes the self-service page talks to.

`/self-services-employees-api` is the action endpoint and
`/self-services-employees/setlang` switches language. Both exist as page renderers
rather than `/api/method` calls for the same reason: on a bench running the `press`
app, its auth hook rejects every `/api/` path for a non-system user, and a guest
self-service must not depend on the API auth layer at all. Requests are still authorised -
by the short-lived Employee Self Service Session token minted at sign-in - so nothing is opened up
by moving off `/api/`.

The routes are named constants, and `PAGE_ROUTE` is the single place the URL is
written down. It also fixes the two `www/` filenames, because Frappe derives a
page's route from the template and replaces hyphens with underscores for the
controller module name - see `template_page.set_pymodule`. A route constant that
drifted from the filenames would serve a 404 from the desk with nothing logged,
so `TestWiring` checks the two against each other.
"""

import re

import frappe
from frappe.utils.response import build_response
from frappe.website.utils import build_response as build_website_response

from sharm_dreams_group.self_service import api, approval, time_off, webauthn
from sharm_dreams_group.www.self_services_employees import (
	LANG_COOKIE_NAME,
	LANG_COOKIE_MAX_AGE,
	installed_languages,
	wants_html,
)

# The URL the employee opens, and the two routes hung off it.
#
# `PAGE_ROUTE` is deliberately not the word "self-service": the desk calls this surface
# "Self Services Employees" and the address an employee bookmarks should agree
# with it. The DocTypes keep their `Employee Self Service *` names and the `self_service/` package keeps
# its name - this is the address, not a rename of the feature.
PAGE_ROUTE = "self-services-employees"
API_ROUTE = f"{PAGE_ROUTE}-api"
LANG_ROUTE = f"{PAGE_ROUTE}/setlang"
RESET_LANG_ROUTE = f"{PAGE_ROUTE}/resetlang"

# Every guest-callable self-service action, keyed by the `action` the browser posts.
#
# This map is the front door: an action missing from it is unreachable over HTTP
# even though the function behind it works perfectly. Keep it in sync with the
# `@frappe.whitelist()` functions of the modules above.
ACTIONS = {
	# attendance and profile
	"sign_in": api.sign_in,
	"sign_out": api.sign_out,
	"my_profile": api.my_profile,
	"punch": api.punch,
	"payslips": api.payslips,
	"payslip": api.payslip,
	# time off
	"leave_types": api.leave_types,
	"request_leave": api.request_leave,
	"my_leaves": api.my_leaves,
	"leave_balances": time_off.leave_balances,
	"cancel_leave": time_off.cancel_leave,
	# The badge count on the dashboard tile, and the list behind it.
	"approvals": api.approvals,
	# biometric sign-in
	"webauthn_login": webauthn.webauthn_login,
	"webauthn_verify": webauthn.webauthn_verify,
	"webauthn_status": webauthn.webauthn_status,
	# approval requests
	"approval_categories": approval.approval_categories,
	"approval_contact_search": approval.approval_contact_search,
	"approval_requests": approval.approval_requests,
	"approval_request_submit": approval.approval_request_submit,
}

# Frappe's own parameters, which are not action arguments.
RESERVED_KEYS = ("action", "csrf_token", "cmd")


def _error_message(exc):
	"""The most recent message Frappe recorded for this request.

	`frappe.throw` puts a human sentence in the log and the exception carries only
	a code, so reading the log is the only way to tell the employee *why* their
	punch was refused instead of showing them "Unknown error".
	"""
	for entry in reversed(frappe.local.message_log or []):
		message = entry.get("message") if isinstance(entry, dict) else entry
		if message:
			return re.sub(r"<[^>]+>", "", str(message)).strip()
	return str(exc)


class SelfServiceApiRenderer:
	"""Serve ``/self-services-employees-api`` as a token-authorised JSON endpoint."""

	def __init__(self, path=None, http_status_code=None):
		self.path = (path or (frappe.local.request.path if frappe.local.request else "")).strip("/ ")
		self.http_status_code = http_status_code or 200

	def can_render(self):
		return self.path == API_ROUTE

	def render(self):
		frappe.local.response["type"] = "json"

		if frappe.request.method != "POST":
			return self._respond({"error": "POST required"}, 405)

		action = frappe.form_dict.get("action")
		handler = ACTIONS.get(action)
		if not handler:
			return self._respond({"error": f"Unknown self-service action: {action}"}, 400)

		kwargs = {
			key: value
			for key, value in frappe.form_dict.items()
			if key not in RESERVED_KEYS
		}
		try:
			frappe.local.response["message"] = handler(**kwargs)
		except Exception as exc:
			# Roll back before answering: a half-applied punch or request must not
			# survive a failure, or the employee retries and gets a duplicate.
			frappe.db.rollback()
			return self._respond({"error": _error_message(exc)}, 400)

		return build_response("json")

	def _respond(self, message, status_code):
		frappe.local.response["http_status_code"] = status_code
		frappe.local.response["message"] = message
		return build_response("json")


class SelfServiceLangRenderer:
	"""Serve ``/self-services-employees/setlang`` and ``.../resetlang``.

	Frappe's website router maps ``www/<name>.py`` to ``/<name>`` only, so a
	sub-path like this needs a page renderer - the same mechanism
	`SelfServiceApiRenderer` uses, and for the same reason.

	A missing or unknown code is an error and a valid one is stored in a cookie;
	a browser navigation then reloads the self-service page in that language.
	"""

	SETLANG = LANG_ROUTE
	RESETLANG = RESET_LANG_ROUTE

	def __init__(self, path=None, http_status_code=None):
		self.path = (path or (frappe.local.request.path if frappe.local.request else "")).strip("/ ")
		self.http_status_code = http_status_code or 200

	def can_render(self):
		return self.path in (self.SETLANG, self.RESETLANG)

	def render(self):
		frappe.local.response["type"] = "json"

		if self.path == self.RESETLANG:
			frappe.local.cookie_manager.delete_cookie(LANG_COOKIE_NAME)
			frappe.local.lang = frappe.local.conf.lang or "en"
			return self._finish({"lang": frappe.local.lang})

		lang = (frappe.form_dict.get("lang") or "").strip()
		if not lang:
			return self._respond({"error": "Missing language code."}, 400)

		# Checked against the *installed* languages, not the self-service page's own list: this
		# route is public, so without the check a caller could set an arbitrary
		# cookie value that `resolve_self_service_lang` would then reject on the next load.
		installed = {row["code"]: row["name"] for row in installed_languages()}
		if lang not in installed:
			return self._respond({"error": "Language not available."}, 400)

		frappe.local.lang = lang
		frappe.local.cookie_manager.set_cookie(
			LANG_COOKIE_NAME, lang, max_age=LANG_COOKIE_MAX_AGE
		)
		return self._finish({"lang": lang, "name": installed[lang]})

	def _finish(self, message):
		"""A browser navigation reloads the self-service page; a fetch() gets the JSON."""
		if wants_html():
			# Same contract as Frappe's own RedirectPage renderer.
			target = f"/{PAGE_ROUTE}"
			frappe.local.response["location"] = target
			return build_website_response(target, "", 302, {"Location": target})
		return self._respond(message, 200)

	def _respond(self, message, status_code):
		frappe.local.response["http_status_code"] = status_code
		frappe.local.response["message"] = message
		return build_response("json")