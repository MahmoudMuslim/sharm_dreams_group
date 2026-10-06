# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

from __future__ import annotations

from typing import ClassVar

import frappe
from frappe.model.document import Document


class SDGBaseVocabulary(Document):
	"""Shared behaviour for the SDG vocabulary tables.

	`SDG Employment Category` and `SDG Contract Type` are both small link-target
	tables: one row per real value, a name taken from the value itself, and an
	`active` flag rather than deletion.  Keeping the values as rows means the
	vocabulary is enforced by a Link field rather than drifting as free text.

	Retiring a row must not be blocked when employees still reference it, so this
	only ever warns.
	"""

	def validate(self):
		self.normalise_name()
		self.warn_if_in_use()

	def normalise_name(self):
		"""Collapse double spaces and trim, so ' Rowad  Misr ' and 'Rowad Misr'
		are not two different rows."""
		for fieldname in ("category_name", "contract_type_name"):
			if self.get(fieldname):
				cleaned = " ".join(str(self.get(fieldname)).split())
				if cleaned != self.get(fieldname):
					self.set(fieldname, cleaned)

	# which Employee field holds a link to this vocabulary
	_LINK_FIELD: ClassVar[dict[str, str]] = {
		"SDG Employment Category": "sdg_employment_category",
		"SDG Contract Type": "sdg_contract_type",
		"SDG Sub Department": "sdg_sub_department",
	}

	def warn_if_in_use(self):
		"""Retiring a value that people still hold should be noticed, not silent."""
		if self.active or self.is_new() or not self.has_value_changed("active"):
			return
		field = self._LINK_FIELD.get(self.doctype)
		if not field:
			return
		users = frappe.get_all(
			"Employee",
			filters={field: self.name, "status": ("!=", "Left")},
			pluck="name",
			limit=5,
		)
		if users:
			frappe.msgprint(
				frappe._("{0} is still used by {1} active employee(s).").format(self.name, len(users)),
				title=frappe._("Still in use"),
				indicator="orange",
			)
