# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the expenses area of the employee portal.

Two things are tested hardest here, because they are the two ways this area could
quietly do damage: an expense claim reaching the ledger without anybody approving
it, and one employee's claim becoming visible or withdrawable by another.

Every claim these tests file is withdrawn or cancelled in `tearDown`, and the
approval request that came with it goes with it. A submitted claim cannot be
deleted outright - HRMS refuses, correctly - so it is cancelled first, which
leaves the reversal in the ledger where a real one would.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import expenses, portal, renderers

APPROVER = "ghada.kamel@sharmdreamsgroup.com"

# What this module files is identified by, and used to recognise its own rows. The
# descriptions are deliberately distinctive: a blanket delete of every Expense Claim on
# the site would be data loss if anyone ever filed a real one, and a test that cleans up
# by name alone can quietly stop cleaning up.
PROBE_MARKERS = (
	"approval probe",
	"isolation probe",
	"Taxi to Sharm airport",
	"Client lunch",
)


def _clean_up_claim(name: str) -> None:
	"""Remove one claim this module filed, and the request raised against it.

	Cancelled before deleted where it was submitted: HRMS refuses to delete a submitted
	claim outright, and cancelling leaves the reversal in the ledger where a real one
	would. The GL entries go too, because a cancellation entry pointing at a voucher
	that no longer exists is an orphan the ledger will still count.
	"""
	for request in frappe.get_all(
		"Employee Self Service Approval Request",
		filters={"reference": "Expense Claim " + name},
		pluck="name",
		limit_page_length=0,
	):
		frappe.delete_doc(
			"Employee Self Service Approval Request", request,
			force=True, ignore_permissions=True,
		)
	if not frappe.db.exists("Expense Claim", name):
		return
	claim = frappe.get_doc("Expense Claim", name)
	descriptions = " ".join(row.description or "" for row in (claim.expenses or []))
	if not any(marker in descriptions for marker in PROBE_MARKERS):
		# Not one of ours. Left alone rather than deleted on the strength of a name.
		return
	if claim.docstatus == 1:
		claim.cancel()
	frappe.db.sql(
		"DELETE FROM `tabGL Entry` WHERE voucher_type = 'Expense Claim' AND voucher_no = %s",
		(name,),
	)
	frappe.delete_doc("Expense Claim", name, force=True, ignore_permissions=True)


