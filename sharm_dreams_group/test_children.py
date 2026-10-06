# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Children and the company school fee each one attracts.

Ported from `hr.employment.children` / `hr.employment.school.fee` in
/home/mahmoud/hr_managing/models. The shape is kept deliberately: one child per
row, and the fee itself is a master record rather than a number typed into every
employee's form.
"""

from __future__ import annotations

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.hr import children as ch
from sharm_dreams_group.hr.children import eligible_children, totals

PRIMARY = "School Fees Test - Primary"
SECONDARY = "School Fees Test - Secondary"


class TestTotals(FrappeTestCase):
	"""The arithmetic, with no database behind it."""

	@staticmethod
	def _child(name, age, eligible=1, fee=None):
		return frappe._dict(child_name=name, age=age, eligible_for_school_fee=eligible, school_fee=fee)

	def test_no_children_costs_nothing(self):
		self.assertEqual(totals([]), (0, 0, 0.0))
		self.assertEqual(totals(None), (0, 0, 0.0))

	def test_an_ineligible_child_is_counted_but_not_costed(self):
		"""Kept on the record, not paid for. Conflating the two would make the
		headcount of children disagree with the number HR is paying."""
		rows = [self._child("Sara", 4, eligible=0, fee=PRIMARY)]
		count, eligible, total = totals(rows)
		self.assertEqual(count, 1)
		self.assertEqual(eligible, 0)
		self.assertEqual(total, 0.0)

	def test_an_eligible_child_with_no_fee_is_not_costed(self):
		"""`Children Claiming Fees` counts children that actually claim one.

		A child marked eligible whose fee has not been set up yet is not claiming
		anything, so counting it would report a company cost it is not paying. It
		is still counted as a child, which is how the gap stays visible.
		"""
		count, eligible, total = totals([self._child("Mariam", 9, fee=None)])
		self.assertEqual((count, eligible), (1, 0))
		self.assertEqual(total, 0.0)

	def test_only_eligible_rows_are_costed(self):
		rows = [
			self._child("Mariam", 9, fee=PRIMARY),
			self._child("Sara", 4, eligible=0, fee=PRIMARY),
			self._child("Youssef", 14, fee=SECONDARY),
		]
		self.assertEqual(len(eligible_children(rows)), 2)


class TestAgainstTheDatabase(FrappeTestCase):
	"""The same arithmetic, but reading the real fee master.

	The unit tests above stub the fee lookup by name. They cannot catch a fee that
	is stored under a different key from the one a child links to, which is
	exactly how the first version of `totals` returned zero while looking
	correct.
	"""

	def setUp(self):
		super().setUp()
		self._fees = {PRIMARY: 4500.5, SECONDARY: 9800}
		for name, amount in self._fees.items():
			if not frappe.db.exists("SDG School Fee", name):
				frappe.get_doc(
					{
						"doctype": "SDG School Fee",
						"school_fee_name": name,
						"amount": amount,
					}
				).insert(ignore_permissions=True)

	def tearDown(self):
		for name in self._fees:
			if frappe.db.exists("SDG School Fee", name):
				frappe.delete_doc("SDG School Fee", name, ignore_permissions=True, force=True)
		super().tearDown()

	def _employee(self) -> str:
		return frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1)[0]

	def _child(self, name, age, eligible=1, fee=None):
		return frappe._dict(child_name=name, age=age, eligible_for_school_fee=eligible, school_fee=fee)

	def test_the_total_reads_the_stored_fee(self):
		rows = [
			self._child("Mariam", 9, fee=PRIMARY),
			self._child("Youssef", 14, fee=SECONDARY),
			self._child("Sara", 4, eligible=0, fee=PRIMARY),
		]
		count, eligible, total = totals(rows)
		self.assertEqual((count, eligible), (3, 2))
		self.assertEqual(total, 4500.5 + 9800)

	def test_two_children_on_one_fee_are_both_charged(self):
		rows = [self._child("Mariam", 9, fee=PRIMARY), self._child("Nour", 7, fee=PRIMARY)]
		self.assertEqual(totals(rows)[2], 4500.5 * 2)

	def test_a_fee_deleted_out_from_under_a_child_does_not_raise(self):
		"""A deleted link must not turn every Employee save into an error."""
		rows = [self._child("Mariam", 9, fee="SDG School Fee - long gone")]
		self.assertEqual(totals(rows)[2], 0.0)

	def test_the_employee_fields_are_derived_on_save(self):
		emp = self._employee()
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_children or []):
			doc.remove(row)
		doc.append("sdg_children", self._child("Mariam", 9, fee=PRIMARY))
		doc.append("sdg_children", self._child("Sara", 4, eligible=0, fee=None))
		doc.save(ignore_permissions=True)
		doc.reload()

		self.assertEqual(doc.sdg_children_count, 2)
		self.assertEqual(doc.sdg_school_fee_children, 1)
		self.assertEqual(doc.sdg_school_fee_total, 4500.5)

	def test_the_fee_counts_are_read_off_the_child_rows(self):
		emp = self._employee()
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_children or []):
			doc.remove(row)
		doc.append("sdg_children", self._child("Mariam", 9, fee=PRIMARY))
		doc.save(ignore_permissions=True)

		ch.refresh_fee_counts()

		fee = frappe.get_doc("SDG School Fee", PRIMARY)
		self.assertEqual(fee.child_count, 1)
		self.assertEqual(fee.employee_count, 1)

	def test_two_children_on_one_employee_count_as_one_employee(self):
		"""`employee_count` is employees, not children - the two would otherwise
		differ on the very first row and quietly agree by accident."""
		emp = self._employee()
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_children or []):
			doc.remove(row)
		doc.append("sdg_children", self._child("Mariam", 9, fee=PRIMARY))
		doc.append("sdg_children", self._child("Nour", 7, fee=PRIMARY))
		doc.save(ignore_permissions=True)

		ch.refresh_fee_counts()

		fee = frappe.get_doc("SDG School Fee", PRIMARY)
		self.assertEqual(fee.child_count, 2)
		self.assertEqual(fee.employee_count, 1)


class TestChildRowRules(FrappeTestCase):
	"""The row-level guard."""

	def _save(self, age, name="Test Child"):
		"""Saved through the Employee, deliberately.

		Inserting an `SDG Children` row on its own would fire the child
		controller's own `validate`, which is the wrong path: on the real form the
		row is written by `Document.update_child_table` using `db_update()`, and
		that never runs `validate`. A test that opens the row directly passes
		while the form lets the same value through.
		"""
		emp = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1)[0]
		doc = frappe.get_doc("Employee", emp)
		for row in list(doc.sdg_children or []):
			doc.remove(row)
		doc.append(
			"sdg_children",
			{"child_name": name, "age": age, "eligible_for_school_fee": 0},
		)
		doc.save(ignore_permissions=True)

	def test_the_guard_is_reachable_from_the_form_not_just_the_row(self):
		"""Pins the Frappe behaviour this depends on.

		If a Frappe upgrade ever starts running child `validate` on a parent save,
		this still passes; if the guard were removed from the Employee hook, it
		fails here rather than passing on the isolated-insert path.
		"""
		with self.assertRaises(frappe.ValidationError):
			self._save(-1)

	def test_a_negative_age_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._save(-1)

	def test_an_implausible_age_is_refused(self):
		"""`age` is a stored number rather than a date of birth, so it is a typo
		magnet - and a typo in an eligibility field is a wrong benefit decision."""
		with self.assertRaises(frappe.ValidationError):
			self._save(140)

	def test_an_ordinary_age_is_accepted(self):
		self._save(9)

	def test_a_negative_fee_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "SDG School Fee",
					"school_fee_name": "School Fees Test - negative",
					"amount": -1,
				}
			).insert(ignore_permissions=True)


class TestChildTableShape(FrappeTestCase):
	def test_the_children_table_is_a_child_doctype(self):
		"""It hangs off Employee. A non-child doctype linked here would open a list
		that nothing writes to."""
		self.assertTrue(frappe.db.get_value("DocType", "SDG Children", "istable"))

	def test_the_fee_is_a_document_not_a_child(self):
		self.assertFalse(frappe.db.get_value("DocType", "SDG School Fee", "istable"))

	def test_the_fee_name_is_unique(self):
		"""Two records called the same thing would make a child's link ambiguous
		and the count ambiguous with it."""
		self.assertTrue(frappe.db.get_value("DocType", "SDG School Fee", "autoname"))
		self.assertEqual(
			frappe.db.get_value("DocType", "SDG School Fee", "autoname"), "field:school_fee_name"
		)

	def test_the_employee_carries_the_table(self):
		field = frappe.get_meta("Employee").get_field("sdg_children")
		self.assertTrue(field, "Employee has no sdg_children field")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "SDG Children")


# The Odoo sources live on the host, not in the container the tests run in, so
# the parity check is skipped rather than failed where it cannot be seen.
ODOO_DIR = "/home/mahmoud/hr_managing/models"


class TestOdooParity(unittest.TestCase):
	"""The shape promised by the Odoo module this was ported from.

	Plain unittest: these read the Odoo sources, not the database.
	"""

	ODOO = ODOO_DIR

	def test_the_odoo_module_is_still_there_to_compare_against(self):
		import os

		if not os.path.isdir(self.ODOO):
			self.skipTest("the Odoo sources are not visible from here")
		for name in ("hr_children.py", "hr_school_fee.py"):
			self.assertTrue(
				os.path.exists(os.path.join(self.ODOO, name)),
				f"{name} has gone; the ported shape can no longer be checked against it",
			)

	def test_the_port_carries_every_field_of_the_odoo_child(self):
		import os

		if not os.path.isdir(self.ODOO):
			self.skipTest("the Odoo sources are not visible from here")
		with open(os.path.join(self.ODOO, "hr_children.py")) as handle:
			source = handle.read()
		for fieldname in ("name", "age", "eligible_for_school_fee", "school_fee_id"):
			self.assertIn(fieldname, source)
		meta = frappe.get_meta("SDG Children")
		self.assertTrue(meta.get_field("child_name"))
		self.assertTrue(meta.get_field("age"))
		self.assertTrue(meta.get_field("eligible_for_school_fee"))
		self.assertTrue(meta.get_field("school_fee"))
