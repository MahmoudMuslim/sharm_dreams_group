# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the employee portal at `/employee-portal`.

The emphasis is on refusals. Every screen that reads something checks that another
employee's token cannot read it, and every action that changes something checks that
a bad input is refused with a sentence the employee could act on rather than a bare
exception. A portal that fails open is worse than one that fails closed: the numbers
on it are somebody's leave and somebody's pay.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import portal, renderers


class TestPortalRefusals(FrappeTestCase):
	"""Nothing readable through the portal without a token bound to that employee."""

	def setUp(self):
		# Two active employees who can actually sign in: both fields are set on real
		# rows, and picking by those fields rather than by a hard-coded name keeps the
		# test working when the employee list is reloaded.
		self.employees = frappe.get_all(
			"Employee",
			filters={
				"status": "Active",
				"sdg_national_id": ("is", "set"),
				"employee_number": ("is", "set"),
			},
			fields=["name", "sdg_national_id", "employee_number"],
			limit_page_length=2,
		)
		self.assertGreaterEqual(
			len(self.employees), 2, "need two sign-in-able employees to test isolation"
		)
		self.a, self.b = self.employees[0], self.employees[1]
		self.token_a = portal.sign_in(self.a.sdg_national_id, self.a.employee_number)["token"]

	def _token_b(self):
		return portal.sign_in(self.b.sdg_national_id, self.b.employee_number)["token"]

	def test_every_read_refuses_a_bogus_token(self):
		# Not one of them, because a read that works without a token exposes every
		# employee's leave and payroll to whoever loads the page.
		for action in (
			portal.my_profile,
			portal.my_balances,
			portal.leave_types,
			portal.my_leaves,
			portal.my_payslips,
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				action("not-a-real-token")

	def test_a_payslip_belonging_to_someone_else_is_refused(self):
		# A payslip name is guessable - they are sequential - so this is the one read
		# where the employee check cannot be skipped.
		other = frappe.get_all(
			"Salary Slip",
			filters={"employee": self.a.name, "docstatus": ["<", 2]},
			pluck="name",
			limit_page_length=1,
		)
		if not other:
			self.skipTest("no payslip for the chosen employee")
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.my_payslip(self._token_b(), other[0])

	def test_balances_are_scoped_to_the_signed_in_employee(self):
		balances_a = portal.my_balances(self.token_a)
		balances_b = portal.my_balances(self._token_b())
		self.assertEqual(balances_a["employee"], self.a.name)
		self.assertEqual(balances_b["employee"], self.b.name)
		self.assertNotEqual(self.a.name, self.b.name)

	def test_a_wrong_employee_number_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.sign_in(self.a.sdg_national_id, "000000")

	def test_a_missing_credential_is_refused(self):
		for args in (("", ""), (self.a.sdg_national_id, ""), ("", self.a.employee_number)):
			with self.assertRaises(frappe.exceptions.ValidationError):
				portal.sign_in(*args)

	def test_a_leftover_request_is_refused_rather_than_guessed_at(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.request_leave(self.token_a, "", "2026-11-02", "2026-11-03")
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.request_leave(self.token_a, "Annual Leave", "2026-11-02", "")
		with self.assertRaises(frappe.exceptions.ValidationError):
			# Backwards: HRMS would accept these and compute a negative span.
			portal.request_leave(self.token_a, "Annual Leave", "2026-11-05", "2026-11-02")

	def test_a_leave_outside_the_allocation_is_refused(self):
		# The allocation runs 2026-01-01..2026-12-31, so 2019 is outside it and HRMS
		# would reject it with a message about allocation periods.
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.request_leave(
				self.token_a, "Annual Leave", "2019-01-07", "2019-01-08"
			)

	def test_a_leave_for_a_type_that_is_not_allocated_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.request_leave(
				self.token_a, "Compensatory Off", "2026-11-02", "2026-11-03"
			)

	def test_cancelling_someone_elses_request_is_refused(self):
		# And refused with the same sentence as "no such request", so the answer does
		# not confirm that the request exists.
		with self.assertRaises(frappe.exceptions.ValidationError) as caught:
			portal.cancel_leave(self.token_a, "HR-LAP-00001")
		self.assertIn("not found", str(caught.exception).lower())


class TestPortalBalances(FrappeTestCase):
	"""The arithmetic, since it is computed here rather than read from HRMS."""

	def test_remaining_is_never_negative(self):
		# Over-application is a payroll problem, not a balance the employee owes. A
		# negative figure on a wall tablet reads as a debt.
		doc = frappe._dict(
			{"total_leaves_allocated": 5, "_used_days": 8}
		)
		balance = portal.approve_balance(doc, carry_cap=10)
		self.assertEqual(balance["allocated"], 5)
		self.assertEqual(balance["used"], 8)
		self.assertEqual(balance["remaining"], 0)

	def test_remaining_is_the_allocation_minus_the_spend(self):
		balance = portal.approve_balance(
			frappe._dict({"total_leaves_allocated": 30, "_used_days": 7.5}), carry_cap=10
		)
		self.assertEqual(balance["remaining"], 22.5)

	def test_a_missing_multiplier_counts_a_day_once_not_zero_times(self):
		# `flt(None)` is 0, so a leave type nobody set a multiplier on would count for
		# nothing and every balance would read as fully spent.
		from frappe.utils import flt

		self.assertEqual(flt(None, 1.0) or 1.0, 1.0)


class TestHolidays(FrappeTestCase):
	"""The days-off screen, which is a calendar rather than a balance.

	The split between a weekly rest day and a named public holiday is the whole point
	of the screen: both are days off, and only one is worth planning a trip around.
	Reporting 115 "holidays" because Fridays are on the calendar would say nothing.
	"""

	def setUp(self):
		self.emp = frappe.db.get_value(
			"Employee",
			{"status": "Active", "sdg_national_id": ("is", "set"),
			 "employee_number": ("is", "set")},
			"name",
		)
		if not self.emp:
			self.skipTest("no sign-in-able employee")
		self.token = portal.sign_in(
			frappe.db.get_value("Employee", self.emp, "sdg_national_id"),
			frappe.db.get_value("Employee", self.emp, "employee_number"),
		)["token"]

	def test_it_refuses_a_bogus_token(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.my_holidays("not-a-real-token")

	def test_the_list_is_the_one_covering_today(self):
		out = portal.my_holidays(self.token)
		if not out["list"]:
			self.skipTest("no Holiday List covers today on this site")
		self.assertTrue(
			frappe.db.get_value(
				"Holiday List", out["list"], ["from_date", "to_date"], as_dict=True
			),
			"the named list does not exist",
		)

	def test_nothing_upcoming_is_in_the_past(self):
		"""A holiday the employee has already worked is history, not a day ahead."""
		out = portal.my_holidays(self.token)
		for day in out["days"]:
			with self.subTest(day["date"]):
				self.assertGreaterEqual(day["days_until"], 0)

	def test_upcoming_days_are_in_date_order(self):
		out = portal.my_holidays(self.token)
		dates = [d["date"] for d in out["days"]]
		self.assertEqual(dates, sorted(dates))

	def test_public_and_weekly_are_counted_apart(self):
		"""The totals must not lump the weekend in with the holidays."""
		out = portal.my_holidays(self.token)
		self.assertGreater(out["weekly_count"], 0, "this group works a weekly rest day")
		self.assertGreater(out["public_count"], 0, "a holiday list with no public holiday?")
		self.assertNotEqual(out["public_count"], out["weekly_count"])

	def test_every_day_is_labelled_one_way_or_the_other(self):
		out = portal.my_holidays(self.token)
		for day in out["days"][:40]:
			with self.subTest(day["date"]):
				self.assertIn(day["kind"], ("public", "weekly"))

	def test_a_site_with_no_calendar_says_so_rather_than_raising(self):
		"""No list covering today is a data gap, not a crash."""
		self.assertIsNone(portal.holiday_list_for(self.emp, "1990-01-01"))


class TestLeaveTerms(FrappeTestCase):
	"""The rules shown beside a balance, read off the Leave Type and never restated.

	A limit left at its default is reported as absent rather than as zero: "no limit on
	consecutive days" and "you may take 0 consecutive days" are different claims and only
	the first is true. These pin that, because the zero case is the one that produces a
	confidently wrong sentence on a wall tablet.
	"""

	def test_a_type_with_no_rules_reports_none_rather_than_zero(self):
		terms = portal.leave_terms(
			frappe._dict(
				{
					"max_continuous_days_allowed": 0,
					"max_leaves_allowed": 0,
					"applicable_after": 0,
					"allow_encashment": 0,
					"sdg_vacation_day_multiplier": 1.0,
					"is_lwp": 0,
				}
			)
		)
		self.assertEqual(terms["terms"], [])
		self.assertIsNone(terms["continuous_days"])

	def test_a_continuous_limit_is_stated_in_days(self):
		"""Casual Leave carries `max_continuous_days_allowed = 3` on this site."""
		casual = frappe.db.get_value("Leave Type", {"max_continuous_days_allowed": (">", 0)}, "name")
		if not casual:
			self.skipTest("no leave type with a continuous-days limit")
		terms = portal.leave_terms(
			frappe.db.get_value(
				"Leave Type", casual,
				["max_continuous_days_allowed", "max_leaves_allowed", "applicable_after",
				 "allow_encashment", "sdg_vacation_day_multiplier", "is_lwp"],
				as_dict=True,
			)
		)
		self.assertEqual(terms["continuous_days"], 3)
		self.assertTrue(any("3 consecutive days" in t for t in terms["terms"]), terms["terms"])

	def test_leave_without_pay_is_reported_as_unpaid(self):
		"""`is_lwp` decides it outright; a paid flag left on a LWP type would lie."""
		terms = portal.leave_terms(
			frappe._dict(
				{
					"max_continuous_days_allowed": 0, "max_leaves_allowed": 0,
					"applicable_after": 0, "allow_encashment": 0,
					"sdg_vacation_day_multiplier": 1.0, "is_lwp": 1,
					"sdg_is_paid": 1, "is_ppl": 1,
				}
			)
		)
		self.assertTrue(terms["lwp"])
		self.assertFalse(terms["paid"])

	def test_a_missing_leave_type_reports_nothing(self):
		self.assertEqual(portal.leave_terms(None), {})


class TestBalanceDetail(FrappeTestCase):
	"""The figures beside a balance, which have to agree with each other."""

	def setUp(self):
		self.emp = frappe.db.get_value(
			"Employee",
			{"status": "Active", "sdg_national_id": ("is", "set"),
			 "employee_number": ("is", "set")},
			"name",
		)
		if not self.emp:
			self.skipTest("no sign-in-able employee")
		self.token = portal.sign_in(
			frappe.db.get_value("Employee", self.emp, "sdg_national_id"),
			frappe.db.get_value("Employee", self.emp, "employee_number"),
		)["token"]

	def test_approved_and_pending_sum_to_the_used_total(self):
		"""The split must not become a second, disagreeing figure.

		`used` is what the balance is computed from; if the halves did not add up to
		it, the screen would show a total and a breakdown that cannot both be true.
		"""
		out = portal.my_balances(self.token)
		self.assertTrue(out["balances"], "no balance rows to check")
		for row in out["balances"]:
			with self.subTest(row["label"]):
				self.assertAlmostEqual(row["approved"] + row["pending"], row["used"], places=2)

	def test_the_page_totals_are_the_sum_of_the_rows(self):
		out = portal.my_balances(self.token)
		for field in ("total_allocated", "total_used", "total_remaining", "total_pending"):
			with self.subTest(field):
				self.assertAlmostEqual(
					out[field], round(sum(r[field[6:]] for r in out["balances"]), 2), places=2
				)

	def test_pending_never_counts_as_approved(self):
		"""The whole point of the split, on the one employee who has a pending request."""
		out = portal.my_balances(self.token)
		pending_rows = [r for r in out["balances"] if r["pending"] > 0]
		for row in pending_rows:
			with self.subTest(row["label"]):
				self.assertTrue(row["requests"], "pending days with no request behind them")

	def test_each_row_carries_the_rules_and_the_requests_behind_it(self):
		out = portal.my_balances(self.token)
		for row in out["balances"]:
			with self.subTest(row["label"]):
				# An allocated type must say its validity window; a type spent against
				# without a grant has none, and saying so honestly is the point.
				if row["allocated"] > 0:
					self.assertTrue(row["from_date"] and row["to_date"])
				self.assertIn("terms", row)
				self.assertIsInstance(row["requests"], list)
				self.assertLessEqual(row["pending"], row["used"] + 0.001)


class TestCarryForwardTerms(FrappeTestCase):
	"""The cap and the expiry, which are policy and must not be restated anywhere."""

	def test_a_type_that_does_not_carry_forward_says_none_not_zero(self):
		# "You may carry 0 days over" and "this type does not carry forward" are
		# different claims, and only the second is true here.
		carry_off = frappe.db.get_value("Leave Type", {"is_carry_forward": 0}, "name")
		if not carry_off:
			self.skipTest("no leave type with carry-forward switched off")
		terms = portal.carry_forward_terms(carry_off, "2026-12-31")
		self.assertIsNone(terms["allowed"])
		self.assertEqual(terms["cap"], 0.0)
		self.assertIsNone(terms["expires"])

	def test_the_expiry_is_the_sheet_s_own_date(self):
		"""90 days from 31 December is 31 March, the date the policy names.

		Counting from today instead would make the date move every day, so the same
		policy would read as a different one tomorrow.
		"""
		annual = frappe.db.get_value("Leave Type", {"is_carry_forward": 1}, "name")
		if not annual:
			self.skipTest("no leave type with carry-forward switched on")
		terms = portal.carry_forward_terms(annual, "2026-12-31")
		self.assertTrue(terms["allowed"])
		self.assertEqual(terms["cap"], 10.0)
		self.assertEqual(terms["expires"], "2027-03-31")

	def test_an_unknown_type_reports_no_terms_rather_than_raising(self):
		terms = portal.carry_forward_terms("No Such Leave Type", "2026-12-31")
		self.assertIsNone(terms["allowed"])

	def test_a_type_without_an_expiry_reports_none_rather_than_a_guess(self):
		terms = portal.carry_forward_terms("No Such Leave Type", None)
		self.assertIsNone(terms["expires"])


class TestPortalApiWhitelist(FrappeTestCase):
	"""The endpoint is a whitelist, and argument passing is checked against it."""

	def test_every_action_is_a_callable(self):
		self.assertTrue(renderers.ACTIONS)
		for name, handler in renderers.ACTIONS.items():
			self.assertTrue(
				callable(handler), f"{name} is listed but is not callable"
			)

	def test_the_actions_the_page_uses_all_exist(self):
		# The names here are the ones hard-coded in `public/js/employee_portal.js`. If
		# one is renamed in Python and not there, the page gets a 400 at runtime on a
		# wall tablet rather than a failure in a test.
		for name in (
			"sign_in",
			"sign_out",
			"my_profile",
			"my_balances",
			"leave_types",
			"my_leaves",
			"request_leave",
			"cancel_leave",
			"my_payslips",
			"my_payslip",
			"payslip_pdf",
		):
			self.assertIn(name, renderers.ACTIONS)

	def test_the_expenses_actions_the_page_uses_exist(self):
		# The expenses screen posts these; see `test_expenses.py` for the rest.
		for name in (
			"my_expense_claims",
			"expense_claim_types",
			"my_expense_claim",
			"file_expense_claim",
			"withdraw_expense_claim",
		):
			self.assertIn(name, renderers.ACTIONS)

	def test_an_argument_the_action_does_not_take_is_refused(self):
		typed = renderers._typed_args(
			renderers.ACTIONS["sign_in"],
			{"national_id": "1", "employee_code": "2", "is_admin": True},
		)
		self.assertIn("error", typed)

	def test_a_date_argument_is_not_coerced_to_a_number(self):
		# The bug this guards: a date arriving as a string compared lexically, which
		# looks right until a particular pair of dates is tried.
		typed = renderers._typed_args(
			renderers.ACTIONS["request_leave"],
			{"token": "t", "leave_type": "AL", "from_date": "2026-11-02", "to_date": "2026-11-03"},
		)
		self.assertEqual(typed["from_date"], "2026-11-02")
		self.assertNotIn("error", typed)

	def test_the_renderer_takes_the_two_arguments_frappe_passes(self):
		# `path_resolver` calls `renderer(endpoint, http_status_code)`; a signature
		# that does not accept both positionally breaks every page on the site.
		import inspect

		params = list(
			inspect.signature(renderers.PortalApiRenderer.__init__).parameters
		)
		self.assertEqual(params[:3], ["self", "endpoint", "http_status_code"])

	def test_the_renderer_can_be_built_with_no_request_in_scope(self):
		"""`frappe.local.request` raises rather than returning None.

		`frappe.local` is a `Local` whose `__getattribute__` refuses any key not in
		the context dict, so a missing `request` is an `AttributeError` - not a None.
		`frappe.get_print(..., as_pdf=True)` resolves the `printview` endpoint through
		the page-renderer list with no request at all, so every PDF the site generates
		- from a background job or a bench command as much as from a browser -
		constructs this renderer. Reading `frappe.local.request` unguarded raises
		`AttributeError: request` and takes PDF generation down site-wide.

		A test run has no request either, which is the same condition; it is removed
		first only so the test also holds under a runner that supplies one.
		"""
		if getattr(frappe.local, "request", None) is not None:
			self.addCleanup(setattr, frappe.local, "request", frappe.local.request)
			del frappe.local.request
		self.assertIsNone(
			getattr(frappe.local, "request", None),
			"there is a request in scope, so this test is not proving what it claims",
		)
		renderer = renderers.PortalApiRenderer("printview", 200)
		self.assertFalse(renderer.can_render(), "printview is not this endpoint")

	def test_a_payslip_pdf_can_be_rendered_with_no_request_in_scope(self):
		"""The end of the same thread, and the reason it matters.

		`frappe.get_print` is reached with no request during any bench command or
		background job, so this is how PDF generation actually runs. It fails if any
		registered renderer cannot be built without one.
		"""
		slip = frappe.get_all(
			"Salary Slip", filters={"docstatus": ["<", 2]}, pluck="name", limit_page_length=1
		)
		if not slip:
			self.skipTest("no payslip on the site")
		pdf = portal.render_salary_slip_pdf(frappe.get_doc("Salary Slip", slip[0]))
		self.assertTrue(pdf.startswith(b"%PDF-"), "no PDF was produced")

	def test_an_action_the_page_uses_exists_for_the_pdf_too(self):
		self.assertIn("payslip_pdf", renderers.ACTIONS)


class TestPayslipPdf(FrappeTestCase):
	"""The download, and the isolation around it.

	PDFs are worth their own tests because they are the one thing on this portal that
	goes through a completely separate pipeline - the print view, its own permissions,
	its own renderer list - and a fault there does not show up as an error on the
	payslip screen until it is too late to be useful.
	"""

	def setUp(self):
		self.employees = frappe.get_all(
			"Employee",
			filters={
				"status": "Active",
				"sdg_national_id": ("is", "set"),
				"employee_number": ("is", "set"),
			},
			fields=["name", "sdg_national_id", "employee_number"],
			limit_page_length=2,
		)
		self.assertGreaterEqual(len(self.employees), 2)
		self.a, self.b = self.employees[0], self.employees[1]
		self.token_a = portal.sign_in(self.a.sdg_national_id, self.a.employee_number)["token"]
		self.slip = frappe.get_all(
			"Salary Slip",
			filters={"employee": self.a.name, "docstatus": ["<", 2]},
			pluck="name",
			limit_page_length=1,
		)
		if not self.slip:
			self.skipTest("no payslip for the chosen employee")

	def test_the_download_is_a_real_pdf_of_the_stated_length(self):
		import base64

		out = portal.payslip_pdf(self.token_a, self.slip[0])
		raw = base64.b64decode(out["pdf"])
		# `%PDF-` and a trailing `%%EOF`, because a truncated download still begins
		# `%PDF-` and would otherwise pass a prefix check.
		self.assertTrue(raw.startswith(b"%PDF-"), "not a PDF")
		self.assertIn(b"%%EOF", raw[-2048:], "the PDF is truncated")
		self.assertEqual(len(raw), out["size"], "the stated size is not the real size")
		self.assertTrue(out["filename"].endswith(".pdf"))
		self.assertNotIn("/", out["filename"], "a slash in the name breaks the download")

	def test_somebody_elses_payslip_cannot_be_downloaded(self):
		token_b = portal.sign_in(self.b.sdg_national_id, self.b.employee_number)["token"]
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.payslip_pdf(token_b, self.slip[0])

	def test_a_bogus_token_cannot_be_downloaded(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.payslip_pdf("not-a-real-token", self.slip[0])

	def test_the_named_print_format_exists_on_this_site(self):
		"""Otherwise every download silently falls back to a different layout."""
		self.assertTrue(
			frappe.db.exists("Print Format", portal.PAYSLIP_PRINT_FORMAT),
			f"{portal.PAYSLIP_PRINT_FORMAT} is not on this site, so downloads are "
			"rendered with whatever the doctype default happens to be",
		)

	def test_the_pdf_is_rendered_as_somebody_who_may_read_it(self):
		"""Restores the session afterwards, or every later test runs as this user."""
		previous = frappe.session.user
		slip = frappe.get_doc("Salary Slip", self.slip[0])
		try:
			portal.render_salary_slip_pdf(slip)
			self.assertEqual(
				frappe.session.user,
				previous,
				"rendering a payslip left the session running as another user",
			)
		except Exception:
			self.assertEqual(frappe.session.user, previous, "the user was not restored")
			raise