import frappe
from frappe.model.document import Document


class EmployeeSelfServiceBiometricCredential(Document):
	def validate(self):
		self.validate_unique_credential()

	def validate_unique_credential(self):
		"""An employee can only register each device once.

		Without this, a re-enrolment would add a second row rather than replacing
		the first, and the employee would accumulate credentials from tablets they
		no longer own.
		"""
		if not self.credential_id or not self.employee:
			return
		existing = frappe.db.exists(
			"Employee Self Service Biometric Credential",
			{
				"employee": self.employee,
				"credential_id": self.credential_id,
				"name": ("!=", self.name),
			},
		)
		if existing:
			frappe.throw(
				"This device is already registered to your account.",
				title="Device Already Registered",
			)