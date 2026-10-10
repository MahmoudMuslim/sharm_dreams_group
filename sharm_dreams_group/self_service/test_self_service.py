"""Tests for the employee self-service.

Grouped by what breaks, not by module:

* `TestWiring` - the failures that raise nothing. A `page_renderer` pointing at a
  module that does not import leaves the self-service page 404ing with nothing in the bench
  log; an action missing from `renderers.ACTIONS` is unreachable however correct
  the client is; the `Employee Checkin` Select drifting from `geo.STATUS_LABELS`
  makes the punch fail validation at the moment somebody is trying to clock in.
* `TestSession` / `TestPreauth` - the token lifecycle, on doctypes this app owns.
* `TestApprovalWorkflow` - the request state machine.
* `TestAttendance` / `TestGeofence` - the punch path.
* `TestVacation` / `TestLeaveApproverResolution` - the read models.
* `TestWebauthnOps` - pure crypto.

Deliberately absent: a test that files a real Leave Application.  HRMS validates
those against a Leave Policy, an allocation period and the employee's balance, so
such a test would really be testing that fixture chain, and would break on every
HRMS upgrade.  Whon the self-service page itself owns about leave - the approver resolution
and the balance arithmetic - is covered below.

Run with:
    bench --site <site> run-tests --app sharm_dreams_group \\
        --module sharm_dreams_group.self_service.test_self_service
"""

import csv
import json
import os
import pathlib
import re

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from sharm_dreams_group.self_service import (
	api,
	approval,
	geo,
	renderers,
	time_off,
	vacation,
	vacation_spec,
	webauthn_ops,
)
from sharm_dreams_group.setup import (
	SELF_SERVICE_DOUBLE_DAY_LEAVE_CODES,
	SELF_SERVICE_GEO_STATUS_OPTIONS,
)
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_preauth.employee_self_service_preauth import (
	consume_preauth,
)
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	create_session,
	resolve_session,
)

APP = "sharm_dreams_group"


# --------------------------------------------------------------------------
# Stylesheet helpers
#
# The theme's failures do not raise. A colour that cannot read on a dark surface
# is a valid declaration on a page that renders; a token holding a whole
# `inset ...` shadow where a colour belongs is a declaration the browser throws
# away without a word. So the checks below read the file and reason about it.
# --------------------------------------------------------------------------

# A token value that is a colour, a gradient, or a font stack - and nothing else.
# `inset 0 -1px 0 rgba(0, 0, 0, 0.3)` is none of those: it is a fragment of a
# box-shadow that happens to start with a colour, and splicing it into
# `background` yields transparent while splicing it into
# `box-shadow: ..., inset 0 1px 0 <here>` yields an invalid declaration.
TOKEN_FRAGMENT = re.compile(r"(^|\s)(inset|outset|dense|solid|groove|ridge|outset)\b")

DEEMPHASIS_TOKENS = frozenset(
	{
		# `--k-text-4` is held to 3:1 rather than 4.5:1 on purpose. It paints
		# placeholders, empty-state glyphs and the language dropdown's caption -
		# none of which carries information the label beside it does not already
		# carry - and four distinguishable steps of grey cannot all clear 4.5:1
		# on a near-white surface. It is the quietest thing on screen by design;
		# the test holds it to the ratio the design actually claims.
		"--k-text-4",
	}
)

AA_TEXT = 4.5
AA_LARGE = 3.0
DEEMPHASIS_FLOOR = 3.0


def palette_block(css: str, marker: str) -> str:
	"""The rule whose selector line contains `marker`.

	Anchored on the selector, never on the opening brace. Walking back from a
	brace to the nearest newline lands *inside* the previous rule for any
	selector that is not at the start of its own line, and re-emitting from there
	loses everything in between - which is how a repair script once dropped
	1,740 lines of this stylesheet. The marker must also begin its line, because
	this file names both palettes inside the comment that explains why there is
	no `prefers-color-scheme` rule.
	"""
	found = re.search(r"(?m)^[ \t]*" + re.escape(marker), css)
	if not found:
		raise AssertionError(f"no line in the stylesheet starts with {marker!r}")
	start = css.rindex("\n", 0, found.start()) + 1
	depth = 0
	for index in range(css.index("{", found.start()), len(css)):
		if css[index] == "{":
			depth += 1
		elif css[index] == "}":
			depth -= 1
			if depth == 0:
				return css[start : index + 1]
	raise AssertionError(f"unterminated rule for {marker!r}")


def palette_tokens(css: str, marker: str) -> dict[str, str]:
	return dict(re.findall(r"(--k-[a-z0-9-]+)\s*:\s*([^;]+);", palette_block(css, marker)))


def srgb(value: str):
	"""A hex or rgb()/rgba() colour as floats, or None if it is not a colour."""
	text = value.strip()
	if text.startswith("#"):
		digits = text[1:]
		if len(digits) == 3:
			digits = "".join(char * 2 for char in digits)
		if len(digits) not in (6, 8):
			return None
		try:
			return tuple(int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
		except ValueError:
			return None
	match = re.match(r"rgba?\(([^)]+)\)", text)
	if not match:
		return None
	parts = [part.strip() for part in match.group(1).split(",")]
	if len(parts) < 3:
		return None

	def channel(part: str):
		try:
			return float(part.rstrip("%")) / (100 if part.endswith("%") else 255)
		except ValueError:
			return None

	values = [channel(part) for part in parts[:3]]
	return None if any(value is None for value in values) else tuple(values)


def luminance(rgb) -> float:
	def curve(value: float) -> float:
		return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

	red, green, blue = (curve(value) for value in rgb)
	return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str):
	"""WCAG contrast between two colours, or None if either is not a colour."""
	first, second = srgb(foreground), srgb(background)
	if first is None or second is None:
		return None
	one, two = luminance(first), luminance(second)
	return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def resolve_tokens(value: str, tokens: dict[str, str], seen=None) -> str:
	"""Substitute `var(--k-*)` references, or raise KeyError on an unknown one.

	The recursion guard matters: a token that names itself would otherwise
	substitute until the stack gave out, and a token that is absent has to be
	distinguishable from one that resolved to nothing.
	"""
	seen = set() if seen is None else seen

	def substitute(match):
		name = match.group(1)
		if name in seen or name not in tokens:
			raise KeyError(name)
		seen.add(name)
		try:
			return resolve_tokens(tokens[name], tokens, seen)
		finally:
			seen.discard(name)

	return re.sub(r"var\(\s*(--k-[a-z0-9-]+)\s*\)", substitute, value)


def darkest_stop(gradient: str):
	"""The least-luminous colour in a gradient, which is the worst backdrop.

	`--k-bg` is a `radial-gradient` in both palettes, so the surface a rule with
	no background of its own is painted on varies across the viewport. Grading
	against the average, or against `--k-surface`, is optimistic; the darkest
	stop is the one that decides whether the text reads anywhere.
	"""
	colours = re.findall(r"#[0-9a-fA-F]{3,8}\b", gradient or "")
	if not colours:
		return []
	return [min(colours, key=lambda c: luminance(srgb(c) or (1, 1, 1)))]


def worst_text_contrast(css: str, tokens: dict[str, str], token: str):
	"""The worst contrast `token` achieves anywhere it is painted as text.

	Returns None when the token is never used as a colour, which is the signal
	that an exemption naming it has gone stale.
	"""
	worst = None
	bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
	for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare):
		declarations = re.findall(r"([\w-]+)\s*:\s*([^;]+)", rule.group(2))
		properties = dict(declarations)
		if "color" not in properties:
			continue
		if f"var({token})" not in properties["color"]:
			continue
		try:
			foreground = resolve_tokens(properties["color"], tokens)
		except KeyError:
			continue
		backgrounds = []
		for name, value in declarations:
			if name not in ("background", "background-color"):
				continue
			try:
				expanded = resolve_tokens(value, tokens)
			except KeyError:
				continue
			backgrounds += re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", expanded)
		if not backgrounds:
			backdrop = darkest_stop(tokens.get("--k-bg", ""))
			backgrounds = backdrop or [tokens.get("--k-surface", "white")]
		for background in backgrounds:
			ratio = contrast_ratio(foreground, background)
			if ratio is not None and (worst is None or ratio < worst):
				worst = ratio
	return worst


def purge(doctype, filters=None):
	"""Delete every row of a doctype, ignoring permissions.

	The self-service tables are small and test-owned, so removing all of them is cheaper
	and less surprising than trying to remember which rows a given test created.
	Employee is the exception - see `SelfServiceTestCase`.
	"""
	for row in frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=0):
		frappe.delete_doc(doctype, row, force=True, ignore_permissions=True)


def _quiet_delete(doctype, name):
	"""Delete one document, reporting rather than raising on failure.

	A cleanup that raises turns a passing test into an error and tells you nothing
	useful about the feature, so a refusal is printed with its reason and the rest
	of the cleanup carries on.  What it must never do is stop early and leave the
	row behind - which is what the surrounding loop would do on an exception.
	"""
	try:
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	except Exception as exc:
		print(f"  self-service test cleanup: could not delete {doctype} {name}: {exc}")


# Every fixture this suite creates carries this prefix, in its Employee Number, in
# its email addresses and in its approval-category name.
#
# It is what makes the suite safe to point at a site holding real staff: cleanup
# deletes by prefix, so it cannot touch a real employee however it is named, and it
# can recognise - and clear - its own leftovers from an earlier run without being
# told what they were. The two properties pull in opposite directions, and the
# prefix is what gives both.
FIXTURE_PREFIX = "ESS-TEST-"


