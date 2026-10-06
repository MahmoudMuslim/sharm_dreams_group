# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the default leave approver the seeder assigns.

The failure this guards is not a crash. It is 310 employees whose leave requests
quietly queue against nobody, or against an employee name written into a Link to
`User` - which looks assigned in a list and is not a valid approver at all.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group import seeder


class TestDefaultLeaveApprover(FrappeTestCase):
	def test_the_default_approver_resolves_to_a_real_user(self):
		"""`leave_approver` is a Link to `User`, not to `Employee`.

		An employee name passes every check that only looks at the database and fails
		on insert with `Could not find Leave Approver: H0050`.
		"""
		user = seeder.resolve_leave_approver_user()
		self.assertTrue(
			frappe.db.exists("User", user),
			f"the default approver resolves to {user!r}, which is not a User",
		)
		self.assertNotEqual(
			user,
			seeder.DEFAULT_LEAVE_APPROVER,
			"the resolved value is still the employee name, not a user id",
		)

	def test_an_approver_who_is_not_an_employee_is_refused(self):
		original = seeder.DEFAULT_LEAVE_APPROVER
		seeder.DEFAULT_LEAVE_APPROVER = "NOPE-1"
		self.addCleanup(setattr, seeder, "DEFAULT_LEAVE_APPROVER", original)
		with self.assertRaises(frappe.exceptions.ValidationError):
			seeder.resolve_leave_approver_user()

	def test_an_approver_who_has_left_is_refused(self):
		"""A queue nobody reads still looks like leave is being processed."""
		leaver = frappe.db.get_value(
			"Employee", {"status": ("!=", "Active"), "leave_approver": ("is", "set")}, "name"
		)
		if not leaver:
			self.skipTest("no inactive employee with an approver to borrow")
		original = seeder.DEFAULT_LEAVE_APPROVER
		seeder.DEFAULT_LEAVE_APPROVER = leaver
		self.addCleanup(setattr, seeder, "DEFAULT_LEAVE_APPROVER", original)
		with self.assertRaises(frappe.exceptions.ValidationError):
			seeder.resolve_leave_approver_user()

	def test_an_approver_with_no_frappe_user_is_refused(self):
		"""The Link has nothing valid to hold, and the request would go nowhere."""
		candidate = frappe.db.get_value(
			"Employee", {"status": "Active", "user_id": ("is", "not set")}, "name"
		)
		if not candidate:
			self.skipTest("no active employee without a Frappe user")
		original = seeder.DEFAULT_LEAVE_APPROVER
		seeder.DEFAULT_LEAVE_APPROVER = candidate
		self.addCleanup(setattr, seeder, "DEFAULT_LEAVE_APPROVER", original)
		with self.assertRaises(frappe.exceptions.ValidationError):
			seeder.resolve_leave_approver_user()

	def test_it_only_fills_blanks_and_never_overwrites_hr(self):
		"""Overwriting a populated approver would undo HR's routing on every re-run."""
		employee = frappe.db.get_value("Employee", {"leave_approver": ("is", "set")}, "name")
		if not employee:
			self.skipTest("nobody has an approver yet")
		before = frappe.db.get_value("Employee", employee, "leave_approver")

		# A run with nothing blank must change nothing at all.
		blank = frappe.db.count("Employee", {"leave_approver": ("in", ("", None))})
		if blank:
			# Fill one, put it back, and confirm the count reports no work to do.
			frappe.db.set_value("Employee", employee, "leave_approver", "")
			self.addCleanup(
				frappe.db.set_value, "Employee", employee, "leave_approver", before
			)
			filled = seeder.assign_leave_approver()
			self.assertEqual(filled, 1)

		self.assertEqual(seeder.assign_leave_approver(), 0)
		self.assertEqual(
			frappe.db.get_value("Employee", employee, "leave_approver"),
			seeder.resolve_leave_approver_user(),
			"HR's own choice was overwritten by a second run",
		)

	def test_every_assigned_approver_is_a_real_user(self):
		"""The whole point: nothing may hold a value that cannot be resolved."""
		invalid = frappe.db.sql(
			"SELECT name, leave_approver FROM `tabEmployee` "
			"WHERE leave_approver IS NOT NULL AND leave_approver <> '' "
			"AND leave_approver NOT IN (SELECT name FROM `tabUser`)"
		)
		self.assertFalse(
			list(invalid),
			f"these employees point at something that is not a User, so a Leave "
			f"Application naming them would fail link validation: {invalid}",
		)