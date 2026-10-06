import frappe
from frappe.model.document import Document
from frappe.utils import flt


class EmployeeSelfServiceLocation(Document):
	def validate(self):
		self.validate_radius()
		self.validate_company_unique()

	def validate_radius(self):
		if flt(self.radius_km) <= 0:
			frappe.throw("The acceptable radius must be positive.")

	def validate_company_unique(self):
		"""One punch zone per company.

		The enforcement in `self_service.geo` reports the zone by name when a single one
		is configured, so a second zone would make that message ambiguous.  The
		company is the unit because it is what the Employee carries - HRMS has no
		work-location doctype.
		"""
		existing = frappe.db.exists(
			"Employee Self Service Location",
			{"company": self.company, "name": ("!=", self.name)},
		)
		if existing:
			frappe.throw(
				"Only one punch location can be configured per company. "
				f"Already configured as '{existing}'.",
				title="Duplicate Punch Location",
			)