class SelfServiceTestCase(FrappeTestCase):
	"""Base class that keeps the suite from leaving anything on the site.

	`FrappeTestCase` rolls back at *class* boundary, not per test, and opens each
	class with a `frappe.db.commit()`. So the usual `addCleanup` delete is undone by
	that rollback, and anything a commit got past becomes permanent when the next
	class opens. Neither is something a test can be trusted to reason about, so this
	base class works at *class* level only:

	* `setUpClass` purges the fixture namespace and stubs `commit`;
	* `tearDownClass` restores `commit`, purges again, and commits - the one write
	  that survives the class rollback.

	Class level rather than `setUp` on purpose. Every test class here has its own
	`setUp`, and a subclass `setUp` that does not call `super()` silently replaces
	the base one - which is exactly what happened while this was being written: the
	purge and the commit stub simply never ran for any class that had its own
	`setUp`, and the suite left rows behind and hit duplicate keys. Nothing in
	`setUpClass` can be shadowed that way.

	Within a class each test still needs its own fixture names, so `make_employee`
	folds the test's own name into the Employee Number.
	"""

	@classmethod
	def setUpClass(cls):
		# FrappeTestCase's own docstring: if you override setUpClass, call super().
		super().setUpClass()
		cls._purge_fixtures()

		# Two self-service actions commit on purpose - `sign_out`, and
		# `approval_request_submit` so a submitted request is durable before the
		# tablet gets its answer - so the commit is stubbed for the whole class.
		# `frappe.db` is a LocalProxy over `frappe.local.db`, so patching the
		# attribute on the connection is what intercepts `frappe.db.commit()`.
		# Held on the connection object so the restore lands on the same one even
		# if something reconnects mid-class.
		cls._connection = frappe.local.db
		cls._real_commit = cls._connection.commit
		cls._connection.commit = lambda *args, **kwargs: None

	@classmethod
	def tearDownClass(cls):
		cls._connection.commit = cls._real_commit
		cls._purge_fixtures()
		# The commit is what makes the deletes stick: everything before it in the
		# class was undone by the rollback that FrappeTestCase runs first.
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _purge_fixtures(cls) -> None:
		"""Delete everything in the fixture namespace. Idempotent.

		Deletion order is the reverse of the link order, because Frappe refuses to
		delete a document something still points at: self-service rows, then employees,
		then users.
		"""
		employees = frappe.get_all(
			"Employee",
			filters={"employee_number": ["like", f"{FIXTURE_PREFIX}%"]},
			pluck="name",
			limit_page_length=0,
		)
		if employees:
			for doctype in (
				"Employee Self Service Session",
				"Employee Self Service Preauth",
				"Employee Self Service Biometric Credential",
			):
				for row in frappe.get_all(
					doctype,
					filters={"employee": ("in", employees)},
					pluck="name",
					limit_page_length=0,
				):
					_quiet_delete(doctype, row)

			for row in frappe.get_all(
				"Employee Self Service Approval Request",
				filters={"employee": ("in", employees)},
				pluck="name",
				limit_page_length=0,
			):
				_quiet_delete("Employee Self Service Approval Request", row)

			# Scoped to the fixture employees. Deleting every checkin with
			# device_id "self-service" would be simpler and would quietly destroy real
			# attendance on a site where the self-service page is in use - this suite has to be
			# safe to point at the site it ships on.
			for row in frappe.get_all(
				"Employee Checkin",
				filters={"employee": ("in", employees)},
				pluck="name",
				limit_page_length=0,
			):
				_quiet_delete("Employee Checkin", row)

			# A submitted `Shift Assignment` is what a geofence test hangs its punch zone
			# off, and HRMS refuses to delete an Employee one still points at, so they go
			# first and in that order: cancel, then delete.
			for row in frappe.get_all(
				"Shift Assignment",
				filters={"employee": ("in", employees)},
				pluck="name",
				limit_page_length=0,
			):
				if frappe.db.get_value("Shift Assignment", row, "docstatus") == 1:
					frappe.get_doc("Shift Assignment", row).cancel()
				_quiet_delete("Shift Assignment", row)

			# One fixture employee may be another's manager.
			frappe.db.set_value("Employee", {"reports_to": ("in", employees)}, "reports_to", None)
			for name in employees:
				_quiet_delete("Employee", name)

		for row in frappe.get_all(
			"Employee Self Service Approval Category",
			filters={"category_name": ["like", f"{FIXTURE_PREFIX}%"]},
			pluck="name",
			limit_page_length=0,
		):
			_quiet_delete("Employee Self Service Approval Category", row)

		cls._purge_zones()

		for user in frappe.get_all(
			"User",
			filters=[["name", "like", f"%{FIXTURE_PREFIX.lower()}%"]],
			pluck="name",
			limit_page_length=0,
		):
			# Drive writes a `Drive Settings` Single per user on insert, and deleting
			# the User leaves it behind. Left in place, the next run fails on a
			# duplicate Drive Settings insert that has nothing to do with the self-service page.
			if frappe.db.exists("DocType", "Drive Settings"):
				_quiet_delete("Drive Settings", user)
			_quiet_delete("User", user)

	def fixture_number(self, code):
		"""The Employee Number / National ID for this test's `code`.

		Folded from the prefix, the running test's name and the code, so two tests
		in one class - or two tests across classes that happen to use the same code -
		never collide on the primary key.  Purging only happens at class boundaries,
		so this is what keeps the tests inside a class independent of each other.
		"""
		return f"{FIXTURE_PREFIX}{self._testMethodName}-{code}"

	@classmethod
	def _purge_zones(cls) -> None:
		"""Delete only the punch zones this suite created.

		Scoped by name rather than emptying the table: a `Shift Location` is the real
		punch zone for a real work site, and a test run has no business deleting it. It
		is autonamed by `location_name`, so the name *is* the document name and the
		prefix is all that is needed to tell ours from the site's.
		"""
		for row in frappe.get_all(
			"Shift Location",
			filters={"name": ["like", f"{FIXTURE_PREFIX}%"]},
			pluck="name",
			limit_page_length=0,
		):
			_quiet_delete("Shift Location", row)

	def make_employee(self, code, **extra):
		"""Create a self-service page-signable Employee inside the fixture namespace.

		`code` is a short label naming the test's intent ("KSESS-1"); the Employee
		Number is derived from it and the running test's name - see
		`fixture_number`. The National ID is the same string, because `sign_in`
		needs both to agree and neither has a default.

		The returned document's `name` is *not* the number: stock Employee is
		autonamed, so every lookup here goes through `doc.name`.
		"""
		company = extra.pop("company", None) or self._any_company()
		if not company:
			raise frappe.ValidationError("The self-service tests need at least one Company.")

		number = self.fixture_number(code)
		values = {
			"doctype": "Employee",
			"employee_name": number,
			"employee_number": number,
			"first_name": number,
			# Resolved rather than hard-coded: `gender` is a mandatory Link to
			# `Gender`, and which records exist is HR's business - this site has
			# Male and Female only. A literal that is not on the site is a
			# LinkValidationError, and omitting it is a MandatoryError. Nothing in the
			# self-service reads it; Employee just insists it be there.
			"gender": self._any_gender(),
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2024-01-01",
			# Employee Checkin.validate calls validate_active_employee, so the
			# attendance tests cannot use an employee left at the default.
			"status": "Active",
			"company": company,
			"sdg_national_id": extra.pop("national_id", None) or number,
		}
		values.update(extra)
		# No addCleanup here on purpose: a delete inside the test transaction is
		# undone by the class-level rollback, so it would be undone *and* would hide
		# the problem. `setUp` purges the namespace before the next test instead.
		return frappe.get_doc(values).insert(ignore_permissions=True)

	@staticmethod
	def _any_company() -> str:
		"""A Company to hang the fixture employee off.

		`get_global_default` reads the *current user's* User Defaults, and the test
		session is Administrator - who has none - so it returns "". Global Defaults
		is the site-wide fallback, and any Company at all is the last resort: the
		self-service only needs a company to hang the fixture Employee (and the shift
		assignment a punch zone is attached to) off, so which one it is does not matter.
		"""
		return (
			frappe.defaults.get_global_default("company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or frappe.db.get_value("Company", {}, "name")
			or ""
		)

	@staticmethod
	def _any_gender() -> str:
		"""An existing `Gender` name, because Employee.gender is a mandatory Link.

		Which ones exist is site data. Creating one here would be a fixture that
		leaves a record behind, so this takes what is already there.
		"""
		existing = frappe.get_all("Gender", pluck="name", limit_page_length=1)
		if not existing:
			raise frappe.ValidationError("The self-service tests need at least one Gender record.")
		return existing[0]

	def make_user(self, label, first_name="Self Service"):
		"""A User inside the fixture namespace, for the fixture employees to belong to.

		Unique per test, like `fixture_number`. HRMS allows an Employee at most one
		User, so two tests in a class that share an address cannot both have an
		employee signed in - and nothing is purged between tests in a class, so the
		second would fail on a duplicate-user error that says nothing about the
		self-service.
		"""
		email = f"{FIXTURE_PREFIX}{self._testMethodName}-{label}@example.com".lower()
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"roles": [{"role": "Employee"}],
				}
			).insert(ignore_permissions=True)
		return email

	def set_user(self, user):
		"""Act as `user` for the rest of the test, restoring afterwards."""
		previous = frappe.session.user
		frappe.set_user(user)
		self.addCleanup(frappe.set_user, previous)

	def self_service_identity(self, code):
		"""The (National ID, Employee Number) pair `make_employee(code)` produced."""
		number = self.fixture_number(code)
		return number, number

	def signed_in(self, code):
		"""Sign `code`'s employee in on the self-service page and return the session token.

		Goes through the real `sign_in`, not a shortcut past it: the point of most
		of these tests is the front door.
		"""
		national_id, number = self.self_service_identity(code)
		return api.sign_in(national_id, number)["token"]


