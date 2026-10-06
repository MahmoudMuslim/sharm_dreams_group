# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGSchoolFee(Document):
	"""The company school fee for one child.

	Kept as its own doctype, mirroring `hr.employment.school.fee` in the Odoo
	module this was ported from. The alternative - typing the figure into every
	employee's child row - means the same fee lives in as many places as there are
	children, and a fee change becomes an edit nobody can be sure is complete.

	`child_count` and `employee_count` are derived rather than stored: they are
	only ever read off the child rows, and a stored count drifts the moment a row
	is edited from the Employee form without this record being touched.
	"""

	def validate(self):
		if flt(self.amount) < 0:
			frappe.throw(frappe._("A school fee cannot be negative."))

	def refresh_counts(self) -> None:
		"""Recount the children and employees on this fee."""
		# `parent`, not an employee column: a child table has no employee field of
		# its own, the employee *is* the parent row. Asking for `employee` here
		# raises "Unknown column" the first time anything recounts.
		children = frappe.get_all(
			"SDG Children",
			filters={"school_fee": self.name},
			fields=["parent"],
			limit_page_length=0,
		)
		employees = {row.parent for row in children if row.parent}
		self.db_set(
			{
				"child_count": len(children),
				"employee_count": len(employees),
			},
			update_modified=False,
		)
