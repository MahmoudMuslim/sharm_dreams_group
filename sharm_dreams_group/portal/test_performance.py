# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the performance screen and the self-assessment it submits.

The site has no Appraisal Cycle, no Appraisal Template, no Appraisal and no
`Employee Feedback Criteria`, so every test here builds the fixture HR would build. That
is the point: the empty case is one `my_performance` call and a list of blockers, while
the paths that matter - filling in a self-assessment, submitting it, an approver
accepting or sending it back - only run against data.

Each test removes its own cycle, appraisal and criteria in `tearDown`. An `Appraisal`
is submitted or cancelled rather than deleted once it has been acted on.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import performance, portal, renderers
from sharm_dreams_group.seeder import DEFAULT_LEAVE_APPROVER, seed_approval_categories

CRITERIA = ["Portal Test Criterion A", "Portal Test Criterion B"]
CYCLE = "Portal Test Cycle 2026"
APPRAISEE_CYCLE = "Portal Test Cycle With Appraisees"


class PerformanceTestCase(FrappeTestCase):
	"""A signed-in employee, a rating criterion, and a cycle to appraise against."""

	def setUp(self):
		self.employee = frappe.db.get_value(
			"Employee",
			{
				"status": "Active",
				"sdg_national_id": ("is", "set"),
				"employee_number": ("is", "set"),
			},
			"name",
		)
		if not self.employee:
			self.skipTest("no active employee who can sign in")
		self.token = portal.sign_in(
			frappe.db.get_value("Employee", self.employee, "sdg_national_id"),
			frappe.db.get_value("Employee", self.employee, "employee_number"),
		)["token"]

		self.company = frappe.db.get_value("Employee", self.employee, "company")
		self._make_criteria()
		self._make_cycle()
		frappe.db.commit()

	def tearDown(self):
		# Approval requests first: they are keyed by a `reference` string, not a link,
		# so nothing cascades when the appraisal goes. Left behind they match the *next*
		# test's appraisal by name substring and make it look like that test submitted
		# something.
		for name in frappe.get_all(
			"Employee Self Service Approval Request",
			filters={"employee": self.employee, "request_type": performance.PERFORMANCE_CATEGORY},
			pluck="name",
			limit_page_length=0,
		):
			frappe.delete_doc(
				"Employee Self Service Approval Request", name, force=True, ignore_permissions=True
			)
		for name in frappe.get_all(
			"Appraisal",
			filters={"employee": self.employee, "appraisal_cycle": ("in", [CYCLE, APPRAISEE_CYCLE])},
			pluck="name",
			limit_page_length=0,
		):
			doc = frappe.get_doc("Appraisal", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Appraisal", name, force=True, ignore_permissions=True)
		for name in [CYCLE, APPRAISEE_CYCLE]:
			if frappe.db.exists("Appraisal Cycle", name):
				frappe.delete_doc("Appraisal Cycle", name, force=True, ignore_permissions=True)
		for name in CRITERIA:
			if frappe.db.exists("Employee Feedback Criteria", name):
				frappe.delete_doc(
					"Employee Feedback Criteria", name, force=True, ignore_permissions=True
				)
		frappe.db.commit()

	# ------------------------------------------------------------------ fixtures
	def _make_criteria(self):
		"""Two criteria at 50/50.

		50/50 rather than one at 100 so a partial self-assessment is a *different*
		weightage total from a complete one, which is what `validate_total_weightage`
		actually checks and therefore what the submit guard has to get right.
		"""
		for name in CRITERIA:
			if not frappe.db.exists("Employee Feedback Criteria", name):
				frappe.get_doc(
					{
						"doctype": "Employee Feedback Criteria",
						"criteria": name,
						"description": "Created by the portal test suite.",
					}
				).insert(ignore_permissions=True)

	def _make_cycle(self):
		"""An in-progress cycle.

		"In Progress", not "Not Started": `validate_active_appraisal_cycle` only refuses
		`Completed`, so both would pass, but "In Progress" is the state an appraisal
		actually gets submitted in.
		"""
		if frappe.db.exists("Appraisal Cycle", CYCLE):
			return
		frappe.get_doc(
			{
				"doctype": "Appraisal Cycle",
				"cycle_name": CYCLE,
				"company": self.company,
				"status": "In Progress",
				"start_date": "2026-01-01",
				"end_date": "2026-12-31",
			}
		).insert(ignore_permissions=True)

	def _clear_appraisals(self):
		"""Remove this employee's appraisals against the test cycles.

		Called at the *start* of `_make_appraisal` as well as in `tearDown`. Relying on
		`tearDown` alone is not enough: a test that leaves a draft behind makes the next
		test's `next(...)` find the wrong appraisal, and the failure then reads as a
		problem with the code under test rather than with the leftover.
		"""
		for name in frappe.get_all(
			"Appraisal",
			filters={
				"employee": self.employee,
				"appraisal_cycle": ("in", [CYCLE, APPRAISEE_CYCLE]),
			},
			pluck="name",
			limit_page_length=0,
		):
			doc = frappe.get_doc("Appraisal", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Appraisal", name, force=True, ignore_permissions=True)

	def _make_appraisal(self, rated: bool = False, reflections: str = "") -> str:
		"""A draft appraisal for this employee, exactly as HR would create one.

		Rows are written rather than pulled from a template because
		`set_appraisal_template` is called by the cycle's create step, not by
		`Appraisal.validate`. Weightages total 100 on both tables, which is what HRMS
		insists on.
		"""
		self._clear_appraisals()
		doc = frappe.get_doc(
			{
				"doctype": "Appraisal",
				"employee": self.employee,
				"company": self.company,
				"appraisal_cycle": CYCLE,
				"start_date": "2026-01-01",
				"end_date": "2026-12-31",
				"rate_goals_manually": 1,
				"goals": [
					{"kra": "Portal test goal one", "per_weightage": 60.0},
					{"kra": "Portal test goal two", "per_weightage": 40.0},
				],
				"self_ratings": [
					{"criteria": name, "per_weightage": 50.0, "rating": 4 if rated else 0}
					for name in CRITERIA
				],
				"reflections": reflections,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _rate_all(self, value: int = 4) -> list:
		return [{"criteria": name, "rating": value} for name in CRITERIA]


class TestBlockers(FrappeTestCase):
	"""Why the screen explains itself instead of rendering a blank frame.

	The dashboard trap, avoided: an empty list with no explanation is indistinguishable
	from a broken report. `_blockers` is a pure function of the appraisals and criteria it
	is given, so it is tested directly rather than through whatever the site happens to
	hold - which is better anyway, because these tests used to depend on every employee
	in this database having no appraisal, and stopped passing the moment the seeder gave
	them one.
	"""

	def test_nothing_set_up_names_everything_that_is_missing(self):
		blockers = performance._blockers([], [], has_cycle=False)
		self.assertEqual(len(blockers), 3, blockers)
		joined = " ".join(blockers).lower()
		for expected in ("cycle", "criteria", "appraisal"):
			self.assertIn(expected, joined)

	def test_each_reason_appears_only_while_it_applies(self):
		"""The point of the list is that it is not a fixed string.

		A blocker that persists after the thing it names exists is noise, and noise in a
		list of explanations is how the explanations stop being read.
		"""
		# Criteria defined, appraisal created, but still no cycle - which is the state
		# right after HR sets criteria up.
		only_cycle = performance._blockers([{"name": "x"}], ["Job knowledge"], has_cycle=False)
		self.assertEqual(len(only_cycle), 1, only_cycle)
		self.assertIn("cycle", only_cycle[0].lower())

		# Nothing missing at all: a cycle, criteria and an appraisal.
		self.assertEqual(
			performance._blockers([{"name": "x"}], ["Job knowledge"], has_cycle=True),
			[],
			"a fully set-up employee was still told something was missing",
		)

	def test_a_cycle_without_criteria_still_blocks(self):
		"""Two missing things must produce two sentences, not one."""
		self.assertEqual(
			len(performance._blockers([{"name": "x"}], [], has_cycle=True)), 1
		)
		self.assertEqual(
			len(performance._blockers([], [], has_cycle=True)), 2
		)

	def test_a_missing_appraisal_is_about_hr_not_the_employee(self):
		"""The wording matters: an employee must not think they are the problem."""
		blockers = performance._blockers([], ["Job knowledge"], has_cycle=True)
		self.assertEqual(len(blockers), 1)
		self.assertIn("your hr team", blockers[0].lower())

	def test_the_screen_renders_and_its_counts_match_its_lists(self):
		"""The invariant, on whatever this employee actually has."""
		employee = frappe.db.get_value(
			"Employee",
			{"status": "Active", "sdg_national_id": ("is", "set"),
			 "employee_number": ("is", "set")},
			"name",
		)
		if not employee:
			self.skipTest("no active employee who can sign in")
		token = portal.sign_in(
			frappe.db.get_value("Employee", employee, "sdg_national_id"),
			frappe.db.get_value("Employee", employee, "employee_number"),
		)["token"]
		out = performance.my_performance(token)
		counts = out["counts"]
		for key, rows in (
			("appraisals", out["appraisals"]),
			("goals", out["goals"]),
			("criteria", out["criteria"]),
		):
			self.assertEqual(
				counts[key], len(rows),
				f"the {key} figure and the list it opens disagree",
			)
		self.assertEqual(
			counts["awaiting_me"],
			len([a for a in out["appraisals"] if a["can_submit"]]),
		)

	def test_no_blocker_is_shown_to_an_employee_who_has_nothing_to_do(self):
		"""Every blocker must be actionable by somebody other than the employee.

		A sentence telling an employee to go and create an appraisal cycle is a dead end
		- they cannot, and it reads as the app being broken.
		"""
		for line in performance._blockers([], [], has_cycle=False):
			self.assertIn("your hr team", line.lower(), line)

	def test_a_bogus_token_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.my_performance("not-a-real-token")

class TestMyPerformance(PerformanceTestCase):
	def test_a_draft_appraisal_is_listed_with_its_goals_and_rating_rows(self):
		name = self._make_appraisal(reflections="A year of shipping.")
		out = performance.my_performance(self.token)
		view = next(a for a in out["appraisals"] if a["name"] == name)
		self.assertEqual(view["docstatus"], 0)
		self.assertEqual(view["status_label"], "Draft")
		self.assertEqual(view["reflections"], "A year of shipping.")
		self.assertEqual(len(view["goals"]), 2)
		self.assertEqual(len(view["self_ratings"]), 2)
		self.assertEqual(view["rated_count"], 0)
		self.assertTrue(view["can_edit"])
		self.assertFalse(view["can_submit"], "an unrated draft must not offer Submit")

	def test_the_counts_match_the_lists(self):
		self._make_appraisal()
		out = performance.my_performance(self.token)
		self.assertEqual(out["counts"]["appraisals"], len(out["appraisals"]))
		self.assertGreaterEqual(out["counts"]["appraisals"], 1)

	def test_another_employees_appraisal_is_not_listed(self):
		name = self._make_appraisal()
		other = frappe.db.get_value(
			"Employee",
			{"status": "Active", "name": ("!=", self.employee),
			 "sdg_national_id": ("is", "set"), "employee_number": ("is", "set")},
			"name",
		)
		if not other:
			self.skipTest("no second employee who can sign in")
		token = portal.sign_in(
			frappe.db.get_value("Employee", other, "sdg_national_id"),
			frappe.db.get_value("Employee", other, "employee_number"),
		)["token"]
		out = performance.my_performance(token)
		self.assertEqual(out["employee"], other)
		# Asserted as "not this one" rather than "empty": the seeder gives every active
		# employee an appraisal, so the other employee's list is not empty. What must
		# not happen is ours appearing on it.
		self.assertNotIn(
			name,
			[a["name"] for a in out["appraisals"]],
			"one employee's appraisal appeared on another's screen",
		)
		self.assertEqual(
			frappe.db.get_value("Appraisal", name, "employee"), self.employee
		)

	def test_a_cancelled_appraisal_is_not_listed(self):
		"""docstatus 2 is not history the employee needs on this screen."""
		name = self._make_appraisal()
		doc = frappe.get_doc("Appraisal", name)
		doc.submit()
		doc.cancel()
		frappe.db.commit()
		out = performance.my_performance(self.token)
		self.assertNotIn(name, [a["name"] for a in out["appraisals"]])


class TestSaveSelfAppraisal(PerformanceTestCase):
	def test_ratings_and_reflections_are_written(self):
		name = self._make_appraisal()
		out = performance.save_self_appraisal(
			self.token, name,
			ratings=self._rate_all(4),
			reflections="Did well on the portal work.",
		)
		self.assertTrue(out["saved"])
		self.assertEqual(out["rated_count"], 2)
		doc = frappe.get_doc("Appraisal", name)
		# Stored on the field's own 0-1 scale: four stars is 0.8.
		self.assertEqual([row.rating for row in doc.self_ratings], [0.8, 0.8])
		self.assertEqual(doc.reflections, "Did well on the portal work.")

	def test_a_rating_is_stored_as_a_fraction_of_the_star_scale(self):
		"""The bug this pins: a Rating field is clamped to 0-1 on every save.

		Writing 4 straight into the column is silently clamped to 1, which is five stars
		- so every employee who rated themselves 4 would have been recorded as perfect.
		The employee picks 0-5; the field holds a fraction of it.
		"""
		name = self._make_appraisal()
		performance.save_self_appraisal(
			self.token, name, ratings=[{"criteria": CRITERIA[0], "rating": 2}]
		)
		self.assertEqual(
			frappe.db.get_value(
				"Employee Feedback Rating",
				{"parent": name, "parenttype": "Appraisal", "criteria": CRITERIA[0]},
				"rating",
			),
			0.4,
			"two stars out of five was not stored as 0.4",
		)
		view = next(
			a for a in performance.my_performance(self.token)["appraisals"]
			if a["name"] == name
		)
		self.assertEqual(view["self_ratings"][0]["rating"], 2.0)

	def test_five_stars_survives_the_save(self):
		"""The top of the scale is the one value a clamp would have swallowed."""
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(5))
		self.assertEqual(
			frappe.get_all(
				"Employee Feedback Rating",
				filters={"parent": name, "parenttype": "Appraisal"},
				pluck="rating",
			),
			[1.0, 1.0],
		)

	def test_a_fully_rated_draft_offers_submit(self):
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(5))
		out = performance.my_performance(self.token)
		view = next(a for a in out["appraisals"] if a["name"] == name)
		self.assertTrue(view["can_submit"])
		self.assertEqual(view["rated_count"], view["rating_count"])

	def test_a_criterion_that_is_not_on_the_appraisal_is_refused(self):
		"""Silently dropping it would let an employee think they had rated it."""
		name = self._make_appraisal()
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.save_self_appraisal(
				self.token, name,
				ratings=[{"criteria": "Some Other Criterion", "rating": 5}],
			)

	def test_a_rating_off_the_scale_is_refused(self):
		name = self._make_appraisal()
		for bad in (-1, performance.RATING_STARS + 1):
			with self.assertRaises(frappe.exceptions.ValidationError):
				performance.save_self_appraisal(
					self.token, name, ratings=[{"criteria": CRITERIA[0], "rating": bad}]
				)

	def test_a_partly_rated_appraisal_is_still_a_draft(self):
		"""Half the criteria rated must not be submittable.

		`validate_total_weightage` would pass - the weightages are unchanged at 100 -
		so nothing upstream stops a partial submission. That is why the guard is here.
		"""
		name = self._make_appraisal()
		performance.save_self_appraisal(
			self.token, name, ratings=[{"criteria": CRITERIA[0], "rating": 4}]
		)
		view = next(
			a for a in performance.my_performance(self.token)["appraisals"]
			if a["name"] == name
		)
		self.assertEqual(view["rated_count"], 1)
		self.assertFalse(view["can_submit"])

	def test_somebody_elses_appraisal_cannot_be_written(self):
		other = frappe.db.get_value(
			"Employee",
			{"status": "Active", "name": ("!=", self.employee),
			 "sdg_national_id": ("is", "set"), "employee_number": ("is", "set")},
			"name",
		)
		if not other:
			self.skipTest("no second employee who can sign in")
		name = self._make_appraisal()
		token = portal.sign_in(
			frappe.db.get_value("Employee", other, "sdg_national_id"),
			frappe.db.get_value("Employee", other, "employee_number"),
		)["token"]
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.save_self_appraisal(token, name, ratings=self._rate_all(1))

	def test_an_appraisal_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.save_self_appraisal(self.token, "HR-APR-2026-NOPE", ratings=[])

	def test_submitting_works_without_an_appraisal_permission(self):
		"""The permission trap, which only shows up over HTTP.

		`bench execute` runs as Administrator, so a submission path that forgot
		`ignore_permissions` passes every test in this file and then fails in production
		with "User Guest does not have doctype access ... for document Appraisal".
		Acting as a session with no Appraisal role reproduces it.
		"""
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(4))

		previous = frappe.session.user
		frappe.set_user("Guest")
		try:
			out = performance.submit_appraisal(self.token, name)
		finally:
			frappe.set_user(previous)

		self.assertTrue(out["submitted"])
		self.assertEqual(frappe.db.get_value("Appraisal", name, "docstatus"), 1)

	def test_a_bogus_token_cannot_write(self):
		name = self._make_appraisal()
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.save_self_appraisal("not-a-real-token", name, ratings=[])


class TestSubmitAppraisal(PerformanceTestCase):
	def test_a_fully_rated_draft_submits_and_raises_a_request(self):
		seed_approval_categories()
		frappe.db.commit()
		name = self._make_appraisal(reflections="Ready for review.")
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(4))
		out = performance.submit_appraisal(self.token, name)
		self.assertTrue(out["submitted"])
		self.assertEqual(frappe.db.get_value("Appraisal", name, "docstatus"), 1)
		self.assertTrue(out["approval"], "submitted with nobody to approve it")

		request = frappe.get_doc("Employee Self Service Approval Request", out["approval"])
		self.assertEqual(request.request_type, performance.PERFORMANCE_CATEGORY)
		self.assertEqual(request.employee, self.employee)
		self.assertIn(name, request.reference)
		self.assertEqual(
			request.status, "Pending",
			"the request was left in Draft, so no approver can act on it",
		)
		self.assertTrue(
			request.approvers, "the request has no approver rows, so may_act is False"
		)

	def test_an_unrated_draft_cannot_be_submitted(self):
		name = self._make_appraisal()
		with self.assertRaises(frappe.exceptions.ValidationError) as caught:
			performance.submit_appraisal(self.token, name)
		self.assertIn(CRITERIA[0], str(caught.exception))
		self.assertEqual(
			frappe.db.get_value("Appraisal", name, "docstatus"), 0,
			"the refusal still submitted the appraisal",
		)

	def test_submitting_twice_is_refused(self):
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(3))
		performance.submit_appraisal(self.token, name)
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.submit_appraisal(self.token, name)

	def test_an_appraisal_in_a_completed_cycle_is_refused(self):
		"""HRMS refuses this on validate; the refusal must not leave a stray request."""
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(3))
		frappe.db.set_value("Appraisal Cycle", CYCLE, "status", "Completed")
		frappe.db.commit()
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.submit_appraisal(self.token, name)
		self.assertEqual(
			frappe.db.count("Employee Self Service Approval Request",
				{"reference": ("like", f"%{name}%")}),
			0,
			"a refused submission still raised an approval request",
		)


