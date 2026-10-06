# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""`Hiring vs Attrition Count` has to mean the same thing as the chart above it.

The chart is HRMS's: it counts `Employee.date_of_joining` and
`Employee.relieving_date` per month and draws them as two lines. This app's half is
the drilldown - which month, which line, which field - and the whole of the risk in
that is that the list behind a bar is not the rows the bar counted.

Three ways that goes wrong, and each has a test here:

  * **the windows drift from the chart's months.** `from_date` is
    `add_months(today, -12)` - the 4th of October - while a month on the x-axis is a
    calendar month. Nine of the thirteen came out wrong that way, and the totals
    still summed correctly, so only a per-month check finds it.
  * **a line's field is the wrong one.** Hiring counts joiners, attrition counts
    leavers, and the two are different columns on the row. Getting them crossed
    lists a completely different set of people, and the list would be plausible.
  * **a line is missing from the map.** The widget resolves a series by index, so a
    third line with no entry is simply unresolvable, and unresolvable reads as
    unclickable rather than as wrong.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from sharm_dreams_group.hr.dashboard_chart_source.hiring_vs_attrition import (
	hiring_vs_attrition as mine,
)


class TestHiringVsAttrition(FrappeTestCase):
	"""Every bar lists exactly the rows it drew, on the right column."""

	def _chart_bands(self):
		"""`(name, {month: value})` per line, from the source itself.

		Asked of HRMS rather than recomputed: the point of every check here is that
		this app's windows and HRMS's months agree, and a recount of the same Python
		would only prove the arithmetic is self-consistent. The month is the dict's key
		rather than a field beside it, because every check below walks months inside a
		line and there is no third thing for a month-level value to be.
		"""
		labels, hired, left = mine.counts_by_month_as_the_widget_sees_them()
		self.assertTrue(labels, "the chart source returned no months")
		return [
			(name, dict(zip(labels, values)))
			for name, values in zip(mine.SERIES_NAMES, (hired, left))
		]

	def _drawn_by_field(self, by_series):
		"""`{field: total drawn}` across the whole window."""
		totals = {field: 0 for field in by_series.values()}
		for name, values in self._chart_bands():
			totals[by_series[str(mine.SERIES_NAMES.index(name))]] += sum(values.values())
		return totals

	def test_the_chart_source_draws_something(self):
		"""A chart with a drilldown and no data is a click that opens an empty list."""
		bands = self._chart_bands()
		self.assertEqual(len(bands), 2, "the source no longer draws two lines")
		months = list(bands[0][1])
		for name, values in bands:
			with self.subTest(line=name):
				self.assertEqual(
					list(values), months, f"{name} covers different months to the other line"
				)
				self.assertTrue(
					any(values.values()),
					f"{name} is flat at zero across every month, so the chart says nothing",
				)

	def test_every_month_opens_the_rows_it_draws(self):
		"""Bar *n* lists the people bar *n* counted.

		Counted from the database, resolved the way `sharm_bucket_filter` resolves it.
		The same check on the totals would pass with the windows shifted a month -
		thirteen windows cover the same year either way - so this is per month, and the
		per-line sum is asserted as well.
		"""
		spec = self._drilldown()
		buckets = {b["label"]: b for b in spec["buckets"]}
		by_series = spec["by_series"]
		self.assertTrue(by_series, "no per-series map, so a two-line chart is not clickable")

		listed = {field: 0 for field in by_series.values()}
		for name, values in self._chart_bands():
			# Which field this line uses: the map is keyed by index, and the two
			# lines come back from the source in that order.
			series = mine.SERIES_NAMES.index(name)
			filter_field = by_series[str(series)]
			for label, drawn in values.items():
				with self.subTest(month=label, line=name):
					self.assertIn(label, buckets, f"the chart draws {label} and no window names it")
					bucket = buckets[label]
					opened = frappe.db.count(
						"Employee",
						{filter_field: ["between", (bucket["date_from"], bucket["date_to"])]},
					)
					listed[filter_field] += opened
					self.assertEqual(
						opened,
						drawn,
						f"{label} on the {name} line draws {drawn} and its list "
						f"holds {opened}",
					)

		# And the windows must not double-count: months share boundaries, and a
		# joiner on the 1st of a month is in exactly one window.
		for field, total in listed.items():
			drawn = self._drawn_by_field(by_series)[field]
			self.assertEqual(
				total,
				drawn,
				f"the {field} lists sum to {total} but the chart draws {drawn}, so "
				"some employee is in two months",
			)

	def test_the_lines_count_the_columns_they_name(self):
		"""Hiring counts joiners, attrition counts leavers, and they are not swapped.

		A crossed map lists a completely different set of people, and nothing about the
		result would look wrong - it is a plausible list of employees, correctly
		filtered, of the wrong question. So this asks for the two columns' own totals
		and requires them to differ: on this site `date_of_joining` and
		`relieving_date` over the same window are 43 and 130, and a swapped map would
		have the hiring bar saying 130.

		Equal totals are a failure here, not a pass. Two columns that happen to count
		the same thing over the same window cannot be told apart by this test, and the
		per-month check above is what would still be doing the work.
		"""
		by_series = self._drilldown()["by_series"]
		totals = self._drawn_by_field(by_series)
		for series, field in by_series.items():
			with self.subTest(series=series, field=field):
				self.assertGreater(
					totals[field], 0, f"{field} has no rows in the window at all"
				)
		fields = sorted(set(by_series.values()))
		if len(fields) == 2:
			self.assertNotEqual(
				totals[fields[0]],
				totals[fields[1]],
				f"{fields[0]} and {fields[1]} count {totals[fields[0]]} over the same "
				"window, so this check cannot tell the two lines apart - a swapped map "
				"would pass every other test here",
			)

	def test_the_windows_are_calendar_months(self):
		"""The window is a calendar month, not thirty days from the window's start.

		`from_date` falls back to `add_months(today, -12)`, which is the 4th of
		October, and the first month of the x-axis is that October - so a window from
		the 4th runs into November and lists four weeks of November under an October
		label. Nine of the thirteen were wrong that way and the totals still summed to
		the same 43, which is why only this can catch it.
		"""
		from calendar import monthrange

		from frappe.utils import getdate

		today = getdate()
		windows = mine.month_windows(today)
		for label, date_from, date_to in windows:
			with self.subTest(month=label):
				start = frappe.utils.getdate(date_from)
				end = frappe.utils.getdate(date_to)
				# The label names a month; the window has to be inside it.
				self.assertEqual(
					(start.year, start.month),
					_mapped_month(label),
					f"{label}'s window starts in {start.year}-{start.month}",
				)
				self.assertLessEqual(
					end,
					frappe.utils.getdate(
						f"{start.year:04d}-{start.month:02d}-"
						f"{monthrange(start.year, start.month)[1]:02d}"
					),
					f"{label}'s window runs past the end of that month",
				)
				# And never past today: a window into next month lists employees who
				# have not joined yet.
				self.assertLessEqual(end, today, f"{label}'s window is in the future")
				self.assertLessEqual(
					start, end, f"{label}'s window ends before it starts"
				)

	def test_the_lines_are_in_the_order_the_source_returns_them(self):
		"""The map is keyed by index, so the order has to be the source's.

		Not by name - the source labels its datasets with `_()`, so an Arabic user sees
		`عدد التعيينات` and a name-keyed map stops resolving. By index, which is
		stable across languages, and which is only stable if it matches the order the
		datasets come back in.
		"""
		self.assertEqual(
			sorted(int(key) for key in self._drilldown()["by_series"]),
			list(range(len(mine.SERIES_FIELDS))),
			f"by_series covers {sorted(self._drilldown()['by_series'])}, so a line is "
			"missing from the map and its bars cannot be clicked",
		)
		# And the fields are the two date columns, in the order a reader would expect:
		# what you joined, then what you left.
		fields = [self._drilldown()["by_series"][str(n)] for n in range(len(mine.SERIES_FIELDS))]
		self.assertEqual(fields, ["date_of_joining", "relieving_date"])

	def test_the_drilldown_covers_exactly_the_months_drawn(self):
		"""A bucket nobody can reach is a bucket that will not be reached.

		One extra window is harmless to click through; one *missing* is a bar with no
		list. Checked as a set so a renamed month on either side is caught.
		"""
		labels = list(self._chart_bands()[0][1])
		self.assertEqual(
			sorted(b["label"] for b in self._drilldown()["buckets"]),
			sorted(labels),
			"the drilldown's months are not the chart's",
		)

	def test_base_filters_do_not_narrow_the_attrition_line(self):
		"""Attrition counts leavers, and most leavers are not Active.

		The other charts on these dashboards all carry `status = Active` because they
		count active staff. This one cannot: 130 of the 130 rows on the attrition line
		are people who have left, so an Active scope would open a list of 0 for the
		largest bar on the chart while the chart above it says 130.
		"""
		base = self._drilldown().get("base_filters") or {}
		self.assertNotIn(
			"status",
			base,
			f"base_filters carries {base}, which excludes the leavers this chart's "
			"attrition line is about",
		)

	def _drilldown(self):
		"""What is actually on the chart document, not what the module would write.

		Asked of the document because the document is what the browser reads. A
		drilldown module that is right and a chart document that was never written
		describe the same working chart and a completely dead one.
		"""
		self.assertTrue(
			frappe.db.exists("Dashboard Chart", "Hiring vs Attrition Count"),
			"the chart is not on this site",
		)
		doc = frappe.get_doc("Dashboard Chart", "Hiring vs Attrition Count")
		self.assertEqual(doc.chart_type, "Custom")
		spec = (frappe.parse_json(doc.custom_options) or {}).get("drilldown") or {}
		self.assertTrue(
			spec.get("buckets"),
			"the chart document carries no drilldown, so the chart is a picture",
		)
		self.assertEqual(spec.get("doctype"), "Employee")
		return spec


