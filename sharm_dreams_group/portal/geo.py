"""Geofenced attendance punch.

The allowed zone is the **work location** on the employee's current `Shift Assignment`:
`Shift Assignment.shift_location` links to an HRMS `Shift Location`, which carries a
centre (latitude/longitude) and a `checkin_radius` in metres.  When an employee punches,
the browser sends its coordinates and this module verifies the distance before the
attendance is written.

The zone is anchored to the shift assignment rather than to the company because this
group runs many sites - Head Office, Sharm El Sheikh, Katamya, Marsa Alam, Hurghada,
Dokki and more - spread across a handful of companies.  A per-company circle would have
to span the country to cover them, and would let an employee in Hurghada punch against
a Head Office fence.  HRMS's own `Employee Checkin.validate_distance_from_shift_location`
models this same work location; this module reads the same `Shift Location` rows, and
checks them itself so the portal can store the verdict on the checkin and answer with a
sentence rather than a generic validation error.

Enforcement is toggled by `geo_punch_enforce` in Employee Self Service Settings (default
on).  Latitude and longitude are always recorded on the checkin for audit, whatever the
outcome - so a punch that was refused can still be audited, and one that was allowed with
enforcement off is still locatable.
"""

import math

import frappe
from frappe.utils import flt, today

EARTH_RADIUS_KM = 6371.0

STATUS_OK = "ok"
STATUS_OUTSIDE = "outside"
STATUS_NO_FENCE = "nofence"
STATUS_UNAVAILABLE = "unavailable"

# A Select field stores the option text, so the codes used on the wire are mapped
# to the labels persisted on the checkin. `Employee Checkin.sdg_self_service_geo_status`
# carries exactly these four options - changing one means changing both, which is
# what `setup.SELF_SERVICE_GEO_STATUS_OPTIONS` exists to catch.
STATUS_LABELS = {
	STATUS_OK: "Verified",
	STATUS_OUTSIDE: "Outside allowed zone",
	STATUS_NO_FENCE: "No zone configured",
	STATUS_UNAVAILABLE: "Location unavailable",
}


def status_label(status):
	"""Map a status code to the label persisted on Employee Checkin."""
	return STATUS_LABELS.get(status, "")


NO_POINT_MESSAGE = (
	"Location sharing is required to punch. "
	"Please allow your browser location access and try again."
)
NO_FENCE_MESSAGE = (
	"No work location is configured for you. Please contact HR."
)


def haversine_km(lat1, lon1, lat2, lon2):
	"""Distance in km between two WGS-84 points."""
	if None in (lat1, lon1, lat2, lon2):
		return float("inf")

	dlat = math.radians(lat2 - lat1)
	dlon = math.radians(lon2 - lon1)
	a = (
		math.sin(dlat / 2) ** 2
		+ math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
	)
	return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def is_enforced():
	"""Whether geofencing is enforced site-wide (default on).

	A missing or unreadable setting fails *closed* to enforced. A self-service page that
	silently stopped enforcing because of a transient database error would be a
	far worse outcome than a punch that has to be retried.
	"""
	try:
		value = frappe.db.get_single_value("Employee Self Service Settings", "geo_punch_enforce")
	except Exception:
		value = None
	if value is None:
		return True
	return bool(value)


def get_zones(employee, on_date=None):
	"""Active work-location zones the employee may punch within on `on_date`.

	Reads the same rows HRMS's own `validate_distance_from_shift_location` reads:
	submitted, active `Shift Assignment`s that carry a `shift_location` and cover the
	date.  A `Shift Location` whose `checkin_radius` is not positive is a row HR has
	created but not yet fenced - it is skipped rather than treated as a zero-metre
	point that would refuse everybody standing there, which is what HRMS does too.
	"""
	on_date = on_date or today()
	locations = frappe.get_all(
		"Shift Assignment",
		filters={
			"employee": employee,
			"shift_location": ["is", "set"],
			"docstatus": 1,
			"status": "Active",
			"start_date": ["<=", on_date],
		},
		or_filters=[["end_date", ">=", on_date], ["end_date", "is", "not set"]],
		pluck="shift_location",
		limit_page_length=0,
	)
	locations = [name for name in locations if name]
	if not locations:
		return []

	return [
		zone
		for zone in frappe.get_all(
			"Shift Location",
			filters={"name": ("in", locations)},
			fields=["name", "location_name", "latitude", "longitude", "checkin_radius"],
			limit_page_length=0,
		)
		if flt(zone.checkin_radius) > 0
	]


def check_punch(employee, latitude, longitude):
	"""Validate coordinates against the employee's work-location zone.

	Returns ``(ok, status_code, message)``, where `ok` is True when the check
	passes - or when enforcement is off, in which case the status is still
	reported so the punch is auditable.
	"""
	enforce = is_enforced()
	no_point = latitude is None or longitude is None

	if no_point:
		if enforce:
			return False, STATUS_UNAVAILABLE, NO_POINT_MESSAGE
		return True, STATUS_UNAVAILABLE, ""

	zones = get_zones(employee)
	if not zones:
		if enforce:
			return False, STATUS_NO_FENCE, NO_FENCE_MESSAGE
		return True, STATUS_NO_FENCE, ""

	for zone in zones:
		distance = haversine_km(
			zone.latitude, zone.longitude, latitude, longitude
		)
		if distance <= flt(zone.checkin_radius) / 1000.0:
			return True, STATUS_OK, ""

	if not enforce:
		return True, STATUS_OUTSIDE, ""

	if len(zones) == 1:
		zone = zones[0]
		message = (
			f"You must be within {int(flt(zone.checkin_radius))} meters of "
			f"{zone.location_name} to punch in. "
			"Please move closer and try again."
		)
	else:
		message = (
			"You are not within an allowed punch zone. Please move closer "
			"to your work location and try again."
		)

	return False, STATUS_OUTSIDE, message