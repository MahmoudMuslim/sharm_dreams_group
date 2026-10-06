# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""How many people left, month by month.

The other half of the split described in `monthly_joiners`, and the reason the split
was worth doing.

On this site 130 people left in September 2026 and none in the twelve months before,
which is one spike and a flat line. Drawn against the joiners line it also *flattened*
the joiners: 130 set the y-scale and a month with nine joiners became nine pixels, so
a chart about hiring showed no variation in hiring at all. On its own axis this reads
what it says - nothing left for twelve months, then everyone - and every month is
clickable, because a single series has no second line to be confused with.

The two charts can no longer be read against each other, which is the overlay's one
real advantage and the cost of this change. Both are on the `People` band, side by
side, so the comparison is one glance rather than one overlay.

`base_filters` here is empty where most charts on these dashboards carry
`status = Active`, and that is the whole subtlety of this figure: a leaver is by
definition not Active. All 130 rows on the September point are people who have left,
so an Active scope would open a list of zero for the largest bar on the chart while
the chart still said 130.
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

FIELD = "relieving_date"


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

	return {"labels": labels, "datasets": [{"name": _("Leavers per Month"), "values": values}]}


def counts_by_month() -> tuple[list[str], list[int]]:
	"""The labels and the counts, without the decorators or the company filter.

	Kept separate from `get_data` for the reason `monthly_joiners` keeps its own: the
	chart's answer and a recount of the same windows only catch a window that has
	drifted from the month it names when both are asked.
	"""
	labels, values = [], []
	for label, date_from, date_to in month_windows(getdate()):
		labels.append(label)
		values.append(frappe.db.count("Employee", {FIELD: ["between", (date_from, date_to)]}))
	return labels, values


def drilldown_options() -> dict:
	"""The whole `custom_options` value for `Monthly Attrition`.

	No `by_series`, one series, so every month resolves. `fieldtype` is `Int`; the desk
	reads the same column to format the tooltip.

	`base_filters` carries no `status`, and that is load-bearing rather than an
	oversight: every row on this chart is a leaver, so scoping it to Active would list
	zero people for the largest bar on the chart.
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
