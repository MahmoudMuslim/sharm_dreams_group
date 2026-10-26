# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The `/employee-portal` page.

Frappe derives the route from the template filename and the controller module name
from the same, with hyphens becoming underscores in the module: the route is
`/employee-portal` from `employee-portal.html`, and the controller is
`employee_portal.py`. That is the arrangement Frappe's own `update-password` page
uses, and it is required rather than chosen.
"""

import frappe

from sharm_dreams_group.portal.portal import IDLE_TIMEOUT_SECONDS

no_cache = 1

# The languages the portal offers. Kept to en/fr/ar for now: those are the ones the
# old page shipped and the ones the employees' devices are set to.
PORTAL_LANGUAGES = (
	{"code": "en", "name": "English", "icon": "fa-globe"},
	{"code": "fr", "name": "Francais", "icon": "fa-globe"},
	{"code": "ar", "name": "العربية", "icon": "fa-globe"},
)

# The cookie holding the chosen language, read on every render so a tablet that
# remembers its language does not ask again. Same name convention as the old page.
LANG_COOKIE = "employee_portal_lang"

# The page route, and the switcher beside it. Named here because both the template's
# context and `portal/renderers.py` need them, and a second copy in the renderer can
# only ever drift into a route that 404s.
PAGE_ROUTE = "employee-portal"
API_ROUTE = f"{PAGE_ROUTE}-api"
SETLANG_ROUTE = f"/{PAGE_ROUTE}/setlang"
# The offline worker, at the site root: a worker registered from `/assets/...` can only
# control `/assets/...`, so the page it is meant to keep readable offline would never
# be intercepted by it at all.
SW_ROUTE = "employee-portal-sw.js"


def get_context(context):
	"""Everything the template needs, and nothing it does not."""
	lang = _resolve_lang()
	# `as_dict=True` because a list of fields returns a *tuple* - a missing Language
	# row then raises `TypeError: tuple indices must be integers or slices, not str`
	# on the very first render, which is the whole page being a 500.
	current = frappe.db.get_value(
		"Language", {"name": lang}, ["name", "flag"], as_dict=True
	) or {"name": lang}
	context.no_cache = 1
	context.portal_lang = lang
	context.portal_current_lang = current.get("name") or lang
	# Arabic is right-to-left and the whole layout has to follow, not just the words,
	# so the direction is decided here rather than per string in the template.
	context.portal_dir = "rtl" if lang == "ar" else "ltr"
	context.portal_languages = PORTAL_LANGUAGES
	context.portal_api = f"/{API_ROUTE}"
	context.portal_lang_api = SETLANG_ROUTE
	context.portal_sw = f"/{SW_ROUTE}"
	# The idle window the page script signs itself out on. Rendered into the page
	# rather than hardcoded in the bundle so the rule lives in Python beside the
	# session code it protects.
	context.portal_idle_ms = IDLE_TIMEOUT_SECONDS * 1000
	# Frappe does not set a `csrf_token` *cookie* for website visitors - the desk
	# passes the value into the page and the client echoes it in a header. Without it
	# every POST is refused by `Auth.validate_csrf_token` as "Invalid Request", and
	# the client sees an HTML error page where it expected JSON.
	# `get_csrf_token()` generates one if the session has none, so it is never empty.
	context.portal_csrf = frappe.sessions.get_csrf_token()
	frappe.db.commit()


def _resolve_lang() -> str:
	"""The language to render in: cookie, then Accept-Language, then English.

	Read from the cookie rather than stored per session, because a tablet is shared
	between employees and a language choice belongs to the device, not to whoever
	signed in last.
	"""
	cookie = frappe.request.cookies.get(LANG_COOKIE)
	if cookie in {lang["code"] for lang in PORTAL_LANGUAGES}:
		return cookie
	preferred = (frappe.request.headers.get("Accept-Language") or "").lower()
	for code in ("ar", "fr", "en"):
		if code in preferred:
			return code
	return "en"