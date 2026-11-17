# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""The words the portal speaks, and how they reach the browser.

The page offers English, French and Arabic. Only English exists in the source: every
label in `public/js/employee_portal.js` and `www/employee-portal.html` is written in
English, and the other two are looked up at render time.

The catalog is the app's own translation files - `translations/ar.csv` and
`translations/fr.csv` - which have one deliberate property worth more than they look
worth: they are what Frappe itself reads when it translates anything else in this app, so
there is one list of words for the whole app, and a desk screen and a wall tablet cannot
drift into calling the same thing two different things. Adding a language means adding a
CSV beside those, not editing a dictionary in Python.

Two consumers, and they need the words by different routes:

* **The client.** `employee_portal.js` builds its screens by concatenating HTML, and the
  page loads no Frappe JavaScript at all - there is no `window._()`, no
  `frappe.boot.__messages`, nothing. So `catalog()` is handed to the template, which
  writes it into `window.EMPLOYEE_PORTAL.strings`, and `localizeNode()` in the client
  swaps the words in after each render. This reads the file off disk, keyed on its mtime,
  so a new row shows up on the next page load without any restart.
* **The server.** The messages the portal throws - "Self-service session expired or
  invalid" - are already wrapped in `_()`, which resolves against `frappe.local.lang`. A
  guest's language is not a user preference and Frappe never asks the one this tablet was
  left on, so `resolve_lang()` decides it and `portal/renderers.py` puts it in
  `frappe.local.lang` before any action runs. Frappe reaches the same CSV rows through a
  cached merge rather than the database, so editing one needs a cache clear or a worker
  restart rather than a migrate.

A string with no row is not an error: both routes fall back to the English source, so an
untranslated label reads English on an Arabic page - wrong, visible, and harmless - rather
than breaking the screen it sits in.
"""

from __future__ import annotations

import csv
import os

import frappe

APP = "sharm_dreams_group"

# Languages the switcher offers. `en` is absent from the map below because it needs no
# file: the source strings are English.
TRANSLATION_FILES = {"fr": "fr.csv", "ar": "ar.csv"}

# (path, mtime) -> parsed catalog, so a CSV edit is picked up on the next render without
# a restart and a render does not re-read the four hundred rows it already has. The mtime
# is the whole invalidation story; a file that changes within its mtime granularity is a
# CSV being edited by a person who can also touch the file again. Only the current mtime of
# a path is kept, and the two languages are kept side by side: a fleet with Arabic tablets
# and French ones alternating would otherwise re-read a file on every request.
_CACHE: dict[tuple[str, float], dict[str, str]] = {}


def catalog(lang: str) -> dict[str, str]:
	"""`{english_source: translated}` for `lang`.

	Empty for English (the client's own fallback is the source) and for a language with
	no file. A row with no translation is skipped rather than mapped to `""`, because
	mapping it would blank out the label on screen - the one failure mode here that
	would look like a bug in the client.
	"""
	filename = TRANSLATION_FILES.get(lang)
	if not filename:
		return {}

	path = os.path.join(frappe.get_app_path(APP, "translations"), filename)
	try:
		mtime = os.path.getmtime(path)
	except OSError:
		return {}

	key = (path, mtime)
	if key not in _CACHE:
		for stale in [cached for cached in _CACHE if cached[0] == path]:
			del _CACHE[stale]
		_CACHE[key] = _read(path)
	return _CACHE[key]


def _read(path: str) -> dict[str, str]:
	"""The CSV, as `{source: translation}`.

	`translations/*.csv` is written by Frappe's own export with the header
	`source,translated,context`, and `csv.DictReader` is used rather than a split because
	neither column is free of commas: "Submit request, with notes" is a sentence a
	translator will write sooner or later, and a naive split would put half of it in the
	context column and lose the translation.

	The `context` column is read and then dropped, which is a deliberate loss. Frappe keys
	a contextual row as `"source:context"` because the same English word means different
	things on two screens; the client has no context to pass, and a portal label is only
	ever the one thing. So the last row for a source wins here, and a row that exists only
	under a context still translates the tablet - where the desk would need the context
	supplied by `_()` to show it.
	"""
	out: dict[str, str] = {}
	try:
		handle = open(path, encoding="utf-8", newline="")
	except OSError:
		return out
	with handle:
		for row in csv.DictReader(handle):
			source = (row.get("source") or "").strip()
			translated = (row.get("translated") or "").strip()
			if source and translated:
				out[source] = translated
	return out
