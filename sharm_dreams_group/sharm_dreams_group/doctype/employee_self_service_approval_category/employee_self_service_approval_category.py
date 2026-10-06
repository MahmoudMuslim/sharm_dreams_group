from frappe.model.document import Document


class EmployeeSelfServiceApprovalCategory(Document):
	"""A request type: what it collects, and who has to sign it off.

	Deliberately an inert document.  The rules it carries are read by
	`self_service.approval` when a request is raised and by `Employee Self Service Approval Request` when
	its approvers are computed, so putting them in `validate` here would run them
	on the request as well as on the category.
	"""

	pass