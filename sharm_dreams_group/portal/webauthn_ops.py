"""WebAuthn (FIDO2) building blocks for the employee self-service.

Pure helpers with no Frappe dependencies: base64url encoding, a small CBOR decoder
for the attestation objects authenticators return, extraction of the ECDSA/P-256
public key, and verification of an authentication assertion.  The `cryptography`
library (bundled with Frappe) provides the elliptic-curve math.

Kept free of Frappe imports on purpose - it is the one module on the self-service page that
can be reasoned about, and tested, without a site.
"""

import base64
import hashlib
import json
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# Conventions, fixed by the WebAuthn spec.
FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40
ES256_ALG = -7
P256_CURVE_ID = 1
EC2_KEY_TYPE = 2

# SPKI DER prefix for an uncompressed P-256 public key (rfc 5480).
_P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


class WebAuthnError(Exception):
	"""Every rejection reason, deliberately indistinguishable to the caller.

	The caller (`portal.webauthn`) turns all of these into one generic message: the
	tablet learns that the attempt failed, not which of the six checks failed it.
	"""


def b64url_encode(data):
	"""bytes -> base64url string (no padding)."""
	if isinstance(data, str):
		data = data.encode("utf-8")
	return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text):
	"""base64url string -> bytes (tolerates missing padding).

	`validate=True` is the point of this function rather than a detail.  Python's
	`urlsafe_b64decode` does not expose it and *discards* any character outside the
	alphabet, so `"!!!!"` quietly decoded to `b""` - a malformed credential id
	becoming empty bytes and the failure surfacing three calls later as "credential
	not registered", which is both the wrong message and a wrong place to look.
	Decoding through `b64decode(..., altchars=b"-_", validate=True)` rejects it here.
	"""
	if isinstance(text, bytes):
		try:
			text = text.decode("ascii")
		except UnicodeDecodeError as error:
			raise WebAuthnError("invalid base64url input") from error
	if not isinstance(text, str) or not text:
		raise WebAuthnError("invalid base64url input")
	try:
		return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
	except (ValueError, TypeError) as error:
		raise WebAuthnError("invalid base64url input") from error


def sha256(data):
	return hashlib.sha256(data).digest()


def _cbor_decode(data, offset=0):
	"""Decode one CBOR item, returning ``(value, next_offset)``."""
	if offset >= len(data):
		raise WebAuthnError("invalid CBOR payload")
	initial = data[offset]
	offset += 1
	major = initial >> 5
	additional = initial & 0x1F

	if additional < 24:
		value = additional
	elif additional == 24:
		value = data[offset]
		offset += 1
	elif additional == 25:
		value = struct.unpack(">H", data[offset : offset + 2])[0]
		offset += 2
	elif additional == 26:
		value = struct.unpack(">I", data[offset : offset + 4])[0]
		offset += 4
	elif additional == 27:
		value = struct.unpack(">Q", data[offset : offset + 8])[0]
		offset += 8
	elif additional == 31:
		value = None
	else:
		raise WebAuthnError("unsupported CBOR additional info %d" % additional)

	if major == 0:
		return value, offset
	if major == 1:
		return -1 - value, offset
	if major == 2:
		if value is None:
			chunks = []
			while data[offset] != 0xFF:
				chunk, offset = _cbor_decode(data, offset)
				chunks.append(chunk)
			return b"".join(chunks), offset + 1
		return bytes(data[offset : offset + value]), offset + value
	if major == 3:
		if value is None:
			chunks = []
			while data[offset] != 0xFF:
				chunk, offset = _cbor_decode(data, offset)
				chunks.append(chunk)
			return "".join(chunks), offset + 1
		return data[offset : offset + value].decode("utf-8"), offset + value
	if major == 4:
		if value is None:
			result = []
			while data[offset] != 0xFF:
				item, offset = _cbor_decode(data, offset)
				result.append(item)
			return result, offset + 1
		result = []
		for _ in range(value):
			item, offset = _cbor_decode(data, offset)
			result.append(item)
		return result, offset
	if major == 5:
		if value is None:
			result = {}
			while data[offset] != 0xFF:
				key, offset = _cbor_decode(data, offset)
				item, offset = _cbor_decode(data, offset)
				result[key] = item
			return result, offset + 1
		result = {}
		for _ in range(value):
			key, offset = _cbor_decode(data, offset)
			item, offset = _cbor_decode(data, offset)
			result[key] = item
		return result, offset
	if major == 6:
		return _cbor_decode(data, offset)
	if major == 7:
		if additional == 20:
			return False, offset
		if additional == 21:
			return True, offset
		if additional == 22:
			return None, offset
		if additional == 25:
			return struct.unpack(">e", data[offset : offset + 2])[0], offset + 2
		if additional == 26:
			return struct.unpack(">f", data[offset : offset + 4])[0], offset + 4
		if additional == 27:
			return struct.unpack(">d", data[offset : offset + 8])[0], offset + 8
		raise WebAuthnError("unsupported CBOR simple value %d" % additional)
	raise WebAuthnError("unsupported CBOR major type %d" % major)


