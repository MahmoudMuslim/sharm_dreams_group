"""Self-service biometric (WebAuthn) sign-in.

A two-step flow, because the browser has to touch the sensor between the two:

1. `webauthn_login` validates National ID + Employee Code and returns a challenge.
   An employee with a registered device gets an *assertion* challenge; everyone
   else gets an *attestation* challenge for first-time registration.
2. `webauthn_verify` consumes the pre-auth token, verifies the assertion - or
   stores a newly attested credential - and only then mints the self-service page session.

So no session exists until the biometric has actually been checked. The challenge
lives in `Employee Self Service Preauth`, which is consumed on use, so it cannot be replayed.

Every rejection raises the same message: a tablet that can tell "this employee
does not exist" from "this credential is not registered" is an account oracle.
"""

import json
import secrets
from datetime import timedelta

import frappe
import frappe.utils
from frappe.utils import now_datetime

from sharm_dreams_group.portal import webauthn_ops
from sharm_dreams_group.portal.portal import REMEMBER_ME_TTL_DAYS
from sharm_dreams_group.portal.webauthn_ops import WebAuthnError
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_preauth.employee_self_service_preauth import (
	consume_preauth,
)
from sharm_dreams_group.sharm_dreams_group.doctype.employee_self_service_session.employee_self_service_session import (
	create_session,
)

# Five minutes to touch a fingerprint sensor. Long enough for a tablet that had to
# wake up, short enough that a challenge left behind is worthless.
PREAUTH_TTL_MINUTES = 5

GENERIC_FAILURE = "Biometric verification failed. Please try again."


def _normalise_origin(url):
	"""Strip the default port from a URL, the way a browser reports its origin.

	The browser puts the page origin into `clientDataJSON.origin` and omits the
	default port - a page served from `http://dev.localhost` reports exactly that,
	never `http://dev.localhost:80`. `verify_client_data` compares the two as
	plain strings, so a trailing `:80` would fail every enrolment.
	"""
	origin = (url or "").strip().rstrip("/")
	for scheme, port in (("http://", ":80"), ("https://", ":443")):
		if origin.startswith(scheme) and origin.endswith(port):
			return origin[: -len(port)]
	return origin


def _rp_config():
	"""Return ``(rp_id, origin)`` for WebAuthn calls.

	The relying-party ID is the bare hostname (no port), as the FIDO spec requires.
	It can be overridden in Employee Self Service Settings when the self-service page URL does not match the
	real hostname (reverse proxy, LAN IP, ...).

	The origin comes from the live request's `url_root` rather than from
	`frappe.utils.get_url()`, which rebuilds the URL from the site config's
	`host_name` and an explicit port - which is not whon the self-service page is actually
	served from behind a proxy.
	"""
	override = (
		frappe.db.get_single_value("Employee Self Service Settings", "webauthn_rp_id") or ""
	).strip()

	request = getattr(frappe.local, "request", None)
	host = ""
	origin = ""
	if request is not None:
		host = (request.host or "").split(":")[0]
		url_root = getattr(request, "url_root", "") or ""
		if url_root:
			origin = _normalise_origin(url_root)

	if not origin:
		origin = _normalise_origin(frappe.utils.get_url())

	return override or host, origin


