# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Biometric sign-in for the portal, verified by the server.

The fingerprint or face is checked here rather than merely tapped: the browser
answers a challenge this server issued, this server verifies the signature with the
key it stored, and only then is a session minted. That is what made the retired
`/self-services-employees` page worth keeping, and the portal had a weaker version of
it - a credential the browser kept to itself and the server never saw - so the reader
was a latch on the tablet rather than a door.

Three ceremonies share one pair of primitives:

* **enrol** - signed in, with the session token. The key lands in
  `Employee Self Service Biometric Credential`, where HR can see it and revoke it.
* **unlock** - the tablet already knows which credential is its own, so no National ID
  is typed; the credential id picks the employee, and the assertion proves the touch.
* **numbers + touch** - the retired page's way in, kept because it is the one that
  works on a tablet nobody has enrolled: the numbers find the employee, and the reader
  answers for them.

What is deliberately *not* ported is the retired page's lock-out: it refused the two
numbers once a credential existed, so a wet finger or a browser that changed its mind
left a person unable to reach their own record. Here the numbers always work, and the
biometric is what stands between a shared tablet and the next pair of hands.

The verification itself lives in `self_service.webauthn_ops` (CBOR, COSE keys,
signature checks) and `self_service.webauthn` (`_rp_config`, `_create_preauth`,
`_register`, `_assert`). Only the *semantics* differ from the retired page - who is
asked, what session comes out the other side - so those functions are reused rather
than copied: a second implementation of a signature check is a second way to get it
wrong, and the two would drift apart silently.
"""

from __future__ import annotations

import json
import secrets

import frappe
from frappe import _

from sharm_dreams_group.portal.portal import (
	REMEMBER_ME_TTL_DAYS,
	create_session,
	resolve_session,
	session_payload,
)
from sharm_dreams_group.self_service.webauthn import (
	GENERIC_FAILURE,
	_assert,
	_create_preauth,
	_register,
	_rp_config,
)
from sharm_dreams_group.self_service.webauthn_ops import (
	ES256_ALG,
	WebAuthnError,
	b64url_decode,
	b64url_encode,
)
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_preauth.employee_self_service_preauth import (
	consume_preauth,
)

CREDENTIAL = "Employee Self Service Biometric Credential"

TIMEOUT_MS = 60000


def _active_credential_ids(employee: str) -> list[str]:
	return frappe.get_all(
		CREDENTIAL,
		filters={"employee": employee, "active": 1},
		pluck="credential_id",
		limit_page_length=20,
	)


def _reject(reason: str) -> None:
	"""Refuse without telling the caller which half of the attempt was wrong.

	The tablet reads `GENERIC_FAILURE`, which is the right sentence for whoever is
	standing at it and a useless one for whoever has to fix the ceremony, so the reason
	goes to `sites/<site>/logs/employee_portal.log`. A file rather than Error Log: the
	refusal rolls the request back, and a diagnostic that rolls back with the attempt it
	describes is not a diagnostic.

	What lands in the file is the failed check and the two origins. Never the
	challenge, the preauth token, a signature or a public key - any of those would let
	somebody reading the log finish a ceremony they never touched the reader for.
	"""
	rp_id, origin = _rp_config()
	# `error` and not `warning`: outside the dev server Frappe's logger defaults to
	# ERROR level, so a warning is written nowhere and the diagnostic is silent on the
	# one site it was written for.
	frappe.logger("employee_portal").error(
		"biometric ceremony refused: %s | rp_id=%s expected_origin=%s", reason, rp_id, origin
	)
	frappe.throw(GENERIC_FAILURE, exc=frappe.PermissionError)


def _posted_origin(client_data_json: str) -> str:
	"""The origin the browser says it was on, read from its own public clientDataJSON.

	`verify_client_data` compares that string with the one `_rp_config()` derived from
	the request, so when a proxy rewrites the scheme every enrolment fails with the same
	sentence and nothing says which of the two disagree. Reading the claim back is the
	whole difference between a config fix and a guess.
	"""
	if not client_data_json:
		return "absent"
	try:
		claimed = json.loads(b64url_decode(client_data_json).decode("utf-8")).get("origin")
	except Exception:
		# Malformed input is the thing being diagnosed, so it must not raise here.
		return "unreadable"
	return str(claimed or "absent")


def _blame(error: Exception, client_data_json: str) -> str:
	return (
		f"{type(error).__name__}: {str(error)[:200]}"
		f" | posted_origin={_posted_origin(client_data_json)}"
	)


def _attestation_options(emp, challenge: str, rp_id: str) -> dict:
	"""The `navigator.credentials.create` arguments for this employee.

	`excludeCredentials` is filled rather than left empty: the retired page sent an
	empty list, so a second tap on the same reader enrolled a second key and the
	employee never learned which of them the portal was checking.
	"""
	return {
		"rp": {"name": emp.company or _("Employee Portal"), "id": rp_id},
		"user": {
			# A handle, not an identity: nothing about the person is recoverable from
			# what the authenticator ends up bound to.
			"id": b64url_encode(f"emp-{emp.name}".encode("utf-8")),
			"name": str(emp.employee_number or emp.name),
			"displayName": emp.employee_name or emp.name,
		},
		"challenge": challenge,
		# ES256 alone, and Chrome warns about it - "pubKeyCredParams is missing at least
		# one of the default algorithm identifiers: ES256 and RS256". The warning is
		# advisory and listing RS256 would be the bug: `webauthn_ops` verifies with an
		# ECDSA/P-256 key only, and `cose_to_spki` refuses an RSA COSE key outright. An
		# authenticator that picked RS256 would then enrol a key this server can read but
		# never check, so a touch would fail for good rather than once. ES256 is the
		# algorithm every FIDO2 platform authenticator implements, so nothing here is
		# asked to do something it cannot.
		"pubKeyCredParams": [{"type": "public-key", "alg": ES256_ALG}],
		"timeout": TIMEOUT_MS,
		"excludeCredentials": [
			{"type": "public-key", "id": credential_id}
			for credential_id in _active_credential_ids(emp.name)
		],
		"authenticatorSelection": {
			"authenticatorAttachment": "platform",
			"residentKey": "preferred",
			"userVerification": "required",
		},
	}


def _assertion_options(challenge: str, rp_id: str, credential_ids: list[str]) -> dict:
	return {
		"challenge": challenge,
		"rpId": rp_id,
		"timeout": TIMEOUT_MS,
		"userVerification": "required",
		"allowCredentials": [
			{
				"type": "public-key",
				"id": credential_id,
				"transports": ["internal", "hybrid"],
			}
			for credential_id in credential_ids
		],
	}


def _employee_of(credential_id: str):
	"""The active, working employee behind this tablet's credential, or None."""
	if not credential_id:
		return None
	employee = frappe.db.get_value(
		CREDENTIAL, {"credential_id": credential_id, "active": 1}, "employee"
	)
	if not employee:
		return None
	if frappe.db.get_value("Employee", employee, "status") != "Active":
		return None
	return employee


