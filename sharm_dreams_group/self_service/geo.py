"""Geofenced attendance punch.

Each `Employee Self Service Location` holds a centre plus a radius.  When an employee punches,
the browser sends its coordinates and this module verifies the distance before the
attendance is written.  Enforcement is toggled by `geo_punch_enforce` in Employee Self Service
Settings (default on).  Latitude and longitude are always recorded on the checkin
for audit, whatever the outcome - so a punch that was refused can still be
audited, and one that was allowed with enforcement off is still locatable.

The zone is anchored to the employee's **company** rather than to a work location.
HRMS has no work-location doctype and `Employee.branch` is a bare Data field, so
the company is the only site-level thing an Employee reliably carries.  That is
also why `Employee Self Service Location` enforces one row per company.
"""

import math

import frappe

EARTH_RADIUS_KM = 6371.0

STATUS_OK = "ok"
STATUS_OUTSIDE = "outside"
STATUS_NO_FENCE = "nofence"
STATUS_UNAVAILABLE = "unavailable"

# A Select field stores the option text, so the codes used on the wire are mapped
# to the labels persisted on the checkin. `Employee Checkin.sdg_self_service_geo_status`
# carries exactly these four options - changing one means changing both, which is
# what `setup.SELF_SERVICE_GEO_STATUS_OPTIONS` and `self_service.test_self_service` exist to catch.
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
	"No punch location is configured for your company. Please contact HR."
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


def get_fences(company):
	"""Active punch zones configured for a company."""
	if not company:
		return []
	return frappe.get_all(
		"Employee Self Service Location",
		filters={"company": company, "active": 1},
		fields=["name", "employee_self_service_location_name", "latitude", "longitude", "radius_km"],
	)


def check_punch(employee, latitude, longitude):
	"""Validate coordinates against the employee's geofence.

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

	fences = get_fences(frappe.db.get_value("Employee", employee, "company"))
	if not fences:
		if enforce:
			return False, STATUS_NO_FENCE, NO_FENCE_MESSAGE
		return True, STATUS_NO_FENCE, ""

	for fence in fences:
		distance = haversine_km(
			fence.latitude, fence.longitude, latitude, longitude
		)
		if distance <= fence.radius_km:
			return True, STATUS_OK, ""

	if not enforce:
		return True, STATUS_OUTSIDE, ""

	if len(fences) == 1:
		fence = fences[0]
		message = (
			f"You must be within {fence.radius_km:.2f} km of "
			f"{fence.employee_self_service_location_name} to punch in. "
			"Please move closer and try again."
		)
	else:
		message = (
			"You are not within an allowed punch zone. Please move closer "
			"to your work location and try again."
		)

	return False, STATUS_OUTSIDE, message