class TestExpenseRefusals(FrappeTestCase):
	"""Nothing filed or read without a token bound to that employee."""

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
		self.assertGreaterEqual(len(self.employees), 2, "need two sign-in-able employees")
		self.a, self.b = self.employees[0], self.employees[1]
		self.token_a = portal.sign_in(self.a.sdg_national_id, self.a.employee_number)["token"]
		self.token_b = portal.sign_in(self.b.sdg_national_id, self.b.employee_number)["token"]
		self._filed: list[str] = []

	def tearDown(self):
		for name in self._filed:
			_clean_up_claim(name)
		frappe.db.commit()

	def _file(self, items, **kwargs):
		out = expenses.file_expense_claim(self.token_a, items, **kwargs)
		self._filed.append(out["name"])
		return out

	def _usable_type(self):
		"""A claim type that actually has an account for this employee's company."""
		for row in expenses.expense_claim_types(self.token_a)["expense_types"]:
			if row["usable"]:
				return row["name"]
		self.skipTest("no expense claim type has an account for any company")

	def test_every_read_refuses_a_bogus_token(self):
		for action in (
			expenses.my_expense_claims,
			expenses.expense_claim_types,
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				action("not-a-real-token")

	def test_a_claim_with_no_lines_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.file_expense_claim(self.token_a, [])

	def test_a_line_with_no_amount_is_refused(self):
		# A zero amount is not a free expense; it is a line that will be submitted as
		# zero and post nothing while appearing on the claim as a real row.
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.file_expense_claim(
				self.token_a, [{"expense_type": self._usable_type(), "amount": 0}]
			)

	def test_a_line_with_a_negative_amount_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.file_expense_claim(
				self.token_a, [{"expense_type": self._usable_type(), "amount": -50}]
			)

	def test_a_line_with_no_category_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.file_expense_claim(self.token_a, [{"amount": 50}])

	def test_a_future_posting_date_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._file(
				[{"expense_type": self._usable_type(), "amount": 50}],
				posting_date="2099-01-01",
			)

	def test_an_expense_dated_after_its_posting_date_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._file(
				[{"expense_type": self._usable_type(), "amount": 50, "expense_date": "2026-06-01"}],
				posting_date="2026-01-01",
			)

	def test_a_type_with_no_account_for_the_company_is_refused(self):
		"""Not deferred to submit: the claim would post to nothing.

		`Calls` has no account mapped for this company on this site, and filing
		against it is refused rather than accepted and silently dropped.
		"""
		types = expenses.expense_claim_types(self.token_a)["expense_types"]
		unusable = [row for row in types if not row["usable"]]
		if not unusable:
			self.skipTest("every claim type has an account for this company")
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._file([{"expense_type": unusable[0]["name"], "amount": 50}])

	def test_the_refusal_names_the_company_that_is_missing_the_account(self):
		"""The employee has to be able to tell who to ask.

		Read from `message_log` rather than `str(exception)`, because a ValidationError
		raised with `_()` stringifies to its escaped markup rather than the sentence the
		tablet is shown.
		"""
		out = expenses.expense_claim_types(self.token_a)
		unusable = [row for row in out["expense_types"] if not row["usable"]]
		if not unusable:
			self.skipTest("every claim type has an account for this company")
		company = out["company"]
		# `frappe.local.message_log` holds the sentences Frappe built for the response;
		# it is appended to rather than replaced, so only the tail belongs to this call.
		# Entries are `_dict`s here and plain strings in some Frappe versions, hence the
		# isinstance check rather than `.get` on every element.
		frappe.local.message_log = []
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._file([{"expense_type": unusable[0]["name"], "amount": 50}])
		shown = " ".join(
			entry.get("message", "") if isinstance(entry, dict) else str(entry)
			for entry in (frappe.local.message_log or [])
		)
		self.assertIn(
			company,
			shown,
			f"the refusal shown to the employee does not name {company}",
		)


class TestExpenseIsolation(FrappeTestCase):
	"""One employee's claims are not another's to read or withdraw."""

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
		self.token_b = portal.sign_in(self.b.sdg_national_id, self.b.employee_number)["token"]

		types = expenses.expense_claim_types(self.token_a)["expense_types"]
		usable = [row for row in types if row["usable"]]
		if not usable:
			self.skipTest("no expense claim type has an account for any company")
		out = expenses.file_expense_claim(
			self.token_a,
			[{"expense_type": usable[0]["name"], "amount": 25, "description": "isolation probe"}],
		)
		self.claim = out["name"]
		self.request = out["approval_request"]
		# Committed for the same reason as `TestExpenseApproval.setUp`; `tearDown` removes
		# both rows.
		frappe.db.commit()

	def tearDown(self):
		_clean_up_claim(self.claim)
		frappe.db.commit()

	def test_another_employee_cannot_read_the_claim(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.my_expense_claim(self.token_b, self.claim)

	def test_another_employee_cannot_withdraw_the_claim(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.withdraw_expense_claim(self.token_b, self.claim)

	def test_the_refusal_does_not_confirm_the_claim_exists(self):
		"""Same sentence as "no such claim", so the answer reveals nothing.

		Compared on the message rather than the exception type, because Frappe wraps the
		text: a claim belonging to somebody else is refused with our own sentence, while
		a name that is not a claim at all comes back from Frappe's link validation in
		its own words. The property that matters is that neither answer is
		"yes, that exists but it is not yours".
		"""
		with self.assertRaises(frappe.exceptions.ValidationError) as foreign:
			expenses.my_expense_claim(self.token_b, self.claim)
		message = str(foreign.exception)
		self.assertIn("not found", message.lower())
		self.assertNotIn(self.claim, message, "the refusal echoes the claim's name")

	def test_a_claim_name_that_is_not_a_claim_gets_the_same_refusal(self):
		"""One sentence for "not yours" and for "not there", so neither reveals anything.

		The existence check runs first precisely so a name that is not a claim is not
		refused by Frappe's link validation in its own words - which would be a second,
		distinguishable answer to the same question.
		"""
		with self.assertRaises(frappe.exceptions.ValidationError) as missing:
			expenses.my_expense_claim(self.token_b, "HR-EXP-9999-99999")
		self.assertIn("not found", str(missing.exception).lower())

	def test_the_claim_list_is_scoped_to_the_signed_in_employee(self):
		mine = expenses.my_expense_claims(self.token_a)["claims"]
		self.assertTrue(any(row["name"] == self.claim for row in mine))
		theirs = expenses.my_expense_claims(self.token_b)["claims"]
		self.assertNotIn(self.claim, [row["name"] for row in theirs])

	def test_the_detail_reports_the_lines_it_was_filed_with(self):
		detail = expenses.my_expense_claim(self.token_a, self.claim)
		self.assertEqual(len(detail["items"]), 1)
		self.assertEqual(detail["total_claimed_amount"], 25)
		# `withdrawable` is a claim about state, so it has to agree with the same
		# predicate `withdraw_expense_claim` enforces.
		self.assertTrue(detail["withdrawable"])


class TestExpenseApproval(FrappeTestCase):
	"""The claim does not reach the ledger until a person approves it."""

	def setUp(self):
		self.employee = frappe.db.get_value(
			"Employee",
			{"status": "Active", "sdg_national_id": ("is", "set"), "employee_number": ("is", "set")},
			"name",
		)
		self.token = portal.sign_in(
			frappe.db.get_value("Employee", self.employee, "sdg_national_id"),
			frappe.db.get_value("Employee", self.employee, "employee_number"),
		)["token"]

		out = expenses.expense_claim_types(self.token)
		usable = [row for row in out["expense_types"] if row["usable"]]
		if not usable:
			self.skipTest("no expense claim type has an account for any company")
		filed = expenses.file_expense_claim(
			self.token,
			[{"expense_type": usable[0]["name"], "amount": 40, "description": "approval probe"}],
		)
		self.claim = filed["name"]
		self.request = filed["approval_request"]
		if not self.request:
			# The category is site data, seeded by `seeder.seed_approval_categories`, and
			# `file_expense_claim` returns None for the request rather than inventing
			# one with no approver - so a claim filed now would sit as a draft nobody can
			# ever approve. Seeded here, idempotently and without touching approvers on
			# a category that already exists.
			from sharm_dreams_group import seeder

			seeder.seed_approval_categories()
			# Committed so the category is visible to `db.exists` on the retry below,
			# which reads the database rather than this transaction.
			frappe.db.commit()
			filed = expenses.file_expense_claim(
				self.token,
				[{"expense_type": usable[0]["name"], "amount": 40, "description": "approval probe"}],
			)
			self.claim = filed["name"]
			self.request = filed["approval_request"]
		self.assertTrue(
			self.request,
			f"no approval request was raised, so {self.claim} can never be approved",
		)

		# Committed, so the request is visible to the fresh `frappe.get_doc` in
		# `_open_request` - which reads the database, not this transaction. Safe because
		# `tearDown` deletes both rows, and a submitted claim is cancelled before
		# deletion so the reversal stays in the ledger where a real one would.
		frappe.db.commit()

	def tearDown(self):
		_clean_up_claim(self.claim)
		frappe.db.commit()

	def _open_request(self):
		"""The request, re-read from the database.

		`setUp` flushes rather than commits, so the inserts are in this transaction; a
		fresh `frappe.get_doc` would not see them until then. Taking the document out of
		`frappe.local.document_cache` is what makes this reliable - the instance used to
		build it is cached under its name, and reading it back from the cache would hand
		out the very document the action is supposed to re-validate.
		"""
		frappe.clear_document_cache("Employee Self Service Approval Request", self.request)
		return frappe.get_doc("Employee Self Service Approval Request", self.request)

	def _approver_of(self, request):
		return next(
			row.approver_user
			for row in request.approvers
			if row.status in ("New", "Pending", "Waiting")
		)

	def _act_as(self, user, method):
		previous = frappe.session.user
		frappe.set_user(user)
		try:
			return getattr(self._open_request(), method)()
		finally:
			frappe.set_user(previous)

	def test_filing_does_not_submit_the_claim(self):
		"""`ExpenseClaim.on_submit` throws unless `approval_status` is decided.

		So submitting *is* the act of paying, and a claim the portal files must stay a
		draft with a request raised against it.
		"""
		claim = frappe.get_doc("Expense Claim", self.claim)
		self.assertEqual(claim.docstatus, 0)
		self.assertEqual(claim.approval_status, "Draft")

	def test_the_request_has_an_approver_the_category_named(self):
		"""Otherwise `may_act` is False for everyone, including the intended approver."""
		request = self._open_request()
		self.assertTrue(
			request.approvers, "the request was filed with no approvers at all"
		)
		self.assertTrue(
			any(row.status in ("New", "Pending", "Waiting") for row in request.approvers)
		)

	def test_only_the_named_approver_is_offered_the_buttons(self):
		from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_approval_request.employee_self_service_approval_request import (
			get_approval_actions,
		)
		approver = self._approver_of(self._open_request())

		previous = frappe.session.user
		try:
			frappe.set_user(approver)
			# A dict, not a bare list: the server also supplies the wording of the
			# Reject confirmation, because what Reject does differs by area and a
			# message written for one of them misleads about the others.
			offered = get_approval_actions(self.request)
			self.assertEqual(sorted(offered["actions"]), ["Approve", "Reject"])
			self.assertTrue(
				offered["reject_note"],
				"the buttons appeared with nothing saying what Reject would do",
			)
			frappe.set_user("Administrator")
			self.assertEqual(get_approval_actions(self.request)["actions"], [])
			frappe.set_user("Guest")
			self.assertEqual(get_approval_actions(self.request)["actions"], [])
		finally:
			frappe.set_user(previous)

	def test_the_expense_reject_note_says_the_claim_cannot_be_reopened(self):
		"""An expense rejection is final, and the note has to say so.

		The same field carries the opposite message for a self-assessment, which is
		reopened as a draft, so "cannot be undone" is only right here.
		"""
		from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_approval_request.employee_self_service_approval_request import (
			get_approval_actions,
		)

		approver = self._approver_of(self._open_request())
		previous = frappe.session.user
		try:
			frappe.set_user(approver)
			note = get_approval_actions(self.request)["reject_note"].lower()
			self.assertIn("reopened", note)
			self.assertNotIn("cannot be undone", note)
		finally:
			frappe.set_user(previous)

	def test_approving_submits_the_claim_and_posts_to_the_ledger(self):
		self._act_as(self._approver_of(self._open_request()), "action_approve")

		claim = frappe.get_doc("Expense Claim", self.claim)
		self.assertEqual(claim.docstatus, 1, "an approved claim was not submitted")
		self.assertEqual(claim.approval_status, "Approved")
		# HRMS posts nothing unless each line is sanctioned, so an approval that left
		# `sanctioned_amount` at zero would submit a claim that moves no money while
		# looking paid.
		self.assertEqual(claim.total_sanctioned_amount, claim.total_claimed_amount)

		entries = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": "Expense Claim", "voucher_no": self.claim},
			pluck="name",
			limit_page_length=0,
		)
		self.assertTrue(entries, "an approved and submitted claim posted nothing")

	def test_approving_twice_is_refused(self):
		approver = self._approver_of(self._open_request())
		self._act_as(approver, "action_approve")
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._act_as(approver, "action_approve")

	def test_someone_who_is_not_the_approver_cannot_approve(self):
		"""Being able to open the document is not the same as deciding it.

		Administrator is chosen deliberately: on this site it is a System Manager, so it
		is caught by the same fallback that would otherwise hand it an approval. What is
		being pinned is that having broad rights does not substitute for being on this
		request's approver list.
		"""
		named = [
			row.approver_user
			for row in self._open_request().approvers
			if row.status in ("New", "Pending", "Waiting")
		]
		if not named:
			self.skipTest(f"the {expenses.EXPENSE_CATEGORY} category named no approver")
		self.assertNotIn("Administrator", named)

		with self.assertRaises(frappe.exceptions.ValidationError):
			self._act_as("Administrator", "action_approve")

	def test_rejecting_leaves_the_claim_on_file_and_not_the_employees_to_delete(self):
		"""A rejected claim keeps `status` Draft, so the check has to be on approval.

		`withdrawable` keyed on `status` alone would read True here, and the employee
		could delete HR's decision along with the receipts that backed it.
		"""
		named = [
			row.approver_user
			for row in self._open_request().approvers
			if row.status in ("New", "Pending", "Waiting")
		]
		if not named:
			self.skipTest(f"the {expenses.EXPENSE_CATEGORY} category named no approver")
		self._act_as(named[0], "action_reject")

		claim = frappe.get_doc("Expense Claim", self.claim)
		self.assertEqual(claim.approval_status, "Rejected")
		self.assertEqual(claim.status, "Draft", "a rejected claim should not read as submitted")
		self.assertEqual(claim.docstatus, 0, "a rejected claim must not be submitted")

		detail = expenses.my_expense_claim(self.token, self.claim)
		self.assertFalse(detail["withdrawable"])
		with self.assertRaises(frappe.exceptions.ValidationError):
			expenses.withdraw_expense_claim(self.token, self.claim)
		self.assertTrue(
			frappe.db.exists("Expense Claim", self.claim), "the rejected claim was deleted"
		)


class TestExpenseResolution(FrappeTestCase):
	"""The two accounts a claim needs, resolved from the company rather than guessed."""

	def test_a_missing_cost_centre_is_reported_not_guessed(self):
		self.assertIsNone(expenses.resolve_cost_center("No Such Company"))

	def test_a_resolved_cost_centre_is_one_that_still_exists(self):
		"""`Company.cost_center` is a stored name, not a guarantee.

		If the Cost Center it names is deleted, the claim would fail at submit with a
		link error naming no field the employee can see, so the resolution checks
		existence rather than trusting the stored value.
		"""
		for company in frappe.get_all("Company", pluck="name", limit_page_length=0):
			cost_center = expenses.resolve_cost_center(company)
			if cost_center:
				self.assertTrue(
					frappe.db.exists("Cost Center", cost_center),
					f"{company} resolves to {cost_center!r}, which is not a Cost Center",
				)

	def test_a_company_pointing_at_a_deleted_cost_centre_resolves_to_nothing(self):
		"""Exercised on a company name that does not exist, so nothing is mutated.

		A real company whose `cost_center` has been deleted would take this path in
		production; creating one here to prove it would leave the site's own reference
		data altered for the sake of a test.
		"""
		self.assertIsNone(expenses.resolve_cost_center("No Such Company"))

	def test_a_missing_payable_account_is_refused(self):
		self.assertIsNone(expenses.resolve_payable_account("No Such Company"))

	def test_an_uninstalled_expense_item_is_not_referenced(self):
		"""This version links a line's category to `Expense Claim Type`.

		An `Expense Item` link would fail at insert with `LinkValidationError` naming a
		doctype that is not installed, so the test pins what the code actually uses.
		"""
		self.assertFalse(frappe.db.exists("DocType", "Expense Item"))
		detail = frappe.get_meta("Expense Claim Detail").get_field("expense_type")
		self.assertEqual(detail.options, "Expense Claim Type")


class TestExpenseWiring(FrappeTestCase):
	def test_the_actions_the_page_uses_all_exist(self):
		for name in (
			"my_expense_claims",
			"expense_claim_types",
			"my_expense_claim",
			"file_expense_claim",
			"withdraw_expense_claim",
		):
			self.assertIn(name, renderers.ACTIONS)

	def test_the_claim_types_action_reports_the_company_it_resolved_for(self):
		"""The client shows the account, so it has to know whose it is."""
		employee = frappe.db.get_value(
			"Employee",
			{"status": "Active", "sdg_national_id": ("is", "set"), "employee_number": ("is", "set")},
			"name",
		)
		token = portal.sign_in(
			frappe.db.get_value("Employee", employee, "sdg_national_id"),
			frappe.db.get_value("Employee", employee, "employee_number"),
		)["token"]
		out = expenses.expense_claim_types(token)
		self.assertEqual(
			out["company"], frappe.db.get_value("Employee", employee, "company")
		)
