# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Strips the per-user Company dynamic filter off the desk dashboard widgets.

Covers `Dashboard Chart` and `Number Card`, which store their filters the same
way and were both shipped by ERPNext/HRMS with `company` pinned to the
*viewer's* default.

Run with:
    bench --site <site> execute sharm_dreams_group.dashboard_widgets.remove_company_filters

Why the group does not want it: an employee moving between the ~30 group
companies has no single correct company, so a widget scoped to the viewer's
default shows one company's slice and looks like the group's figures are wrong.
The widgets should aggregate across all of them.

Why this is not a one-off `bench execute`: an ERPNext or HRMS upgrade that
re-imports these standard records would silently put the filter back, with no
error to notice.  So it is re-applied from `setup.after_migrate`, for the same
reason the workspace links are.

Why `frappe.db.set_value` and not `doc.save()`: on a site with
`developer_mode=1`, both `DashboardChart.on_update` and `NumberCard.on_update`
call `export_to_files()`, so saving a standard record rewrites the owning app's
JSON on disk - it would dirty the `erpnext` and `hrms` checkouts on every
migrate.  `db.set_value` bypasses the controller and leaves the upstream files
alone.

Both `dynamic_filters_json` (the per-user default) and `filters_json` (a
hardcoded pin) are cleaned, because both scope the widget to one company and the
requirement is that no widget needs a company to be set.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import frappe

# The key/report-filter name Frappe uses for the company filter.  Report-backed
# records key `dynamic_filters_json` by this name; doctype-backed ones use it as
# the fieldname at index 1 of each filter row.
COMPANY_KEY = "company"

# The desk widgets to scrub, in the order they are applied.
WIDGET_DOCTYPES = ("Dashboard Chart", "Number Card")

# Reports that a Dashboard Chart uses, and that cannot render without a Company
# no matter what the chart asks for, because they throw inside their own Python
# before any filter is applied.  Empty, and expected to stay empty: the three
# source files that caused it are rewritten by `upstream_patches.py`.
#
# The check is still worth having.  An ERPNext or HRMS upgrade reverts those
# files, `after_migrate` re-applies the patch, and if a future report is added
# that demands a company it shows up here as `unknown_company_required` instead
# of becoming a chart that is permanently blank.
COMPANY_REQUIRED_REPORTS: dict[str, str] = {}

# The six charts that needed a company before the patches, and the report each
# one is backed by.  Not used to decide behaviour - it is the list a reader wants
# when asking "what did the patch actually buy me", and it is what the patch
# test asserts against so a reverted file is caught.
UNBLOCKED_BY_PATCH = {
	"Delivery Note Trends": "Top Customers",
	"Item-wise Sales History": "Item-wise Annual Sales",
	"Monthly Attendance Sheet": "Attendance Count",
	"Purchase Order Trends": "Purchase Order Trends",
	"Purchase Receipt Trends": "Top Suppliers",
	"Sales Order Trends": "Sales Order Trends",
}

# The client-side allow list lives in dashboard_widgets.js because it has to be
# evaluated in the browser, where it neutralises the report's `reqd` company
# filter.  It is read back out of that file rather than duplicated here, so the
# two lists cannot drift apart silently: `verify_company_optional` checks that
# they partition the report-backed charts exactly.
JS_PATH = ("public", "js", "dashboard_widgets.js")


def company_optional_reports() -> set[str]:
	"""The report names the client-side patch relaxes, read out of the JS.

	Parsed rather than restated so that adding a report to the JS and forgetting
	Python (or the reverse) is a failed test, not a silently blank chart.

	Whole-line comments inside the Set are dropped before the names are read.  They
	have to be: the Set is commented - it says which entries came from a patch and
	which were verified on the panel - and a naive "every quoted string in this span"
	turns a phrase like `the "needs a company" bucket` into a report called
	`needs a company`.  That happened, and it surfaced as a chart whose report was
	"relaxed" under a name that does not exist, which is a confusing way to learn
	that a parser cannot tell a comment from an entry.

	Only lines whose first non-whitespace characters are `//` are dropped, so a `//`
	inside a string on a line of its own would survive; report names do not contain
	one, and stripping mid-line would risk corrupting a legitimate value.
	"""
	import re

	source = frappe.get_app_path("sharm_dreams_group", *JS_PATH)
	with open(source) as handle:
		text = handle.read()
	match = re.search(r"COMPANY_OPTIONAL_REPORTS\s*=\s*new Set\(\[(.*?)\]\)", text, re.S)
	if not match:
		raise ValueError(f"COMPANY_OPTIONAL_REPORTS not found in {source}")
	body = "\n".join(
		line for line in match.group(1).splitlines() if not line.lstrip().startswith("//")
	)
	return set(re.findall(r'"([^"]+)"', body))


