"""The self-service page, served at ``/self-service``.

Frappe maps ``<app>/www/<name>.py`` to the route ``/<name>``, so this file and
``self-service.html`` together are the whole page.  ``get_context`` does three things:
it resolves the self-service page's language, hands the browser its CSRF token, and builds the
two asset URLs.

The self-service renders its screens in JavaScript, so every string it shows is
translated here and handed to the page as ``window.SELF_SERVICE_I18N``.  The keys are the
slug the client looks up; the values are the English source text passed to
``frappe._()`` so the app's translation catalogs can pick it up.
"""

import hashlib
import json
import os

import frappe
from frappe import _
from frappe.sessions import get_csrf_token
from frappe.translate import get_all_languages

APP = "sharm_dreams_group"

# The cookie name, read by `SelfServiceLangRenderer` and by `resolve_self_service_lang`. Frappe
# already has the plumbing (the `preferred_language` cookie and `frappe.local.lang`),
# so this keeps the name for compatibility rather than for any technical need.
#
# The client writes this same name in `document.cookie`, by hand, in two places.
# Nothing reconciles the two, so `TestWiring` reads the constant back out of the
# JavaScript and compares - a rename here that misses the client leaves the
# language preference being written and never read, which looks exactly like the
# toggle not working.
LANG_COOKIE_NAME = "employee_self_service_lang"

# One year, matching a "sticky" language preference.
LANG_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# The light/dark preference, kept in its own cookie rather than reusing the
# language one. Same reason as the language cookie: the page is a guest, so there
# is no User record to hang a preference off, and a cookie is readable by both
# halves - the server renders the initial state from it, and the client writes it
# without a round trip.
#
# "system" is a real value, not a synonym for unset: it means "follow the tablet's
# own setting", which is what a wall tablet in an office wants and what it keeps
# following as the OS flips at sunset.
THEME_COOKIE_NAME = "employee_self_service_theme"
THEME_CHOICES = ("light", "dark", "system")
THEME_COOKIE_MAX_AGE = LANG_COOKIE_MAX_AGE

# The picker, server-side so it cannot drift from `THEME_CHOICES` or from the
# strings. `icon` is the Font Awesome 4 name the option shows when it is *not*
# selected - the selected one shows a tick instead, which the template handles.
SELF_SERVICE_THEME_ITEMS = [
	{"code": "light", "label": "Light", "icon": "fa-sun-o"},
	{"code": "dark", "label": "Dark", "icon": "fa-moon-o"},
	{"code": "system", "label": "Match device", "icon": "fa-adjust"},
]

# How long the tablet may sit untouched before the client re-arms the session.
# Mirrored by `self_service.api.SELF_SERVICE_INACTIVITY_SECONDS`, which sends the same value in
# `my_profile`; both have to agree or the header goes idle while the token is
# still good.
INACTIVITY_MS = 30 * 1000

