# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SDGInsuredPerson(Document):
	"""Medical cover, statutory identity and the document checklist for one employee.

	Named after the employee (`autoname: field:employee`), so there is at most one
	record per person and it is reached directly from the Employee form.

	The `file_*` checks are the master workbook's "EMPLOYEE FILE INDEX" block.
	They are per-person HR records with no home on Employee, and keeping them
	here means the checklist can be reported on without widening the employee
	master.
	"""

	def validate(self):
		self.copy_employee_number()
		self.validate_card_expiry()
		self.resolve_family_band()

	def copy_employee_number(self):
		"""Mirror the employee number so the record is searchable without a join."""
		if self.employee and not self.employee_number:
			self.employee_number = frappe.db.get_value("Employee", self.employee, "employee_number")

	def validate_card_expiry(self):
		"""Warn once, not at 90 days: Egyptian health cards are annual."""
		if not self.card_expiry:
			return
		from frappe.utils import date_diff, getdate, nowdate

		days = date_diff(getdate(self.card_expiry), getdate(nowdate()))
		if days < 0:
			frappe.msgprint(
				frappe._("The insurance card expired {0} days ago.").format(abs(days)),
				title=frappe._("Card expired"),
				indicator="orange",
			)
		elif days <= 45:
			frappe.msgprint(
				frappe._("The insurance card expires in {0} days.").format(days),
				title=frappe._("Card expiring"),
				indicator="yellow",
			)

	def resolve_family_band(self):
		"""Pick the family premium from band, spouse and children.

		Three of the four inputs are per-person, so the combination is resolved
		here rather than on the salary slip.  A missing combination is left blank
		deliberately: the report surfaces it for a broker quote rather than
		silently charging nothing or guessing a price.
		"""
		if not self.medical_band or not (self.has_spouse or flt(self.dependants_count)):
			self.family_band = None
			return

		match = frappe.db.get_value(
			"SDG Family Medical Band",
			{
				"medical_band": self.medical_band,
				"has_spouse": 1 if self.has_spouse else 0,
				"children_count": flt(self.dependants_count),
				"active": 1,
			},
			"name",
		)
		if not match:
			match = frappe.db.get_value(
				"SDG Family Medical Band",
				{
					"medical_band": self.medical_band,
					"has_spouse": 1 if self.has_spouse else 0,
					"children_count": 0,
					"active": 1,
				},
				"name",
			)
		self.family_band = match

	@staticmethod
	def get_for(employee: str) -> SDGInsuredPerson | None:
		return frappe.get_doc("SDG Insured Person", employee) if employee else None

	@staticmethod
	def missing_file_documents(limit: int = 0) -> list[dict]:
		"""Employees whose file is incomplete, worst first.

		Used by the migration report and available as a stand-alone check.
		"""
		flags = [
			("file_hra", "HRA"),
			("file_loi", "LOI"),
			("file_id", "ID"),
			("file_six_pic", "6 PIC"),
			("file_military", "Military"),
			("file_medical_111", "Medical 111"),
			("file_social_s1", "Social S1"),
			("file_criminal_record", "Criminal Record"),
			("file_education_certificate", "Education Certificate"),
			("file_employment_permit", "Employment Permit"),
			("file_security_check", "Security Check"),
			("file_bank_details", "Bank Details"),
			("file_employment_of_relatives", "Employment of Relatives"),
			("file_beneficiary", "Beneficiary"),
			("file_code_of_business_conduct", "Code of Business Conduct"),
			("file_updated_cv", "Updated CV"),
			("file_job_description", "Job Description"),
			("file_reference_check", "Reference Check"),
		]
		rows = frappe.get_all(
			"SDG Insured Person",
			filters={"active": 1},
			fields=["employee", "employee_number"] + [f for f, _ in flags],
			limit_page_length=limit or 0,
		)
		results = []
		for row in rows:
			missing = [label for fieldname, label in flags if not flt(row.get(fieldname))]
			if missing:
				results.append(
					{
						"employee": row.employee,
						"employee_number": row.employee_number,
						"missing_count": len(missing),
						"missing": missing,
					}
				)
		results.sort(key=lambda r: -r["missing_count"])
		return results
