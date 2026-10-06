"""The in-flight WebAuthn challenge for one self-service sign-in attempt.

A pre-auth row is created after the employee's National ID and Employee Code have
been verified but *before* the tablet has touched the fingerprint sensor, which is
the window in which a challenge could be replayed.  `consume_preauth` therefore
deletes the row it reads and returns the data, so a challenge has exactly one
possible answer.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class EmployeeSelfServicePreauth(Document):
	pass


def consume_preauth(token):
	"""Return pre-auth data as a plain dict and delete the record.

	Returns ``None`` when the token is unknown or already expired, so the sign-in
	attempt must restart from step 1.
	"""
	if not token:
		return None

	names = frappe.get_all("Employee Self Service Preauth", filters={"token": token}, pluck="name", limit_page_length=1)
	if not names:
		return None

	preauth = frappe.get_doc("Employee Self Service Preauth", names[0])
	data = {
		"employee": preauth.employee,
		"mode": preauth.mode,
		"challenge": preauth.challenge,
		"allowed_credential_ids": preauth.allowed_credential_ids,
		"expiry": preauth.expiry,
	}
	frappe.delete_doc("Employee Self Service Preauth", preauth.name, ignore_permissions=True, force=True)

	if frappe.utils.get_datetime(data["expiry"]) < now_datetime():
		return None
	return data