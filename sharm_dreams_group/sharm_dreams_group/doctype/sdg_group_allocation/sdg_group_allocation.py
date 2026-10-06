# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGGroupAllocation(Document):
	"""One cost share: part of an employee's pay charged to another group company.

	The employee has a single Salary Slip with their employing company, and that
	slip's gross pay already includes the cost share, because the employee is paid
	one amount.  This record is the other side of the same fact: it says which
	company is being charged, and for how much, so the group's books add up.

	Nothing here affects what the employee is paid.  The `SDG Group Allocation`
	hooks in `hooks.py` are registered only once this doctype exists.

	For H0257 the sheet charges a cost share to the company that already employs
	them, so `charged_company` is not always a sister company.
	"""

	def validate(self):
		# the charged company may legitimately be the employing one: H0257's cost
		# share in the Master Payroll sheet names the same company as their own
		# row, which reads as a reallocation inside one company (a second
		# department or engagement) rather than a charge to a sister company.
		# The record is still worth keeping, so this is recorded rather than
		# rejected - but it is called out so it is not mistaken for a cross-company
		# charge.
		if self.charged_company and self.charged_company == self.employing_company:
			note = "charged to the employing company (within-company reallocation)"
			self.remarks = f"{self.remarks}; {note}" if self.remarks else note
		# the net is what the charged company is billed
		self.net_amount = max(flt(self.gross_amount) - flt(self.income_tax) - flt(self.martyrs_fund), 0.0)
