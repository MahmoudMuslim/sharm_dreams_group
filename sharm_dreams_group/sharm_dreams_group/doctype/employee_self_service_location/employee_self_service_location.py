from frappe.model.document import Document


class EmployeeSelfServiceLocation(Document):
	"""Superseded punch zone. Nothing reads this.

	The punch fence is now the `Shift Location` on the employee's `Shift Assignment`,
	which carries the centre and the radius in metres - see `self_service.geo`. This
	DocType and its rows are kept only so a site that already configured zones here is
	not left with data it cannot open; new zones go on the Shifts card instead.

	The validators are deliberately not called. A rule enforced on a doctype nothing
	reads constrains nothing, and the old one - one zone per company - was actively
	wrong for a group running many sites inside a handful of companies, so leaving it in
	place would refuse the very rows that make the old model unnecessary to abandon.
	"""

	def validate(self):
		pass