def panel_reports() -> list[tuple[str, str]]:
	"""`(label, report_name)` for every report the side panel's Reports band lists.

	The band is the reason the company relax covers the report *page* and not just
	charts, so the allow list has a second consumer and needs a second reason to be
	trustworthy.  Read from the panel spec rather than restated, so a report added
	to the band is covered by the same probe.
	"""
	from sharm_dreams_group.setup import REPORT_PANEL_GROUPS

	out: list[tuple[str, str]] = []
	for _title, groups in REPORT_PANEL_GROUPS:
		for _sub, links in groups:
			for label, link_type, name in links:
				if link_type == "Report":
					out.append((label, name))
	return out


# The required filters `verify_panel_reports` has to supply before it can reach a
# verdict on Company, keyed by report.
#
# Every one of these is a filter the report declares as mandatory in its own `.js`
# and which the probe cannot invent: the desk is saying "a person must choose this".
# Without them the probe fails before reaching any Company logic and files the
# report under "needs another filter", which is a misleading answer - a reader
# would conclude the Company relax had not reached it, when it had.
#
# An explicit map rather than a parser, and the reason is worth recording.  The
# obvious general fix is to read `default:` out of each report's `.js`; it does not
# work, because the filters in here are `reqd` with *nothing* beside them.  Guessing
# from `options` would be inventing an answer and then reporting it as a finding.
#
# The values are ones that work, not the only ones: Employee Analytics takes any
# `parameter`, Employee Birthday any month name, Employee Leave Balance any date
# range.  They are computed rather than written down because a hard-coded fiscal
# year or an `as_on_date` would silently stop matching once the year rolled over,
# and the failure would read as "this report now needs a company".
def panel_companion_filters(report: str) -> dict:
	"""The extra filters `report` insists on before it will answer a Company question."""
	today = frappe.utils.nowdate()
	fiscal_year = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name")
	dates: dict = {}
	if fiscal_year:
		row = frappe.get_all(
			"Fiscal Year",
			filters={"name": fiscal_year},
			fields=["year_start_date", "year_end_date"],
			limit=1,
		)
		if row:
			dates = {"from_date": row[0].year_start_date, "to_date": row[0].year_end_date}

	return {
		"Employee Analytics": {"parameter": "Department"},
		"Employee Birthday": {"month": "May"},
		"Trial Balance": {"fiscal_year": fiscal_year} if fiscal_year else {},
		"Employee Leave Balance": dates,
		# "Loan Outstanding Report" used to need {"as_on_date": today}.  It is a
		# `lending` report, `lending` is not installed on this site, and a default for a
		# report that is not there is a claim about nothing.
		# Month-based, and the month/year pair has to agree with the filter's own
		# options or the report rejects it before reaching any Company logic.
		"Monthly Attendance Sheet": {"filter_based_on": "Month", "month": "May", "year": today[:4]},
	}.get(report, {})


def _strip(parsed: dict | list):
	"""Return `(filtered, changed)` for one parsed filters field."""
	if isinstance(parsed, dict):
		# Report-backed: a flat {"company": <js expr>, ...} of report filters.
		if COMPANY_KEY not in parsed:
			return parsed, False
		return {k: v for k, v in parsed.items() if k != COMPANY_KEY}, True

	if isinstance(parsed, list):
		# Doctype-backed: rows of [doctype, fieldname, operator, value].
		kept = [
			row
			for row in parsed
			if not (isinstance(row, list | tuple) and len(row) > 1 and row[1] == COMPANY_KEY)
		]
		return kept, len(kept) != len(parsed)

	return parsed, False


def remove_company_filters(doctype: str = WIDGET_DOCTYPES, commit: bool = True) -> dict:
	"""Remove every company filter from every record of `doctype`.

	`doctype` is one name or an iterable of them, so a caller can scrub a single
	widget type.  Idempotent: a record with nothing to remove is not written at
	all, which is what makes it safe to call on every migrate.  Returns a
	per-doctype summary and prints it - this is the kind of edit that is worth
	being able to audit afterwards.
	"""
	names = (doctype,) if isinstance(doctype, str) else tuple(doctype)
	summary: dict[str, dict] = {}

	for name in names:
		summary[name] = _scrub(name)

	if commit:
		frappe.db.commit()

	return summary