def _consume(preauth_token: str, mode: str, employee: str | None = None) -> dict:
	"""The challenge behind this attempt, if it is still theirs to answer."""
	data = consume_preauth(preauth_token)
	if not data:
		_reject(f"no live preauth for this attempt (asked mode={mode})")
	if (data.get("mode") or "").strip().lower() != (mode or "").strip().lower():
		_reject(f"preauth mode is {data.get('mode')}, this route wants {mode}")
	if employee is not None and data["employee"] != employee:
		# The signed-in employee enrolling a key for somebody else is a session
		# theft, not a typo, and the answer is still the same sentence.
		_reject("preauth was raised for a different employee than this session")
	return data


# -------------------------------------------------------------------------- enrol
# Reached with a session token: the employee is already in, and is handing this
# tablet a key for next time.


@frappe.whitelist()
def biometric_enroll_options(token: str) -> dict:
	emp = resolve_session(token)
	rp_id, _origin = _rp_config()
	challenge = secrets.token_urlsafe(32)
	return {
		"mode": "register",
		"preauth_token": _create_preauth(emp.name, "register", challenge, []),
		"public_key": _attestation_options(emp, challenge, rp_id),
	}


@frappe.whitelist()
def biometric_enroll_verify(
	token: str,
	preauth_token: str,
	mode: str,
	client_data_json: str,
	attestation_object: str,
	credential_id: str = "",
) -> dict:
	emp = resolve_session(token)
	data = _consume(preauth_token, mode, emp.name)
	rp_id, origin = _rp_config()

	try:
		stored = _register(
			emp,
			rp_id,
			origin,
			data["challenge"],
			client_data_json,
			attestation_object,
			credential_id or None,
		)
	except (WebAuthnError, ValueError) as error:
		_reject("enrol: " + _blame(error, client_data_json))

	frappe.db.commit()
	return {"credential_id": stored, "enrolled": True}