def _create_preauth(employee, mode, challenge, allowed_ids):
	# Clear stale attempts from earlier sign-ins: an employee whose tablet was
	# replaced has one, and a leftover challenge is an attack surface.
	frappe.db.delete("Employee Self Service Preauth", {"employee": employee})

	doc = frappe.get_doc(
		{
			"doctype": "Employee Self Service Preauth",
			"employee": employee,
			"mode": mode,
			"challenge": challenge,
			"token": secrets.token_urlsafe(24),
			"allowed_credential_ids": json.dumps(allowed_ids),
			"expiry": now_datetime() + timedelta(minutes=PREAUTH_TTL_MINUTES),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.token


def _session_payload(emp, remember_me=False):
	return {
		"token": create_session(
			emp.name,
			ttl_hours=REMEMBER_ME_TTL_DAYS * 24 if remember_me else None,
		).access_token,
		"employee": emp.name,
		"employee_name": emp.employee_name,
	}


@frappe.whitelist(allow_guest=True)
def webauthn_login(national_id, employee_code):
	"""Step 1: validate identity and return a WebAuthn challenge.

	`employee_code` is the wire name; it carries the Employee Number.
	"""
	national_id = str(national_id or "").replace(" ", "").strip()
	employee_code = str(employee_code or "").strip()

	if not national_id.isdigit() or not employee_code:
		frappe.throw("Please enter a valid National ID and Employee Number.")

	emp = frappe.db.get_value(
		"Employee",
		{"sdg_national_id": national_id, "employee_number": employee_code},
		"name",
	)
	if not emp:
		frappe.throw("No employee matches this National ID and Employee Number.")

	emp = frappe.get_doc("Employee", emp)
	rp_id, _origin = _rp_config()
	challenge = secrets.token_urlsafe(32)

	credentials = frappe.get_all(
		"Employee Self Service Biometric Credential",
		filters={"employee": emp.name, "active": 1},
		pluck="credential_id",
	)

	if credentials:
		token = _create_preauth(emp.name, "assert", challenge, credentials)
		return {
			"mode": "assert",
			"preauth_token": token,
			"assertion_options": {
				"challenge": challenge,
				"rpId": rp_id,
				"timeout": 60000,
				"userVerification": "required",
				"allowCredentials": [
					{"type": "public-key", "id": cid, "transports": ["internal", "hybrid"]}
					for cid in credentials
				],
			},
		}

	user_id = f"emp-{emp.name}"
	token = _create_preauth(emp.name, "register", challenge, [])
	return {
		"mode": "register",
		"preauth_token": token,
		"attestation_options": {
			"rp": {"name": emp.company or "Employee Self Service", "id": rp_id},
			"user": {
				"id": webauthn_ops.b64url_encode(user_id.encode("utf-8")),
				"name": str(emp.sdg_national_id or employee_code),
				"displayName": emp.employee_name or emp.name,
			},
			"challenge": challenge,
			"pubKeyCredParams": [{"type": "public-key", "alg": webauthn_ops.ES256_ALG}],
			"timeout": 60000,
			"excludeCredentials": [],
			"authenticatorSelection": {
				"authenticatorAttachment": "platform",
				"residentKey": "preferred",
				"userVerification": "required",
			},
		},
	}


@frappe.whitelist(allow_guest=True)
def webauthn_verify(
	preauth_token,
	mode,
	client_data_json,
	authenticator_data=None,
	signature=None,
	credential_id=None,
	attestation_object=None,
	remember_me: bool = False,
):
	"""Step 2: verify the WebAuthn response and mint a self-service page session."""
	data = consume_preauth(preauth_token)
	if not data:
		frappe.throw(
			"Your sign-in attempt has expired or is invalid. Please try again.",
			exc=frappe.PermissionError,
		)

	emp_name = data["employee"]
	if not frappe.db.exists("Employee", emp_name):
		frappe.throw("Employee no longer exists.")

	# Case-insensitive: the client echoes whatever `webauthn_login` returned, and a
	# capitalisation slip here would fail every biometric sign-in with a bare
	# "Invalid verification mode." instead of anything actionable.
	mode = (mode or "").strip().lower()
	if mode != (data.get("mode") or "").strip().lower():
		frappe.throw("Invalid verification mode.")

	emp = frappe.get_doc("Employee", emp_name)
	rp_id, origin = _rp_config()

	try:
		if mode == "register":
			_register(emp, rp_id, origin, data["challenge"], client_data_json, attestation_object, credential_id)
		else:
			_assert(
				emp,
				rp_id,
				origin,
				data["challenge"],
				data["allowed_credential_ids"],
				client_data_json,
				authenticator_data,
				signature,
				credential_id,
			)
	except (WebAuthnError, ValueError):
		frappe.throw(GENERIC_FAILURE, exc=frappe.PermissionError)

	frappe.db.commit()
	return _session_payload(emp, remember_me)


def _register(emp, rp_id, origin, challenge, client_data_json, attestation_object, credential_id):
	"""Verify a `navigator.credentials.create` response and store the key."""
	client_data = webauthn_ops.b64url_decode(client_data_json)
	if not webauthn_ops.verify_client_data(client_data, challenge, "webauthn.create", origin):
		raise WebAuthnError("clientData mismatch")

	decoded = webauthn_ops.cbor_load(webauthn_ops.b64url_decode(attestation_object))
	auth_data = decoded.get("authData")
	if not auth_data:
		raise WebAuthnError("missing authenticatorData in attestation")

	rp_hash, flags, sign_count, aaguid, cred_id, spki = webauthn_ops.extract_credential_public_key(auth_data)

	# The rpIdHash is the browser's proof that it was talking to the site whose
	# key we are about to store. Checking it is what stops a credential enrolled
	# on another origin being replayed here.
	if rp_hash != webauthn_ops.sha256(rp_id.encode("utf-8")):
		raise WebAuthnError("rpIdHash mismatch")
	if not (flags & webauthn_ops.FLAG_USER_PRESENT):
		raise WebAuthnError("no user presence")
	if not spki:
		raise WebAuthnError("unsupported authenticator")

	if credential_id:
		expected_cid = webauthn_ops.b64url_decode(credential_id)
		if cred_id != expected_cid:
			raise WebAuthnError("credential id mismatch")

	frappe.get_doc(
		{
			"doctype": "Employee Self Service Biometric Credential",
			"employee": emp.name,
			"credential_id": webauthn_ops.b64url_encode(cred_id),
			"public_key": webauthn_ops.b64url_encode(spki),
			"counter": sign_count,
			"authenticator_aaguid": aaguid.hex() if aaguid else "",
			"label": emp.company or "Employee Self Service",
		}
	).insert(ignore_permissions=True)


def _assert(
	emp,
	rp_id,
	origin,
	challenge,
	allowed_ids_json,
	client_data_json,
	authenticator_data,
	signature,
	credential_id,
):
	"""Verify a `navigator.credentials.get` response."""
	# The allowed list comes from the pre-auth row, so a credential registered
	# between the two steps cannot be asserted with.
	allowed = json.loads(allowed_ids_json) if allowed_ids_json else []
	if credential_id not in allowed:
		raise WebAuthnError("credential not allowed")

	names = frappe.get_all(
		"Employee Self Service Biometric Credential",
		filters={"employee": emp.name, "credential_id": credential_id},
		pluck="name",
		limit_page_length=1,
	)
	if not names:
		raise WebAuthnError("credential not registered")

	credential = frappe.get_doc("Employee Self Service Biometric Credential", names[0])
	if not credential.active:
		raise WebAuthnError("credential not registered")

	client_data = webauthn_ops.b64url_decode(client_data_json)
	if not webauthn_ops.verify_client_data(client_data, challenge, "webauthn.get", origin):
		raise WebAuthnError("clientData mismatch")

	auth_data = webauthn_ops.b64url_decode(authenticator_data)
	rp_hash, flags, sign_count, _rest = webauthn_ops.parse_authenticator_data(auth_data)

	if rp_hash != webauthn_ops.sha256(rp_id.encode("utf-8")):
		raise WebAuthnError("rpIdHash mismatch")
	if not (flags & webauthn_ops.FLAG_USER_PRESENT):
		raise WebAuthnError("no user presence")
	# An authenticator that always reports 0 has no counter to compare; anything
	# non-zero must strictly increase, which is what makes a copied assertion
	# detectable.
	if sign_count and sign_count <= credential.counter:
		raise WebAuthnError("stale signature counter")

	spki = webauthn_ops.b64url_decode(credential.public_key)
	ok = webauthn_ops.verify_signature(
		spki,
		webauthn_ops.b64url_decode(signature),
		auth_data,
		client_data,
	)
	if not ok:
		raise WebAuthnError("signature verification failed")

	credential.db_set(
		{
			"counter": sign_count,
			"last_used": now_datetime(),
		},
		update_modified=False,
	)


@frappe.whitelist()
def webauthn_status():
	"""Whether the signed-in *desk* user has a self-service page biometric registered.

	Only meaningful for an employee who also has a Frappe account; the self-service page itself
	never calls it, it uses the token from `webauthn_verify`.
	"""
	emp = frappe.session.user
	if not emp or emp == "Guest":
		return {"registered": False}
	count = frappe.db.count(
		"Employee Self Service Biometric Credential", {"employee": emp, "active": 1}
	)
	return {"registered": bool(count), "count": count}