def _scrub(doctype: str) -> dict:
	"""Strip one doctype, returning its counts.

	Both halves of a widget's filters are cleaned, not just the dynamic one: a
	company pinned in `filters_json` scopes the widget just as hard, and the one
	that was actually present here (`Appraisal Overview`, pinned to
	"The Coffee Company") named a company this group does not have, so its chart
	was permanently empty.
	"""
	records = frappe.get_all(
		doctype, fields=["name", "filters_json", "dynamic_filters_json"], limit_page_length=0
	)

	changed: list[str] = []
	skipped: list[str] = []

	for record in records:
		wrote = False
		for field in ("filters_json", "dynamic_filters_json"):
			raw = record.get(field)
			if not raw:
				continue

			try:
				parsed = json.loads(raw)
			except ValueError:
				# Not a shape this can reason about.  Left alone rather than
				# overwritten, and surfaced so it is not silently ignored.
				if field not in skipped:
					skipped.append(record.name)
				continue

			filtered, did_change = _strip(parsed)
			if not did_change:
				continue

			frappe.db.set_value(
				doctype,
				record.name,
				field,
				json.dumps(filtered),
				update_modified=False,
			)
			wrote = True

		if not wrote:
			continue

		if doctype == "Dashboard Chart":
			# Only Dashboard Chart caches its rendered data, under this key.
			# Number Card recomputes on every request and has no cache to drop.
			frappe.cache.delete_value(f"chart-data:{record.name}")
		changed.append(record.name)

	summary = {"scanned": len(records), "changed": len(changed), "skipped": len(skipped)}
	_report(doctype, summary, changed, skipped)
	return summary


def _report(doctype: str, summary: dict, changed: list[str], skipped: list[str]) -> None:
	"""Print what `_scrub` did to one doctype."""
	if not (changed or skipped):
		print(f"{doctype}: no company filter on any of {summary['scanned']} records")
		return
	print(f"{doctype}: removed the company filter from {summary['changed']} of {summary['scanned']}")
	for name in changed:
		print(f"  updated  {name}")
	for name in skipped:
		print(f"  SKIPPED (unparseable filters field)  {name}")


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
# The JS expressions the shipped charts actually use in dynamic_filters_json.
# Anything not listed here is passed through untouched, which only makes a
# probe noisier - it never makes it wrongly report success.
_DYNAMIC_EXPR = {
	"erpnext.utils.get_fiscal_year()": lambda: frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name"),
	"frappe.sys_defaults.fiscal_year": lambda: frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name"),
	"frappe.datetime.nowdate()": frappe.utils.nowdate,
	"frappe.datetime.now_date()": frappe.utils.nowdate,
	"frappe.datetime.add_months(frappe.datetime.nowdate(), -1)": lambda: frappe.utils.add_months(
		frappe.utils.nowdate(), -1
	),
	"frappe.datetime.add_months(frappe.datetime.nowdate(), -12)": lambda: frappe.utils.add_months(
		frappe.utils.nowdate(), -12
	),
	'frappe.defaults.get_user_default("year_start_date")': lambda: None,
	'frappe.defaults.get_user_default("year_end_date")': lambda: None,
}

# Operators that make a list row a plain equality-style filter, which can be
# replayed server-side.  Timespan/Relative are left out: they are resolved by
# the desk, and a chart that uses one is not company-dependent anyway.
_REPLAYABLE = {"=", "!=", "in", "not in"}


def desk_filters(chart: str) -> dict:
	"""The filters the desk would send for `chart`, with dynamic expressions resolved.

	`filters_json` is the static half; `dynamic_filters_json` is the dynamic
	half, and only the company entry was ever removed from the latter - so the
	company key is simply absent here and the caller supplies it.
	"""
	out: dict = {}
	for blob in (
		frappe.db.get_value("Dashboard Chart", chart, "filters_json"),
		frappe.db.get_value("Dashboard Chart", chart, "dynamic_filters_json"),
	):
		if not blob:
			continue
		try:
			parsed = json.loads(blob)
		except ValueError:
			continue
		if isinstance(parsed, dict):
			for key, value in parsed.items():
				expr = _DYNAMIC_EXPR.get(value.strip()) if isinstance(value, str) else None
				out[key] = expr() if expr else value
		elif isinstance(parsed, list):
			for row in parsed:
				if (
					isinstance(row, list)
					and len(row) == 4
					and isinstance(row[2], str)
					and row[2] in _REPLAYABLE
				):
					expr = _DYNAMIC_EXPR.get(row[3].strip()) if isinstance(row[3], str) else None
					out[row[1]] = expr() if expr else row[3]
	return out


