# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the portal's word lists.

`portal/locales.py` hands `translations/<lang>.csv` to the browser as
`{english: translated}`, and the client then rewrites what it has rendered against those
rows. Nothing in that arrangement is checked by a browser, and every way it comes apart
looks the same on the screen: a row whose columns have shifted, a French sentence the
Arabic file does not have, a `{1}` in a line the client can only fill to `{0}`. Each of
those shows as one English word on an Arabic tablet, which is a bug nobody reports
because nothing broke. So the shape of the file is the thing under test here, and the
client's own matching is what it is a contract for: a key is only ever found if the
screen says it exactly.
"""

from __future__ import annotations

import csv
import os
import re

from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import locales

TRANSLATIONS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations")
LANGUAGES = ("fr", "ar")

# The words the portal is mostly made of. Not the whole list - a sample chosen because
# each one is on a screen somebody opens every day, so that a catalog trimmed back to the
# desk's labels fails here rather than on a tablet.
PORTAL_LABELS = (
	"Employee Portal",
	"Overview",
	"Shift & attendance",
	"Time off",
	"Pay",
	"Assets",
	"No payslips yet",
	"Submit for approval",
	"Sign-in recorded",
	"Location verified.",
	"Waiting on you ({0})",
	"{0} days left",
	"Reports to",
)


def _read(language: str) -> list[list[str]]:
	path = os.path.join(TRANSLATIONS, f"{language}.csv")
	with open(path, encoding="utf-8", newline="") as handle:
		return [row for row in csv.reader(handle)]


def _keys(language: str) -> set[str]:
	return {row[0].strip() for row in _read(language) if row and row[0].strip()}


def _rows(language: str):
	"""`(source, translated)` for every data row, the header excluded."""
	for row in _read(language)[1:]:
		if len(row) < 2:
			continue
		source = row[0].strip()
		if source:
			yield source, row[1].strip()


def _holes(text: str) -> list[int]:
	return [int(match) for match in re.findall(r"\{(\d+)\}", text)]


class TestCatalogShape(FrappeTestCase):
	"""The files the portal is read from are parseable and complete."""

	def test_header_is_the_one_frappe_reads(self):
		# Frappe's own loader accepts two or three columns; the portal's accepts three and
		# ignores the third. Either way the first line has to be the header, or every
		# language's first row is a word called "source".
		for language in LANGUAGES:
			with self.subTest(language=language):
				self.assertEqual(_read(language)[0], ["source", "translated", "context"])

	def test_every_row_has_a_translated_column(self):
		for language in LANGUAGES:
			for number, row in enumerate(_read(language)[1:], start=2):
				if not any(cell.strip() for cell in row):
					continue  # a blank line, which the loader skips too
				with self.subTest(language=language, line=number):
					self.assertGreaterEqual(len(row), 2, f"{language}.csv line {number} lost its column")

	def test_the_two_languages_cover_the_same_words(self):
		# A row missing from one of them is not a smaller menu: it is English on the screen
		# of whichever tablet reads that file.
		french, arabic = _keys("fr"), _keys("ar")
		self.assertEqual(sorted(french - arabic), [], "rows only in fr.csv")
		self.assertEqual(sorted(arabic - french), [], "rows only in ar.csv")
		self.assertGreater(len(french), 300)

	def test_the_holes_in_a_translation_are_the_holes_in_the_english(self):
		# The client fills `{0}`, `{1}`, ... in the order the numbers appear on the screen,
		# so an English key whose holes are not numbered from nought upwards can never be
		# filled at all, and a translation with a hole the key does not have shows the
		# reader a brace.
		for language in LANGUAGES:
			for source, translated in _rows(language):
				with self.subTest(language=language, source=source):
					self.assertEqual(
						_holes(source),
						list(range(len(_holes(source)))),
						"the English key's placeholders must be 0, 1, 2 ... in order",
					)
					self.assertEqual(
						sorted(_holes(translated)),
						sorted(_holes(source)),
						"the translation must fill each hole once and only once",
					)

	def test_no_translation_is_empty(self):
		# A row with the second column blank is Frappe's way of saying "not translated
		# yet", and the portal's loader skips it - which is right, but on a tablet it is
		# invisible, so the app's own files are held to being filled in.
		for language in LANGUAGES:
			for source, translated in _rows(language):
				with self.subTest(language=language, source=source):
					self.assertTrue(translated, f"{source!r} is untranslated in {language}")


class TestCatalogDelivery(FrappeTestCase):
	"""What the page is handed, and what a language the portal does not offer costs."""

	def test_english_needs_no_rows(self):
		# The page is written in English, so an English tablet is given `{}` and the
		# client's walker is never installed. Reading fr.csv as though it were English
		# would swap every word twice.
		self.assertEqual(locales.catalog("en"), {})

	def test_an_unoffered_language_gets_nothing(self):
		# `PORTAL_LANGUAGES` is en/fr/ar. A caller that asks for Spanish gets an empty
		# catalog rather than a missing file and a page that fails to render.
		self.assertEqual(locales.catalog("es"), {})
		self.assertEqual(locales.catalog(""), {})

	def test_the_portal_labels_survive_the_load(self):
		for language in LANGUAGES:
			catalog = locales.catalog(language)
			self.assertTrue(catalog, f"{language} catalog came back empty")
			for label in PORTAL_LABELS:
				with self.subTest(language=language, label=label):
					self.assertIn(label, catalog)

	def test_the_catalog_reaches_the_browser(self):
		# The client cannot read a file: the template has to pass it the rows, and the
		# controller has to put them on the context. Either half missing is a portal that
		# changes `dir` and `lang` and leaves the words in English - which is how this
		# worked before there was a catalog at all.
		app = os.path.dirname(os.path.dirname(__file__))
		with open(os.path.join(app, "www", "employee-portal.html"), encoding="utf-8") as handle:
			template = handle.read()
		self.assertRegex(
			template,
			r'strings:\s*\{\{\s*portal_strings\s*\|\s*default\(\{\}\)\s*\|\s*tojson',
			# `| default({})` because the template is re-read from disk the moment it
			# changes and the controller is not: until the worker restarts, `portal_strings`
			# is undefined there, and `tojson` of an undefined value is a 500 for the page.
			"A stale worker must be able to serve an English portal, not no portal.",
		)

		with open(os.path.join(app, "www", "employee_portal.py"), encoding="utf-8") as handle:
			controller = handle.read()
		self.assertIn("context.portal_strings", controller)
		self.assertIn("from sharm_dreams_group.portal.locales import catalog", controller)

		with open(
			os.path.join(app, "public", "js", "employee_portal.js"), encoding="utf-8"
		) as handle:
			client = handle.read()
		self.assertIn("var STRINGS = CFG.strings", client)


class TestCatalogCoversTheClient(FrappeTestCase):
	"""The phrases the client asks for by name, rather than by what it renders.

	`t("Sign-in recorded at {0}", ...)` has no second chance: nothing on screen says the
	English afterwards, so a key invented at a call site and never written into the CSV
	shows up as that English. These are the only portal phrases that can be checked
	without a browser, so they are checked here.
	"""

	def test_every_call_to_t_has_a_row(self):
		client = os.path.join(
			os.path.dirname(os.path.dirname(__file__)), "public", "js", "employee_portal.js"
		)
		with open(client, encoding="utf-8") as handle:
			source = handle.read()

		keys = _client_translate_keys(source)
		self.assertTrue(keys, "no calls to t() found - the client's shape has changed")
		self.assertIn("Sign-in recorded", keys, "the reader stopped finding a key it knows")

		for language in LANGUAGES:
			catalog = locales.catalog(language)
			for key in sorted(keys):
				with self.subTest(language=language, key=key):
					self.assertIn(key, catalog, f"{key!r} is asked for but never translated")


def _client_translate_keys(source: str) -> set[str]:
	"""Every string literal handed to `t(...)` as its key, in the whole file.

	Reading the first argument rather than the call means a `t(out ? "a" : "b", [...])`
	contributes both of its rows and the arguments - which are data, and may be any string
	at all - contribute none.
	"""
	keys = set()
	for match in re.finditer(r"(?<![A-Za-z0-9_$.])t\(", source):
		index, depth, argument = match.end(), 1, []
		quoted = False
		while index < len(source) and depth:
			char = source[index]
			if quoted:
				# A comma inside the key is the key's own, so it cannot be the end of the
				# argument; neither can a bracket, and a `\"` is not a closing quote.
				if char == "\\":
					argument.append(char)
					argument.append(source[index + 1 : index + 2])
					index += 2
					continue
				if char == '"':
					quoted = False
			elif char == '"':
				quoted = True
			elif char == "(":
				depth += 1
			elif char == ")":
				depth -= 1
				if not depth:
					break
			elif char == "," and depth == 1:
				break
			argument.append(char)
			index += 1
	for literal in re.finditer(r'"((?:[^"\\]|\\.)*)"', "".join(argument)):
		keys.add(literal.group(1).replace('\\"', '"'))
	return keys