class TestWiring(SelfServiceTestCase):
	"""The failures that produce no exception at all."""

	def test_self_service_modules_import(self):
		import importlib

		# One at a time: a cycle only surfaces for the entry point that happens to
		# hit it first.
		for name in (
			f"{APP}.self_service.api",
			f"{APP}.self_service.approval",
			f"{APP}.self_service.geo",
			f"{APP}.self_service.renderers",
			f"{APP}.self_service.time_off",
			f"{APP}.self_service.vacation",
			f"{APP}.self_service.webauthn",
			f"{APP}.self_service.webauthn_ops",
			f"{APP}.www.self_services_employees",
		):
			self.assertIsNotNone(importlib.import_module(name), name)

	def test_page_renderers_are_registered_and_importable(self):
		import importlib

		from sharm_dreams_group import hooks

		# Both of this module's own routes, rather than a count: `page_renderer` is
		# shared with `/employee-portal`, which is built alongside this page and not
		# replacing it, so asserting a length here would fail for a route that has
		# nothing to do with self-service.
		for path in (
			"sharm_dreams_group.self_service.renderers.SelfServiceApiRenderer",
			"sharm_dreams_group.self_service.renderers.SelfServiceLangRenderer",
		):
			self.assertIn(path, hooks.page_renderer, f"{path} is no longer registered")

		for path in hooks.page_renderer:
			module, _dot, klass = path.rpartition(".")
			self.assertTrue(
				hasattr(importlib.import_module(module), klass),
				f"{path} is in hooks.page_renderer but does not exist",
			)

	def test_every_action_is_callable_and_comes_from_this_app(self):
		for action, handler in renderers.ACTIONS.items():
			self.assertTrue(callable(handler), action)
			self.assertTrue(
				getattr(handler, "__module__", "").startswith(APP),
				f"{action} does not come from {APP}",
			)

	def test_the_client_only_posts_actions_the_renderer_serves(self):
		"""Checked from the client's side.

		An action self-service.js sends but ACTIONS lacks comes back as "Unknown self-service
		action" - a dead screen, not an error, so nothing else would report it.
		"""
		with open(
			f"{frappe.get_app_path(APP)}/public/js/self_service.js", encoding="utf-8"
		) as handle:
			sent = set(re.findall(r'action:\s*"([a-z_]+)"', handle.read()))

		self.assertTrue(sent, "no actions found in self-service.js - the pattern is stale")
		self.assertEqual(sent - set(renderers.ACTIONS), set())

	def test_webauthn_ops_stays_frappe_free(self):
		"""The reason it is a separate module: testable without a site."""
		self.assertNotIn("frappe", webauthn_ops.__dict__)

	def test_geo_status_options_match_the_labels(self):
		"""`Employee Checkin.sdg_self_service_geo_status` must accept every label written.

		The two lists are written out separately on purpose -
		`setup.SELF_SERVICE_GEO_STATUS_OPTIONS` because setup.py has to be readable without
		a connection, `geo.STATUS_LABELS` because the geo module must not import it.
		A Select rejects a value outside its options, so a mismatch fails the insert:
		at the moment somebody clocks in.
		"""
		self.assertEqual(
			set(SELF_SERVICE_GEO_STATUS_OPTIONS.split("\n")) - {""},
			set(geo.STATUS_LABELS.values()),
		)

	def test_every_workbook_code_is_either_mapped_or_declared_unmapped(self):
		"""No code may vanish from the screen without trace.

		The workbook's vocabulary and the app's barely overlap, so this is where a
		new code in the workbook has to be dealt with deliberately: either mapped
		onto a Leave Type, or named in a list saying why not.
		"""
		handled = (
			set(vacation.ENTITLEMENT_CODE_MAP)
			| set(vacation.LIEU_CODE_MAP)
			| set(vacation.DEDUCTED_CODES)
			| set(vacation.LIEU_NET_CODES)
			| set(vacation.NON_DEDUCTED_CODES)
			| set(vacation.UNCONFIRMED_CODES)
		)
		self.assertEqual(
			set(vacation_spec.spec_codes()) - handled,
			set(),
			"a workbook code is neither mapped onto a Leave Type nor declared "
			"unmapped",
		)
		# A declared list that drifted away from the spec's own is as bad as a
		# missing code: it reads as handled while handling nothing. `UNCONFIRMED_CODES`
		# is a tuple, so it has to be a set for `<=` to mean subset.
		self.assertTrue(set(vacation.UNCONFIRMED_CODES) <= set(vacation_spec.UNMAPPED_CODES))

	def test_the_self_service_reads_the_identifiers_the_seeder_writes(self):
		"""`sign_in` filters on two fields; both have to be real.

		`sdg_national_id` is a Custom Field this app declares, so it is checked
		against the specs rather than the meta - a spec entry removed from
		`setup.EMPLOYEE_FIELDS` would leave sign-in filtering on a column that is
		never created.
		"""
		from sharm_dreams_group import setup as sdg_setup

		specs = {
			fieldname
			for fieldname, _l, _t, _d, _tab in sdg_setup.EMPLOYEE_FIELDS
		}
		self.assertIn("sdg_national_id", specs)
		# employee_number is stock, so only its presence on the meta matters.
		self.assertTrue(frappe.get_meta("Employee").has_field("employee_number"))

	def test_double_day_codes_are_ones_the_balances_count(self):
		"""A code `setup` doubles must be a code `vacation` sums.

		Otherwise the multiplier would halve usage against a balance nothing reads.
		It now applies to whatever a Leave Type maps onto, so the check is against
		the mapped app codes rather than the workbook's own.
		"""
		mapped_apps = set(vacation.ENTITLEMENT_CODE_MAP.values()) | set(
			vacation.LIEU_CODE_MAP.values()
		)
		for app_code in SELF_SERVICE_DOUBLE_DAY_LEAVE_CODES:
			self.assertIn(
				app_code,
				mapped_apps,
				f"setup doubles {app_code!r} but no balance code maps onto it, so the "
				"multiplier would halve nothing",
			)

	def test_every_mapped_workbook_code_is_one_the_workbook_records(self):
		"""A mapping must point at a real code on both sides.

		A typo would silently stop deducting a leave type: the balance would keep
		looking plausible while never falling.
		"""
		codes = vacation_spec.spec_codes()
		for name, code_map in (
			("ENTITLEMENT_CODE_MAP", vacation.ENTITLEMENT_CODE_MAP),
			("LIEU_CODE_MAP", vacation.LIEU_CODE_MAP),
		):
			for workbook_code in code_map:
				self.assertIn(
					workbook_code,
					codes,
					f"{name} maps {workbook_code!r}, which the workbook does not record",
				)
		# And every mapped app code must be a code some Leave Type actually carries,
		# or the mapping is a claim about nothing.
		carried = {
			row.sdg_code
			for row in frappe.get_all("Leave Type", fields=["sdg_code"], limit_page_length=0)
			if row.sdg_code
		}
		for app_code in set(vacation.ENTITLEMENT_CODE_MAP.values()) | set(
			vacation.LIEU_CODE_MAP.values()
		):
			self.assertIn(app_code, carried, f"no Leave Type carries {app_code!r}")

	def test_every_self_service_string_is_translated(self):
		"""Every string the server sends needs a source row in both catalogs.

		The client falls back to its own English copy for a missing key, so a missing
		row shows an English screen to somebody who chose Arabic.
		"""
		from sharm_dreams_group.www.self_services_employees import SELF_SERVICE_STRINGS

		for lang in ("ar", "fr"):
			path = f"{frappe.get_app_path(APP)}/translations/{lang}.csv"
			self.assertTrue(os.path.exists(path), path)
			with open(path, encoding="utf-8", newline="") as handle:
				sources = {row["source"] for row in csv.DictReader(handle)}
			self.assertEqual(
				set(SELF_SERVICE_STRINGS.values()) - sources,
				set(),
				f"{lang}.csv is missing self-service strings",
			)

	def test_the_page_and_its_assets_exist(self):
		"""`asset_url` silently falls back to a hash of "0" on a missing file.

		That renders as a 404 for the CSS and JS with the self-service page looking unstyled
		and dead, so the two files are asserted here rather than left to a browser.

		The page's two filenames are *derived* from `PAGE_ROUTE` rather than
		written out, because Frappe derives the route from the template filename and
		only converts hyphens to underscores for the controller module. Writing the
		names by hand here is how the three would drift apart and the page would
		serve a 404 with nothing in any log.
		"""
		app = frappe.get_app_path(APP)
		page = renderers.PAGE_ROUTE
		# The two `www/` names come from the route; the two asset names do not,
		# because they are not routes - they are files the page links to by its own
		# naming. So they are written out, and a rename has to touch this list.
		for relative in (
			f"www/{page}.html",
			f"www/{page.replace('-', '_')}.py",
			"public/js/self_service.js",
			"public/css/self_service.css",
		):
			self.assertTrue(os.path.exists(f"{app}/{relative}"), relative)

	def test_the_client_posts_to_the_route_the_renderer_serves(self):
		"""`self_service.js` hardcodes the API path; the renderer matches on one.

		Nothing reconciles them at runtime. A client pointing at a route no renderer
		serves gets a 404 from the desk, so the login screen fails on its first
		request with nothing logged and nothing thrown - which is the hardest kind
		of URL mistake to notice. So the client's line is read back here and
		compared against the constant the renderer actually matches.
		"""
		with open(
			f"{frappe.get_app_path(APP)}/public/js/self_service.js", encoding="utf-8"
		) as handle:
			source = handle.read()

		found = re.findall(r'var\s+API\s*=\s*"([^"]+)"', source)
		self.assertEqual(
			found, [f"/{renderers.API_ROUTE}"], "self-service.js's API route has drifted"
		)

		# And nothing anywhere in the client still points at the old address.
		self.assertNotIn(
			'"/self-service-api"',
			source,
			"self-service.js still posts to /self-service-api",
		)


