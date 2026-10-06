# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Make `Hiring vs Attrition Count` clickable, month by month and line by line.

The chart is HRMS's: a `Custom` chart whose source counts `Employee.date_of_joining`
and `Employee.relieving_date` per month. On this site it is the most informative
chart the group has - steady hiring of one to nine a month, then September 2026
with nothing hired and 130 leavers - and it is a picture, because a `Custom` chart
has no group-by field to filter a list on.

Two things make this one harder than `Employees by Age`, and both are about the
chart having **two lines**:

  * **A month is not a bar.** The age chart draws one bar per band, so a tapped bar
    names exactly one thing. This draws two bars per month - one on each line -
    and they mean different things: 9 hires in Feb 2026 and 0 leavers. Tapping the
    February point has to say which line it was on.

  * **The line names are translated.** The source labels its datasets with `_()`, so
    "Hiring Count" is "عدد التعيينات" for an Arabic user. A drilldown keyed on the
    series *name* would resolve for one language and quietly stop resolving for
    another, which is the failure this app treats as worse than a dead click.

So the buckets are keyed by **dataset index**, not by series name, and the widget
resolves which line the cursor was nearest. See `SERIES_PX` in `dashboard_view.js`
for the near-miss rule - when the two lines are closer than that at the tapped
month, the click is refused rather than guessed, because at February's 9-vs-0 the
distance is unarguable and at a month where the two lines cross it is not.