# ------------------------------------------------------------------------- unlock
# Reached with no token and no numbers: the credential id this tablet stored picks the
# employee, and the assertion is what proves the touch rather than the storage.


@frappe.whitelist(allow_guest=True)
def biometric_unlock_options(credential_id: str = "") -> dict:
	employee = _employee_of(credential_id)
	if not employee:
		# The id itself is public - it is what the tablet keeps - and whether one
		# arrived at all is the difference between a wire bug and a revoked key.
		_reject(f"no active credential for unlock (posted id: {credential_id or 'empty'})")

	rp_id, _origin = _rp_config()
	challenge = secrets.token_urlsafe(32)
	return {
		"mode": "assert",
		"preauth_token": _create_preauth(employee, "assert", challenge, [credential_id]),
		"public_key": _assertion_options(challenge, rp_id, [credential_id]),
	}


@frappe.whitelist(allow_guest=True)
def biometric_unlock_verify(
	preauth_token: str,
	credential_id: str = "",
	client_data_json: str = "",
	authenticator_data: str = "",
	signature: str = "",
	remember_me: bool = False,
) -> dict:
	"""Mint a session from a verified touch, with no numbers and no token.

	Note the absence of a `mode` argument: on this route the ceremony is always an
	assertion, so the server decides it rather than accepting whatever the caller
	echoed back - a `register` challenge handed here could otherwise be answered by
	*storing* a key instead of proving one.
	"""
	data = _consume(preauth_token, "assert")
	emp = _verified_employee(
		data, "assert", credential_id, client_data_json, authenticator_data, signature
	)

	# The session is minted here rather than lifted from device storage: what a tablet
	# keeps after enrolment is a credential id, which is public, not a token somebody
	# could take off the disk and use.
	return session_payload(emp, _fresh_session(emp, remember_me))


# ----------------------------------------------------------- numbers and a touch
# The retired page's way in, for a tablet that has never been enrolled: the numbers
# find the employee, and the first touch is what enrols this device.


@frappe.whitelist(allow_guest=True)
def biometric_login_options(national_id: str = "", employee_code: str = "") -> dict:
	national_id = str(national_id or "").replace(" ", "").strip()
	employee_code = str(employee_code or "").strip()
	if not national_id or not employee_code:
		frappe.throw(_("National ID and Employee Number are required."))

	employee = frappe.db.get_value(
		"Employee",
		{"sdg_national_id": national_id, "employee_number": employee_code, "status": "Active"},
		"name",
	)
	if not employee:
		# The same sentence as the ordinary sign-in, which is what stops this route
		# being an employee-number oracle.
		frappe.throw(_("No employee matches the National ID and Employee Number."))

	emp = frappe.get_doc("Employee", employee)
	rp_id, _origin = _rp_config()
	challenge = secrets.token_urlsafe(32)
	credential_ids = _active_credential_ids(emp.name)

	if credential_ids:
		return {
			"mode": "assert",
			"preauth_token": _create_preauth(emp.name, "assert", challenge, credential_ids),
			"public_key": _assertion_options(challenge, rp_id, credential_ids),
		}

	return {
		"mode": "register",
		"preauth_token": _create_preauth(emp.name, "register", challenge, []),
		"public_key": _attestation_options(emp, challenge, rp_id),
	}