@contextmanager
def quiet_error_log():
	"""Run a probe that is *expected* to fail without leaving a trace.

	`frappe.log_error` has no suppression flag - it returns early only when there is
	no database - and `frappe.desk.query_report.run` calls it for every report that
	raises.  These verifiers run reports that are supposed to fail (that is the
	question being asked: does this one still demand a Company?), so each probe
	wrote a row.

	The cost was not tidiness.  It made the Error Log useless for its actual job: a
	real failure reported as "Could not generate PDF - check the Error Log" sent the
	reader to a log holding 516 rows of this app's own diagnostics, none of them
	theirs, and no PDF entry among them.  A diagnostic that makes the thing it
	diagnoses harder to find is worse than no diagnostic.

	Rows are identified by name rather than by timestamp because Error Log names are
	random, so a same-second tie cannot hide a row that was not created here, and a
	concurrent genuine failure is left alone unless this probe actually created it.

	No commit: under `run-tests` the whole thing rolls back anyway, and under
	`bench execute` the command's own commit makes the delete stick.
	"""
	before = set(frappe.get_all("Error Log", pluck="name", limit_page_length=0))
	try:
		yield
	finally:
		created = [
			name
			for name in frappe.get_all("Error Log", pluck="name", limit_page_length=0)
			if name not in before
		]
		if created:
			frappe.db.delete("Error Log", {"name": ["in", created]})


def _runs(chart: str, report_name: str, company: str) -> tuple[bool, str]:
	"""Run a chart's report with `company` blank or set.  Returns `(ok, detail)`."""
	from frappe.desk.query_report import run

	filters = desk_filters(chart)
	filters[COMPANY_KEY] = company
	try:
		with quiet_error_log():
			result = run(report_name, filters, ignore_prepared_report=True)
	except Exception as error:  # the point is to report, not to raise
		return False, f"{type(error).__name__}: {str(error).splitlines()[0][:70]}"
	result = result[0] if isinstance(result, tuple) else result
	return True, f"{len((result or {}).get('result') or [])} rows"


def verify_company_optional() -> dict:
	"""Report which report-backed charts still need a Company to render.

	Run:
	    bench --site <site> execute sharm_dreams_group.dashboard_widgets.verify_company_optional

	The chart's own filter is irrelevant here - the point is whether the
	underlying report tolerates a blank Company, which is what the client-side
	relax in `public/js/dashboard_widgets.js` depends on.  A report that has
	started demanding a Company again shows up as `needs_company` and is worth
	acting on; anything unexpected shows up as `broken_either_way`.
	"""

	reference = frappe.db.get_single_value("Global Defaults", "default_company")
	charts = frappe.get_all(
		"Dashboard Chart",
		filters={"chart_type": "Report"},
		fields=["name", "report_name"],
		limit_page_length=0,
		order_by="name",
	)

	optional, needs_company, broken_either_way = [], [], []
	for chart in charts:
		blank_ok, blank_detail = _runs(chart.name, chart.report_name, "")
		if blank_ok:
			optional.append(chart.name)
			continue
		set_ok, set_detail = _runs(chart.name, chart.report_name, reference)
		(needs_company if set_ok else broken_either_way).append(
			(chart.name, chart.report_name, blank_detail, set_detail)
		)

	print(f"report-backed charts: {len(charts)}")
	print(f"  run without a company : {len(optional)}")
	print(f"  need a company        : {len(needs_company)}")
	for name, report, blank, _ in needs_company:
		known = "" if report in COMPANY_REQUIRED_REPORTS else "   <== NOT in COMPANY_REQUIRED_REPORTS"
		print(f"      {name} -> {report}: {blank}{known}")
	print(f"  fail either way       : {len(broken_either_way)}")
	for name, report, blank, detail in broken_either_way:
		print(f"      {name} -> {report}: {blank} | with company: {detail}")

	unknown = [r for _, r, _, _ in needs_company if r not in COMPANY_REQUIRED_REPORTS]
	stale = [r for r in COMPANY_REQUIRED_REPORTS if r not in {r for _, r, _, _ in needs_company}]

	# The two lists have to partition the report-backed charts: everything the
	# client-side patch claims to relax must actually tolerate a blank company,
	# and everything it does not must be the documented exception.
	relaxed = company_optional_reports()
	still_needed = {report for _, report, _, _ in needs_company}
	# A report in the client-side list that in fact demands a Company is the
	# failure that matters: the chart would render as a permanent "Please set
	# filters" with no obvious cause.
	misclassified = sorted(relaxed & still_needed)
	if misclassified:
		print(f"  MISCLASSIFIED (relaxed client-side but the report demands one): {misclassified}")
	unknown_relaxed = sorted(relaxed - {c.report_name for c in charts} - {n for _l, n in panel_reports()})
	if unknown_relaxed:
		print(f"  RELAXED BUT UNUSED (no chart and no panel row points at them): {unknown_relaxed}")

	return {
		"optional": optional,
		"needs_company": needs_company,
		"broken_either_way": broken_either_way,
		"unknown_company_required": unknown,
		"stale_company_required": stale,
		"misclassified": misclassified,
		"relaxed": sorted(relaxed),
		"relaxed_but_unused": unknown_relaxed,
	}


