# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SDGPenaltyLevel(Document):
	"""One band of the penalty scale.

	A plain configuration record.  The banding is read from the highest
	`from_points` down, so bands may overlap in the data without the level an
	employee is given becoming ambiguous - the only overlap check needed is
	that one band cannot start where another one ends.
	"""

	def validate(self):
		if self.from_points is not None and self.from_points < 0:
			frappe.throw(frappe._("From Points cannot be negative."))
		if self.to_points and self.from_points is not None:
			if self.to_points < self.from_points:
				frappe.throw(
					frappe._("To Points must be at least From Points ({0}).").format(self.from_points)
				)