class TestApproval(PerformanceTestCase):
	def _submitted(self) -> tuple[str, str]:
		seed_approval_categories()
		frappe.db.commit()
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(4))
		out = performance.submit_appraisal(self.token, name)
		return name, out["approval"]

	def test_approving_leaves_the_submitted_appraisal_alone(self):
		"""There is nothing to post, so approval is a decision and not an action."""
		name, request_name = self._submitted()
		frappe.set_user(
			frappe.db.get_value("Employee", DEFAULT_LEAVE_APPROVER, "user_id")
		)
		frappe.get_doc("Employee Self Service Approval Request", request_name).action_approve()
		self.assertEqual(frappe.db.get_value("Appraisal", name, "docstatus"), 1)
		self.assertEqual(
			frappe.db.get_value("Employee Self Service Approval Request", request_name, "status"),
			"Approved",
		)

	def test_rejecting_reopens_the_appraisal_as_an_editable_draft(self):
		"""`cancel()` alone would leave docstatus 2, which is not editable.

		The document has to be *amended*: the submitted one is cancelled and a new draft
		appears, or an employee told to revise something they cannot open is stuck.
		"""
		name, request_name = self._submitted()
		frappe.set_user(
			frappe.db.get_value("Employee", DEFAULT_LEAVE_APPROVER, "user_id")
		)
		frappe.get_doc("Employee Self Service Approval Request", request_name).action_reject()
		self.assertEqual(frappe.db.get_value("Appraisal", name, "docstatus"), 2)
		self.assertEqual(
			frappe.db.get_value("Employee Self Service Approval Request", request_name, "status"),
			"Rejected",
		)

		out = performance.my_performance(self.token)
		# Scoped to the reopened one rather than "the only editable appraisal": the
		# seeder gives every active employee an editable draft of their own, so this
		# employee has more than one. `amended_from` is what identifies it.
		reopened = next(
			(
				a
				for a in out["appraisals"]
				if a["amended_from"] == name
			),
			None,
		)
		self.assertIsNotNone(reopened, "rejecting left nothing the employee can revise")
		self.assertTrue(reopened["can_edit"])
		self.assertEqual(reopened["rated_count"], reopened["rating_count"])

		# And it can be revised and sent again.
		performance.save_self_appraisal(self.token, reopened["name"], ratings=self._rate_all(2))
		again = performance.submit_appraisal(self.token, reopened["name"])
		self.assertTrue(again["submitted"])

	def test_approving_an_expense_request_does_not_touch_an_appraisal(self):
		"""The dispatch is on `request_type`, and this is what proves it."""
		seed_approval_categories()
		frappe.db.commit()
		frappe.set_user(
			frappe.db.get_value("Employee", DEFAULT_LEAVE_APPROVER, "user_id")
		)
		request = frappe.get_doc(
			{
				"doctype": "Employee Self Service Approval Request",
				"request_type": "Expenses",
				"employee": self.employee,
				"from_self_service": 1,
				"date": frappe.utils.today(),
				"subject": "Not an appraisal",
				"reference": "Expense Claim HR-EXP-2026-99999",
			}
		)
		request.insert(ignore_permissions=True)
		request.compute_approvers()
		request.action_confirm()
		request.action_approve()
		self.assertEqual(
			frappe.db.get_value(
				"Employee Self Service Approval Request", request.name, "status"
			),
			"Approved",
		)


	def test_the_reject_note_says_the_appraisal_can_be_revised(self):
		"""The opposite of the expense wording, and it has to be the opposite.

		The note used to be one fixed sentence, "It cannot be undone". That is true of an
		expense claim and false of a self-assessment, which rejecting amends into a fresh
		draft. A confirmation that overstates the consequence is how an approver stops
		reading confirmations, so the server now supplies the wording per category.
		"""
		from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_approval_request.employee_self_service_approval_request import (
			get_approval_actions,
		)

		seed_approval_categories()
		frappe.db.commit()
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(4))
		request_name = performance.submit_appraisal(self.token, name)["approval"]

		approver = frappe.db.get_value("Employee", DEFAULT_LEAVE_APPROVER, "user_id")
		previous = frappe.session.user
		try:
			frappe.set_user(approver)
			offered = get_approval_actions(request_name)
			self.assertEqual(sorted(offered["actions"]), ["Approve", "Reject"])
			note = offered["reject_note"].lower()
			self.assertIn("draft", note)
			self.assertIn("revise", note)
			self.assertNotIn(
				"cannot be undone",
				note,
				"the note still calls rejecting irreversible, which it is not",
			)
		finally:
			frappe.set_user(previous)

	def test_no_approver_is_offered_the_buttons(self):
		"""The gate reads the request's own rows, not who happens to be logged in."""
		from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_approval_request.employee_self_service_approval_request import (
			get_approval_actions,
		)

		seed_approval_categories()
		frappe.db.commit()
		name = self._make_appraisal()
		performance.save_self_appraisal(self.token, name, ratings=self._rate_all(4))
		request_name = performance.submit_appraisal(self.token, name)["approval"]

		previous = frappe.session.user
		try:
			for impostor in ("Administrator", "Guest"):
				frappe.set_user(impostor)
				self.assertEqual(
					get_approval_actions(request_name)["actions"],
					[],
					f"{impostor} was offered Approve on somebody else's request",
				)
		finally:
			frappe.set_user(previous)

