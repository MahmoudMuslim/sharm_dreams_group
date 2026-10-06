# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Make `Employees by Age` clickable, band by band.

The chart itself is HRMS's: a `Custom` chart whose source buckets `date_of_birth`
into five-year bands in Python. It draws correctly and is the best picture of the
group's age profile on the dashboard - and it is also the one chart on the page a
click does nothing on, because `dashboard_view.js` drills a chart down through its
`group_by_based_on` field and a `Custom` chart has none.

So this module is only the half that says what each band *means*. Read it next to
HRMS's `get_ranges()`: the bands are restated here deliberately rather than
imported, because the two have to agree exactly and the day offsets below are
written against a specific set of labels. `test_the_bands_are_hrms_bands` pins that
agreement from both sides - it calls HRMS's own function and compares, so an HRMS
upgrade that changes the bands fails here rather than producing a chart whose bars
open the wrong list.

The two rules that make this work:

  * **Day offsets, not dates.** These values are stored on the chart document, so a
    date written at setup time would be next year's answer when the bar was tapped.
    The widget adds the offsets to today, so the band a bar claims to be is the band
    it filters. See `contract_end_dates.window_specs` for the same rule on a chart
    this app owns.

  * **A band is a window on `date_of_birth`, not on age.** An age has no column to
    filter on, and Frappe cannot express "age between 25 and 29" - so the band is
    inverted into the dates that produce it. Age 25-29 means born between
    `(today - 30 years)` and `(today - 25 years)`, which is why the `from`/`to`
    below are *inverted* relative to the band's own numbers, and why the upper bound
    is exclusive. Getting that backwards yields a list of people ten years older than
    the bar they tapped, which is the one outcome this app treats as worse than a
    dead click.

