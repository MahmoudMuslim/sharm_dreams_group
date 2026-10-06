# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""How soon active staff contracts run out.

A Custom chart rather than a Group By, and the reason is that `Group By` cannot
answer the question.  `get_group_by_chart_config` groups on the field's raw
value, so pointing it at `contract_end_date` produces one category per distinct
day - 180 of them, one bar wide - instead of the handful of windows anyone
actually reads.  Bucketing the dates has to happen in Python, which is what this
module is.

Read this next to `hr.dashboard.METRICS`: the "Contracts Ending (30d)" and
"(90d)" cards are the same date comparisons counted, so the chart and the tiles
can never disagree about how many contracts are about to run out.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate
from frappe.utils.dashboard import cache_source
from frappe.utils.nestedset import get_descendants_of

from sharm_dreams_group.hr.dashboard import DATED_FLOOR, require_dashboard_access

# The bar every contract with no end date lands in, named rather than left blank.
# It is a bucket and not a gap because on this site it is the *answer*: the stock
# `Employee.contract_end_date` is empty for every employee, so a chart drawing only
# the windows would be five empty bars on a page that looks broken rather than one
# that says what is wrong.
NO_END_DATE = _("No end date on file")


def window_specs() -> tuple[tuple[str, object, dict], ...]:
	"""Every bar, as `(label, matches, drill_down)`.

	One definition on purpose.  A bar is drawn by the predicate and opens a list
	by the spec, and a chart whose two halves disagree is a chart that navigates
	to the wrong rows - the single outcome this app treats as worse than a dead
	click.  Keeping them in one tuple makes that impossible rather than merely
	unlikely, and `test_the_bars_and_their_drilldowns_are_the_same_buckets` in
	`hr.test_dashboard` pins the agreement from the other side.

	Predicates are tried in order and the first match wins, so each one only has
	to exclude the bars above it.  A date in the past is "Ended" rather than a
	window of its own: an active employee whose contract already lapsed is a
	records problem, not a forecast.

	Spans are deliberately coarse.  30 days is the "start talking to them" window
	and 12 months the "notice period" one; anything finer is a spreadsheet
	question, and this chart is a list.

	The specs are *day offsets* rather than dates, and never dates.  They are
	stored on the chart document, so a date written here would still be next
	month's answer when someone tapped the bar; the widget adds them to today,
	which keeps the window a bar claims to be the window it filters.  The one
	exception is `floor`, a fixed literal that has to stay a literal: it is the
	cut-off that keeps a blank date out of a `between` (see `DATED_FLOOR`), and
	shifting it by today would defeat the purpose.
	"""
	today = getdate()
	return (
		(
			_("Ended"),
			lambda end: end < today,
			# A floor, not a `<`. Frappe compares a Date as
			# `ifnull(field, '0001-01-01')`, so "before today" on its own would also
			# match the rows with no date - which here is the largest bar on the chart,
			# and would be listed under a heading it does not belong to. `to: -1` keeps
			# it off today's bar too, so the bar and the list are the same set.
			{"op": "between", "floor": DATED_FLOOR, "to": -1},
		),
		(
			_("Ends in 30 days"),
			lambda end: today <= end <= add_days(today, 30),
			{"op": "between", "from": 0, "to": 30},
		),
		(
			_("Ends in 3 months"),
			lambda end: add_days(today, 30) < end <= add_days(today, 90),
			{"op": "between", "from": 31, "to": 90},
		),
		(
			_("Ends in 12 months"),
			lambda end: add_days(today, 90) < end <= add_days(today, 365),
			{"op": "between", "from": 91, "to": 365},
		),
		(
			_("Later"),
			lambda end: end > add_days(today, 365),
			# `>` needs no floor: a blank date becomes `0001-01-01`, which fails it.
			{"op": ">", "offset": 365},
		),
	)


def contract_end_windows() -> tuple[tuple[str, object], ...]:
	"""The `(label, matches)` pairs, in reading order - what `get_data` plots."""
	return tuple((label, matches) for label, matches, _drill_down in window_specs())


def drilldown_buckets() -> list[dict]:
	"""Every bar with the filter it opens, for `Dashboard Chart.custom_options`.

	The label is carried on each bucket so the widget can match the bar it was
	clicked against a bucket *by name* rather than by position.  Position would be
	the shorter key and a worse one: reordering the bars in Python would silently
	point each one at the wrong list, which is exactly the failure this app
	prefers a dead click to.
	"""
	return [dict(drill_down, label=label) for label, _matches, drill_down in window_specs()] + [
		{"label": NO_END_DATE, "op": "is", "value": "not set"},
	]


def drilldown_options() -> dict:
	"""The whole `custom_options` value for this chart.

	`Custom` charts have no `group_by_based_on`, and that field is what every other
	chart on these dashboards is drillable by - so without this the chart would be
	the one picture on the page.  See `dashboard_view.js`'s `sharm_custom_drilldown`.

	`fieldtype` rides along because the desk widget reads the same column to format
	the tooltip (`chart_widget.js`, "chart_type == Custom"), and handing that
	formatter nothing would put `undefined` in front of every number.
	"""
	return {
		"fieldtype": "Int",
		"drilldown": {
			"doctype": "Employee",
			"field": "contract_end_date",
			# Active only, because the chart counts active staff only.  A bar saying
			# 12 that opens a list of 40 is the disagreement this whole mechanism
			# exists to prevent.
			"base_filters": {"status": "Active"},
			"buckets": drilldown_buckets(),
		},
	}


def bucket_end_dates(dates, windows) -> tuple[list[int], int]:
	"""Count `dates` into `windows`; returns the per-bar counts and the count of
	contracts with no date at all.

	Free of the database, so the bucketing can be tested against dates sitting on
	each boundary rather than against whatever the site happens to hold.
	"""
	values = [0] * len(windows)
	undated = 0
	for raw in dates or ():
		if not raw:
			undated += 1
			continue
		end = getdate(raw)
		for index, (_label, matches) in enumerate(windows):
			if matches(end):
				values[index] += 1
				break
	return values, undated


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

	# A company filter means this company and everything under it, so selecting the
	# group parent reports the whole group and no company means every company - the
	# same widening the upstream chart sources are patched for.
	company = filters.get("company")
	companies = (
		[company, *get_descendants_of("Company", company)]
		if company
		else frappe.get_all("Company", pluck="name")
	)

	# Active only.  A leaver's contract ending is history, and the point of the chart
	# is to decide what to do about a contract that is still running.
	ends = frappe.db.get_list(
		"Employee",
		filters={"company": ("in", companies), "status": "Active"},
		pluck="contract_end_date",
	)

	windows = contract_end_windows()
	values, undated = bucket_end_dates(ends, windows)

	return {
		"labels": [label for label, _matches in windows] + [NO_END_DATE],
		"datasets": [
			{"name": _("Active contracts"), "values": values + [undated]},
		],
	}