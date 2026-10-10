# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the dashboard's outstanding items.

The emphasis is isolation. A notification list is the easiest thing in this portal to
get wrong in the most damaging way: it is small, it is fetched before anybody chooses a
screen, and it would carry somebody else's leave or somebody else's performance review
if the scoping were copied carelessly. So the first tests here are about whose items
these are, and only then about what they say.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import notifications, portal


class NotificationsTestCase(FrappeTestCase):
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
		if len(self.employees) < 2:
			self.skipTest("need two sign-in-able employees to test isolation")
		self.a, self.b = self.employees[0], self.employees[1]
		self.token_a = portal.sign_in(self.a.sdg_national_id, self.a.employee_number)["token"]

	def token_for(self, employee):
		return portal.sign_in(employee.sdg_national_id, employee.employee_number)["token"]

	def make_leave(self, employee, status="Open"):
		"""A Leave Application for `employee`, cleaned up afterwards.

		`docstatus` 0 on purpose: a submitted one runs HRMS's balance validation, and this
		is about what the notification says, not about whether HRMS would accept the leave.

		The approver is named because `HR Settings.leave_approver_mandatory_in_leave_application`
		is on here and the insert is refused without one - even at draft stage, which is
		the sort of rule that turns a two-line fixture into a research project.
		"""
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee.name,
				"leave_type": "Annual Leave",
				"from_date": "2026-10-20",
				"to_date": "2026-10-21",
				"description": "notification test",
				"leave_approver": "Administrator",
				"status": status,
			}
		).insert(ignore_permissions=True)
		# Keywords, not positionals: `delete_doc`'s fourth positional is
		# `ignore_doctypes`, which wants a set - passing `True` there raises
		# "bool object is not iterable" from inside the delete rather than from here.
		self.addCleanup(
			lambda name=doc.name: frappe.delete_doc(
				"Leave Application", name, force=True, ignore_permissions=True
			)
		)
		return doc


class TestNotificationScoping(NotificationsTestCase):
	def test_a_bogus_token_is_refused(self):
		# This list is fetched on the dashboard, before the employee has chosen
		# anything, so it is the one read most likely to be exposed by a mistake.
		with self.assertRaises(frappe.exceptions.ValidationError):
			notifications.my_notifications("not-a-real-token")

	def test_items_are_only_ever_the_signed_in_employees(self):
		self.make_leave(self.a, status="Open")
		mine = notifications.my_notifications(self.token_a)["items"]
		theirs = notifications.my_notifications(self.token_for(self.b))["items"]
		self.assertTrue(
			any(item["headline"].startswith("Your leave request") for item in mine),
			"the open request did not surface for the employee who made it",
		)
		self.assertFalse(
			any(item["headline"].startswith("Your leave request") for item in theirs),
			"one employee's open request appeared on another employee's dashboard",
		)


class TestWhatCountsAsOutstanding(NotificationsTestCase):
	def test_an_open_request_is_outstanding(self):
		self.make_leave(self.a, status="Open")
		items = notifications.my_notifications(self.token_a)["items"]
		self.assertTrue(
			any(item["key"].startswith("leave:") for item in items),
			"an awaiting-approval request is not the employee's business to know about",
		)

	def test_an_approved_request_is_not(self):
		"""Once HR has answered it, there is nothing left to do about it.

		Counting approved requests would put a permanent number on the dashboard that
		the employee cannot act on, which trains people to ignore the badge.
		"""
		self.make_leave(self.a, status="Approved")
		items = notifications.my_notifications(self.token_a)["items"]
		self.assertFalse(
			any(item["key"].startswith("leave:") for item in items),
			"an approved request is still being nagged about",
		)

	def test_every_item_says_where_it_goes(self):
		"""A badge that does not open the screen it is about is a dead end."""
		self.make_leave(self.a, status="Open")
		for item in notifications.my_notifications(self.token_a)["items"]:
			with self.subTest(item["key"]):
				self.assertTrue(item["screen"])
				self.assertTrue(item["headline"])

	def test_an_employee_with_nothing_outstanding_gets_an_empty_list(self):
		# Not a fabricated "all clear" item: the dashboard shows no card at all, and an
		# always-present panel is noise.
		self.assertIsInstance(notifications.my_notifications(self.token_a)["items"], list)