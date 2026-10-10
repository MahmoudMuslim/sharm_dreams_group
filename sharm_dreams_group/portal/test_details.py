# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Tests for the employee's own record and the fields they may correct on it.

Most of this file is about what the portal must *not* be able to write. A screen that
edits a record is only safe because of the fields it refuses; "we did not think of that
one" is not a defence, so the refusals are pinned here by name.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.portal import portal


class TestMyDetails(FrappeTestCase):
	"""The employee's own record, and the few fields they may correct on it.

	Most of this file is about what the portal must *not* be able to write. A screen that
	edits a record is only safe because of the fields it refuses; "we did not think of
	that one" is not a defence, so the refusals are pinned here by name.
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
		self._before = {
			field: frappe.db.get_value("Employee", self.emp, field)
			for field in portal.DETAIL_FIELDS
		}
		self.addCleanup(self._restore)

	def _restore(self):
		for field, value in self._before.items():
			frappe.db.set_value("Employee", self.emp, field, value)
		frappe.db.commit()

	def test_it_refuses_a_bogus_token(self):
		for action in (portal.my_details, portal.update_my_details):
			with self.assertRaises(frappe.exceptions.ValidationError):
				action("not-a-real-token")

	def test_it_returns_the_record_the_employee_can_see(self):
		out = portal.my_details(self.token)
		self.assertEqual(out["employee"], self.emp)
		self.assertEqual(out["employee_name"], frappe.db.get_value("Employee", self.emp, "employee_name"))
		# The editable half is exactly the whitelist, so the screen cannot be offered a
		# field the server would then refuse.
		self.assertEqual(set(out["editable"]), set(portal.DETAIL_FIELDS))

	def test_a_corrected_field_is_saved(self):
		portal.update_my_details(self.token, personal_email="someone@example.com")
		self.assertEqual(
			frappe.db.get_value("Employee", self.emp, "personal_email"),
			"someone@example.com",
		)

	def test_an_invalid_email_is_refused_and_nothing_is_written(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.update_my_details(
				self.token, personal_email="not-an-email", emergency_phone_number="01000000000"
			)
		# Refused whole, not field by field: a half-saved contact form is worse than one
		# that saved nothing, because the employee will believe all of it went in.
		self.assertEqual(
			frappe.db.get_value("Employee", self.emp, "emergency_phone_number"),
			self._before["emergency_phone_number"],
		)

	def test_a_blood_group_outside_the_list_is_refused(self):
		"""The Select's options are read from the doctype, not restated here."""
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.update_my_details(self.token, blood_group="Z+")

	def test_the_options_offered_come_from_the_doctype(self):
		self.assertIn("A+", portal.BLOOD_GROUPS)
		self.assertIn("O-", portal.BLOOD_GROUPS)

	def test_a_field_the_employee_does_not_own_is_refused_by_name(self):
		"""The whole point of the whitelist, tested one field at a time.

		Each of these decides pay or entitlement. A portal that could write any one of
		them would be a portal an employee could raise their own salary through.
		"""
		for field in ("company", "designation", "date_of_joining", "status", "sdg_national_id"):
			with self.subTest(field):
				with self.assertRaises(frappe.exceptions.ValidationError) as caught:
					portal.update_my_details(self.token, **{field: "anything"})
				self.assertIn(field, str(caught.exception))

	def test_a_refusal_does_not_write_the_fields_that_were_allowed(self):
		"""One bad field refuses the request; it does not silently drop itself."""
		with self.assertRaises(frappe.exceptions.ValidationError):
			portal.update_my_details(
				self.token, personal_email="fine@example.com", company="Somewhere Else"
			)
		self.assertEqual(
			frappe.db.get_value("Employee", self.emp, "personal_email"),
			self._before["personal_email"],
			"the allowed half was written even though the request was refused",
		)