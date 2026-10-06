# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SDGChildren(Document):
	"""One child of an employee.

	`age` is validated rather than trusted: it is a stored number rather than a
	date of birth, so a child entered as 6 stays 6 until somebody remembers to
	change it. That is a property of the model this was ported from, not a
	decision made here - but a negative or implausible age is always a typo, and a
	typo in an eligibility field is a wrong benefit decision.
	"""

	def validate(self):
		if self.age is not None and self.age < 0:
			frappe.throw(
				frappe._("{0} is not a valid age for {1}.").format(self.age, self.child_name),
				title=frappe._("Check the age"),
			)
		if self.age is not None and self.age > 100:
			frappe.throw(
				frappe._("An age of {0} looks like a typo for {1}.").format(self.age, self.child_name),
				title=frappe._("Check the age"),
			)