@frappe.whitelist(allow_guest=True)
def biometric_login_verify(
	preauth_token: str,
	mode: str,
	client_data_json: str = "",
	credential_id: str = "",
	attestation_object: str = "",
	authenticator_data: str = "",
	signature: str = "",
	remember_me: bool = False,
) -> dict:
	mode = (mode or "").strip().lower()
	data = _consume(preauth_token, mode)

	if mode == "register":
		emp = _verified_employee(
			data, "register", credential_id, client_data_json, attestation_object=attestation_object
		)
		stored = frappe.db.get_value(
			CREDENTIAL, {"employee": emp.name, "active": 1}, "credential_id", order_by="creation desc"
		)
		return {
			**session_payload(emp, _fresh_session(emp, remember_me)),
			"credential_id": stored or credential_id,
		}

	emp = _verified_employee(
		data, "assert", credential_id, client_data_json, authenticator_data, signature
	)
	return session_payload(emp, _fresh_session(emp, remember_me))


# ---------------------------------------------------------------------- housekeeping


@frappe.whitelist()
def biometric_status(token: str) -> dict:
	"""Whether this employee has a key on file, and how many.

	Read by the portal on the screen where the choice is made, so "this tablet asks for
	your fingerprint" describes the server's record rather than whatever the browser
	happens to still be holding after a reset.
	"""
	emp = resolve_session(token)
	credentials = _active_credential_ids(emp.name)
	return {"enrolled": bool(credentials), "credentials": credentials}


@frappe.whitelist()
def biometric_remove(token: str, credential_id: str = "") -> dict:
	"""Turn the reader off. The row is deactivated, not deleted.

	A deleted row leaves no trace of a key that was in use, and the counter on it is the
	thing that makes a replayed assertion detectable; switching `active` off keeps the
	history and stops the door.
	"""
	emp = resolve_session(token)
	filters = {"employee": emp.name, "active": 1}
	if credential_id:
		filters["credential_id"] = credential_id

	names = frappe.get_all(CREDENTIAL, filters=filters, pluck="name", limit_page_length=0)
	for name in names:
		frappe.db.set_value(CREDENTIAL, name, {"active": 0}, update_modified=False)
	frappe.db.commit()

	return {
		"ok": True,
		"removed": len(names),
		"enrolled": bool(_active_credential_ids(emp.name)),
	}


def _fresh_session(emp, remember_me: bool) -> str:
	"""Mint a new session row rather than reissue a remembered token.

	The alternative - storing a 30-day token on the device and handing it back once the
	reader agrees - has a token outliving the enrolment that decided it should exist: a
	credential can be revoked from the desk, and a stolen token cannot. Here the only
	thing on the device is a public credential id, and every entry costs a fresh touch.
	"""
	session = create_session(
		emp.name,
		ttl_hours=REMEMBER_ME_TTL_DAYS * 24 if remember_me else None,
	)
	frappe.db.commit()
	return session.access_token


def _verified_employee(data, mode: str, credential_id, client_data_json, authenticator_data="", signature="", attestation_object=""):
	"""Check the browser's answer against the challenge, and return the Employee.

	One function because the two ceremonies differ only in which primitive verifies the
	response, and both end with "this person touched this reader".
	"""
	employee = data["employee"]
	if not frappe.db.exists("Employee", employee):
		_reject("the preauth points at an employee that no longer exists")
	if frappe.db.get_value("Employee", employee, "status") != "Active":
		_reject("the preauth points at an employee who has left")

	emp = frappe.get_doc("Employee", employee)
	rp_id, origin = _rp_config()

	try:
		if mode == "register":
			_register(
				emp,
				rp_id,
				origin,
				data["challenge"],
				client_data_json,
				attestation_object,
				credential_id or None,
			)
		else:
			_assert(
				emp,
				rp_id,
				origin,
				data["challenge"],
				data.get("allowed_credential_ids") or json.dumps([]),
				client_data_json,
				authenticator_data,
				signature,
				credential_id,
			)
	except (WebAuthnError, ValueError) as error:
		_reject(f"{mode} ceremony: " + _blame(error, client_data_json))

	return emp
