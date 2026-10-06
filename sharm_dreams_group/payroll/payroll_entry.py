"""Adds the payroll-building action to HRMS's own Payroll Entry.

A `Button` custom field's `options` is resolved against the *document*, not
against a module: the desk posts to `run_doc_method`, which does
`getattr(doc, options)`.  So a whitelisted function elsewhere is not enough - the
entry needs a method of its own for the button to find.

The work itself stays in `sharm_dreams_group.payroll.run`; this is only the
handle the button pulls, and it exists so HR can build the month's slips from the
desk rather than asking someone to run a script.
"""

import frappe
from hrms.payroll.doctype.payroll_entry.payroll_entry import PayrollEntry

from sharm_dreams_group.payroll.run import build_for_entry


class SDGPayrollEntry(PayrollEntry):
	"""HRMS's Payroll Entry with one added action."""

	@frappe.whitelist()
	def build_for_entry(self):
		"""List this month's employees and write their figures onto the slips.

		Called by the "Build SDG Salary Slips" button.  The whitelist decorator is
		what lets the desk reach it: `run_doc_method` resolves the action on the
		document and then refuses to call anything that is not whitelisted, which
		shows up as "Function ... is not whitelisted".

		Deliberately leaves the slips as drafts so the figures can be checked before
		anything is posted.
		"""
		return build_for_entry(self.name)