# Only the languages the self-service page actually ships strings for.  Deriving this from the
# installed languages - as Frappe's own page context does - would offer a language
# that silently fell back to English.  All three have a catalog in
# `translations/`.
SELF_SERVICE_LANGUAGES = [
	{"code": "en", "name": "English"},
	{"code": "fr", "name": "Fran\u00e7ais"},
	{"code": "ar", "name": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629"},
]

SELF_SERVICE_STRINGS = {
	# header / footer
	"employee_self_service": "Employee Self Service",
	"powered_by": "Touch anywhere to keep the session alive",
	"language": "Language",
	"lang_name": "English",
	# the light/dark picker. Deliberately three options and not two: a wall tablet
	# never moves, so "follow the tablet" is a setting somebody has to be able to
	# pin rather than a thing they can leave to chance.
	"theme": "Appearance",
	"theme_light": "Light",
	"theme_dark": "Dark",
	"theme_system": "Match device",
	# login screen
	"employee_sign_in": "Employee Sign In",
	"sign_in_hint": "Use your National ID and employee number.",
	"national_id": "National ID",
	"national_id_ph": "e.g. 29901011234567",
	"employee_code": "Employee Number",
	"employee_code_ph": "e.g. V0010",
	"remembered_identities": "Remembered identities",
	"remove": "Remove",
	"both_fields_required": "Enter both your national ID and employee number.",
	"biometric_waiting": "Waiting for biometric confirmation (Face ID / fingerprint)...",
	"no_biometric": "Biometric sign-in is not configured for your account.",
	"biometric_cancelled": "Biometric authentication was cancelled or is not available on this device.",
	"sign_in": "Sign In",
	"verifying": "Verifying...",
	"protected_by": "Protected by",
	"fingerprint": "Fingerprint",
	# dashboard
	"good_morning": "Good Morning",
	"good_afternoon": "Good Afternoon",
	"good_evening": "Good Evening",
	"today": "Today",
	"last_in": "Last In",
	"session": "Session",
	"checked_in": "Checked In",
	"checked_out": "Checked Out",
	"checked_in_label": "Checked In",
	"checked_out_label": "Checked Out",
	"attendance_today": "Attendance Today",
	"check_in": "Check In",
	"check_out": "Check Out",
	"getting_location": "Getting your location...",
	"location_note": "Your location is captured securely with this entry.",
	"end_session": "End Session",
	# greet screen
	"welcome": "Welcome",
	"goodbye": "Goodbye",
	"checked_in_at": "Checked in at",
	"checked_out_at": "Checked out at",
	"time": "Time",
	# menu tiles
	"menu": "Menu",
	"new_request": "New Request",
	"my_requests": "My Requests",
	"my_payslips": "My Payslips",
	"time_off": "Time Off",
	"approvals": "Approvals",
	"approvals_n": "Approvals",
	# panels
	"back": "Back",
	"back_to_dashboard": "Back to Dashboard",
	"all": "All",
	"view": "View",
	"earnings": "Earnings",
	"deductions": "Deductions",
	"net_pay": "Net Pay",
	"employer_side": "Employer Side (not deducted from net pay)",
	"payslip": "Payslip",
	"search": "Search",
	"grouped": "Grouped",
	"list": "List",
	"group_by": "Grouping",
	"ungroup": "Toggle grouping",
	"no_date": "No date",
	"uncategorised": "Uncategorised",
	"offline": "Cannot reach the server",
	"offline_hint": "Check the network connection, then retry.",
	"retry": "Retry",
	"back_online": "Back online",
	"days": "days",
	"error": "Error",
	"nothing_here": "Nothing here",
	"no_payslips": "No payslips found",
	"no_payslips_sub": "Payslips appear here once a payroll has been submitted for you.",
	"no_approvals": "Nothing to approve",
	"no_approvals_sub": "Requests waiting for your approval will appear here.",
	"no_requests": "No requests yet",
	"no_requests_sub": "Requests you submit from this self-service page will appear here.",
	"no_time_off": "No leave types available",
	"no_time_off_sub": "Your leave types will appear here.",
	"no_leave_types": "No leave types available",
	"no_leave_types_sub": "Ask HR to add a leave type and allocation.",
	# request flow
	"leave_type": "Leave Type",
	"from": "From",
	"to": "To",
	"description": "Description",
	"reason": "Reason",
	"submit": "Submit",
	"submitting": "Submitting...",
	"submit_request": "Submit Request",
	"pick_dates": "Choose both a start and an end date.",
	"cancel_leave": "Cancel this leave request?",
	"cancelled": "Cancelled.",
	# time off screen
	"vacation_balance": "Vacation Balance",
	"left": "left",
	"search_time_off": "Search time-off types...",
	"only_available": "Show only types with remaining balance",
	"available_only": "Available only",
	"all_types": "All types",
	"no_match": "No time-off types match your filters.",
	"clear_filters": "Clear filters",
	"no_balance": "No balance",
	"all_time_off_types": "All Time Off Types",
	"leave_summary": "Leave Summary",
	"period": "Period",
	"duration": "Duration",
	"day": "day",
	"days_left": "days left",
	"enough_balance": "Enough balance",
	"exceeds_balance": "Exceeds balance",
	# statuses
	"approved": "Approved",
	"rejected": "Rejected",
	"pending": "Pending",
	"cancelled_status": "Cancelled",
	"no_leave": "No leave requests found",
	"no_leave_sub": "Anything you request from the self-service page will be listed here.",
	"no_balances": "No balances yet",
	"balances_sub": "Balances appear once leave has been allocated to you.",
	"balance": "Balance",
	"entitled": "Entitled",
	"used": "Used",
	"remaining": "Remaining",
	"employee": "Employee",
	"type": "Type",
	"no_records": "No records found",
	# server errors surfaced by the client
	"server_error_detail": "Server error",
	"self_service_error": "Self service error",
	"js_error": "Self service error",
	"dismiss": "Dismiss",
	"not_allowed_to_punch": "You are not allowed to punch from here.",
	"biometric_unsupported": "This device does not support biometric sign-in.",
	"could_not_sign_in": "Could not sign you in",
}


def asset_url(relative_path):
	"""``/assets/...`` with a content hash, so a self-service page browser picks up fixes.

	Frappe serves app assets with ``Cache-Control: max-age=31536000``.  That is
	right in production, where the filename carries a build hash, but a hand-rolled
	self-service keeps the same URL forever - so a wall tablet would run the JS from the
	day it was first provisioned.  Hashing the file contents into the query string
	invalidates the cached copy exactly when the file changes and keeps the long
	cache in every other case.

	Deliberately **not** memoised.  Caching this would freeze the URL for the
	lifetime of the web worker, so an edited asset keeps being served under its
	*old* URL and the browser answers from its own year-long cache.  Hashing ~50KB
	of JS and CSS per page render costs nothing next to the rest of the request.
	"""
	full = os.path.join(frappe.get_app_path(APP), "public", relative_path)
	try:
		with open(full, "rb") as fh:
			digest = hashlib.sha256(fh.read()).hexdigest()[:10]
	except OSError:
		digest = "0"
	return "/assets/{}/{}?v={}".format(APP, relative_path, digest)


def get_context(context):
	lang = resolve_self_service_lang()
	# Set before the template renders so `frappe._()` in Jinja uses it.
	frappe.local.lang = lang

	context.title = _("Employee Self Service")

	# `get_csrf_token()` returns the session's token and *generates* one if the
	# session has none. `frappe.session.csrf_token` is not a real attribute, so
	# reading it here always yielded "" and the page could never send a valid
	# token: any browser that also held a session cookie (i.e. anyone who had
	# opened the desk) then failed Frappe's CSRF check and got an HTML 400, which
	# the self-service page JS surfaced as "Unexpected token '<'".
	context.csrf_token = get_csrf_token()

	i18n = {key: _(text) for key, text in SELF_SERVICE_STRINGS.items()}

	# NB: the template must NOT read a context key called `lang`.  Frappe's website
	# context already defines one (the site default), so `lang` in Jinja was always
	# "en" - which left the server-rendered shell English and the header language
	# button reading "English"/"EN" in every language, while the JS-rendered
	# screens were correctly translated.  Hence `self_service_lang`.
	current = next((x for x in SELF_SERVICE_LANGUAGES if x["code"] == lang), SELF_SERVICE_LANGUAGES[0])

	context.self_service_lang = lang
	context.self_service_current_lang = current["name"]
	context.SELF_SERVICE_STRINGS = {lang: i18n, "en": {k: v for k, v in SELF_SERVICE_STRINGS.items()}}
	context.SELF_SERVICE_STRINGS_JSON = json.dumps(i18n, ensure_ascii=False)
	# The template iterates this directly. Jinja resolves a bare module name
	# against the page's globals, and `get_context` is the only thing that puts
	# anything there - so it has to be passed in like any other context value
	# rather than imported by the template.
	context.SELF_SERVICE_THEME_ITEMS = SELF_SERVICE_THEME_ITEMS
	context.INACTIVITY_MS = INACTIVITY_MS
	context.self_service_languages = SELF_SERVICE_LANGUAGES
	context.self_service_theme = resolve_theme()
	context.self_service_theme_cookie = THEME_COOKIE_NAME
	context.self_service_css = asset_url("css/self_service.css")
	context.self_service_js = asset_url("js/self_service.js")
	context.no_cache = 1
	return context


def resolve_theme():
	"""The page's light/dark preference: ``?theme=`` first, then the cookie.

	Anything outside `THEME_CHOICES` is ignored rather than raising, so a stale or
	hand-edited cookie falls back to "system" instead of serving a page whose
	stylesheet references a theme attribute nobody set.

	"system" is a value in its own right, not a synonym for unset: it means "follow
	the tablet's own setting". The server cannot resolve it - only the browser can,
	via `prefers-color-scheme` - so it is passed through as-is and the inline
	script in `<head>` turns it into a concrete light or dark before first paint.
	"""
	# `frappe.local.request` raises AttributeError rather than returning None when
	# there is no request - which is exactly the case this has to survive, because
	# `get_context` is called from a console and a test as well as from a visit.
	# Hence getattr rather than a plain attribute test.
	request = getattr(frappe.local, "request", None)
	candidates = [
		(frappe.form_dict.get("theme") or "").strip(),
		(request.cookies.get(THEME_COOKIE_NAME) or "").strip() if request else "",
	]
	for candidate in candidates:
		if candidate in THEME_CHOICES:
			return candidate
	return "system"


def resolve_self_service_lang():
	"""The self-service's language: ``?lang=`` first, then the cookie, then the site default.

	Anything outside the self-service page's own language list is ignored rather than raising,
	so a stale cookie from a language an admin later dropped cannot break the
	self-service.
	"""
	supported = {row["code"] for row in SELF_SERVICE_LANGUAGES}
	# getattr, not `if frappe.local.request`: that attribute *raises* when there is
	# no request rather than being false, so the guard used to be the thing that
	# broke. This function is called from `get_context`, which runs in a console
	# and in tests as well as on a visit.
	request = getattr(frappe.local, "request", None)
	candidates = [
		(frappe.form_dict.get("lang") or "").strip(),
		(request.cookies.get(LANG_COOKIE_NAME) or "").strip() if request else "",
		(frappe.translate.get_preferred_language_cookie() or "").strip()
		if request
		else "",
	]
	for candidate in candidates:
		if candidate and candidate in supported:
			return candidate
	return "en"


def installed_languages():
	"""``[{"code": "fr", "name": "français"}, ...]`` for the enabled languages."""
	rows = get_all_languages(with_language_name=True)
	languages = []
	seen = set()
	for row in rows:
		if isinstance(row, dict):
			code = row.get("language_code") or row.get("name")
			name = row.get("language_name") or code
		else:
			code, name = row, row
		if code and code not in seen:
			seen.add(code)
			languages.append({"code": code, "name": name})
	return languages


def wants_html():
	"""True for a browser navigation, False for a JSON/fetch request."""
	if not frappe.local.request:
		return False
	if frappe.form_dict.get("format") == "json":
		return False
	accept = frappe.request.headers.get("Accept", "")
	return "text/html" in accept