The month windows are absolute dates and are rewritten on every migrate, for the
same reason the age bands are: they live on a chart document, and a window written
once would be last year's answer in December. See
`hr/dashboard_chart_source/employees_by_age`.
"""

import frappe
from frappe.utils import add_days, add_months, getdate
from frappe.utils.dateutils import get_period

# How many months of history the chart shows when the site has no fiscal year
# start set. Must match `_HVA_FROM_DATE` in `upstream_patches.py`: the drilldown's
# windows have to be the months the chart draws, and the chart's range comes from
# the same twelve.
MONTHS_BACK = 12

# Which date each line counts, by dataset index rather than by name - see the
# module docstring. Index 0 is the first dataset the source returns, which is
# hiring; `test_the_lines_are_in_the_order_the_source_returns_them` pins that.
SERIES_FIELDS = {
	0: "date_of_joining",
	1: "relieving_date",
}

# The two series names, in the same order. Only the *counts* are read off these
# because the widget resolves a series by index rather than by name - see
# `test_the_lines_are_in_the_order_the_source_returns_them`, which pins the order
# against the source itself so this table cannot drift from the chart it describes.
SERIES_NAMES = ("Hiring Count", "Attrition Count")

# The field `custom_options` declares for the chart as a whole. Required, because
# the widget refuses a drilldown with no field, and it is the *hiring* field: the
# first line is the one a reader reaches for first, and a per-series map that fell
# back to nothing would have no line to fall back to.
PRIMARY_FIELD = SERIES_FIELDS[0]


def month_window_start(today=None):
	"""The first day of the earliest month the chart draws."""
	today = today or getdate()
	return add_months(today, -MONTHS_BACK)


def month_windows(today=None) -> list[tuple[str, str, str]]:
	"""Every `(label, date_from, date_to)` the chart's x-axis has.

	One window per calendar month from twelve months ago to this month inclusive -
	thirteen windows and the same thirteen labels the chart draws.

	**The windows are calendar months, not spans of thirty days, and the difference
	is not cosmetic.** The chart's `from_date` is `add_months(today, -12)` - the 4th of
	October, because that is what the patch below falls back to - and
	`get_result(..., "Monthly")` groups from *there*, so "Oct 2025" on the chart is
	1st to 31st of October while this app's window would be 4th October to 3rd
	November. Nine of the thirteen months came out wrong that way: Dec 2025 drew 4
	and its list held 0, Jan 2026 drew 1 and its list held 4, and the totals still
	summed to the same 43 across the band. A chart whose bars disagree with its lists
	while the total agrees is the exact failure this whole mechanism exists to
	prevent, and it is invisible in a total.

	So the first and last windows are the truncated ones, and they are truncated at
	the *month* boundary the chart uses rather than at the day the window happens to
	start:

	    Oct 2025  2025-10-01 .. 2025-10-31     not 10-04 .. 11-03
	    ...
	    Oct 2026  2026-10-01 .. 2026-10-31     the current month, still open

	`test_every_month_opens_the_rows_it_draws` compares these against the chart's own
	per-month counts rather than against a recount of this loop.
	"""
	today = today or getdate()
	# Walk whole months, from the start of the month the window begins in. The
	# middle ten are then exactly a calendar month and need no thought; only the
	# first and the last are clipped, by the lines below.
	first_month = month_window_start(today).replace(day=1)
	current_month = today.replace(day=1)

	firsts = []
	cursor = first_month
	while cursor <= current_month:
		firsts.append(cursor)
		cursor = add_months(cursor, 1)

	windows = []
	for first in firsts:
		month_end = add_days(add_months(first, 1), -1)
		windows.append(
			(
				# `get_period` is what the chart source calls to name the bucket, so
				# the two spell a month identically and a label match cannot fail on
				# a format.
				get_period(first, "Monthly"),
				# A month before the chart's window starts is outside its range and draws
				# no bar, so it gets no window - and the start is the first of the month.
				# `first` is already that boundary: first_month is the chart's from_date
				# (add_months(today, -12), truncated to the 1st by the upstream patch) and
				# the cursor only moves.  The previous expression clipped the first window
				# to `month_window_start(today)` - the calendar *day* - which made the
				# chart's first bucket, 1 to 31 October, disagree with this window, 6
				# October to 31 October, on every employee who joined in the first days
				# of that month.  It also contradicted this docstring, which promised the
				# opposite - truncation at the month boundary rather than the day.
				str(first),
				# Never past today: a window into next month would list employees who
				# have not joined yet, which is not nothing, it is wrong.
				str(month_end if month_end <= today else today),
			)
		)
	return windows


def drilldown_buckets() -> list[dict]:
	"""Every month with the filter it opens, for `Dashboard Chart.custom_options`.

	The label rides along so the widget can match the tapped x-axis label to a
	window *by name*. Matching on position would repoint every month if the source
	ever changed its range.
	"""
	return [
		{"label": label, "op": "between", "date_from": date_from, "date_to": date_to}
		for label, date_from, date_to in month_windows()
	]


def drilldown_options() -> dict:
	"""The whole `custom_options` value for `Hiring vs Attrition Count`.

	`fieldtype` is `Int` because both lines count people.

	`base_filters` is empty, and that is the whole subtlety of this chart: the
	hiring line counts everyone who joined in the month *including* anyone who has
	since left, and the attrition line counts leavers - most of whom are not Active.
	Adding the `status = Active` scope the other charts carry would drop 130 people
	from the September attrition list while the line above it still drew 130.
	"""
	return {
		"fieldtype": "Int",
		"drilldown": {
			"doctype": "Employee",
			"field": PRIMARY_FIELD,
			"base_filters": {},
			"by_series": {str(index): field for index, field in SERIES_FIELDS.items()},
			"buckets": drilldown_buckets(),
		},
	}


def counts_by_month(today=None) -> tuple[list[str], list[int], list[int]]:
	"""The labels and both lines' counts, as the chart draws them.

	Duplicated from the source rather than imported, for the reason
	`employees_by_age.counts_by_band` gives: the windows and the counts have to
	agree, and both are written here and checked against the source by
	`test_every_month_opens_the_rows_it_draws`.

	Only used by the tests, which is why it takes no company - it is always asked
	about the whole group, the same question the source is asked.
	"""
	today = today or getdate()
	start = month_window_start(today)
	labels: list[str] = []
	hired: list[int] = []
	left: list[int] = []
	for label, date_from, date_to in month_windows(today):
		labels.append(label)
		for series in SERIES_FIELDS:
			# The chart source counts every employee, leavers included, so no status
			# filter here: a `status = Active` scope would drop 130 people out of the
			# September attrition line while the chart above it still drew 130.
			count = frappe.db.count(
				"Employee", {SERIES_FIELDS[series]: ["between", (date_from, date_to)]}
			)
			if series == 0:
				hired.append(count)
			else:
				left.append(count)
	return labels, hired, left


def counts_by_month_as_the_widget_sees_them():
	"""The source's own output, through the same decorators the widget goes through.

	What `counts_by_month` computes from the same windows written here, this reads
	back off HRMS. The two agreeing is the point: it is the only way to tell that the
	windows describe the chart rather than something beside it.

	`from_date` is left unset on purpose. That is what the widget does on this site -
	the source's JS defaults it to the fiscal year, which is not set - so passing one
	would measure the patched default instead of the thing that could break.
	"""
	from hrms.hr.dashboard_chart_source.hiring_vs_attrition_count import (
		hiring_vs_attrition_count as hrms,
	)

	get_data = hrms.get_data
	while hasattr(get_data, "__wrapped__"):
		get_data = get_data.__wrapped__
	data = get_data(filters='{"time_interval": "Monthly"}')
	datasets = data.get("datasets") or []
	return (
		list(data.get("labels") or []),
		list((datasets[0] if datasets else {}).get("values") or []),
		list((datasets[1] if len(datasets) > 1 else {}).get("values") or []),
	)


def source_series_count() -> int:
	"""How many lines the source actually draws.

	`SERIES_FIELDS` has two entries because the source returns two datasets. If an
	HRMS upgrade added a third line, the table would be one short and every click on
	the new line would be refused - silently, because the widget resolves a series by
	index and an index past the end of the map simply is not there. So this is asked
	of the source rather than assumed.
	"""
	labels, hired, left = counts_by_month_as_the_widget_sees_them()
	return len(hired) + len(left)


__all__ = [
	"MONTHS_BACK",
	"PRIMARY_FIELD",
	"SERIES_FIELDS",
	"SERIES_NAMES",
	"counts_by_month",
	"counts_by_month_as_the_widget_sees_them",
	"drilldown_buckets",
	"drilldown_options",
	"month_window_start",
	"month_windows",
	"source_series_count",
]