def verify_panel_reports() -> dict:
	"""Which of the side panel's reports still need a Company, and why.

	Run:
	    bench --site <site> execute sharm_dreams_group.dashboard_widgets.verify_panel_reports

	The counterpart to `verify_company_optional`, for the other consumer of the same
	allow list: a report opened from the Reports band.  Same probe, same shape, but
	the classification is three-way rather than two, because a panel report fails
	for reasons that have nothing to do with Company:

	    optional        - runs with a blank Company
	    needs_company   - the failure names Company, so relaxing the filter is wrong
	    needs_companion - the failure is some *other* mandatory filter, so this says
	                      nothing about Company either way

	That third bucket is why this does not simply reuse the chart verifier's
	two-way split.  Nine of the 32 land there - six of them because the probe sends
	no fiscal year, date range or employee, which those reports insist on - and
	counting them as "needs a company" would send someone off to patch a report whose
	Company handling is fine.

	`panel_companion_filters` supplies the filters each report insists on, so the
	probe can reach a verdict at all; see the note on it for why it is a map and not
	a parser.

	A report is left out of `COMPANY_OPTIONAL_REPORTS` unless it is in `optional`,
	which is asserted in test_dashboard_widgets.py.  So this list and that one cannot
	drift apart quietly.
	"""
	optional, needs_company, needs_companion, missing = [], [], [], []

	for label, report in panel_reports():
		if not frappe.db.exists("Report", report):
			missing.append(report)
			continue
		# The companion filters are what a reader has to choose before the report can
		# run at all; without them the probe would answer a question about those
		# rather than about Company.
		filters = {COMPANY_KEY: "", **panel_companion_filters(report)}
		ok, detail = _runs_report(report, filters)
		if ok:
			optional.append(report)
		elif COMPANY_KEY in detail or "company" in detail.lower():
			needs_company.append((label, report, detail))
		else:
			needs_companion.append((label, report, detail))

	relaxed = company_optional_reports()
	in_panel = {name for _label, name in panel_reports()}
	# The failure that matters: relaxed client-side, but the report demands a Company.
	misclassified = sorted(name for name in relaxed & in_panel if name in {r for _l, r, _d in needs_company})
	# Relaxed for a chart but no longer on the panel is fine - it has a chart.
	not_relaxed = sorted(name for name in optional if name not in relaxed)

	total = len(in_panel)
	print(f"panel reports: {total}")
	print(f"  run with no Company : {len(optional)}")
	print(f"  still need one      : {len(needs_company)}")
	for label, report, detail in needs_company:
		print(f"      {label:42s} {report:34s} {detail}")
	print(f"  need another filter : {len(needs_companion)}")
	for label, report, detail in needs_companion:
		print(f"      {label:42s} {report:34s} {detail}")
	if missing:
		print(f"  MISSING FROM THE SITE: {missing}")
	print(f"  MISCLASSIFIED (relaxed but demands a Company): {misclassified}")
	print(f"  optional but NOT relaxed client-side        : {not_relaxed}")

	return {
		"optional": optional,
		"needs_company": needs_company,
		"needs_companion_filter": needs_companion,
		"misclassified": misclassified,
		"optional_but_not_relaxed": not_relaxed,
		"missing": missing,
	}


def _runs_report(report_name: str, filters: dict) -> tuple[bool, str]:
	"""`run` one report with `filters`, reporting rather than raising.

	Deliberately sends *only* the filters given: no fiscal year, no date range, no
	employee.  A report that insists on one of those fails, which is what puts it in
	`needs_companion_filter` rather than being mistaken for a Company dependency.
	"""
	from frappe.desk.query_report import run

	try:
		with quiet_error_log():
			result = run(report_name, filters, ignore_prepared_report=True)
	except Exception as error:  # the point is to report, not to raise
		return False, f"{type(error).__name__}: {str(error).splitlines()[0][:70]}"
	result = result[0] if isinstance(result, tuple) else result
	return True, f"ok, {len((result or {}).get('result') or [])} rows"