class TestSeededPerformance(FrappeTestCase):
	"""Assertions about the seeded state, skipped when the seeder has not been run.

	All read-only. The seeder writes an appraisal per active employee, so running it
	inside the suite would be slow and would leave 180 rows behind; what matters is that
	what it produced is *correct*, and that is checkable without touching anything.
	"""

	def setUp(self):
		from sharm_dreams_group import seeder

		self.seeder = seeder
		if not frappe.db.exists("Appraisal Template", seeder.PERFORMANCE_TEMPLATE_NAME):
			self.skipTest("performance has not been seeded on this site")

	def test_the_weights_the_seeder_writes_total_one_hundred(self):
		"""HRMS refuses anything else, with a message about weightage allocation.

		Which is a confusing way to learn that a list has the wrong number of entries, so
		it is worth knowing before the seeder runs rather than after.
		"""
		template = frappe.get_doc("Appraisal Template", self.seeder.PERFORMANCE_TEMPLATE_NAME)
		for rows, label in ((template.goals, "goals"), (template.rating_criteria, "criteria")):
			total = sum(float(row.per_weightage) for row in rows)
			self.assertEqual(round(total, 2), 100.0, f"{label} total {total}")

	def test_every_seeded_object_is_one_the_teardown_can_find(self):
		"""The teardown is only as good as its idea of what was seeded.

		It rebuilds each cycle name through `performance_cycle_name`, so this checks that
		every seeded cycle is one that function would produce - and the prefix bug that
		made the teardown silently miss all thirteen could not survive it.
		"""
		expected = {
			self.seeder.performance_cycle_name(company)
			for company in frappe.get_all("Company", pluck="name", limit_page_length=0)
		}
		seeded = frappe.get_all(
			"Appraisal Cycle",
			filters={"name": ("in", tuple(expected))},
			pluck="name",
			limit_page_length=0,
		)
		self.assertTrue(seeded, "no seeded cycles found by the names the teardown builds")
		for name in seeded:
			self.assertIn(name, expected)

	def test_every_active_employee_has_exactly_one_seeded_appraisal(self):
		"""Coverage, and no duplicates."""
		rows = frappe.db.sql(
			"""
			SELECT e.name, COUNT(a.name)
			FROM `tabEmployee` e
			LEFT JOIN `tabAppraisal` a ON a.employee = e.name
			WHERE e.status = 'Active' AND e.company IS NOT NULL AND e.company <> ''
			GROUP BY e.name
			"""
		)
		zero = [name for name, count in rows if not count]
		more_than_one = [name for name, count in rows if count > 1]
		self.assertEqual(zero, [], "active employees with no seeded appraisal")
		self.assertEqual(more_than_one, [], "employees with more than one")

	def test_no_seeded_appraisal_is_on_the_wrong_company(self):
		"""The reason there is a cycle per company rather than one cycle.

		`create_appraisals_for_cycle` stamps each appraisal with the *cycle's* company,
		and nothing in `Appraisal.validate` compares it to the employee's. One cycle for
		13 companies would therefore put the wrong company on most of them, silently.
		"""
		wrong = frappe.db.sql(
			"""
			SELECT COUNT(*) FROM `tabAppraisal` a
			JOIN `tabEmployee` e ON e.name = a.employee
			WHERE a.company <> e.company
			"""
		)
		self.assertEqual(wrong[0][0], 0, f"{wrong[0][0]} appraisal(s) on the wrong company")

	def test_nothing_was_seeded_that_is_not_a_draft(self):
		"""Nobody has been appraised by this.

		The seeder creates drafts and leaves them there. If a seeded appraisal had been
		submitted, somebody had submitted a self-assessment nobody filled in.
		"""
		self.assertEqual(frappe.db.count("Appraisal", {"docstatus": 1}), 0)
		self.assertEqual(
			frappe.db.count("Employee Self Service Approval Request",
				{"request_type": "Performance"}),
			0,
			"a performance request exists with no employee submission behind it",
		)