def _mapped_month(label):
	"""`'Sep 2026'` -> `(2026, 9)`, and nothing else.

	A hand-written month table, which is the one thing this test must not have. It
	compares this app's windows against HRMS's labels, so a *second* idea of what
	`Sep 2026` means could agree with both and still be wrong - a table that translated
	`Mai` as `May` would pass on this site and fail for a German user. So it reads the
	one fact that does not vary: the year is the year, and the month is the position.

	Which is also why the label is only ever split on the space. A month name is the
	only part that can be localised, and the year is not.
	"""
	parts = label.split()
	if len(parts) != 2:
		raise AssertionError(f"{label!r} is not a month label this test can read")
	name, year = parts
	months = [
		"Jan",
		"Feb",
		"Mar",
		"Apr",
		"May",
		"Jun",
		"Jul",
		"Aug",
		"Sep",
		"Oct",
		"Nov",
		"Dec",
	]
	if name not in months:
		raise AssertionError(f"{label!r} names a month this test does not know")
	return (int(year), months.index(name) + 1)


class TestTheSplit(FrappeTestCase):
	"""`Monthly Joiners` and `Monthly Attrition` - one line each.

	The two replaced HRMS's `Hiring vs Attrition Count`, which drew both on one axis.
	Split for two reasons that turned out to be the same: 130 leavers in September set
	that axis so hard a month with nine joiners drew nine pixels, and a two-line chart
	could not be clicked because frappe-charts keeps no per-series identity on a Line.

	So what is worth pinning here is what the split bought: **every month is
	clickable**. That is as much a claim about the absence of `by_series` as about the
	windows, and it is invisible from the chart - a single-series chart and a
	two-series one that happens to resolve look identical until the cursor is a pixel
	from a line.
	"""

	def _chart(self, name):
		"""`(field, labels, values, spec, doc)` for one of the two.

		Counted here rather than read off the chart document, because the point is
		that the chart and the windows agree - asking the document would only prove it
		was written once.
		"""
		if name == "Monthly Joiners":
			from sharm_dreams_group.hr.dashboard_chart_source.monthly_joiners import (
				monthly_joiners as module,
			)
		else:
			from sharm_dreams_group.hr.dashboard_chart_source.monthly_attrition import (
				monthly_attrition as module,
			)
		self.assertTrue(
			frappe.db.exists("Dashboard Chart", name),
			f"{name} is not on this site, so the split did not happen",
		)
		doc = frappe.get_doc("Dashboard Chart", name)
		spec = (frappe.parse_json(doc.custom_options) or "{}").get("drilldown") or {}
		labels, values = module.counts_by_month()
		return module.FIELD, labels, values, spec, doc

	def test_each_split_chart_lists_exactly_what_it_draws(self):
		"""The per-month check again, on each half.

		Per month rather than per total, because the totals summed correctly while nine
		of thirteen windows listed the wrong people. And one of these two charts has a
		single non-zero month, where a total-only check would say nothing at all about
		the other one.
		"""
		for name in ("Monthly Joiners", "Monthly Attrition"):
			field, labels, values, spec, _doc = self._chart(name)
			buckets = {b["label"]: b for b in spec["buckets"]}
			with self.subTest(chart=name):
				self.assertEqual(
					sorted(buckets), sorted(labels), f"{name}'s windows are not its months"
				)
				for label, drawn in zip(labels, values):
					with self.subTest(month=label):
						bucket = buckets[label]
						opened = frappe.db.count(
							"Employee",
							{field: ["between", (bucket["date_from"], bucket["date_to"])]},
						)
						self.assertEqual(
							opened,
							drawn,
							f"{label} draws {drawn} and its list holds {opened}",
						)

	def test_a_split_chart_carries_no_per_series_map(self):
		"""One line, so every month resolves without measuring anything.

		A `by_series` map is what lets the widget work out which line was tapped, and
		it is also what makes a month refuse when two lines are close: on the chart this
		replaces, 0.95px per person put eleven of thirteen months inside the refusal
		distance. With one series there is nothing to confuse it with.

		Asserted as the *absence* of a key, which no amount of looking at the chart
		would reveal.
		"""
		for name in ("Monthly Joiners", "Monthly Attrition"):
			field, _labels, _values, spec, _doc = self._chart(name)
			with self.subTest(chart=name):
				self.assertIsNone(
					spec.get("by_series"),
					f"{name} declares a per-series map, so the widget has to work out "
					"which line was tapped when there is only one",
				)
				self.assertEqual(spec.get("field"), field, "the source and the map disagree")

	def test_a_split_chart_counts_people_not_money(self):
		"""`currency` defaults to EGP and the tooltip reads it.

		Every chart in this table counts people, so an EGP on one hovers as "EGP 9
		people". Only reachable on a chart inserted since the last migrate - the update
		path is unaffected because an explicit set outlives the default, which is why
		`create_custom_chart_sources` has to clear it on *both* paths.
		"""
		for name in ("Monthly Joiners", "Monthly Attrition"):
			_field, _labels, _values, _spec, doc = self._chart(name)
			with self.subTest(chart=name):
				self.assertIsNone(
					doc.currency, f"{name} carries {doc.currency!r}, so it hovers as money"
				)

	def test_a_split_chart_is_not_scoped_to_active_staff(self):
		"""Every row on the attrition chart is a leaver, so Active would list nobody.

		The one deliberate absence of `base_filters` on these dashboards. `Monthly
		Joiners` shares it for a different reason - it counts people who joined and may
		since have left - and both are checked because the mistake is the plausible one:
		every other chart here carries `status: Active`, and copying that habit would
		silently empty the largest bar on the page.
		"""
		for name in ("Monthly Joiners", "Monthly Attrition"):
			_field, _labels, values, spec, _doc = self._chart(name)
			with self.subTest(chart=name):
				self.assertNotIn(
					"status",
					spec.get("base_filters") or {},
					f"{name} is scoped by status, and this chart counts people who "
					"have since left",
				)
				self.assertTrue(any(values), f"{name} is flat at zero; the split drew nothing")

	def test_the_split_charts_are_on_the_people_band(self):
		"""A source nobody places draws on no page.

		Read off the *stored* dashboard rather than off `SECTION_LAYOUT`, because that
		table is a table of overrides and is deliberately incomplete - a band with no
		entry falls back to the desk's own defaults, so `Contracts` is simply not a
		key in it. Reading the stored `chart_options` also asks the stronger question:
		not whether the name is in the table, but whether it reached the page.

		On `People` specifically: the reasoning behind the split is reading two trends
		side by side, and one of them landing under Payroll would undo that whatever
		else were true.
		"""
		from sharm_dreams_group.hr.test_dashboard import _layout_sections, _section_charts

		placed = {}
		for section in _layout_sections("SDG Workforce"):
			for item in _section_charts(section):
				placed.setdefault(item, []).append(section["title"])

		for name in ("Monthly Joiners", "Monthly Attrition"):
			with self.subTest(chart=name):
				self.assertEqual(
					placed.get(name),
					["People"],
					f"{name} is on {placed.get(name) or 'no page'}",
				)

	def test_the_two_line_chart_is_no_longer_placed(self):
		"""The chart this replaced is off the page, not merely out of favour.

		Left on it would sit beside the two halves it was split into - the same figures
		twice, and the y-axis problem visible in two places at once.
		"""
		from sharm_dreams_group.hr.test_dashboard import _layout_sections, _section_charts

		placed = [
			item
			for section in _layout_sections("SDG Workforce")
			for item in _section_charts(section)
		]
		self.assertNotIn(
			"Hiring vs Attrition Count",
			placed,
			"the two-line chart is still placed, beside the two halves of itself",
		)