def cbor_load(data):
	"""Decode a complete CBOR payload (no trailing bytes allowed)."""
	value, offset = _cbor_decode(data, 0)
	if offset != len(data):
		raise WebAuthnError("trailing bytes in CBOR payload")
	return value


def parse_authenticator_data(auth_data):
	"""Split an authenticatorData blob: rpIdHash | flags | signCount | rest."""
	if len(auth_data) < 37:
		raise WebAuthnError("authenticatorData too short")
	rp_id_hash = auth_data[0:32]
	flags = auth_data[32]
	sign_count = struct.unpack(">I", auth_data[33:37])[0]
	return rp_id_hash, flags, sign_count, auth_data[37:]


def extract_credential_public_key(auth_data):
	"""Return ``(rp_id_hash, flags, sign_count, aaguid, credential_id, spki)``.

	`spki` is the credential's ECDSA P-256 public key as DER SubjectPublicKeyInfo.
	"""
	rp_id_hash, flags, sign_count, rest = parse_authenticator_data(auth_data)
	if not (flags & FLAG_ATTESTED_CREDENTIAL_DATA):
		raise WebAuthnError("missing attested credential data")
	if len(rest) < 18:
		raise WebAuthnError("authenticatorData too short for credentials")
	aaguid = rest[0:16]
	cred_id_len = struct.unpack(">H", rest[16:18])[0]
	cred_id = rest[18 : 18 + cred_id_len]
	cose_key = cbor_load(rest[18 + cred_id_len :])
	return rp_id_hash, flags, sign_count, aaguid, cred_id, cose_to_spki(cose_key)


def cose_to_spki(cose_key):
	"""Convert a COSE EC2 (P-256) key map into a DER SPKI public key."""
	if cose_key.get(1) != EC2_KEY_TYPE:
		raise WebAuthnError("unsupported COSE key type")
	if cose_key.get(-1) != P256_CURVE_ID:
		raise WebAuthnError("unsupported COSE curve")
	x = cose_key.get(-2)
	y = cose_key.get(-3)
	if not isinstance(x, bytes) or not isinstance(y, bytes):
		raise WebAuthnError("invalid COSE curve point")
	if len(x) != 32 or len(y) != 32:
		raise WebAuthnError("unsupported curve point size")
	return _P256_SPKI_PREFIX + bytes([0x04]) + x + y


def verify_client_data(client_data_json, expected_challenge, expected_type, expected_origin):
	"""Validate the clientDataJSON produced by the browser."""
	try:
		client_data = json.loads(client_data_json.decode("utf-8"))
	except (UnicodeDecodeError, ValueError):
		return False
	return (
		client_data.get("type") == expected_type
		and client_data.get("challenge") == expected_challenge
		and client_data.get("origin") == expected_origin
	)


def _to_der_signature(raw_signature):
	"""Convert a raw 64-byte (r||s) ECDSA signature into a DER SEQUENCE."""
	if len(raw_signature) != 64:
		raise WebAuthnError("invalid signature length")

	def as_der_int(value):
		# Strip leading zeros, then pad: DER integers are signed, so a high bit
		# needs a 0x00 in front of it or the value reads as negative.
		while value and value[0] == 0:
			value = value[1:]
		if not value:
			return b"\x00"
		if value[0] & 0x80:
			value = b"\x00" + value
		return value

	r = as_der_int(raw_signature[:32])
	s = as_der_int(raw_signature[32:])
	body = b"\x02" + bytes([len(r)]) + r + b"\x02" + bytes([len(s)]) + s
	return b"\x30" + bytes([len(body)]) + body


def verify_signature(spki_public_key, signature, auth_data, client_data_json):
	"""Verify an ECDSA/P-256 assertion over ``authData || SHA256(clientData)``.

	Accepts both raw (r||s) and DER-encoded signatures, because authenticators
	differ on which they emit.  Returns ``bool`` rather than raising, so the caller
	has one failure path.
	"""
	signed_bytes = auth_data + sha256(client_data_json)
	try:
		public_key = serialization.load_der_public_key(spki_public_key)
		raw = bytes(signature)
		if len(raw) == 64:
			public_key.verify(_to_der_signature(raw), signed_bytes, ec.ECDSA(hashes.SHA256()))
			return True
		public_key.verify(raw, signed_bytes, ec.ECDSA(hashes.SHA256()))
		return True
	except (ValueError, TypeError, InvalidSignature):
		return False