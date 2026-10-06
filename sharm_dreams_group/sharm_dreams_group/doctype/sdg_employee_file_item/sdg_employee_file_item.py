# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SDGEmployeeFileItem(Document):
	"""One row of an employee's document checklist.

	A plain child table: the status is taken from the source workbook and the
	desk edits it by hand afterwards, so there is nothing to validate beyond
	what the Select options already enforce.
	"""

	pass
