# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class SDGPayrollSettings(Document):
	"""Global rates and caps for the Egyptian payroll calculation."""

	def validate(self):
		self._validate_rates()

	def _validate_rates(self) -> None:
		for field in self._rate_fields():
			value = self.get(field)
			if value is not None and value < 0:
				self.throw_negative(field)

	@staticmethod
	def _rate_fields() -> list[str]:
		return []