`80+` has no upper bound and `15-19` no lower one below 15, so both carry a
`floor` rather than a plain offset. `floor` is the one value taken literally, and
deliberately: Frappe compares a Date as `ifnull(field, '0001-01-01')`, so a bare
`<` on the oldest band would also admit everyone with no date of birth. See
`DATED_FLOOR` in `hr/dashboard.py`.
"""

from dateutil.relativedelta import relativedelta

import frappe
from frappe.utils import add_days, add_years, getdate

from sharm_dreams_group.hr.dashboard import DATED_FLOOR

# Nobody on this site is under 15 or over 80, and both empty bands draw zero bars -
# so the floor below is only reached if that changes, and is then still the right
# cut-off. It is a literal rather than an offset for the reason given above.
OLDEST = 15
NEWEST = 80


def age_ranges() -> tuple[tuple[int, int | None], ...]:
	"""Every band, as `(low, high)` - the same shape as HRMS's `get_ranges()`.

	`high` is `None` for the open-ended top band, where HRMS uses a bare `80`.
	"""
	ranges = []
	for start in range(OLDEST, NEWEST, 5):
		ranges.append((start, start + 4))
	ranges.append((NEWEST, None))
	return tuple(ranges)


def band_label(low: int, high: int | None) -> str:
	"""The label HRMS prints on the bar, built the same way it builds it.

	`f"{bracket}+"` for the open band and `f"{lo}-{hi}"` otherwise, so the widget
	matches a bar to a bucket by name rather than by position - which is what
	`sharm_find_bucket` does. Matching on position would quietly repoint every bar if
	HRMS ever reordered its bands.
	"""
	return f"{low}+" if high is None else f"{low}-{high}"


def _window_for(low: int, high: int | None) -> dict:
	"""The age band as a `date_of_birth` filter on the chart document.

	The band is *inverted*, and this is the part that is easy to get backwards: age
	`low`-`high` means born **after** `(today - (high + 1) years)` and **on or
	before** `(today - low years)`. So the offset that becomes the filter's lower
	bound is the band's *upper* age, negated, and vice versa.

	Both bounds are stored as offsets and the widget resolves them with
	`moment(today).add(n, "days")` - see `sharm_bucket_filter`. That is a problem,
	and it is why this function is not simply the obvious thing:

	`moment(...).add(-30, "days")` subtracts thirty **days**, not thirty years. An
	offset cannot express an age band at all, because the band moves with the
	calendar year while an offset moves with the day. So these buckets store
	*absolute dates* under `date_from` / `date_to` rather than offsets, and the
	widget resolves those instead. They are computed fresh by every migrate, which is
	what keeps them from going stale: an age band written once and never revisited
	would still be last year's answer in December.

	The upper bound is a day past the last date that qualifies, because `between` is
	inclusive at both ends and a birthday on the boundary belongs *inside* this band
	rather than the next one.
	"""
	today = getdate()
	youngest = add_years(today, -low)  # the latest date of birth still in this band
	oldest = add_years(today, -(high + 1)) if high is not None else None

	if oldest is None:
		# The open-ended top band: born on or before `today - low years`, and after a
		# floor so a row with no date of birth is not admitted by the `ifnull`
		# comparison. See `DATED_FLOOR`.
		return {
			"op": "between",
			"date_from": DATED_FLOOR,
			"date_to": str(add_days(youngest, 1)),
		}

	return {
		"op": "between",
		"date_from": str(oldest),
		# `add_days(..., 1)` on the *youngest* date, so the boundary day is included
		"date_to": str(add_days(youngest, 1)),
	}


def drilldown_buckets() -> list[dict]:
	"""Every band with the filter it opens, for `Dashboard Chart.custom_options`.

	The label rides along so the widget can match the tapped bar to a bucket *by
	name* rather than by position - see `band_label`.
	"""
	return [
		dict(_window_for(low, high), label=band_label(low, high))
		for low, high in age_ranges()
	]


def drilldown_options() -> dict:
	"""The whole `custom_options` value for `Employees by Age`.

	`fieldtype` rides along because the desk widget reads the same column to format
	the tooltip, and handing that formatter nothing puts `undefined` in front of every
	number.

	`base_filters` is active staff only, because the chart counts active staff only:
	a bar saying 31 that opens a list of 45 is the disagreement this whole mechanism
	exists to prevent.
	"""
	return {
		"fieldtype": "Int",
		"drilldown": {
			"doctype": "Employee",
			"field": "date_of_birth",
			"base_filters": {"status": "Active"},
			"buckets": drilldown_buckets(),
		},
	}


def counts_by_band() -> tuple[list[str], list[int]]:
	"""The labels and the counts, as the chart draws them.

	Duplicated from HRMS's `get_age_list` / `get_employees_by_age` rather than
	imported, because the bands here and the windows in `_window_for` have to agree:
	both are written from `age_ranges`, and
	`test_every_band_opens_the_rows_it_draws` checks the two against the database.

	Not used by the chart - HRMS's source draws it - only by the tests, which is why
	it takes no company filter: it is always asked about the whole group.
	"""
	employees = frappe.db.get_list("Employee", filters={"status": "Active"}, pluck="date_of_birth")
	ages = []
	for dob in employees:
		if not dob:
			continue
		# Whole years elapsed, the same measure HRMS's `relativedelta(...).years`
		# takes, so a band means the same thing in the chart and in the list.
		years = relativedelta_years(getdate(), getdate(dob))
		ages.append(years)

	labels: list[str] = []
	values: list[int] = []
	for low, high in age_ranges():
		labels.append(band_label(low, high))
		values.append(
			sum(
				1
				for age in ages
				if age >= low and (high is None or age <= high)
			)
		)
	return labels, values


def relativedelta_years(today, then) -> int:
	"""Whole years from `then` to `today`, matching `relativedelta(...).years`."""
	return relativedelta(getdate(), getdate(then)).years


__all__ = [
	"age_ranges",
	"band_label",
	"counts_by_band",
	"drilldown_buckets",
	"drilldown_options",
]