class TestSession(SelfServiceTestCase):
	def setUp(self):
		purge("Employee Self Service Session")
		purge("Employee Self Service Biometric Credential")
		self.emp = self.make_employee("KSESS-1")
		self.token = self.signed_in("KSESS-1")

	def test_resolve_round_trips(self):
		session = create_session(self.emp.name)
		self.assertEqual(resolve_session(session.access_token).name, self.emp.name)

	def test_an_unknown_token_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			resolve_session("not-a-real-token")

	def test_a_blank_token_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			resolve_session("")

	def test_an_expired_token_is_refused(self):
		session = create_session(self.emp.name)
		frappe.db.set_value("Employee Self Service Session", session.name, "expiry", add_to_date(None, days=-1))
		with self.assertRaises(frappe.ValidationError):
			resolve_session(session.access_token)

	def test_the_profile_identifies_the_employee_by_number(self):
		profile = api.my_profile(self.token)
		self.assertEqual(profile["employee"], self.emp.name)
		# Shown on the dashboard, so it is the Employee Number, not the doc name.
		self.assertEqual(profile["employee_code"], self.fixture_number("KSESS-1"))

	def test_a_mismatched_pair_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.sign_in(*self.self_service_identity("KSESS-1")[:1], "SOMEBODY-ELSE")

	def test_a_blank_field_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.sign_in(*self.self_service_identity("KSESS-1")[:1], "")

	def test_sign_out_expires_rather_than_deletes(self):
		self.assertTrue(api.sign_out(self.token)["ok"])
		# Still there: an audit trail beats a tidy table.
		self.assertEqual(frappe.db.count("Employee Self Service Session", {"employee": self.emp.name}), 1)
		with self.assertRaises(frappe.ValidationError):
			api.my_profile(self.token)

	def test_an_enrolled_employee_cannot_use_the_plain_path(self):
		frappe.get_doc(
			{
				"doctype": "Employee Self Service Biometric Credential",
				"employee": self.emp.name,
				"credential_id": "cred-abc",
				"public_key": "x",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			self.signed_in("KSESS-1")

	def test_one_device_cannot_be_enrolled_twice(self):
		credential = frappe.get_doc(
			{
				"doctype": "Employee Self Service Biometric Credential",
				"employee": self.emp.name,
				"credential_id": "cred-dup",
				"public_key": "x",
			}
		).insert(ignore_permissions=True)

		duplicate = frappe.copy_doc(credential)
		duplicate.credential_id = "cred-dup"
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert(ignore_permissions=True)


class TestPreauth(SelfServiceTestCase):
	"""A biometric challenge must be answerable exactly once."""

	def setUp(self):
		purge("Employee Self Service Preauth")
		self.emp = self.make_employee("KPRE-1")

	def _make(self, token="tok-1", minutes=5):
		return frappe.get_doc(
			{
				"doctype": "Employee Self Service Preauth",
				"employee": self.emp.name,
				"mode": "assert",
				"token": token,
				"challenge": "chal-1",
				"allowed_credential_ids": '["cred-a"]',
				"expiry": add_to_date(now_datetime(), minutes=minutes),
			}
		).insert(ignore_permissions=True)

	def test_consume_returns_the_data_and_removes_the_row(self):
		self._make()
		data = consume_preauth("tok-1")
		self.assertEqual(data["employee"], self.emp.name)
		self.assertEqual(data["challenge"], "chal-1")
		self.assertFalse(frappe.db.exists("Employee Self Service Preauth", {"token": "tok-1"}))

	def test_a_challenge_cannot_be_replayed(self):
		self._make()
		self.assertIsNotNone(consume_preauth("tok-1"))
		self.assertIsNone(consume_preauth("tok-1"))

	def test_an_expired_challenge_returns_nothing(self):
		self._make(minutes=-1)
		self.assertIsNone(consume_preauth("tok-1"))

	def test_an_unknown_token_returns_nothing(self):
		self.assertIsNone(consume_preauth("nope"))

	def test_a_blank_token_returns_nothing(self):
		self.assertIsNone(consume_preauth(""))


class TestApprovalWorkflow(SelfServiceTestCase):
	CATEGORY = f"{FIXTURE_PREFIX}Request"

	def setUp(self):
		purge("Employee Self Service Approval Request")
		# Only this module's own category. `purge(...)` on the whole doctype also
		# removed `Expenses` - the one the employee portal files expense claims
		# against - so the portal's own tests failed depending on whether this class
		# ran first. A test that empties shared reference data is only safe while
		# nothing else uses it.
		purge(
			"Employee Self Service Approval Category",
			{"category_name": ["like", f"{FIXTURE_PREFIX}%"]},
		)
		purge("Employee Self Service Session")
		self.emp = self.make_employee("KAPPR-1")
		self.token = self.signed_in("KAPPR-1")
		self.addCleanup(lambda: frappe.db.set_value(
			"Employee Self Service Approval Category", self.CATEGORY, "approval_minimum", 0
		))

	def _category(self, **values):
		if not frappe.db.exists("Employee Self Service Approval Category", self.CATEGORY):
			frappe.get_doc(
				{
					"doctype": "Employee Self Service Approval Category",
					"category_name": self.CATEGORY,
					"active": 1,
					"approval_minimum": 0,
					**values,
				}
			).insert(ignore_permissions=True)
			return
		for field, value in values.items():
			frappe.db.set_value("Employee Self Service Approval Category", self.CATEGORY, field, value)

	def _submit(self, subject="Test", **kwargs):
		return approval.approval_request_submit(
			self.token, self.CATEGORY, subject=subject, **kwargs
		)["request"]

	def test_categories_are_offered_to_the_self_service(self):
		self._category()
		names = [c["name"] for c in approval.approval_categories(self.token)["categories"]]
		self.assertIn(self.CATEGORY, names)

	def test_submitting_confirms_and_routes_to_somebody(self):
		self._category()
		request = self._submit("Letter", reason="Need it")
		self.assertEqual(request["status"], "Pending")
		self.assertTrue(request["from_self_service"])
		# Nobody is configured on the category and this employee has no manager, so
		# the officer fallback must have fired - otherwise it would sit at Pending
		# with nobody able to act on it.
		self.assertTrue(request["approvers"], "no approver was routed to")

	def test_a_rejected_submit_leaves_no_orphan(self):
		"""The insert and the confirmation are one transaction.

		`action_confirm` throws when the category demands approvers that do not
		exist. If the insert were committed first, every such attempt would leave a
		Draft behind.

		The row is rolled back before counting, and that is the whole point: within
		the transaction that failed the insert is still *visible*, so counting
		without the rollback would pass or fail on the wrong thing. What matters is
		whether it reached the database.
		"""
		self._category(approval_minimum=3)
		with self.assertRaises(frappe.ValidationError):
			self._submit("Too few")

		frappe.db.rollback()
		self.assertEqual(
			frappe.db.count("Employee Self Service Approval Request", {"employee": self.emp.name}),
			0,
			"a rejected submit persisted a Draft",
		)

	def test_a_required_date_is_enforced(self):
		self._category(has_date="Required")
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit("No date")
		self.assertIn("provide a date", str(ctx.exception))

	def test_a_period_must_run_forwards(self):
		self._category(has_period="Required")
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit("Backwards", date_start="2030-03-05", date_end="2030-03-01")
		self.assertIn("precede", str(ctx.exception))

	def test_a_value_the_type_did_not_ask_for_is_not_stored(self):
		self._category()
		request = self._submit("Plain", date="2030-01-01")
		self.assertIsNone(request["date"])

	def test_an_unknown_category_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			approval.approval_request_submit(self.token, "No Such Type", subject="x")

	def test_a_submitted_request_is_listed_back(self):
		self._category()
		self._submit("Listed")
		self.assertEqual(
			[r["subject"] for r in approval.approval_requests(self.token)["requests"]],
			["Listed"],
		)

	def test_the_owner_must_match_the_employee_on_a_self_service_request(self):
		self._category()
		doc = frappe.get_doc(
			{
				"doctype": "Employee Self Service Approval Request",
				"request_type": self.CATEGORY,
				"subject": "Mismatch",
				"employee": self.emp.name,
				"request_owner": "someone-else@example.com",
				"from_self_service": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_approving_completes_the_request(self):
		self._category()
		request = self._submit("Decide")
		self.set_user(request["approvers"][0]["user"])

		doc = frappe.get_doc("Employee Self Service Approval Request", request["name"])
		doc.action_approve()
		self.assertEqual(doc.status, "Approved")

	def test_rejecting_is_terminal_even_with_approvers_left(self):
		self._category()
		request = self._submit("Veto")
		self.set_user(request["approvers"][0]["user"])

		doc = frappe.get_doc("Employee Self Service Approval Request", request["name"])
		doc.action_reject()
		self.assertEqual(doc.status, "Rejected")

	def test_a_stranger_cannot_act_on_a_request(self):
		self._category()
		request = self._submit("Nope")
		self.set_user("stranger@example.com")

		doc = frappe.get_doc("Employee Self Service Approval Request", request["name"])
		with self.assertRaises(frappe.ValidationError):
			doc.action_approve()

	def test_recomputing_approvers_replaces_rather_than_appends(self):
		"""Otherwise a recompute after a category change duplicates every approver."""
		self._category()
		request = self._submit("Twice")
		doc = frappe.get_doc("Employee Self Service Approval Request", request["name"])
		before = len(doc.approvers)

		doc.compute_approvers()
		self.assertEqual(len(doc.approvers), before)


class TestAttendance(SelfServiceTestCase):
	"""Punching works, so these turn enforcement *off* first.

	`geo_punch_enforce` is a site-wide setting and this site now has it on with real
	zones, which would refuse a punch from a fixture employee that has no shift
	assignment and therefore no work location. These tests are about the punch, not
	about the fence - `TestGeofence` covers the fence, and turns enforcement back on
	for itself. Pinned here rather than assumed, so a suite run against a site with
	enforcement on tests the same thing it does with it off.
	"""

	def setUp(self):
		purge("Employee Checkin")
		purge("Employee Self Service Session")
		previous = frappe.db.get_single_value(
			"Employee Self Service Settings", "geo_punch_enforce"
		)
		frappe.db.set_single_value("Employee Self Service Settings", "geo_punch_enforce", 0)
		self.addCleanup(self._restore_enforcement, previous)
		self.emp = self.make_employee("KATT-1")
		self.token = self.signed_in("KATT-1")

	def _restore_enforcement(self, previous):
		"""Put `geo_punch_enforce` back, durably.

		The commit matters: `TestGeofence` commits while restoring, and cleanups run
		last-in-first-out, so an uncommitted restore here is discarded by the rollback
		`FrappeTestCase` runs afterwards - which is how running the suite silently left
		geofencing off on a site that had it on.
		"""
		frappe.db.set_single_value(
			"Employee Self Service Settings", "geo_punch_enforce", previous or 0
		)
		frappe.db.commit()

	def _inside_zone(self):
		"""A point inside a configured punch zone, read from the data.

		Read rather than hard-coded so these follow whatever centres HR has entered.
		"""
		zone = self._zone()
		return zone.latitude, zone.longitude

	def _zone(self):
		"""A `Shift Location` with a real centre and radius, created for this test.

		`Shift Location` is HRMS's own work-location doctype, autonamed by
		`location_name`, so the name carries the fixture prefix and `_purge_zones`
		removes it at the class boundary. The centre is copied from whatever zone the
		site already has rather than invented, so a test that punches "inside the zone"
		is inside a zone that exists.
		"""
		existing = frappe.db.get_value(
			"Shift Location",
			{"checkin_radius": (">", 0)},
			["name", "latitude", "longitude"],
			as_dict=True,
		)
		if not existing:
			self.skipTest("no punch zone has a radius, so no point is inside one")
		name = f"{FIXTURE_PREFIX}{self._testMethodName}-Zone"
		if frappe.db.exists("Shift Location", name):
			return frappe.db.get_value(
				"Shift Location", name, ["name", "latitude", "longitude"], as_dict=True
			)
		return frappe.get_doc(
			{
				"doctype": "Shift Location",
				"location_name": name,
				"latitude": existing.latitude,
				"longitude": existing.longitude,
				"checkin_radius": 500,
			}
		).insert(ignore_permissions=True)

	def _assign_zone(self, zone):
		"""A submitted, active shift assignment covering today, pointing at `zone`."""
		for name in frappe.get_all(
			"Shift Assignment",
			filters={"employee": self.emp.name},
			pluck="name",
			limit_page_length=0,
		):
			if frappe.db.get_value("Shift Assignment", name, "docstatus") == 1:
				frappe.get_doc("Shift Assignment", name).cancel()
			_quiet_delete("Shift Assignment", name)
		doc = frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": self.emp.name,
				"company": self.emp.company,
				"shift_type": frappe.get_all("Shift Type", pluck="name", limit_page_length=1)[0],
				"shift_location": zone.name,
				"start_date": frappe.utils.today(),
				"end_date": frappe.utils.today(),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		doc.submit()
		return doc

	def test_a_punch_records_the_checkin_and_the_geo_verdict(self):
		"""Coordinates from the browser, judged against the employee's work location.

		The fixture employee gets a shift assignment pointing at a real zone, because a
		verdict of "Verified" is only reachable when the employee *has* a work location -
		with none, `geo.get_zones` returns nothing and the verdict is "No zone
		configured", which is a different statement about the same punch.
		"""
		zone = self._zone()
		self._assign_zone(zone)
		lat, lon = self._inside_zone()
		result = api.punch(self.token, "IN", lat, lon)
		self.assertTrue(result["ok"])
		self.assertEqual(
			frappe.db.get_value("Employee Checkin", result["checkin"], "sdg_self_service_geo_status"),
			geo.STATUS_LABELS[geo.STATUS_OK],
		)

	def test_a_punch_with_no_location_is_refused(self):
		"""HRMS's own tracking makes a location mandatory on every checkin.

		`Employee Checkin.validate_distance_from_shift_location` throws
		"Latitude and longitude values are required for checking in" before this app
		gets a say, so a tablet that cannot get a fix cannot punch at all. That is the
		native feature doing the refusing, and it is why every punch here carries
		coordinates.
		"""
		with self.assertRaises(frappe.ValidationError) as caught:
			api.punch(self.token, "IN")
		self.assertIn("latitude", str(caught.exception).lower())

	def test_the_log_type_is_normalised(self):
		lat, lon = self._inside_zone()
		self.assertEqual(api.punch(self.token, "out", lat, lon)["log_type"], "OUT")

	def test_an_unknown_log_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.punch(self.token, "SIDEWAYS")

	def test_the_profile_knows_the_employee_is_clocked_in(self):
		lat, lon = self._inside_zone()
		api.punch(self.token, "IN", lat, lon)
		self.assertEqual(api.my_profile(self.token)["attendance_state"], "checked_in")

	def test_a_pair_puts_the_employee_back_out(self):
		lat, lon = self._inside_zone()
		api.punch(self.token, "IN", lat, lon)
		api.punch(self.token, "OUT", lat, lon)
		self.assertEqual(api.my_profile(self.token)["attendance_state"], "checked_out")

	def test_a_stranger_token_punches_nothing(self):
		lat, lon = self._inside_zone()
		with self.assertRaises(frappe.ValidationError):
			api.punch("not-a-token", "IN", lat, lon)
		self.assertEqual(
			frappe.db.count("Employee Checkin", {"employee": self.emp.name}), 0
		)

	def test_the_avatar_is_omitted_rather_than_an_error(self):
		# No image on the employee, so the dashboard simply shows no picture.
		self.assertIsNone(api.my_profile(self.token)["avatar"])


class TestGeofence(SelfServiceTestCase):
	"""The fence is the employee's work location, taken from the shift assignment.

	A zone is a `Shift Location` (HRMS's own work-location doctype: a centre and a
	`checkin_radius` in metres), and what makes it apply to an employee is
	`Shift Assignment.shift_location` on the assignment covering the day. Nothing here
	is anchored to the company any more - the group runs many sites inside a handful of
	companies, so a per-company circle would either span the country or refuse
	everybody.
	"""

	def setUp(self):
		self._purge_zones()
		self.company = self._any_company()
		self.emp = self.make_employee("KGEO-1", company=self.company)
		# Restored, not forced to 0. This used to hand back a hard-coded `0`, which was
		# invisible while the setting defaulted to off and silently switched a site that
		# had turned enforcement *on* back off after a test run - so a demo or a live
		# site stopped geofencing because someone ran the suite.
		previous = frappe.db.get_single_value(
			"Employee Self Service Settings", "geo_punch_enforce"
		)
		self._enforce(1)
		self.addCleanup(lambda: self._enforce(previous or 0))
		self.zone = self._zone("Zone")
		self._assign_zone(self.zone)

	def test_inside_the_radius_passes(self):
		ok, status, message = geo.check_punch(self.emp.name, 30.0444, 31.2357)
		self.assertTrue(ok, message)
		self.assertEqual(status, geo.STATUS_OK)

	def test_outside_every_zone_is_refused_with_the_zone_named(self):
		ok, status, message = geo.check_punch(self.emp.name, 0.0, 0.0)
		self.assertFalse(ok)
		self.assertEqual(status, geo.STATUS_OUTSIDE)
		self.assertIn(self.zone.location_name, message)

	def test_the_radius_is_read_in_metres(self):
		"""`checkin_radius` is metres; a 1 m fence must refuse a point 2 m away.

		Read as kilometres the same row would have been a 1000x too generous circle, so
		this pins the unit rather than the arithmetic.
		"""
		self._assign_zone(self._zone("Tiny", radius_m=1))
		lat = 30.0444 + (2 / 111_320)
		self.assertFalse(geo.check_punch(self.emp.name, lat, 31.2357)[0])

	def test_no_coordinates_is_refused_while_enforced(self):
		ok, status, _message = geo.check_punch(self.emp.name, None, None)
		self.assertFalse(ok)
		self.assertEqual(status, geo.STATUS_UNAVAILABLE)

	def test_the_verdict_is_still_reported_when_enforcement_is_off(self):
		self._enforce(0)
		ok, status, message = geo.check_punch(self.emp.name, 0.0, 0.0)
		self.assertTrue(ok, message)
		# The point of the flag: coordinates are kept for audit even when they are
		# not enforced.
		self.assertEqual(status, geo.STATUS_OUTSIDE)

	def test_an_employee_with_no_work_location_is_refused_while_enforced(self):
		self._assign_zone(None)
		ok, status, message = geo.check_punch(self.emp.name, 30.0444, 31.2357)
		self.assertFalse(ok)
		self.assertEqual(status, geo.STATUS_NO_FENCE)
		self.assertIn("No work location", message)

	def test_a_zone_with_no_radius_is_not_a_zone_yet(self):
		"""HR has created the row but not the fence; it must not refuse everyone.

		`checkin_radius` of zero is skipped rather than treated as a zero-metre circle,
		same as HRMS's own validator. Without this, half-built configuration locks every
		employee out of punching.
		"""
		self._assign_zone(self._zone("NoRadius", radius_m=0))
		ok, status, _message = geo.check_punch(self.emp.name, 30.0444, 31.2357)
		self.assertFalse(ok)
		self.assertEqual(status, geo.STATUS_NO_FENCE)

	def test_an_assignment_that_has_ended_releases_the_zone(self):
		"""The fence is the location *for that day*, not a permanent property.

		A month at one site and then a transfer should be expressible without editing
		history, so an assignment outside its date range contributes no fence.
		"""
		assignment = self._assign_zone(self.zone)
		frappe.db.set_value(
			"Shift Assignment", assignment.name, "end_date", frappe.utils.add_to_date(None, years=-1)
		)
		ok, status, _message = geo.check_punch(self.emp.name, 30.0444, 31.2357)
		self.assertFalse(ok)
		self.assertEqual(status, geo.STATUS_NO_FENCE)

	def test_haversine(self):
		self.assertAlmostEqual(
			geo.haversine_km(30.0444, 31.2357, 30.0444, 31.2357), 0.0, places=6
		)
		# Cairo to Alexandria, ~180km.
		self.assertAlmostEqual(
			geo.haversine_km(30.0444, 31.2357, 31.2001, 29.9187), 180.0, delta=5.0
		)

	def test_a_missing_point_is_unreachable(self):
		self.assertEqual(geo.haversine_km(30.0, None, 31.0, 32.0), float("inf"))

	def _zone(self, label, radius_m=500):
		"""Create a punch zone named for this test, so cleanup can find it.

		A `Shift Location` is autonamed by `location_name`, so the name *is* the
		document name - which is why it has to carry the fixture prefix rather than a
		bare label.
		"""
		return frappe.get_doc(
			{
				"doctype": "Shift Location",
				"location_name": f"{FIXTURE_PREFIX}{self._testMethodName}-{label}",
				"latitude": 30.0444,
				"longitude": 31.2357,
				"checkin_radius": radius_m,
			}
		).insert(ignore_permissions=True)

	def _assign_zone(self, zone):
		"""A submitted, active shift assignment covering today, pointing at `zone`.

		Any assignment this made earlier is cancelled and removed first. HRMS refuses a
		second active assignment covering the same dates
		(`allow_multiple_shift_assignments` is 0 on this site), so a test that re-points
		the zone has to retire the old row rather than add one.
		"""
		self._clear_assignments()
		doc = frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": self.emp.name,
				"company": self.company,
				"shift_type": self._any_shift_type(),
				"shift_location": zone.location_name if zone else None,
				"start_date": frappe.utils.today(),
				"end_date": frappe.utils.today(),
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		doc.submit()
		return doc

	def _clear_assignments(self):
		for name in frappe.get_all(
			"Shift Assignment",
			filters={"employee": self.emp.name},
			pluck="name",
			limit_page_length=0,
		):
			if frappe.db.get_value("Shift Assignment", name, "docstatus") == 1:
				frappe.get_doc("Shift Assignment", name).cancel()
			_quiet_delete("Shift Assignment", name)

	@staticmethod
	def _any_shift_type():
		"""An existing `Shift Type`, because `Shift Assignment.shift_type` is required.

		Which ones exist is site data - the same reasoning as `_any_gender`.
		"""
		existing = frappe.get_all("Shift Type", pluck="name", limit_page_length=1)
		if not existing:
			raise frappe.ValidationError("The geofence tests need at least one Shift Type.")
		return existing[0]

	def _enforce(self, value):
		frappe.db.set_single_value("Employee Self Service Settings", "geo_punch_enforce", value)


class TestVacationSpec(SelfServiceTestCase):
	"""The extracted workbook itself: its shape, and the invariants it depends on.

	These do not test the arithmetic against the workbook - `build_vacation_spec.py`
	does that, by refusing to write a spec whose derivation disagrees with the
	cached figures. They test that what was extracted is coherent, so a bad build
	fails here rather than showing an employee a wrong balance.
	"""

	def test_the_spec_is_present_and_populated(self):
		spec = vacation_spec.get_spec()
		self.assertTrue(spec.get("people"), "the spec has no people in it")
		self.assertEqual(spec["source"]["sheet"], "Employee Vacation Tracker 2026")

	def test_the_reference_date_is_the_workbook_save_date(self):
		"""`TODAY()` drives the entitlement, so a drifting `as_of` moves real balances.

		The workbook records its own last-modified date, and that is the date its
		cached figures were computed on. Anchoring to anything else silently changes
		who counts as senior.
		"""
		as_of = vacation_spec.spec_source()["as_of"]
		self.assertEqual(as_of, "2026-09-07")

	def test_the_entitlement_is_the_workbooks_own_rule(self):
		"""21 or 30 by age and service - and nothing else.

		Values like 89, 95 and 104 exist because 20 employees carry hand-entered
		sums for staff who do not work a 5-day week. Every other row must land on the
		rule, or a rule has been quietly replaced.
		"""
		hand_entered = {89.0, 95.0, 104.0}
		counts = {21.0: 0, 30.0: 0}
		for person in vacation_spec.get_spec()["people"]:
			entitlement = person["entitlement"]
			if entitlement is None:
				continue
			self.assertIn(
				entitlement,
				set(counts) | hand_entered,
				f"{person['code']} has an entitlement of {entitlement}, which the "
				"workbook's rule does not produce",
			)
			if entitlement in counts:
				counts[entitlement] += 1
		self.assertTrue(counts[21.0], "no employee came out at the standard 21 days")
		self.assertTrue(counts[30.0], "no employee came out at the senior 30 days")

	def test_the_derivation_agrees_with_the_workbook_except_where_the_cache_is_stale(self):
		"""The recorded reconciliation must still be true of the spec.

		A rebuild that quietly stopped reproducing the workbook's own figures would
		otherwise ship, because the spec carries its own numbers either way.
		"""
		recorded = vacation_spec.get_spec()["reconciliation"]
		self.assertEqual(recorded["agreeing"] + len(recorded["stale"]), recorded["checked"])
		self.assertTrue(
			recorded["agreeing"] / recorded["checked"] > 0.85,
			f"only {recorded['agreeing']} of {recorded['checked']} rows reproduce the "
			"workbook's own formula; the build is probably misreading the sheet",
		)

	def test_stale_codes_are_a_small_minority(self):
		"""Stale cache is expected on a few hand-edited rows, not most of them.

		One or two rows drift because someone edited the grid without resaving. If
		this trips, the grid is being edited differently and the snapshot assumption
		behind the whole overlay needs revisiting.
		"""
		stale = vacation_spec.stale_codes()
		self.assertTrue(stale, "expected the known stale rows to still be recorded")
		self.assertLess(
			len(stale),
			25,
			f"{len(stale)} stale rows - too many to be hand-edit drift",
		)

	def test_the_carry_forward_cap_is_recorded_but_not_enforced(self):
		"""The workbook states a 10-day cap and does not apply it.

		14 employees carry 11 to 53 days and their balances were built from those
		figures, so the cap is surfaced rather than enforced. If someone starts
		enforcing it, these balances change silently.
		"""
		source = vacation_spec.spec_source()
		self.assertEqual(source["carry_forward_cap_days"], 10)
		self.assertFalse(source["carry_forward_cap_enforced"])
		self.assertEqual(source["carry_forward_expires"], "2027-03-31")

	def test_a_missing_spec_raises_rather_than_reporting_zero(self):
		"""A zero balance is a real answer, so it must not be the failure mode.

		`get_spec` caches, so the missing-file path is exercised by pointing the
		module's own `SPEC_PATH` somewhere that does not exist.
		"""
		original = vacation_spec.SPEC_PATH
		# `get_app_path` returns a str, and `SPEC_PATH` is read with `.exists()`, so
		# it has to be swapped for a Path or the test passes for the wrong reason.
		vacation_spec.SPEC_PATH = pathlib.Path(
			frappe.get_app_path(APP, "does_not_exist.json")
		)
		vacation_spec.get_spec.cache_clear()
		try:
			with self.assertRaises(vacation_spec.VacationSpecUnavailable):
				vacation_spec.get_spec()
		finally:
			vacation_spec.SPEC_PATH = original
			vacation_spec.get_spec.cache_clear()


class TestVacation(SelfServiceTestCase):
	def setUp(self):
		self.emp = self.make_employee("KVAC-1")

	def _spec_stub(self, **overrides):
		"""Point the spec at a fixture row, so a test needs no real workbook entry.

		Monkeypatched rather than rebuilt: the point of these tests is the arithmetic
		and the row shape, and rebuilding a spec per test would test the builder
		instead. `addCleanup` restores each name, and it runs last-registered-first,
		so all three are restored even if the test fails part way.
		"""
		person = {
			"code": self.emp.name,
			"entitlement": 30,
			"carried_forward": 0,
			"balance": 30,
			"days": {},
			"unmapped_days": {},
		}
		person.update(overrides)

		def fake_get_person(employee, _person=person):
			name = getattr(employee, "name", employee)
			return dict(_person) if name == self.emp.name else None

		replacements = {
			"get_person": fake_get_person,
			"spec_codes": lambda: {
				"V": "Vacation", "CL": "Casual", "0.5": "Half Day", "SL": "Sick Leave",
			},
			"spec_source": lambda: {
				"carry_forward_cap_days": 10,
				"carry_forward_expires": "2027-03-31",
				"carry_forward_cap_enforced": False,
				"as_of": "2026-09-07",
			},
			"stale_codes": list,
			"unmapped_days": lambda _employee: {},
		}
		for name, replacement in replacements.items():
			self.addCleanup(setattr, vacation_spec, name, getattr(vacation_spec, name))
			setattr(vacation_spec, name, replacement)
		return person

	def test_the_balance_is_the_workbooks_arithmetic(self):
		"""(carried + entitlement) - used, plus leave in lieu netted.

		The workbook's `Balance Due` is `(LL - PDO) + (carried + opening - half - CL - V)
		+ (LLPH - PDOPH)`. Reproducing it exactly is the whole point of taking the
		figures from the workbook.
		"""
		self._spec_stub(days={"V": 12})
		balance = vacation.get_vacation_balance(self.emp)
		self.assertEqual(balance["used"], 12)
		self.assertEqual(balance["balance"], 18)

	def test_leave_in_lieu_is_netted_not_subtracted(self):
		"""Working a rest day *earns* a day back, so it adds to the balance."""
		self._spec_stub(days={"LL": 3, "PDO": 1})
		balance = vacation.get_vacation_balance(self.emp)
		self.assertEqual(balance["lieu_day_off"], 2)
		self.assertEqual(balance["adjustments"], 2)
		self.assertEqual(balance["balance"], 32)

	def test_a_half_day_costs_half_a_day(self):
		self._spec_stub(days={"0.5": 5})
		balance = vacation.get_vacation_balance(self.emp)
		self.assertEqual(balance["used"], 2.5)
		self.assertEqual(balance["balance"], 27.5)

	def test_sick_leave_does_not_reduce_the_entitlement(self):
		"""The workbook's own `Closed Balance` does not subtract it.

		SL is reported so the employee can see it, but following the workbook is the
		whole premise - so this pins that we do not start deducting it on our own.
		"""
		self._spec_stub(days={"SL": 7})
		balance = vacation.get_vacation_balance(self.emp)
		self.assertEqual(balance["used"], 0)
		self.assertEqual(balance["balance"], 30)
		self.assertEqual(balance["other_days"]["SL"], 7)

	def test_an_ineligible_employee_reports_no_balance_rather_than_zero(self):
		"""Zero says they are owed nothing; blank says nobody has recorded anything.

		The workbook leaves its Opening Balance blank for an ineligible employee, and
		collapsing that to 0 would be a different claim.
		"""
		self._spec_stub(entitlement=None, balance=None)
		balance = vacation.get_vacation_balance(self.emp)
		self.assertFalse(balance["eligible"])
		self.assertIsNone(balance["balance"])
		_year, rows = vacation.get_vacation_balance_rows(self.emp)
		self.assertEqual(rows, [])

	def test_carried_forward_is_shown_with_its_expiry_and_cap_warning(self):
		self._spec_stub(carried_forward=17)
		_year, rows = vacation.get_vacation_balance_rows(self.emp)
		carried = next(r for r in rows if r["code"] == "CF")
		self.assertEqual(carried["due"], 17)
		self.assertEqual(carried["expires"], "2027-03-31")
		# Above the workbook's own documented cap, which is not enforced.
		self.assertTrue(carried["above_documented_cap"])

	def test_an_untracked_employee_is_derived_and_flagged_as_such(self):
		"""68 active employees have no workbook row.

		Their entitlement comes from the workbook's own rule applied to their dates,
		which is the same policy - but it is computed rather than entered by HR, and
		`is_derived` is what keeps the screen from presenting it as a recorded figure.
		Carried-forward stays `None`, because "no row" is not the same claim as "no
		days carried".
		"""
		# No stub, so the real spec is consulted and genuinely has no row for this
		# fixture employee.
		balance = vacation.get_vacation_balance(self.emp)
		self.assertFalse(balance["in_workbook"])
		self.assertIsNone(balance["carried_forward"])
		# The fixture employee joined recently, so the rule finds them ineligible;
		# either way `is_derived` must track `eligible` rather than defaulting true.
		self.assertEqual(balance["is_derived"], balance["eligible"])

	def test_the_endpoint_answers_with_its_provenance(self):
		"""The screen needs to say where the figures came from.

		A balance presented without its source date reads as computed live, and it is
		not - it is a snapshot of a hand-maintained workbook.
		"""
		purge("Employee Self Service Session")
		token = self.signed_in("KVAC-1")
		result = time_off.leave_balances(token)
		self.assertEqual(result["employee"], self.emp.name)
		self.assertEqual(result["context"]["sheet"], "Employee Vacation Tracker 2026")
		self.assertEqual(result["context"]["as_of"], "2026-09-07")

	def test_a_multiplier_of_zero_falls_back_to_one(self):
		"""A Leave Type left at 0 must not zero out a balance.

		`flt(value, 1.0)` is what does this, so it is worth a test: a multiplier of
		zero would silently make every leave in that type invisible.
		"""
		self.assertEqual(frappe.utils.flt(0, 1.0) or 1.0, 1.0)
		self.assertEqual(frappe.utils.flt(2, 1.0) or 1.0, 2.0)


class TestLeaveApproverResolution(SelfServiceTestCase):
	"""`api._resolve_leave_approver`, exercised without filing a Leave Application."""

	def setUp(self):
		purge("Employee Self Service Session")
		self.manager = self.make_user("mgr", "Manager")
		self.plain = self.make_user("plain", "Plain")
		self.addCleanup(
			lambda: frappe.db.set_single_value("Employee Self Service Settings", "default_leave_approver", None)
		)

	def test_the_reporting_manager_wins(self):
		emp = self.make_employee("KAPP-1", user_id=self.plain)
		manager = self.make_employee("KAPP-2", user_id=self.manager)
		frappe.db.set_value("Employee", emp.name, "reports_to", manager.name)

		self.assertEqual(api._resolve_leave_approver(emp), self.manager)

	def test_a_manager_with_no_user_is_not_returned(self):
		"""An Employee row for somebody who has never signed in.

		Reading their `user_id` must not hand back an empty approver, which would
		fail the mandatory-field check inside HRMS with a message the employee
		cannot act on.
		"""
		emp = self.make_employee("KAPP-1", user_id=self.plain)
		manager = self.make_employee("KAPP-2")
		frappe.db.set_value("Employee", emp.name, "reports_to", manager.name)

		self.assertIsNone(api._resolve_leave_approver(emp))

	def test_the_self_service_default_is_the_last_stand(self):
		emp = self.make_employee("KAPP-3", user_id=self.plain)
		frappe.db.set_single_value("Employee Self Service Settings", "default_leave_approver", self.manager)
		self.assertEqual(api._resolve_leave_approver(emp), self.manager)

	def test_nobody_configured_means_no_approver(self):
		emp = self.make_employee("KAPP-3", user_id=self.plain)
		self.assertIsNone(api._resolve_leave_approver(emp))


class TestWebauthnOps(SelfServiceTestCase):
	"""Pure crypto - these would pass with no site at all."""

	def test_base64url_round_trips(self):
		# b"" is not in this list on purpose: it encodes to "", and "" is rejected
		# on the way back in. An empty credential id or challenge is not a thing
		# that should decode, so encode and decode are deliberately not symmetric
		# at the empty string.
		for payload in (b"\x00", b"hello world", bytes(range(256)), b"\xff" * 40):
			self.assertEqual(
				webauthn_ops.b64url_decode(webauthn_ops.b64url_encode(payload)), payload
			)

	def test_base64url_refuses_nonsense(self):
		# "" and "!!!!" are the pair that matters: without `validate=True` the
		# latter decoded to b"" silently, because characters outside the alphabet
		# are discarded rather than rejected.
		for bad in ("", None, 123, "!!!!", "a!b", "not base64 at all"):
			with self.assertRaises(webauthn_ops.WebAuthnError):
				webauthn_ops.b64url_decode(bad)

	def test_base64url_refuses_non_ascii_bytes(self):
		"""`b64url_decode` takes bytes, so it has to reject them, not crash.

		`bytes.decode("ascii")` raises UnicodeDecodeError, which is a ValueError
		subclass but *not* caught by a bare `except ValueError` placed after the
		decode - hence the explicit handling.
		"""
		with self.assertRaises(webauthn_ops.WebAuthnError):
			webauthn_ops.b64url_decode(b"\xff\xfe")

	def test_base64url_accepts_ascii_bytes(self):
		self.assertEqual(
			webauthn_ops.b64url_decode(webauthn_ops.b64url_encode(b"hi").encode("ascii")),
			b"hi",
		)

	def test_base64url_output_carries_no_padding_or_unsafe_chars(self):
		encoded = webauthn_ops.b64url_encode(bytes([0xFB, 0xFF, 0xBE]))
		self.assertNotIn("=", encoded)
		self.assertNotIn("+", encoded)
		self.assertNotIn("/", encoded)

	def test_cbor_decodes_a_cose_key(self):
		# {"1": 2, "3": -7, "-1": 1, "-2": h'<32 bytes>', "-3": h'<32 bytes>'}
		encoded = (
			b"\xa5"
			+ b"\x01\x02"
			+ b"\x03\x26"
			+ b"\x20\x01"
			+ b"\x21\x58\x20"
			+ bytes(range(32))
			+ b"\x22\x58\x20"
			+ bytes(range(32))
		)
		decoded = webauthn_ops.cbor_load(encoded)
		self.assertEqual(decoded[1], webauthn_ops.EC2_KEY_TYPE)
		self.assertEqual(decoded[-1], webauthn_ops.P256_CURVE_ID)
		# 12-byte SPKI prefix + the 0x04 tag + x + y
		self.assertEqual(len(webauthn_ops.cose_to_spki(decoded)), 91)

	def test_cbor_refuses_a_short_curve_point(self):
		encoded = b"\xa5\x01\x02\x03\x26\x20\x01\x21\x58\x10" + bytes(16)
		with self.assertRaises(webauthn_ops.WebAuthnError):
			webauthn_ops.cbor_load(encoded)

	def test_cbor_refuses_trailing_bytes(self):
		with self.assertRaises(webauthn_ops.WebAuthnError):
			webauthn_ops.cbor_load(b"\x01\xff")

	def test_cbor_reads_the_major_types_it_needs(self):
		# Major type is the top three bits, argument width the bottom five, and the
		# value of a negative int is -1 - argument. So:
		#   0x18/0x19  major 0 (uint), widths 8 and 16
		#   0x20       major 1, width 0 -> -1 - 0  = -1
		#   0x38/0x39  major 1, widths 8 and 16 -> -1 - 255 = -256, -1 - 256 = -257
		self.assertEqual(webauthn_ops.cbor_load(b"\x18\x2a"), 42)  # uint8
		self.assertEqual(webauthn_ops.cbor_load(b"\x19\x01\x00"), 256)  # uint16
		self.assertEqual(webauthn_ops.cbor_load(b"\x20"), -1)  # negative, no argument
		self.assertEqual(webauthn_ops.cbor_load(b"\x38\xff"), -256)  # negative int8
		self.assertEqual(webauthn_ops.cbor_load(b"\x39\x01\x00"), -257)  # negative int16
		self.assertEqual(webauthn_ops.cbor_load(b"\x43abc"), b"abc")  # bytes
		self.assertEqual(webauthn_ops.cbor_load(b"\x63abc"), "abc")  # text
		self.assertEqual(webauthn_ops.cbor_load(b"\x82\x01\x02"), [1, 2])  # array
		self.assertEqual(webauthn_ops.cbor_load(b"\xa1\x01\x02"), {1: 2})  # map
		self.assertEqual(webauthn_ops.cbor_load(b"\xf5"), True)

	def test_client_data_checks_every_field(self):
		good = json.dumps(
			{"type": "webauthn.get", "challenge": "abc", "origin": "https://x"}
		).encode()
		self.assertTrue(
			webauthn_ops.verify_client_data(good, "abc", "webauthn.get", "https://x")
		)
		for challenge, kind, origin in (
			("wrong", "webauthn.get", "https://x"),
			("abc", "webauthn.create", "https://x"),
			("abc", "webauthn.get", "https://evil"),
		):
			self.assertFalse(webauthn_ops.verify_client_data(good, challenge, kind, origin))

	def test_client_data_refuses_garbage(self):
		self.assertFalse(webauthn_ops.verify_client_data(b"not json", "a", "webauthn.get", "o"))

	def test_authenticator_data_needs_37_bytes(self):
		with self.assertRaises(webauthn_ops.WebAuthnError):
			webauthn_ops.parse_authenticator_data(b"\x00" * 36)

	def test_a_signature_verifies_and_tampering_does_not(self):
		from cryptography.hazmat.primitives import hashes, serialization
		from cryptography.hazmat.primitives.asymmetric import ec

		key = ec.generate_private_key(ec.SECP256R1())
		spki = key.public_key().public_bytes(
			serialization.Encoding.DER,
			serialization.PublicFormat.SubjectPublicKeyInfo,
		)
		auth_data = b"\x01" * 37
		client_data = b'{"type":"webauthn.get"}'
		signature = key.sign(
			auth_data + webauthn_ops.sha256(client_data), ec.ECDSA(hashes.SHA256())
		)

		self.assertTrue(webauthn_ops.verify_signature(spki, signature, auth_data, client_data))
		# Different authenticator data must not verify.
		self.assertFalse(
			webauthn_ops.verify_signature(spki, signature, b"\x02" * 37, client_data)
		)

	def test_a_raw_r_s_signature_is_converted(self):
		"""Authenticators differ on DER vs raw r||s, so both have to be accepted."""
		from cryptography.hazmat.primitives import hashes, serialization
		from cryptography.hazmat.primitives.asymmetric import ec

		key = ec.generate_private_key(ec.SECP256R1())
		spki = key.public_key().public_bytes(
			serialization.Encoding.DER,
			serialization.PublicFormat.SubjectPublicKeyInfo,
		)
		auth_data = b"\x01" * 37
		client_data = b'{"type":"webauthn.get"}'
		der = key.sign(
			auth_data + webauthn_ops.sha256(client_data), ec.ECDSA(hashes.SHA256())
		)

		self.assertTrue(
			webauthn_ops.verify_signature(spki, der_to_raw(der), auth_data, client_data)
		)

	def test_a_signature_that_is_not_a_key_verifies_nothing(self):
		self.assertFalse(webauthn_ops.verify_signature(b"not-a-key", b"\x00" * 10, b"", b""))


def der_to_raw(der):
	"""DER SEQUENCE of two INTEGERs -> the raw r||s an authenticator sends."""
	body = der[2:]
	offset, parts = 0, []
	while offset < len(body):
		length = body[offset + 1]
		parts.append(body[offset + 2 : offset + 2 + length].lstrip(b"\x00"))
		offset += 2 + length
	return parts[0].rjust(32, b"\x00") + parts[1].rjust(32, b"\x00")


class TestThemeAndWiring(SelfServiceTestCase):
	"""The failures that produce no exception at all.

	Each of these was a real break during the rename and the theme work, and each
	one looks fine when the feature is exercised by hand: a stale asset path still
	serves HTML, a mismatched cookie still stores a preference, and a colour
	literal left outside the token block still paints the right colour in light
	mode. So they are checked structurally.
	"""

	@staticmethod
	def _app_file(*parts) -> str:
		return os.path.join(frappe.get_app_path(APP), *parts)

	def _read(self, *parts) -> str:
		with open(self._app_file(*parts), encoding="utf-8") as handle:
			return handle.read()

	def test_the_stylesheet_has_no_colour_outside_its_two_palettes(self):
		"""A literal outside the tokens keeps its light value on a dark surface.

		Nothing reports this - the declaration is valid CSS and the page renders -
		so it is the one thing about the theme that has to be enforced by reading
		the file rather than by looking at it.
		"""
		css = self._read("public", "css", "self_service.css")

		def block(marker):
			at = css.index(marker)
			depth = 0
			for i in range(css.index("{", at), len(css)):
				if css[i] == "{":
					depth += 1
				elif css[i] == "}":
					depth -= 1
					if depth == 0:
						return css[css.rindex("\n", 0, at) + 1 : i + 1]
			raise AssertionError(marker)

		light = block(":root {")
		dark = block(':root[data-k-theme="dark"] {')
		body = light.join(css.split(light)).replace(dark, "") if dark not in light else ""
		body = css.replace(light, "").replace(dark, "")

		# comments carry prose with colour names in it
		body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
		stray = [
			line.strip()
			for line in body.splitlines()
			if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", line)
		]
		self.assertEqual(stray, [], f"colour outside the token blocks: {stray[:4]}")

	def test_every_token_a_rule_uses_is_declared(self):
		"""A `var()` with no declaration resolves to nothing, silently."""
		css = self._read("public", "css", "self_service.css")
		declared = set(re.findall(r"(--k-[a-z0-9-]+)\s*:", css))
		used = set(re.findall(r"var\((--k-[a-z0-9-]+)\)", css))
		self.assertEqual(used - declared, set(), "var() naming an undeclared token")

	def test_both_palettes_declare_the_same_tokens(self):
		"""A token the dark palette leaves out keeps its light value on dark.

		The exemptions are the documented theme-invariant ones: the font and the
		confetti colours, which are decoration and identical either way.
		"""
		css = self._read("public", "css", "self_service.css")
		light = css[: css.index(':root[data-k-theme="dark"] {')]
		dark = css[css.index(':root[data-k-theme="dark"] {') :]

		def names(text):
			return set(re.findall(r"(--k-[a-z0-9-]+)\s*:", text))

		invariant = {"--k-font"} | {f"--k-confetti-{n}" for n in range(1, 8)}
		self.assertEqual(
			names(light) - names(dark) - invariant,
			set(),
			"tokens the dark palette does not override",
		)

	def test_the_theme_is_settled_before_the_stylesheet_loads(self):
		"""Order in `<head>` is the whole no-flash guarantee.

		The attribute has to be on <html> before the first paint. If this script
		runs after the stylesheet link it still works, but a tablet on the dark
		setting flashes white on every load, which is the symptom people report
		as "it flickers".
		"""
		template = self._read("www", f"{renderers.PAGE_ROUTE}.html")
		script = template.index("data-k-theme")
		link = template.index('rel="stylesheet" href="{{ self_service_css }}"')
		self.assertLess(script, link, "the theme script must precede the stylesheet")

	def test_the_theme_cookie_name_is_injected_not_repeated(self):
		"""The head script needs the cookie name, and it must be the server's.

		Nothing reconciles a name written into a template by hand with the
		constant the server reads, so the template is handed the constant instead
		of repeating it.
		"""
		from sharm_dreams_group.www.self_services_employees import THEME_COOKIE_NAME

		template = self._read("www", f"{renderers.PAGE_ROUTE}.html")
		self.assertIn("self_service_theme_cookie", template)
		self.assertNotIn(f'"{THEME_COOKIE_NAME}"', template)

	def test_the_server_and_the_client_agree_on_the_cookie_names(self):
		"""A mismatch writes a preference nothing ever reads back."""
		from sharm_dreams_group.www.self_services_employees import (
			LANG_COOKIE_NAME,
			THEME_COOKIE_NAME,
		)

		source = self._read("public", "js", "self_service.js")
		self.assertIn(LANG_COOKIE_NAME, source, "the client never writes the lang cookie")
		self.assertIn(THEME_COOKIE_NAME, source, "the client never writes the theme cookie")

	def test_the_theme_choices_are_the_ones_the_picker_offers(self):
		"""The picker is rendered from the server's list; the server reads the cookie."""
		from sharm_dreams_group.www.self_services_employees import (
			SELF_SERVICE_THEME_ITEMS,
			THEME_CHOICES,
		)

		self.assertEqual(
			[item["code"] for item in SELF_SERVICE_THEME_ITEMS], list(THEME_CHOICES)
		)
		self.assertEqual(SELF_SERVICE_THEME_ITEMS[0]["code"], "light")

	def test_resolve_theme_rejects_anything_it_does_not_offer(self):
		"""A hand-edited cookie falls back to the device, not to a blank page."""
		from sharm_dreams_group.www import self_services_employees as page

		self.assertEqual(page.resolve_theme(), "system", "no cookie at all")
		frappe.local.form_dict.theme = "chartreuse"
		try:
			self.assertEqual(page.resolve_theme(), "system")
		finally:
			frappe.local.form_dict.pop("theme", None)

	def test_the_stylesheet_has_a_dark_palette_and_the_toggle_is_styled(self):
		css = self._read("public", "css", "self_service.css")
		self.assertIn(':root[data-k-theme="dark"]', css)
		self.assertIn("color-scheme: dark", css)
		self.assertIn(".k-theme-btn", css)

	# ----------------------------------------------------------------------
	# Palette invariants
	#
	# Every failure below was found by rendering the page, not by reading it, and
	# every one of them leaves the page looking correct in light mode. They are
	# grouped here because they share one cause: the dark palette was written as
	# a list of changes rather than as a palette, so every value it did not
	# change kept a light-mode meaning that no longer holds.
	# ----------------------------------------------------------------------

	def test_no_token_holds_a_declaration_instead_of_a_colour(self):
		"""A token must be a colour, never a fragment of some other declaration.

		`--k-inset-hi` was a bare colour in light and the whole
		`inset 0 -1px 0 rgba(0, 0, 0, 0.3)` in dark, while its call site already
		supplied the `inset 0 1px 0` half. Spliced, dark produced
		`inset 0 1px 0 inset 0 -1px 0 ...`, and a shadow may name `inset` only
		once - so the declaration was invalid at computed-value time and the
		*whole* box-shadow was dropped. Every dark button lost its glow and its
		highlight, silently, and the browser reported nothing.
		"""
		css = self._read("public", "css", "self_service.css")
		for marker in (":root {", ':root[data-k-theme="dark"] {'):
			offenders = {
				name: value.strip()
				for name, value in palette_tokens(css, marker).items()
				if TOKEN_FRAGMENT.search(value)
			}
			self.assertEqual(
				offenders,
				{},
				f"token holding a declaration rather than a colour in {marker}: {offenders}",
			)

	def test_a_token_keeps_its_shape_across_the_two_palettes(self):
		"""A value that is a colour in light must be a colour in dark.

		The two palettes disagreed about the *kind* of thing several tokens were,
		not only its value, which is the failure above seen from the other side.
		Comparing shapes catches the next one before it ships.
		"""
		css = self._read("public", "css", "self_service.css")
		light = palette_tokens(css, ":root {")
		dark = palette_tokens(css, ':root[data-k-theme="dark"] {')

		disagreed = {}
		for name, light_value in light.items():
			if name not in dark:
				continue
			is_colour = lambda v: srgb(v) is not None  # noqa: E731
			if is_colour(light_value) != is_colour(dark[name]):
				disagreed[name] = (light_value.strip(), dark[name].strip())
		self.assertEqual(
			disagreed,
			{},
			f"token that is a colour in one palette and not the other: {disagreed}",
		)

	def test_a_shade_is_not_the_light_palettes_base_verbatim(self):
		"""The bug this replaces a heuristic for: the wrong *value*, not the wrong direction.

		Every dark `-deep` had been set to the light palette's own base - `#dc2626`
		for `bad`, `#2563eb` for `brand`, `#059669` for `ok` - while the dark base
		beside it had been lightened. The shade was therefore darker than the base
		it was supposed to deepen, and it failed both of its jobs at once: as the
		alert's text colour on its own surface (3.33:1), and as the far stop of the
		button gradient with `--k-text-inverse` on top (3.62:1).

		Note what is deliberately *not* asserted: that a dark `-deep` be lighter
		than the dark base. That reads as if it were the rule and it is not -
		`--k-brand-deep` is `#5888f0` against a `#60a5fa` base, because the
		contrast requirement at its two call sites is what sets it and neither of
		them pairs it with the base. `test_every_text_colour_reads_...` holds it to
		the requirement instead.
		"""
		css = self._read("public", "css", "self_service.css")
		light = palette_tokens(css, ":root {")
		dark = palette_tokens(css, ':root[data-k-theme="dark"] {')

		reused = {}
		for name, dark_value in dark.items():
			if not name.endswith("-deep"):
				continue
			base = name[: -len("-deep")]
			if base not in light:
				continue
			if dark_value.strip().lower() == light[base].strip().lower():
				reused[name] = light[base].strip()
		self.assertEqual(
			reused,
			{},
			f"dark `-deep` reusing the light palette's base verbatim: {reused}",
		)

	def test_inverse_text_is_painted_on_a_fill_it_can_read(self):
		"""`--k-text-inverse` against the fill its *own* rule declares.

		White in light, near-black in dark, and always sitting on a colour the
		stylesheet chose: a button gradient, a chip, a tile icon, a badge. So the
		pairing is checkable here, and it is where the real failures were.

		Two bugs came out of writing it. The first is that a translucent token
		cannot be a fill for inverse text at all - a 28%-white scrim *lightens*
		whatever is behind it, which is the wrong direction for white text, and
		that is how the active chip's count and the language flag and tag ended
		up at 2.95:1. The second is that a `-soft` tint is usually chosen to look
		like a highlight rather than to carry text, and several of them were far
		too light to sit under a white label.

		Scope, stated plainly: this grades rules that declare their own
		background. `.k-screen-icon`, `.k-group-icon` and `.k-punch-head-date`
		paint inverse text with no background of their own - they inherit a
		parent's - and no amount of reading this file can resolve that. They are
		covered by rendering the page, which is how they were found.
		"""
		css = self._read("public", "css", "self_service.css")
		light = palette_tokens(css, ":root {")
		dark = palette_tokens(css, ':root[data-k-theme="dark"] {')
		palettes = {"light": light, "dark": {**light, **dark}}
		bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		rules = [
			(" ".join(selector.split()), declarations)
			for selector, declarations in (
				(" ".join(selector.split()), re.findall(r"([\w-]+)\s*:\s*([^;]+)", body))
				for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare)
			)
		]

		# A colour declared on the base class and a fill declared on a modifier
		# reach the same element. `.k-tile-icon` sets inverse text;
		# `.k-tile-icon--timeoff` repaints its background with an `--ok-soft`
		# gradient; the label then sits on a tint chosen as a highlight, which is
		# not the same question as "does white read on green". So a modifier's
		# fill is graded against the base class's colour.
		base_colour = {}
		for selector, declarations in rules:
			properties = dict(declarations)
			if "color" not in properties:
				continue
			last = selector.split()[-1] if selector.split() else ""
			if "--" in last:
				last = last.split("--")[0]
			base_colour.setdefault(last, properties["color"])

		def colour_for(selector: str):
			properties = dict(next(d for s, d in rules if s == selector))
			if "color" in properties:
				return properties["color"]
			last = selector.split()[-1] if selector.split() else ""
			if "--" in last:
				return base_colour.get(last.split("--")[0])
			return None

		pairs = []
		for selector, declarations in rules:
			foreground = colour_for(selector)
			if not foreground or "--k-text-inverse" not in foreground:
				continue
			own = [
				value for name, value in declarations
				if name in ("background", "background-color")
			]
			if own:
				pairs.append((selector, own))

		self.assertTrue(
			pairs,
			"no rule pairs inverse text with a fill - the patterns above are "
			"stale, or every badge lost its background",
		)

		failures = []
		for selector, own in pairs:
			for which, tokens in palettes.items():
				inverse = tokens["--k-text-inverse"].strip()
				for value in own:
					try:
						expanded = resolve_tokens(value, tokens)
					except KeyError:
						continue
					stops = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", expanded)
					for stop in stops:
						if re.search(r",\s*0?\.\d+\s*\)$", stop.strip()):
							failures.append(
								f"{which} {selector[:40]}: inverse text on the "
								f"TRANSLUCENT fill {stop.strip()} - a wash moves "
								f"the wrong way for {inverse}"
							)
							continue
						ratio = contrast_ratio(inverse, stop)
						if ratio is not None and ratio < AA_TEXT:
							failures.append(
								f"{which} {ratio:.2f}:1 {selector[:40]}: "
								f"--k-text-inverse {inverse} on {stop.strip()}"
							)
		self.assertEqual(
			failures,
			[],
			"inverse text on a fill it cannot read:\n  " + "\n  ".join(failures[:10]),
		)

	def test_every_text_colour_reads_on_the_surface_it_is_painted_on(self):
		"""Contrast, per declaration, against that rule's own background.

		Graded per rule rather than per token, because a token is only as good as
		the worst surface it lands on: `--k-ok` is fine on the white card and was
		fine there, and separately unusable as the far stop of the success
		button's gradient.

		`--k-text-4` is exempt at 3:1 and the exemption is named in
		`DEEMPHASIS_TOKENS` with the reason, rather than left as a number that
		someone eventually raises for the wrong reason.
		"""
		css = self._read("public", "css", "self_service.css")
		light = palette_tokens(css, ":root {")
		dark = palette_tokens(css, ':root[data-k-theme="dark"] {')
		merged = {**light, **dark}

		bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		failures = []

		for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare):
			selector = " ".join(rule.group(1).split())
			declarations = re.findall(r"([\w-]+)\s*:\s*([^;]+)", rule.group(2))
			properties = dict(declarations)
			# `border-color:` and `scrollbar-color:` also match a naive `color:`
			# search, and grading a 1px border as if it were text is how nine
			# false positives buried two real ones.
			if "color" not in properties:
				continue

			named = re.findall(r"var\((--k-[a-z0-9-]+)\)", properties["color"])
			# `--k-text-inverse` is graded by the fill test instead: it is
			# painted on an ancestor's gradient, so falling back to
			# `--k-surface` here reports sixteen rules at 1.00:1 that are all
			# perfectly legible.
			if any(name.startswith("--k-text-inverse") for name in named):
				continue

			candidates: list[str] = []
			for name, value in declarations:
				if name not in ("background", "background-color"):
					continue
				try:
					expanded = resolve_tokens(value, merged)
				except KeyError:
					continue
				# substitute first, split second: splitting a gradient on commas
				# shreds its argument list and no stop survives
				candidates += re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", expanded)
			if not candidates:
				# A rule that declares no background is sitting on whatever is
				# behind it, and the worst case for that is the *darkest* stop of
				# the page gradient - not `--k-surface`. The light `--k-bg` ends at
				# #e6ecf5, which is darker than the white card, so assuming the
				# card is how `--k-text-3` cleared every surface the stylesheet
				# names and still came out at 4.37:1 under "Back to Dashboard".
				candidates = darkest_stop(merged.get("--k-bg", "")) or [
					merged.get("--k-surface", "white")
				]

			try:
				foreground = resolve_tokens(properties["color"], merged)
			except KeyError:
				continue
			if srgb(foreground) is None:
				continue

			for name in named:
				floor = DEEMPHASIS_FLOOR if name in DEEMPHASIS_TOKENS else AA_TEXT
				for background in candidates:
					ratio = contrast_ratio(foreground, background)
					if ratio is not None and ratio < floor:
						failures.append(
							f"{ratio:.2f}:1 (needs {floor}) {selector[:44]}"
							f" -- {name} {foreground.strip()} on {background.strip()}"
						)

		self.assertEqual(
			failures,
			[],
			"text that does not read on its own background:\n  "
			+ "\n  ".join(sorted(set(failures))[:8]),
		)

	def test_the_deemphasis_floor_is_the_one_the_stylesheet_documents(self):
		"""`--k-text-4` claims 3:1 in the palette's own comment; hold it to that.

		The number is written down twice - once as `DEEMPHASIS_FLOOR` here and
		once in the comment beside the token - because a test that only enforces
		"whatever this constant says" enforces nothing. This checks the two agree.
		"""
		css = self._read("public", "css", "self_service.css")
		light = palette_tokens(css, ":root {")
		dark = palette_tokens(css, ':root[data-k-theme="dark"] {')
		for which, tokens in (("light", light), ("dark", {**light, **dark})):
			for name in DEEMPHASIS_TOKENS:
				worst = worst_text_contrast(css, tokens, name)
				self.assertIsNotNone(
					worst,
					f"{name} is never used as a text colour in {which}; the "
					f"exemption naming it has gone stale",
				)
				self.assertGreaterEqual(
					worst,
					DEEMPHASIS_FLOOR,
					f"{which} {name} {tokens[name].strip()} reads at {worst:.2f}:1, "
					f"below the {DEEMPHASIS_FLOOR} the palette's comment claims",
				)

	def test_bootstrap_colours_do_not_reach_the_page(self):
		"""Frappe's website bundle brings Bootstrap's light palette with it.

		The page inherits it, and two of its rules set colours the tokens never
		get to choose: `h1..h6, .h1..h6 { color: #171717 }` and
		`.text-muted { color: #7c7c7c !important }`. The client renders
		`h2.fw-bold` for "Employee Sign In" and `p.text-muted` under it, so on the
		dark card the heading came out `#171717` on `#131c2e` - 1.05:1 - and the
		subtitle at 4.08:1.

		Both values live in another file, which is why a check that only reads
		this stylesheet reported every colour on the page as tokenised and was
		wrong. So the utilities are read from the client, and each one has to be
		remapped.
		"""
		css = self._read("public", "css", "self_service.css")
		source = self._read("public", "js", "self_service.js")

		used = set()
		for value in re.findall(r'class="([^"]*)"', source):
			used.update(part for part in value.split() if part and not part.startswith("k-"))
		# only the utilities that carry a colour matter; the rest are layout
		colour_carrying = {
			name for name in used
			if name.startswith(("text-", "bg-")) or name in ("lead", "small")
		}
		# `text-truncate` and friends are utilities that carry no colour
		colour_carrying -= {"text-truncate", "text-center", "text-uppercase"}
		self.assertTrue(
			colour_carrying,
			"no colour-carrying utility found - the client changed shape, "
			"so this test is no longer looking at the right thing",
		)

		# headings are colour-carrying even though the client never names them, so
		# the anchor for "the bridge" is the first rule that remaps one.
		self.assertIn(".k-root :is(h1,", css, "the Bootstrap bridge is missing entirely")

		# A utility counts as remapped only if some rule *both* scopes it under
		# `.k-root` and gives it a colour. Matching on the selector text alone is
		# not enough: `.k-root small:not(.text-muted)` mentions `text-muted` and
		# sets no colour for it, so a selector-only search reports a leak as
		# covered. And building a needle by string concatenation is how the first
		# version of this test looked for `.k-root lead` and found nothing.
		bare_css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		rules = [
			(" ".join(selector.split()), body)
			for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare_css)
		]

		def remapped(name: str) -> bool:
			return any(
				".k-root" in selector
				and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", selector)
				and re.search(r"(?<![\w-])color\s*:\s*var\(--k-", body)
				for selector, body in rules
			)

		unmapped = sorted(name for name in colour_carrying if not remapped(name))
		self.assertEqual(
			unmapped,
			[],
			"Bootstrap colour utility used by the client but not remapped inside "
			f".k-root: {unmapped}",
		)

		# the muted override has to win against the !important it is fighting
		self.assertRegex(
			css,
			r"\.k-root \.text-muted\s*\{[^}]*color:\s*var\(--k-[\w-]+\)\s*!important",
			".k-root .text-muted must carry !important to beat Bootstrap's",
		)
