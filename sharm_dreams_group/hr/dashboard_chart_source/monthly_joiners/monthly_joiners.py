# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""How many people joined, month by month.

One of two charts split out of HRMS's `Hiring vs Attrition Count`, which draws joiners
and leavers as two lines on one axis. The split is not cosmetic.

**One axis, two questions, and only one of them readable.** Leavers on this site are
130 in a single month and zero in the twelve before it, so 130 sets the y-scale and a
month with nine joiners is drawn nine pixels tall. January (1) and February (9) are
indistinguishable and the whole year reads as flat. Given its own axis the same data
draws the variation it is actually about.

**And it is what makes the chart clickable.** A two-line chart cannot say which line
was tapped - frappe-charts records no per-series identity on a Line at all, and
`sharm_clicked_series` in `dashboard_view.js` has to infer it from pixel distance. At
0.95px per person the two lines are 1-9px apart for eleven of thirteen months, so
those clicks are honestly refused. One line has no such question: `sharm_clicked_series`
returns it without measuring anything, so **every month is clickable**. See that
function for why the refusal exists rather than a coin toss.

The cost, stated rather than buried: two charts cannot be read against each other on
one axis, which is the overlay's whole advantage. What the overlay said was "hiring
stopped and 130 left in September", and these two say it as much - and answer it.

The months, and the `Employee` field each one filters on, live in
`hiring_vs_attrition`; this is the joiners-only view of them, and deliberately
imports rather than restating so the two cannot disagree about where a month starts.
"""

import frappe
from frappe import _
from frappe.utils import getdate
from frappe.utils.dashboard import cache_source
from frappe.utils.nestedset import get_descendants_of

from sharm_dreams_group.hr.dashboard import require_dashboard_access
from sharm_dreams_group.hr.dashboard_chart_source.hiring_vs_attrition.hiring_vs_attrition import (
	drilldown_buckets,
	month_windows,
)

# The date on the row. Also what a tapped month filters on, which is why the same
# string is both the query and the drilldown's field.
FIELD = "date_of_joining"


@frappe.whitelist()
@cache_source
def get_data(
	chart_name: str | None = None,
	chart: str | None = None,
	no_cache: str | None = None,
	filters: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	timespan: str | None = None,
	time_interval: str | None = None,
	heatmap_year: str | None = None,
) -> dict[str, list]:
	require_dashboard_access()

	filters = frappe.parse_json(filters) or {}

	# A company filter means this company and everything under it, and no company
	# means every company - the same widening every upstream chart source here is
	# patched for, and the reason `sharm_dreams_group/upstream_patches.py` exists.
	company = filters.get("company")
	companies = (
		[company, *get_descendants_of("Company", company)]
		if company
		else frappe.get_all("Company", pluck="name")
	)

	labels, values = [], []
	for label, date_from, date_to in month_windows(getdate()):
		labels.append(label)
		values.append(
			frappe.db.count(
				"Employee",
				{
					"company": ("in", companies),
					FIELD: ["between", (date_from, date_to)],
				},
			)
		)

	return {"labels": labels, "datasets": [{"name": _("Joiners per Month"), "values": values}]}


def counts_by_month() -> tuple[list[str], list[int]]:
	"""The labels and the counts, without the decorators.

	Separate from `get_data` on purpose: the chart's own answer and a recount of the
	same windows are two different things, and only asking both of them finds a window
	that has drifted from the month it names. No company filter either - it is always
	asked about the whole group, which is the question these charts are for.
	"""
	labels, values = [], []
	for label, date_from, date_to in month_windows(getdate()):
		labels.append(label)
		values.append(frappe.db.count("Employee", {FIELD: ["between", (date_from, date_to)]}))
	return labels, values


def drilldown_options() -> dict:
	"""The whole `custom_options` value for `Monthly Joiners`.

	`fieldtype` is `Int` because this counts people. It matters: the desk reads the
	same column to format the tooltip, and handing that formatter nothing puts
	`undefined` in front of every number.

	No `by_series`, because there is one series and the series *is* the field - which
	is precisely what makes every month clickable rather than one month in thirteen.

	`base_filters` is empty. The joiners line counts everyone who joined that month
	*including* anyone who has since left, so a `status = Active` scope would drop
	them out of the list while the chart above it still drew them.
	"""
	return {
		"fieldtype": "Int",
		"drilldown": {
			"doctype": "Employee",
			"field": FIELD,
			"base_filters": {},
			"buckets": drilldown_buckets(),
		},
	}


__all__ = ["FIELD", "counts_by_month", "drilldown_options", "get_data"]