class TestPerformanceWiring(FrappeTestCase):
	def test_the_actions_the_page_uses_all_exist(self):
		for name in ("my_performance", "save_self_appraisal", "submit_appraisal"):
			self.assertIn(name, renderers.ACTIONS)

	def test_every_performance_action_resolves_the_session_itself(self):
		"""`renderers.ACTIONS` is a whitelist, not a session check."""
		with self.assertRaises(frappe.exceptions.ValidationError):
			performance.my_performance("not-a-real-token")
		for func in (performance.save_self_appraisal, performance.submit_appraisal):
			with self.assertRaises(frappe.exceptions.ValidationError):
				func("not-a-real-token", "HR-APR-2026-NOPE")

	def test_the_category_name_is_the_one_the_seeder_creates(self):
		"""Two spellings of one category would queue for nobody."""
		from sharm_dreams_group.seeder import APPROVAL_CATEGORIES

		self.assertIn(performance.PERFORMANCE_CATEGORY, APPROVAL_CATEGORIES)

	def test_the_rating_scale_matches_the_field(self):
		"""`Employee Feedback Rating.rating` is a `Rating` field, which Frappe clamps to 0-1."""
		field = frappe.get_meta("Employee Feedback Rating").get_field("rating")
		self.assertEqual(field.fieldtype, "Rating")
		self.assertEqual(performance.RATING_STARS, 5)
		# The conversions are what the screen and the save both rely on.
		self.assertEqual(performance._stars_to_stored(5), 1.0)
		self.assertEqual(performance._stars_to_stored(2), 0.4)
		self.assertEqual(performance._stored_to_stars(0.4), 2.0)
		# Out of range is clamped rather than passed through, since `save` would clamp
		# it anyway and the two paths must not disagree.
		self.assertEqual(performance._stars_to_stored(9), 1.0)
		self.assertEqual(performance._stars_to_stored(-3), 0.0)