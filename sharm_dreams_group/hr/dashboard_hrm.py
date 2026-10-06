"""The `SDG HRM` dashboard: compliance, cash benefits, and what is missing.

Aimed at a different question from `hr/dashboard.py`.  That one is *who works
here* - headcount, salary, tenure.  This one is *is our paperwork in order, what
did we hand out in cash, and what can we not answer a question about*.

The last of those is deliberate.  Two of these cards count gaps - a missing
national ID expiry, a leaver with no relieving date - because a record that is
silently incomplete is the failure mode this app keeps running into, and a
dashboard is the right place to make it visible rather than the wrong place to
hide it.  They are not criticisms of anyone; they are a to-do list.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, flt, nowdate

from sharm_dreams_group.hr.dashboard import can_view_dashboard, require_dashboard_access

METRIC_METHOD = "sharm_dreams_group.hr.dashboard_hrm.metric"
MONEY = "EGP"

METRICS: dict[str, dict] = {
	# --- cash benefits, the part that never touches payroll ----------------
	"coupon_recipients": {
		"label": "Coupon Recipients",
		"color": "Green",
		"kind": "count",
		"route": "List/SDG Coupon Granted",
	},
	"coupon_value": {
		"label": "Coupon Value Paid",
		"color": "Green",
		"kind": "sum",
		"route": "List/SDG Coupon Granted",
	},
	# Routed by `coupon_routes()` below, which pins all three coupon cards to the month
	# the card was measured over and adds the unrecorded flag to this one.
	"coupons_outstanding": {
		"label": "Coupons Unrecorded",
		"color": "Red",
		"kind": "count",
		"route": "List/SDG Coupon Granted",
	},
	# --- movement in and out ----------------------------------------------
	"leavers_no_relieving_date": {
		"label": "Leavers Without Relieving Date",
		"color": "Orange",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {"status": "Left", "relieving_date": ["is", "not set"]},
	},
	"leavers_this_period": {
		"label": "Leavers (Recorded)",
		"color": "Orange",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {"status": "Left"},
	},
	# --- records that cannot answer a question ----------------------------
	"no_arabic_name": {
		"label": "Active Staff Without Arabic Name",
		"color": "Red",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_name_arabic": ["is", "not set"]},
	},
	"no_joining_date": {
		"label": "Active Staff Without Joining Date",
		"color": "Red",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {"status": "Active", "date_of_joining": ["is", "not set"]},
	},
	"no_department": {
		"label": "Active Staff Without Department",
		"color": "Red",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {"status": "Active", "department": ["is", "not set"]},
	},
	# --- cover ------------------------------------------------------------
	"uninsured_active": {
		"label": "Active Staff Uninsured",
		"color": "Red",
		"kind": "count",
		"route": "List/Employee",
		# Must name the same values the metric counts. The metric reads
		# `["Uninsured", "None", "", None]`, and a filter of `= "Uninsured"` alone
		# returns 30 of the 34 - a drill-down that lands on a different number than
		# the card it came from. `IN (...)` covers the empty string; the four rows
		# that are genuinely NULL are counted by neither, which is why the metric
		# and this filter are kept in step deliberately.
		"route_options": {"status": "Active", "sdg_si_status": ["in", ["Uninsured", "None", ""]]},
	},
	"id_expired": {
		"label": "National ID Expired",
		"color": "Red",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"sdg_national_id_expiry": ["between", ["1900-01-01", "Today"]],
		},
	},
	"id_expiring_90d": {
		"label": "National ID Expiring (90d)",
		"color": "Orange",
		"kind": "count",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			# Frappe's list filter syntax for a range, relative rather than dated,
			# so the filter does not go stale on the page
			"sdg_national_id_expiry": ["between", ["Today", "Add 90 days from today"]],
		},
	},
}


def _is_money(key: str) -> bool:
	return METRICS[key]["kind"] == "sum"


def metric_definitions() -> list[dict]:
	return [
		dict(key=key, currency=(MONEY if _is_money(key) else None), **spec) for key, spec in METRICS.items()
	]


def _latest_coupon_month() -> str | None:
	"""The most recent month a coupon was handed over, or None.

	Taken from the register rather than from the calendar, so the card is never
	blank in the days between one month's handout and the next.  An empty register
	means the card reads 0 and says so.
	"""
	row = frappe.db.sql("select max(period_month) from `tabSDG Coupon Granted`", as_list=True)
	return str(row[0][0]) if row and row[0][0] else None


@frappe.whitelist()
def metric(filters: str | dict | None = None) -> int | float:
	"""One card's figure, for the card's stored `metric` filter."""
	require_dashboard_access()

	filters = frappe.parse_json(filters) or {}
	key = filters.get("metric")
	if key not in METRICS:
		frappe.throw(frappe._("Unknown HRM dashboard metric {0}.").format(key))

	if key.startswith("coupon"):
		month = _latest_coupon_month()
		if not month:
			return 0
		coupon = {"period_month": month}
		if key == "coupon_recipients":
			return frappe.db.count("SDG Coupon Granted", coupon)
		if key == "coupon_value":
			return _round(
				frappe.db.sql(
					"select sum(amount) from `tabSDG Coupon Granted` where period_month = %s",
					month,
					as_list=True,
				)[0][0]
			)
		# Unrecorded: coupons the workbook paid for that no Employee could be matched
		# to.  These are rows in the register now, flagged `sdg_unrecorded`, so the
		# figure and the list behind it are the same records - which a subtraction of
		# the workbook from the register never was, and never could be listed.
		return frappe.db.count("SDG Coupon Granted", {**coupon, "sdg_unrecorded": 1})

	if key == "leavers_no_relieving_date":
		return frappe.db.count("Employee", {"status": "Left", "relieving_date": ("in", ["", None])})
	if key == "leavers_this_period":
		return frappe.db.count("Employee", {"status": "Left"})

	if key == "no_arabic_name":
		return frappe.db.count("Employee", {"status": "Active", "sdg_name_arabic": ("in", ["", None])})
	if key == "no_joining_date":
		return frappe.db.count("Employee", {"status": "Active", "date_of_joining": ("in", ["", None])})
	if key == "no_department":
		return frappe.db.count("Employee", {"status": "Active", "department": ("in", ["", None])})

	if key == "uninsured_active":
		return frappe.db.count(
			"Employee", {"status": "Active", "sdg_si_status": ("in", ["Uninsured", "None", "", None])}
		)
	if key == "id_expired":
		return frappe.db.count("Employee", {"status": "Active", "sdg_national_id_expiry": ("<", nowdate())})
	if key == "id_expiring_90d":
		return frappe.db.count(
			"Employee",
			{
				"status": "Active",
				"sdg_national_id_expiry": ("between", (nowdate(), add_days(nowdate(), 90))),
			},
		)
	raise AssertionError(key)  # pragma: no cover - the key was checked above


def coupon_routes() -> dict:
	"""The three coupon cards, pinned to the month they were measured over.

	The month is only known once the map is built - the same bargain the payroll
	routes make - and the rebuild happens on every dashboard open, so a coupon month
	that arrives later is picked up without a code change.  Without it the cards would
	open every month the register holds, which happens to be one month today and stops
	being true the moment a second one is loaded.
	"""
	month = _latest_coupon_month()
	options = {"period_month": month} if month else {}
	return {
		"Coupon Recipients": {"route": "List/SDG Coupon Granted", "route_options": dict(options)},
		"Coupon Value Paid": {"route": "List/SDG Coupon Granted", "route_options": dict(options)},
		"Coupons Unrecorded": {
			"route": "List/SDG Coupon Granted",
			"route_options": {**options, "sdg_unrecorded": 1},
		},
	}


MONEY_PRECISION = 2


def _round(value) -> float:
	return flt(round(flt(value), MONEY_PRECISION))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
HRM_SECTIONS = (
	(
		"Compliance",
		("National ID Expired", "National ID Expiring (90d)", "Active Staff Uninsured"),
		("Uninsured by Company", "Compliance Gaps by Company", "Social Insurance Status"),
	),
	(
		"Cash Benefits",
		("Coupon Recipients", "Coupon Value Paid", "Coupons Unrecorded"),
		("Coupon by Band",),
	),
	(
		"Movement",
		("Leavers (Recorded)", "Leavers Without Relieving Date"),
		("Leavers by Company",),
	),
	(
		"Records To Complete",
		(
			"Active Staff Without Arabic Name",
			"Active Staff Without Joining Date",
			"Active Staff Without Department",
		),
		(),
	),
)

# How each band is arranged, and how wide.  The format, and the reason it is a table
# rather than a change to the shape of HRM_SECTIONS, are in
# `hr/dashboard.py`'s `SECTION_LAYOUT`; this is HRM's own copy of it, passed to
# `section_layout` by `create_domain_dashboards`.
#
# It has to be its own: the workforce dashboard has a band called "Records To Complete"
# too, holding eleven different cards laid out as six, a chart, then five. Handing
# that arrangement to this dashboard would be silently ignored rather than wrong -
# `_blocks_for` drops labels the section does not have - so it would never show up
# except as a layout nobody asked for not happening.
HRM_SECTION_LAYOUT: dict[str, tuple] = {
	# Three compliance cards and three charts.  Three across rather than two, because
	# a compliance band is read across: "how bad, and where" is one glance, and the
	# third column is what stops the reader scrolling to compare the second chart with
	# the first.
	"Compliance": (
		(
			"cards",
			("National ID Expired", "National ID Expiring (90d)", "Active Staff Uninsured"),
			{"columns": 3},
		),
		(
			"charts",
			("Uninsured by Company", "Compliance Gaps by Company", "Social Insurance Status"),
			{"columns": 3, "height": 260},
		),
	),
	# Two cards and one chart.  The one chart takes the full width rather than the
	# default two across: at half width the company names on its x-axis rotate, and
	# this band exists to say which company is late.
	"Movement": (
		("cards", ("Leavers (Recorded)", "Leavers Without Relieving Date"), {"columns": 2}),
		("charts", ("Leavers by Company",), {"columns": 1, "height": 260}),
	),
}

# (chart_name, doctype, group-by field, value field, filters, options)
# `SDG Coupon Granted` holds the employee but not the company, and a Dashboard
# Chart groups by a field on its own document - it cannot join through to the
# employee to find the company.  So there is one chart here, the band mix, and no
# "coupons by company" one: the figures exist in the data and would need a
# different mechanism (a Number Card, which does forward a method) to show them.
HRM_CHARTS = (
	(
		"Coupon by Band",
		"SDG Coupon Granted",
		"band",
		None,
		[],
		{"type": "Donut"},
	),
)


def can_view() -> bool:
	"""Whether this user may see these figures.

	Delegated, so the roles are the same ones the workforce dashboard uses rather
	than a second list that drifts from the first.
	"""
	return can_view_dashboard()
