# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, see license.txt

"""Employee dashboard: one metric dispatcher behind a set of Number Cards.

Why a dispatcher rather than a function per card: the Number Card sends its own
`filters_json` verbatim to a `Custom` card's method, so a single method that reads
a `metric` key serves every card.  Adding a card is then a row in `METRICS`, not
another endpoint.  This mirrors `hr/penalty.headcount`, which does the same thing
for the penalty levels.

Which metrics exist is decided by what this group actually has data for, not by
what HRMS ships.  Checked on this site: Attendance has 0 rows, as do Job Applicant,
Job Opening, Employee Onboarding, Employee Grade, Goal, Employee Advance and
Grievance Type - so the stock cards for those read zero and say nothing.  What
*is* populated, and therefore what is worth a card: employment status (310), the
13 companies, 130 leavers, 172 social-insurance numbers, 145 insured persons,
171 national-ID expiry dates and the payroll itself.

The payroll figures read the submitted slips of the period the card names, so a
card shows last month's numbers rather than drifting: the period comes from the
card's own filters, defaulting to the most recently submitted run.

Every entry point is guarded.  Client-side hiding is a courtesy - a card is
reachable by URL with the session cookie alone - so these refuse rather than
quietly answering.
"""

from __future__ import annotations

import re
import sys

import frappe
from frappe.utils import add_days, add_months, add_years, cint, flt, getdate, nowdate

# Contract types that are limited-term, matched by name rather than by a flag.
#
# `SDG Contract Type` has an `is_limited_term` check, and it is unset on all five
# types the site holds - including both of the ones called "Limited" - so reading
# the flag would report zero limited-term staff on a workforce that is mostly
# limited-term.  The seed also wrote each term twice, as "Limited" and "Limited
# Contract" (likewise "Unlimited"), so the prefix has to match both spellings.
#
# This is a name convention, not data, and it is the reason the contract-due
# cards cannot be made correct by filling in dates alone: someone has to decide
# which types are limited-term and tick the flag.  Until then the convention is
# what there is, and it is narrow - "Limited" appears in no other type name.
LIMITED_TERM_PREFIX = "Limited%"

# The Date Frappe substitutes for a blank one when comparing (`ifnull(field,
# '0001-01-01')` in `DatabaseQuery`).  So a comparison on a Date field only means
# "and dated" if it *starts* the day after that: a bare `< today` also matches
# every row whose date was never filled in, and on this app's contract end date
# that is every row.  Every "before such-and-such" filter on a Date starts here
# instead of at a `<`, which cannot express the second half of the question at all.
# Pinned by `test_no_drilldown_uses_a_bare_date_cutoff`.
DATED_FLOOR = "0001-01-02"

# Stock charts borrowed from HRMS rather than rebuilt. Named here so a section can
# place one, and the list is the single place a reused chart is named.
#
# Each was checked for data before being listed, by fill rate rather than row count:
# a `Group By` on a field null on every row draws one empty slice, which reads as a
# fault rather than as "not yet". `Employees by Grade` is the live example of what
# that rules out - grade is null on all 310 employees, and ERPNext's chart for it
# draws a single empty slice.
#
# Two of these are `Sum` on Salary Slip grouped by a field, which is the only place
# on these dashboards where a bar is money rather than a headcount, and the only
# place a figure can be reached by designation rather than by company.
REUSED_CHARTS = (
	# 180 active staff bucketed into fourteen five-year age bands. The only Custom
	# chart here: it computes its own bands in Python, so it has no group-by field
	# and is clickable only through the drilldown `setup.create_reused_custom_chart_
	# drilldowns` writes onto its `custom_options`.
	"Employees by Age",
	# 110 distinct designations across 179 active staff. Twenty bars, not 110 -
	# `number_of_groups` on the chart document takes the largest and the axis can
	# carry them.
	"Designation Wise Employee Count",
	# Net pay by designation and by department, last month: 170 submitted slips
	# worth EGP 3.07m. Money by anything other than company, which nothing else on
	# these dashboards gives.
	#
	# Both carry a `timespan` and a `number_of_groups` written by
	# `setup.tune_reused_charts`, and neither is left as HRMS shipped it. `last month`
	# resolves to the *calendar* month before today (2026-09-01..30 on the 4th of
	# October), which is the one payroll run on file here - so the stock filter is
	# right by luck and stops being right the moment a second month is run. See
	# `setup.latest_payroll_window`.
	"Designation Wise Salary(Last Month)",
	"Department Wise Salary(Last Month)",
)


# How each band is arranged: (kind, items, options) blocks, in the order they render.
#
# This exists because "all the cards, then all the charts" stopped being readable.  A
# band of eleven cards and one chart drew eleven cards and then one chart, so the single
# chart - the only thing on the band that shows *which* company is behind the gap - was
# a full screen below the numbers, and a reader had to scroll past all eleven to reach
# it.  The same shape put ten payroll cards in front of six payroll charts.
#
# A block is `(kind, items, options)`.  `kind` is "cards" or "charts"; `options` carries
# `columns` and, for charts, `height`.  Both are optional, and a band with no entry here
# renders as one card block then one chart block at the desk's own defaults - which is
# why this is a table rather than a change to the shape of DASHBOARD_SECTIONS.  Several
# places unpack those 3-tuples and none of them wants a layout.
#
# Sizing is per block, not per band, because the bands want different shapes: a row of
# gap counts reads better six across than four (they are one-word labels and a number,
# and four across leaves each card a third wider than it needs), while a row of charts
# wants three across at 280 so a company name on the x-axis is not rotated.
#
# `test_section_layout_partitions_its_sections` is what stops this drifting from
# DASHBOARD_SECTIONS: every card and every chart has to appear exactly once across a
# band's blocks, so adding a card to the band without placing it here fails the build
# rather than quietly not being drawn.
SECTION_LAYOUT: dict[str, tuple] = {
	# Six movement figures, the two distributions beside them, then the year's
	# movement and the two per-company charts.
	#
	# `Monthly Joiners` and `Monthly Attrition` replace HRMS's `Hiring vs Attrition
	# Count`, which drew them as one chart on one axis. Split for two reasons that
	# turned out to be the same reason:
	#
	# 130 leavers in September set the shared y-axis, so a month with nine joiners
	# drew nine pixels tall and the hiring line read flat - a chart about hiring that
	# showed no variation in hiring. And a two-line chart could not be clicked:
	# frappe-charts keeps no per-series identity on a Line, so the widget infers it
	# from pixel distance, and at 0.95px per person eleven of thirteen months were
	# too close to tell apart and honestly refused. One line each, one axis each, and
	# every month is clickable because one line has no second line to be confused
	# with.
	#
	# Two across rather than full width: neither needs a wide axis to be read against
	# the other any more, and thirteen monthly labels are legible at half the page
	# where they were not at a third.
	"People": (
		(
			"cards",
			(
				"Active Headcount",
				"Leavers",
				"Joined This FY",
				"Joined (90 days)",
				"Tenure 5+ Years",
				"People of Determination",
			),
			{"columns": 6},
		),
		(
			"charts",
			(
				"Headcount by Company",
				"Headcount by Department",
				"Employment Category",
			),
			{"columns": 3, "height": 260},
		),
		(
			"charts",
			("Monthly Joiners", "Monthly Attrition"),
			{"columns": 2, "height": 280},
		),
		(
			"charts",
			("Leavers by Company", "People of Determination by Company"),
			{"columns": 2, "height": 240},
		),
	),
	# Eleven gap counts: six, the chart that says which company, then the remaining
	# five.  Six across rather than four because these are the shortest labels on any
	# dashboard and they were being stretched to fill a card for the sake of it.
	"Records To Complete": (
		(
			"cards",
			(
				"No SI Number",
				"No ID Expiry",
				"ID Expired",
				"ID Expiring (90d)",
				"No Department",
				"No Designation",
			),
			{"columns": 6},
		),
		("charts", ("Compliance Gaps by Company",), {"columns": 3, "height": 260}),
		(
			"cards",
			(
				"ID Expiring (30d)",
				"No Contract Start Date",
				"No Contract Type",
				"No Contract End Date",
				"Penalty Cases",
			),
			{"columns": 5},
		),
	),
	# Ten payroll figures then six charts.  Five across, because "Average Gross Salary"
	# is the longest label on the dashboard and four across was clipping it.
	"Payroll": (
		(
			"cards",
			(
				"Payroll Net",
				"Payroll Gross",
				"Employer Cost",
				"Average Net Salary",
				"Average Gross Salary",
			),
			{"columns": 5},
		),
		("charts", ("Payroll Net by Company", "Payroll Gross by Company"), {"columns": 2, "height": 240}),
		(
			"cards",
			("Lowest Net Salary", "Highest Net Salary", "Loan Repayment", "Employees On A Loan", "Paid This Payroll"),
			{"columns": 5},
		),
		(
			"charts",
			(
				"Employer Cost by Company",
				"Recharged Net by Charging Company",
				"Recharged Gross by Paying Company",
				"Loans Repaid by Company",
			),
			{"columns": 2, "height": 240},
		),
	),
	# Who the group is made of.  Charts only - the figures here are distributions, not
	# things to act on, so a band of cards beside them would be decoration.
	"Workforce Profile": (
		(
			"cards",
			("Military Service Exempted", "No Gender Record", "No Governorate"),
			{"columns": 3},
		),
		(
			"charts",
			("Headcount by Governorate", "Military Status Split", "Headcount by Gender"),
			{"columns": 3, "height": 280},
		),
		(
			"charts",
			(
				"Headcount by Sub Department",
				"Headcount by Branch",
				"Headcount by Marital Status",
			),
			{"columns": 3, "height": 280},
		),
			# Two at half width rather than three across: `Employees by Age` has fourteen
		# bands on its x-axis, and at a third of the page the labels rotate into
		# each other. The charts above it are counts by category and lose nothing at
		# a third; this one is a distribution and does.
		("charts", ("Employees by Age", "Designation Wise Employee Count"), {"columns": 2, "height": 300}),
	),
}

# Metric key -> (label, kind, colour, extra card filters, description)
#
# `kind` is "count" for a headcount and "sum" for a money figure.  Cards are
# grouped so the workspace reads as a strip rather than a wall; the colour is the
# only thing distinguishing an alert (red) from a neutral fact (grey) from a
# positive (green).
METRICS: dict[str, dict] = {
	# --- headcount ------------------------------------------------------
	"active_headcount": {
		"label": "Active Headcount",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "People",
		"help": "Everyone with status Active, across all 13 companies",
		"route": "List/Employee",
		"route_options": {"status": "Active"},
	},
	"leavers": {
		"label": "Leavers",
		"kind": "count",
		"color": "#8d99ae",
		"group": "People",
		"help": "Everyone with status Left, with a relieving date recorded",
		"route": "List/Employee",
		"route_options": {"status": "Left"},
	},
	"joined_this_fiscal_year": {
		"label": "Joined This FY",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "People",
		"help": "Joined since the fiscal year opened",
		"route": "List/Employee",
		"route_options": {"status": "Active", "date_of_joining": [">=", "Start of fiscal year"]},
	},
	"joined_last_90_days": {
		"label": "Joined (90 days)",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "People",
		"help": "Joined in the last 90 days",
		"route": "List/Employee",
		"route_options": {"status": "Active", "date_of_joining": [">=", "90 days ago"]},
	},
	# --- data quality, the gaps HR actually has to chase ------------------
	"missing_si_number": {
		"label": "No SI Number",
		"kind": "count",
		"color": "#d64545",
		"group": "Compliance",
		"help": "Active staff with no social insurance number",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_si_number": ["is", "not set"]},
	},
	"missing_national_id_expiry": {
		"label": "No ID Expiry",
		"kind": "count",
		"color": "#d64545",
		"group": "Compliance",
		"help": "Active staff with no national ID expiry date recorded",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_national_id_expiry": ["is", "not set"]},
	},
	"id_expired": {
		"label": "ID Expired",
		"kind": "count",
		"color": "#d64545",
		"group": "Compliance",
		"help": "Active staff whose national ID has expired",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"sdg_national_id_expiry": ["between", ["1900-01-01", "Today"]],
		},
	},
	"id_expiring_90_days": {
		"label": "ID Expiring (90d)",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Compliance",
		"help": "Active staff whose national ID expires within 90 days",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"sdg_national_id_expiry": ["between", ["Today", "Add 90 days from today"]],
		},
	},
	"missing_department": {
		"label": "No Department",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Compliance",
		"help": "Active staff with no department recorded",
		"route": "List/Employee",
		"route_options": {"status": "Active", "department": ["is", "not set"]},
	},
	"missing_designation": {
		"label": "No Designation",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Compliance",
		"help": "Active staff with no designation recorded",
		"route": "List/Employee",
		"route_options": {"status": "Active", "designation": ["is", "not set"]},
	},
	# --- profile: who the group is made of -------------------------------
	# Two of these are gaps and one is a fact, and they are all on the
	# "Workforce Profile" band because they answer the same question from different
	# angles: how thoroughly is this workforce described?
	"missing_gender": {
		"label": "No Gender Record",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Profile",
		"help": "Active staff with no gender recorded, which leaves them out of the "
		"split chart beside it",
		"route": "List/Employee",
		"route_options": {"status": "Active", "gender": ["is", "not set"]},
	},
	"missing_governorate": {
		"label": "No Governorate",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Profile",
		"help": "Active staff with no governorate, which leaves them out of the "
		"geographic split beside it",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_governorate": ["is", "not set"]},
	},
	"military_exempted": {
		"label": "Military Service Exempted",
		"kind": "count",
		"color": "#3d5a80",
		"group": "Profile",
		"help": "Active staff recorded as exempt from military service - either a "
		"statutory exemption or a deferment",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_military_status": "Exempted"},
	},
	# --- benefits and statutory ------------------------------------------
	"insured_persons": {
		"label": "Medically Insured",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "Benefits",
		"help": "Records in the medical register",
		"route": "List/SDG Insured Person",
	},
	"active_without_medical_record": {
		# No comma in a label: a Number Card names itself from the label, and a
		# comma splits the name in two - which is how "Active, No Medical Record"
		# became two cards, "Active" and "No Medical Record".
		"label": "Active Without Medical Record",
		"kind": "count",
		"color": "#d64545",
		"group": "Benefits",
		"help": "Active staff absent from the medical register",
		# "Active, minus the rows in `SDG Insured Person`" is a set difference, and a
		# List View cannot express one.  `sync_drilldown_flags` writes the difference
		# onto the employee row as a flag so that it can, and the count above uses the
		# same flag so the two can never disagree.
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_no_medical_record": 1},
	},
	"people_of_determination": {
		"label": "People of Determination",
		"kind": "count",
		"color": "#3d5a80",
		"group": "Benefits",
		"help": "Qualify for the higher personal tax exemption",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_is_person_of_determination": 1},
	},
	# --- payroll ---------------------------------------------------------
	"payroll_net": {
		"label": "Payroll Net",
		"kind": "sum",
		"color": "#2f6f4e",
		"group": "Payroll",
		"field": "net_pay",
		"help": "Net pay on the most recent submitted payroll",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_gross": {
		"label": "Payroll Gross",
		"kind": "sum",
		"color": "#3d5a80",
		"group": "Payroll",
		"field": "gross_pay",
		"help": "Gross pay on the most recent submitted payroll",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_employer_cost": {
		"label": "Employer Cost",
		"kind": "sum",
		"color": "#3d5a80",
		"group": "Payroll",
		"help": "Employer contributions on the most recent submitted payroll",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_loan_repayment": {
		"label": "Loan Repayment",
		"kind": "sum",
		"color": "#e07b39",
		"group": "Payroll",
		"help": "Withheld this payroll towards staff loans",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_headcount": {
		"label": "Paid This Payroll",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "Payroll",
		"help": "Slips on the most recent submitted payroll",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	# --- detail: salary -------------------------------------------------
	# Averages are the questions a manager actually asks; the totals are already on
	# the payroll cards, and a card that repeats a number is a wasted tile.
	"payroll_average_net": {
		"label": "Average Net Salary",
		"kind": "sum",
		"field": "net_pay",
		"divide_by": "payroll_headcount",
		"color": "#2f6f4e",
		"group": "Payroll",
		"help": "Mean net pay across the submitted slips of the latest run",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_average_gross": {
		"label": "Average Gross Salary",
		"kind": "sum",
		"field": "gross_pay",
		"divide_by": "payroll_headcount",
		"color": "#4c6ef5",
		"group": "Payroll",
		"help": "Mean gross pay across the submitted slips of the latest run",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_min_net": {
		"label": "Lowest Net Salary",
		"kind": "extremum",
		"field": "net_pay",
		"extreme": "min",
		"color": "#8d99ae",
		"group": "Payroll",
		"help": "Smallest net pay in the latest run - worth a look",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_max_net": {
		"label": "Highest Net Salary",
		"kind": "extremum",
		"field": "net_pay",
		"extreme": "max",
		"color": "#8d99ae",
		"group": "Payroll",
		"help": "Largest net pay in the latest run",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"loan_bearers": {
		"label": "Employees On A Loan",
		"kind": "count",
		"color": "#f08c00",
		"group": "Payroll",
		"help": "Active staff with a loan repayment in the latest run",
		"route": "List/Salary Slip",
		# The period is added to every Salary Slip route by `card_routes`; the
		# repayment itself is this card's own condition, and without it the list is
		# every slip in the run rather than the ones that repay a loan.
		"route_options": {"docstatus": "1", "total_loan_repayment": [">", 0]},
	},
	# --- detail: contracts ----------------------------------------------
	"contract_counts": {
		"label": "Contract Types In Use",
		"kind": "count",
		"field": "sdg_contract_type",
		"color": "#7048e8",
		"group": "Contracts",
		"help": "Distinct contract types among active staff",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_contract_type": ["is", "set"]},
	},
	"without_contract_type": {
		"label": "No Contract Type",
		"kind": "count",
		"color": "#e03131",
		"group": "Contracts",
		"help": "Active staff with no contract type recorded",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_contract_type": ["is", "not set"]},
	},
	# --- detail: contract dates -------------------------------------------
	# `Employee.contract_end_date` is a stock HRMS field and is empty for every
	# employee on this site, so these three read zero today.  They are kept
	# because the question they answer - which contracts run out, and when - is
	# the one the group cannot currently answer at all, and the figures appear
	# the day dates are entered.  What *is* known today is carried by the two
	# cards below, which report real numbers rather than zeros.
	"missing_contract_date": {
		"label": "No Contract Start Date",
		"kind": "count",
		"color": "#e8a33d",
		"group": "Contracts",
		"help": "Active staff with no contract start date recorded",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_contract_date": ["is", "not set"]},
	},
	"limited_term_staff": {
		# By name, because `SDG Contract Type.is_limited_term` is unset on all
		# five types - including the two called "Limited" - so there is no flag
		# to read.  See LIMITED_TERM_PREFIX.
		"label": "Limited-Term Staff",
		"kind": "count",
		"color": "#7048e8",
		"group": "Contracts",
		"help": "Active staff on a contract the group treats as limited-term",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_contract_type": ["like", LIMITED_TERM_PREFIX]},
	},
	"missing_contract_end_date": {
		"label": "No Contract End Date",
		"kind": "count",
		"color": "#e03131",
		"group": "Contracts",
		"help": "Active staff with no contract end date on file, so no renewal can be planned",
		"route": "List/Employee",
		"route_options": {"status": "Active", "contract_end_date": ["is", "not set"]},
	},
	"contracts_ended": {
		# Reads zero today for the same reason the two below do, and is the only
		# one of the four that is a *records* problem rather than a forecast: an
		# active employee whose contract already lapsed has not been renewed.
		"label": "Contracts Ended",
		"kind": "count",
		"color": "#c92a2a",
		"group": "Contracts",
		"help": "Active staff whose contract end date has already passed",
		"route": "List/Employee",
		# `DATED_FLOOR` rather than a `< today`: that would also list everyone with no
		# end date, which is every employee here.  See the note on the constant.
		"route_options": {
			"status": "Active",
			"contract_end_date": ["between", [DATED_FLOOR, "Yesterday"]],
		},
	},
	"contracts_ending_90_days": {
		"label": "Contracts Ending (90d)",
		"kind": "count",
		"color": "#f76707",
		"group": "Contracts",
		"help": "Contracts ending within the next three months",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"contract_end_date": ["between", ["Today", "Add 90 days from today"]],
		},
	},
	"contracts_ending_30_days": {
		"label": "Contracts Ending (30d)",
		"kind": "count",
		"color": "#d64545",
		"group": "Contracts",
		"help": "Contracts ending within the next month",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"contract_end_date": ["between", ["Today", "Add 30 days from today"]],
		},
	},
	# --- detail: benefits -----------------------------------------------
	"medical_monthly_cost": {
		"label": "Medical Cover Cost",
		"kind": "sum",
		"field": "monthly_cost",
		"color": "#0ca678",
		"group": "Benefits",
		"help": "Monthly premium across every insured person, priced off their band",
		"route": "List/SDG Insured Person",
	},
	"si_contributors": {
		"label": "Social Insurance Enrolled",
		"kind": "count",
		"color": "#0ca678",
		"group": "Benefits",
		"help": "Active staff carrying a social insurance number",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_si_status": "Insured"},
	},
	"employees_with_school_fee": {
		"label": "School Fee Cases",
		"kind": "count",
		"field": "sdg_school_fee",
		"color": "#0ca678",
		"group": "Benefits",
		"help": "Active staff drawing a company school fee",
		"route": "List/Employee",
		"route_options": {"status": "Active", "sdg_school_fee_total": [">", 0]},
	},
	# --- detail: compliance ---------------------------------------------
	"id_expiring_30_days": {
		"label": "ID Expiring (30d)",
		"kind": "count",
		"color": "#f76707",
		"group": "Compliance",
		"help": "Identity documents expiring within a month",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"sdg_national_id_expiry": ["between", ["Today", "Add 30 days from today"]],
		},
	},
	"tenure_over_5_years": {
		"label": "Tenure 5+ Years",
		"kind": "count",
		"color": "#2f6f4e",
		"group": "People",
		"help": "Active staff with at least five years of service",
		"route": "List/Employee",
		"route_options": {
			"status": "Active",
			"date_of_joining": ["between", ["1900-01-01", "5 years ago"]],
		},
	},
	"penalty_cases": {
		"label": "Penalty Cases",
		"kind": "count",
		"color": "#e03131",
		"group": "Compliance",
		"help": "Staff carrying at least one penalty",
		"route": "List/SDG Penalty",
	},
}

# The currency every money figure is in.  Kept as a constant rather than read from
# the site default, because `currency` on a Number Card or a Dashboard Chart says
# "this figure is an amount" and nothing checks whether it is - set it on a
# headcount and the chart's tooltip reads "EGP 180 people".  The desk offers no
# automatic way to tell, so the metric has to say.
MONEY = "EGP"


def is_money(key: str) -> bool:
	"""Whether a metric is a currency amount rather than a count.

	Driven off `kind` so a new money metric cannot forget: anything that aggregates
	(`sum`, `extremum`) is an amount, and a `count` is never one.
	"""
	return METRICS.get(key, {}).get("kind") in ("sum", "extremum")


METRIC_METHOD = "sharm_dreams_group.hr.dashboard.metric"
METRIC_FUNCTION = "sharm_dreams_group.hr.dashboard.employer_cost"


def _fiscal_year_start() -> str:
	from erpnext.accounts.utils import get_fiscal_year

	year = get_fiscal_year(nowdate(), as_dict=True)
	return str(year.get("year_start_date") or "") if year else ""


def _latest_payroll() -> tuple[str, str] | None:
	"""(start_date, end_date) of the newest submitted payroll, or None."""
	row = frappe.db.sql(
		"select start_date, end_date from `tabPayroll Entry` "
		"where docstatus = 1 order by end_date desc, name desc limit 1"
	)
	return (str(row[0][0]), str(row[0][1])) if row else None


def _employee_filters(filters: dict) -> list[dict]:
	"""Base filters - every people metric is about staff, not leavers by accident."""
	base = [{"status": "Active"}]
	if filters.get("include_left"):
		base = []
	if filters.get("company"):
		base.append({"company": filters["company"]})
	return base


@frappe.whitelist()
def metric(filters: str | dict | None = None) -> int | float:
	"""One card's figure.

	The metric arrives in `filters` because that is what a `Custom` Number Card
	sends, so the cards differ only by their stored filters.
	"""
	require_dashboard_access()

	filters = frappe.parse_json(filters) or {}
	key = filters.get("metric")
	if key not in METRICS:
		frappe.throw(frappe._("Unknown dashboard metric {0}.").format(key))
	spec = METRICS[key]

	# "sum", "extremum" and the band-priced kinds are all money questions answered by
	# aggregating; only "count" is a headcount.  Anything else would fall through to the
	# counter and fail with "no count implementation", which is a confusing way to learn
	# a kind was spelled wrong.
	if spec["kind"] in ("sum", "extremum"):
		return _round_money(_sum_metric(key, spec, filters))
	return _count_metric(key, filters)


# Piastres.  A mean net salary is 18076.439764705882 and nobody wants that on a
# card; every money figure is rounded here, once, rather than by each caller -
# the widget, a report, and the API all get the same number.
MONEY_PRECISION = 2


def _round_money(value) -> float:
	return flt(round(flt(value), MONEY_PRECISION))


def _count_metric(key: str, filters: dict) -> int:
	base = _employee_filters(filters)

	if key == "active_headcount":
		return frappe.db.count("Employee", base)
	if key == "leavers":
		return frappe.db.count("Employee", {"status": "Left", "relieving_date": ("is", "set")})
	if key == "joined_this_fiscal_year":
		start = _fiscal_year_start()
		base.append({"date_of_joining": (">=", start)})
		return frappe.db.count("Employee", base)
	if key == "joined_last_90_days":
		base.append({"date_of_joining": (">=", add_days(nowdate(), -90))})
		return frappe.db.count("Employee", base)

	if key == "insured_persons":
		return frappe.db.count("SDG Insured Person")
	if key == "active_without_medical_record":
		# Counted by the flag, not by redoing the set difference.  The card and its
		# route must be the same query or the drill-down opens a list that disagrees
		# with the number it was clicked from - see `sync_drilldown_flags`.
		return frappe.db.count("Employee", {"status": "Active", "sdg_no_medical_record": 1})
	if key == "people_of_determination":
		base.append({"sdg_is_person_of_determination": 1})
		return frappe.db.count("Employee", base)

	# compliance
	if key == "missing_si_number":
		base.append({"sdg_si_number": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "missing_national_id_expiry":
		base.append({"sdg_national_id_expiry": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "id_expired":
		base.append({"sdg_national_id_expiry": ("<", nowdate())})
		return frappe.db.count("Employee", base)
	if key == "id_expiring_90_days":
		base.append({"sdg_national_id_expiry": ("between", (nowdate(), add_days(nowdate(), 90)))})
		return frappe.db.count("Employee", base)
	if key == "missing_department":
		base.append({"department": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "missing_designation":
		base.append({"designation": ("in", ["", None])})
		return frappe.db.count("Employee", base)

	# profile: the gaps behind the "Workforce Profile" splits.  Each condition is the
	# same test as the chart beside it draws, so the chart's unlabelled slice and this
	# card are the same people - a card that said 3 while the donut showed an
	# unlabelled sliver nobody could account for would be worse than no card.
	if key == "missing_gender":
		base.append({"gender": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "missing_governorate":
		base.append({"sdg_governorate": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "military_exempted":
		base.append({"sdg_military_status": "Exempted"})
		return frappe.db.count("Employee", base)

	# contracts: the spread, and the gap behind it
	if key == "contract_counts":
		rows = frappe.db.sql(
			"select sdg_contract_type from `tabEmployee` "
			"where status = 'Active' and ifnull(sdg_contract_type, '') != ''"
		)
		return len({r[0] for r in rows})
	if key == "without_contract_type":
		base.append({"sdg_contract_type": ("in", ["", None])})
		return frappe.db.count("Employee", base)

	# contract dates.  Each condition mirrors its card's `route_options` exactly, so
	# the drill-down opens the list these numbers were counted from - see the note
	# on `active_without_medical_record`.
	if key == "missing_contract_date":
		base.append({"sdg_contract_date": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "limited_term_staff":
		base.append({"sdg_contract_type": ("like", LIMITED_TERM_PREFIX)})
		return frappe.db.count("Employee", base)
	if key == "missing_contract_end_date":
		base.append({"contract_end_date": ("in", ["", None])})
		return frappe.db.count("Employee", base)
	if key == "contracts_ended":
		base.append({"contract_end_date": ("between", (DATED_FLOOR, add_days(nowdate(), -1)))})
		return frappe.db.count("Employee", base)
	if key == "contracts_ending_90_days":
		base.append({"contract_end_date": ("between", (nowdate(), add_days(nowdate(), 90)))})
		return frappe.db.count("Employee", base)
	if key == "contracts_ending_30_days":
		base.append({"contract_end_date": ("between", (nowdate(), add_days(nowdate(), 30)))})
		return frappe.db.count("Employee", base)

	# benefits
	if key == "si_contributors":
		base.append({"sdg_si_status": "Insured"})
		return frappe.db.count("Employee", base)
	if key == "employees_with_school_fee":
		base.append({"sdg_school_fee_total": (">", 0)})
		return frappe.db.count("Employee", base)

	# compliance
	if key == "id_expiring_30_days":
		base.append({"sdg_national_id_expiry": ("between", (nowdate(), add_days(nowdate(), 30)))})
		return frappe.db.count("Employee", base)
	if key == "tenure_over_5_years":
		base.append({"date_of_joining": ("<=", add_years(nowdate(), -5))})
		return frappe.db.count("Employee", base)
	if key == "penalty_cases":
		return frappe.db.count("SDG Penalty")

	# payroll detail
	if key == "loan_bearers":
		period = _latest_payroll()
		if not period:
			return 0
		return frappe.db.count(
			"Salary Slip",
			{
				"docstatus": 1,
				"start_date": period[0],
				"end_date": period[1],
				"total_loan_repayment": (">", 0),
			},
		)

	if key == "payroll_headcount":
		period = _latest_payroll()
		if not period:
			return 0
		return frappe.db.count(
			"Salary Slip", {"docstatus": 1, "start_date": period[0], "end_date": period[1]}
		)

	frappe.throw(frappe._("Metric {0} has no count implementation.").format(key))


def _sum_metric(key: str, spec: dict, filters: dict) -> float:
	period = _latest_payroll()
	if not period:
		return 0.0
	slips = {"docstatus": 1, "start_date": period[0], "end_date": period[1]}
	if filters.get("company"):
		slips["company"] = filters["company"]

	# An average is a sum divided by the number of slips behind it.  Dividing by a
	# separate count query would be two round trips that could disagree if someone
	# submitted a slip mid-request; deriving both from the same period is the whole
	# point of reading the period once.
	if spec.get("divide_by"):
		total, count = _slip_totals(period, spec["field"])
		return total / count if count else 0.0
	if spec.get("extreme"):
		return _slip_extreme(period, spec["field"], spec["extreme"])
	if key == "medical_cost":
		return flt(frappe.db.sql(f"select sum(`{spec['field']}`) from `tabSDG Insured Person`")[0][0])
	if key == "medical_monthly_cost":
		return _monthly_medical_cost()
	if key == "payroll_employer_cost":
		return employer_cost()
	if key == "payroll_loan_repayment":
		return flt(
			frappe.db.sql(
				"select sum(total_loan_repayment) from `tabSalary Slip` "
				"where docstatus = 1 and start_date = %s and end_date = %s",
				(period[0], period[1]),
			)[0][0]
		)
	return flt(
		frappe.db.sql(
			f"select sum({spec['field']}) from `tabSalary Slip` "
			"where docstatus = 1 and start_date = %s and end_date = %s",
			(period[0], period[1]),
		)[0][0]
	)


def _slip_totals(period, field) -> tuple[float, int]:
	"""(sum of `field`, number of slips) for one payroll period, in one query."""
	row = frappe.db.sql(
		f"select sum({field}), count(*) from `tabSalary Slip` "
		"where docstatus = 1 and start_date = %s and end_date = %s",
		(period[0], period[1]),
	)[0]
	return flt(row[0]), cint(row[1])


def _monthly_medical_cost() -> float:
	"""What the medical cover costs the group each month.

	`SDG Insured Person` records which band someone is on, not what it costs - the
	price is a property of the band, so the two are joined here.  Counting each
	person once per row they appear in matters: an employee appears on one insured
	person row per family member covered, and every one of those is billed.
	"""
	return flt(
		frappe.db.sql(
			"""
			select sum(case when ip.family_band is not null and ip.family_band != ''
			             then fb.monthly_premium
			             when ip.medical_band is not null and ip.medical_band != ''
			             then mb.monthly_premium
			             else 0 end)
			from `tabSDG Insured Person` ip
			left join `tabSDG Medical Band` mb on mb.name = ip.medical_band
			left join `tabSDG Family Medical Band` fb on fb.name = ip.family_band
			"""
		)[0][0]
	)


def _slip_extreme(period, field, how: str) -> float:
	"""Smallest or largest value of `field` in one payroll period.

	`how` is not interpolated into the query: it is checked against a literal list and
	used as the SQL function name, so a caller cannot inject through it.
	"""
	if how not in ("min", "max"):
		raise ValueError(f"extreme must be min or max, not {how!r}")
	return flt(
		frappe.db.sql(
			f"select {how}({field}) from `tabSalary Slip` "
			"where docstatus = 1 and start_date = %s and end_date = %s",
			(period[0], period[1]),
		)[0][0]
	)


@frappe.whitelist()
def employer_cost(filters: str | dict | None = None) -> float:
	"""Employer contributions on the latest submitted payroll.

	Separate from the dispatcher because a Dashboard Chart needs it as its own
	`Custom` method, and a chart method is called with no metric to dispatch on.
	"""
	require_dashboard_access()

	period = _latest_payroll()
	if not period:
		return 0.0
	return flt(
		frappe.db.sql(
			"select sum(amount) from `tabSalary Detail` "
			"where parenttype = 'Salary Slip' and parentfield = 'employer_contributions' "
			"and parent in (select name from `tabSalary Slip` "
			"where docstatus = 1 and start_date = %s and end_date = %s)",
			(period[0], period[1]),
		)[0][0]
	)


@frappe.whitelist()
def headcount_by_company() -> list[dict]:
	"""Active headcount per company, for the Custom 'Headcount by Company' chart.

	A Custom chart rather than a Group By because the question is *active staff
	per legal entity* - the group is spread over 13 companies and two of them now
	have more leavers than active staff - and a Group By would mix the two.
	"""
	require_dashboard_access()

	rows = frappe.db.sql(
		"select company, sum(status = 'Active'), sum(status = 'Left') "
		"from `tabEmployee` group by company order by company"
	)
	return [{"company": r[0], "active": cint(r[1]), "left": cint(r[2])} for r in rows if r[0]]


@frappe.whitelist()
def national_id_expiry_buckets() -> list[dict]:
	"""How many active staff fall into each national-ID expiry window.

	Egyptian national IDs are the identity record a payroll is keyed on, so an
	expired or nearly-expired ID is a compliance problem rather than a tidiness
	one.  Counted here rather than in the Employee hook so the numbers cannot drift
	from the dates actually stored.
	"""
	require_dashboard_access()

	today = getdate(nowdate())
	edges = [
		("Expired", lambda d: d < today),
		("0-90 days", lambda d: today <= d < add_days(today, 90)),
		("90-180 days", lambda d: add_days(today, 90) <= d < add_days(today, 180)),
		("180+ days", lambda d: d >= add_days(today, 180)),
	]
	values = frappe.db.sql(
		"select sdg_national_id_expiry from `tabEmployee` "
		"where status = 'Active' and sdg_national_id_expiry is not null and sdg_national_id_expiry != ''"
	)
	buckets = []
	for label, test in edges:
		n = sum(1 for (raw,) in values if raw and test(getdate(raw)))
		buckets.append({"bucket": label, "count": n})
	blank = frappe.db.count("Employee", {"status": "Active", "sdg_national_id_expiry": ("in", ["", None])})
	buckets.append({"bucket": "Not recorded", "count": cint(blank)})
	return buckets


def can_view_dashboard() -> bool:
	"""Whether this user may see workforce figures."""
	user = frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles():
		return True
	return bool(
		{
			"HR Manager",
			"HR User",
			"SDG Payroll Manager",
			"SDG Payroll User",
			"Employee",
			"Employee Self Service",
			"Accounts User",
			"Accounts Manager",
			"Payroll User",
		}
		& set(frappe.get_roles())
	)


def require_dashboard_access() -> None:
	"""Guard for the whitelisted entry points."""
	if not can_view_dashboard():
		frappe.throw(
			frappe._("Not permitted to view workforce figures."),
			frappe.PermissionError,
		)


# The charts this app publishes, in reading order.  Declared here rather than in
# setup.py so the dashboard and the workspace are built from one list.
CHART_NAMES = (
	"Headcount by Company",
	"Leavers by Company",
	"Payroll Net by Company",
	"Headcount by Department",
	"Social Insurance Status",
	"Employment Category",
	"Medical Cover by Band",
	"Payroll Gross by Company",
	"People of Determination by Company",
	# The one chart on the workforce dashboard that is not a Group By: it buckets
	# `contract_end_date` in Python, because grouping the raw date would draw one
	# bar per distinct day.  See sharm_dreams_group/sharm_dreams_group/dashboard_chart_source/
	# contract_end_dates.
	"Contract End Dates",
)


def metric_definitions() -> list[dict]:
	"""The cards, for the workspace builder and for a test to pin.

	`currency` is filled in here rather than typed into every entry, so it follows
	`kind` and cannot say EGP on a headcount.
	"""
	return [
		dict(key=key, currency=(MONEY if is_money(key) else None), **spec) for key, spec in METRICS.items()
	]


# The main dashboard keeps section headers rather than tabs: it is the page people
# land on, and a strip of cards you can see all of is more use there than four panes.
#
# Each band answers one question, which is what makes five bands readable where
# four were not.  The layout this replaced had "Compliance" holding both the
# missing-record cards (things to chase) and the social-insurance and employment
# distributions (things to look at), and "Benefits & Contracts" holding two
# subjects neither of which was big enough to stand alone.  So:
#
#   People              who we are, and how that is moving
#   Workforce Profile   who we are, in detail: where they are, how they are recorded
#   Records To Complete everything missing, in one chase list
#   Contracts           what contracts exist, and which run out
#   Benefits & Insurance what the group provides
#   Payroll             what it costs
#
# "Records To Complete" is the same name `SDG HRM` already uses for its gap cards,
# so the idea is one the desk has already agreed to.  Every card is on exactly one
# band, and `test_every_card_is_on_exactly_one_section` is what keeps it that way.
DASHBOARD_SECTIONS = (
	(
		"People",
		(
			"Active Headcount",
			"Leavers",
			"Joined This FY",
			"Joined (90 days)",
			"Tenure 5+ Years",
			"People of Determination",
		),
		(
			"Headcount by Company",
			"Headcount by Department",
			"Employment Category",
			# Joiners and leavers, month by month, over the last year. The band is
			# called "People" and these are the only charts on it that show a trend
			# rather than a distribution - "Leavers" says 130 and "Leavers by Company"
			# says which companies, and neither says that all 130 left in the same
			# month, which is the one fact about them a reader needs.
			#
			# Named here rather than written: `setup.create_dashboard` links a named
			# chart onto the dashboard if the document exists, and `setup` creates
			# both of these from `CUSTOM_CHART_SOURCES`.
			"Monthly Joiners",
			"Monthly Attrition",
			"Leavers by Company",
			"People of Determination by Company",
		),
	),
	(
		# Who the group is made of, as opposed to how it is moving.  Every field on
		# this band was checked for spread before it went on a dashboard, because a
		# `Group By` on a field that is null on every row draws one empty slice and
		# reads as broken rather than as "not yet": `grade` and `employment_type` are
		# null on all 310 employees and are therefore not on here, while governorate
		# (23 values), sub department (21), branch (14), gender, military status and
		# marital status all have real spread among the 180 active staff.
		#
		# It sits next to "People" rather than inside it because "People" is movement
		# - joined, left, tenure - and a reader who has just seen the headcount go up
		# by nine is asking a different question from the one who wants to know the
		# group is 87% male.  Mixing the two is what made the original "Compliance"
		# band unreadable, in the other direction.
		"Workforce Profile",
		(
			"Military Service Exempted",
			"No Gender Record",
			"No Governorate",
		),
		(
			"Headcount by Governorate",
			"Headcount by Sub Department",
			"Headcount by Branch",
			"Headcount by Gender",
			"Military Status Split",
			"Headcount by Marital Status",
			# Age and designation, which nothing else on the dashboard answers. Both are
			# HRMS charts reused rather than rebuilt - see `REUSED_CHARTS`. They are
			# named here rather than written: `setup.create_dashboard` links a named
			# chart onto the dashboard if the document exists, which is all a reused
			# chart needs.
			"Employees by Age",
			"Designation Wise Employee Count",
		),
		#
		# How this band is *arranged* - which cards and charts go in which block, and
		# at what width - is `SECTION_LAYOUT`'s job, not a fourth element here. It
		# has an entry for this band that names all eight charts above; a layout
		# block as a fourth element made this a 4-tuple while every other section is
		# a 3-tuple, which `setup.create_dashboard` unpacks as three and so raised
		# ValueError on every migrate.
	),
	(
		"Records To Complete",
		(
			"No SI Number",
			"No ID Expiry",
			"ID Expired",
			"ID Expiring (90d)",
			"ID Expiring (30d)",
			"No Department",
			"No Designation",
			"No Contract Start Date",
			"No Contract Type",
			"No Contract End Date",
			"Penalty Cases",
		),
		("Compliance Gaps by Company",),
	),
	(
		"Contracts",
		(
			"Limited-Term Staff",
			"Contract Types In Use",
			"Contracts Ending (90d)",
			"Contracts Ending (30d)",
			"Contracts Ended",
		),
		(
			"Contract End Dates",
			"Contract Type by Company",
		),
	),
	(
		"Benefits & Insurance",
		(
			"Medically Insured",
			"Active Without Medical Record",
			"Medical Cover Cost",
			"Social Insurance Enrolled",
			"School Fee Cases",
		),
		(
			"Medical Cover by Band",
			"Medical Cover by Family Band",
			"Life Insurance Band Split",
			"Social Insurance Status",
			"Uninsured by Company",
		),
	),
	(
		"Payroll",
		(
			"Payroll Net",
			"Payroll Gross",
			"Employer Cost",
			"Average Net Salary",
			"Average Gross Salary",
			"Lowest Net Salary",
			"Highest Net Salary",
			"Loan Repayment",
			"Employees On A Loan",
			"Paid This Payroll",
		),
		(
			"Payroll Net by Company",
			"Payroll Gross by Company",
			"Employer Cost by Company",
			"Loans Repaid by Company",
			"Recharged Net by Charging Company",
			"Recharged Gross by Paying Company",
		),
	),
	(
		# The coloured level strip, and the six cards behind it. "Penalty Cases" is
		# where a reader goes to find out whether penalties are a problem at all;
		# these six are where they find out which ones. Their own band rather than
		# six more cards in "Records To Complete", because that band is a chase
		# list - things missing that someone has to go and fill in - and a
		# distribution of who sits at which level is not that.
		#
		# All six were being created, coloured, wired with a drill-down and counted,
		# and then never rendered: a card in the `cards` child table that no section
		# names is a card no page draws. Nothing complained, because every other
		# check reads the layout rather than comparing it against the child table -
		# which is what `test_every_card_this_app_publishes_is_on_a_section` does.
		"Discipline",
		("With a Penalty", "Clear", "Warning", "Minor", "Serious", "Critical"),
		(),
	),
)


def section_layout(sections, layout: dict | None = None) -> str:
	"""Serialise a section layout into `Dashboard.chart_options`.

	Card labels and chart names are filtered against what exists, so a card renamed in
	METRICS leaves a gap in the layout rather than a broken tile - the dashboard builder
	also reports anything it could not find.

	`layout` decides the order and the sizing; the sections themselves decide what is in
	each band.  The two are kept apart on purpose: every other reader of
	DASHBOARD_SECTIONS (the workspace card bands, the tests, the card audit) wants the
	membership question and none of the layout, and changing the shape of a 3-tuple that
	four places unpack would break all of them for no gain.
	`test_section_layout_partitions_its_sections` holds the two in step.

	`layout` is keyed by section title and is per dashboard, which is why it is an
	argument and not a single table read here.  Two of the three dashboards have a band
	called "Records To Complete" - one of them uses this dashboard's own cards in it and
	the other uses three completely different ones - so a table read unconditionally
	would hand one dashboard the other's arrangement.  It is not visible when it happens,
	because `_blocks_for` drops any label the section does not have, and the result is
	simply the default arrangement.  That is a layout silently not applied.
	"""
	import json

	layout = SECTION_LAYOUT if layout is None else layout
	known_cards = set(frappe.get_all("Number Card", pluck="name", limit_page_length=0))
	known_charts = set(frappe.get_all("Dashboard Chart", pluck="name", limit_page_length=0))

	payload = []
	missing: list[str] = []
	for title, cards, charts in sections:
		kept_cards = [c for c in cards if c in known_cards]
		kept_charts = [c for c in charts if c in known_charts]
		missing += [f"{title}/{c}" for c in cards if c not in known_cards]
		missing += [f"{title}/{c}" for c in charts if c not in known_charts]
		if not (kept_cards or kept_charts):
			continue
		payload.append(
			{"title": title, "blocks": _blocks_for(layout.get(title), kept_cards, kept_charts)}
		)

	if missing:
		print("  dashboard: LAYOUT REFERENCES SOMETHING THAT DOES NOT EXIST: " + ", ".join(missing))
	return json.dumps({"sections": payload})


# How many cards and charts sit on a row, and how tall a chart row is, per section.
#
# Read in this order: this table, then the section's declared blocks, then the defaults
# the desk used before either existed.
#
# The defaults are the ones the dashboards shipped with - four cards across, two charts
# across at 240px - and they are kept rather than restated as a constant, so a section
# with no entry here renders exactly as it did before this table existed.  That matters
# because stock Frappe and HRMS write `cards`/`charts` rather than `blocks`, and this
# factory renders those too; a hard-coded default here would change every stock
# dashboard's layout as a side effect of improving ours.
DEFAULT_CARD_COLUMNS = 4
DEFAULT_CHART_COLUMNS = 2
DEFAULT_CHART_HEIGHT = 240


def _blocks_for(declared, cards: list[str], charts: list[str]) -> list[dict]:
	"""The ordered blocks for one section, sized per block.

	A section's declared layout wins; otherwise the section is one card block then one
	chart block, which is what every dashboard did before interleaving existed.

	Only labels that survived the existence filter above are emitted, and a block left
	empty is dropped, so a renamed card cannot leave a heading with nothing under it.
	"""
	if declared:
		blocks = []
		for kind, items, opts in declared:
			kept = [i for i in items if i in (cards if kind == "cards" else charts)]
			if kept:
				blocks.append({"type": kind, "items": kept, **(opts or {})})
		if blocks:
			return blocks
	if cards:
		blocks = [{"type": "cards", "items": cards, "columns": DEFAULT_CARD_COLUMNS}]
		if charts:
			blocks.append(
				{
					"type": "charts",
					"items": charts,
					"columns": DEFAULT_CHART_COLUMNS,
					"height": DEFAULT_CHART_HEIGHT,
				}
			)
		return blocks
	return [{"type": "charts", "items": charts, "columns": DEFAULT_CHART_COLUMNS, "height": DEFAULT_CHART_HEIGHT}]


@frappe.whitelist()
def card_routes() -> dict:
	"""Drill-down target per card label, for the widgets to click through to.

	The stock Number Card widget only navigates when the card carries its own `route`.
	A `Custom` card driven by a method has none, so every card on this app sat there
	looking like a number and doing nothing.  This is the map the widget patch uses, so
	the drill-down lives with the metric rather than in a template.

	Whitelisted, and it needs to be: without the decorator this raises from an
	unauthenticated call, the widget's `.catch` swallows it, and every card is
	inert with nothing in the console to say why.
	"""
	out = {}
	for module in _metric_modules():
		for spec in module.METRICS.values():
			if not spec.get("route"):
				continue
			out[spec["label"]] = {
				"route": spec["route"],
				"route_options": _expand_relative_dates(spec.get("route_options") or {}),
			}
	out.update(_penalty_routes())
	# HRM owns its coupon cards' scope, because only it knows the latest coupon month.
	for module in _metric_modules():
		if hasattr(module, "coupon_routes"):
			out.update(module.coupon_routes())
	_scope_payroll_routes(out)
	return out


def _scope_payroll_routes(routes: dict) -> None:
	"""Pin every Salary Slip drill-down to the run the card was measured over.

	Each payroll figure - the headcount, the net, an average, a loan bearer - is read
	from the most recent submitted `Payroll Entry` and nothing else.  A route that said
	only `docstatus = 1` opened every slip ever submitted, so the list behind "Paid This
	Payroll" grew with each run while the card stayed on the latest one.

	The dates cannot live in `METRICS`, because the run is not known until the map is
	built and the map is rebuilt on every dashboard open.  That is the same bargain
	`_expand_relative_dates` makes with relative keywords, and it is what keeps this
	correct after the next payroll is submitted rather than only for the first one.
	"""
	period = _latest_payroll()
	if not period:
		return
	start, end = period
	for spec in routes.values():
		if spec.get("route") != "List/Salary Slip":
			continue
		options = spec.setdefault("route_options", {})
		options.setdefault("start_date", start)
		options.setdefault("end_date", end)


# Relative date keywords a filter may use, mapped to the day they mean.
#
# These are the words the desk's own filter UI shows, and they are resolved in the
# browser before the query is sent. A `route_options` value does not get that
# treatment: it reaches the server as-is, where `sdg_national_id_expiry < "Today"`
# compares the date column against the *string* "Today" and matches nothing at all.
# The symptom is the worst kind - a drill-down that opens an empty list, which reads
# as "there is nobody here" rather than as a broken filter.
#
# So they are expanded here, at the moment the map is built. The map is fetched
# afresh whenever a dashboard is opened or a card is tapped, so the dates are current
# to the last page load; a dashboard left open overnight shows yesterday's cut-off,
# which is a far smaller problem than an empty list.
def _relative_day(value: str) -> str | None:
	"""One relative keyword as a real date, or None if it is not one."""
	from frappe.utils import add_days, add_years, nowdate

	word = str(value).strip()
	if word == "Today":
		return nowdate()
	if word == "Yesterday":
		return add_days(nowdate(), -1)
	if word == "Tomorrow":
		return add_days(nowdate(), 1)
	if word in ("Start of fiscal year", "Fiscal year start"):
		# The card's own definition of "this FY" - `get_fiscal_year`, not a calendar
		# year. Anything else here would drift from the number on the card.
		return _fiscal_year_start()
	# "Add 90 days from today", "Add 1 month from today"
	match = re.match(r"^Add\s+(\d+)\s+(day|days|month|months|week|weeks)\s+from\s+today$", word, re.I)
	if match:
		amount, unit = int(match.group(1)), match.group(2).lower().rstrip("s")
		if unit in ("day", "week"):
			return add_days(nowdate(), amount if unit == "day" else amount * 7)
		return add_months(nowdate(), amount)
	# "... ago", and years. Every "when did this happen" card needs to look backwards,
	# and without this the only expressible cut-offs were future ones.
	match = re.match(r"^(\d+)\s+(day|days|week|weeks|month|months|year|years)\s+ago$", word, re.I)
	if match:
		amount, unit = int(match.group(1)), match.group(2).lower().rstrip("s")
		if unit == "day":
			return add_days(nowdate(), -amount)
		if unit == "week":
			return add_days(nowdate(), -(amount * 7))
		if unit == "month":
			return add_months(nowdate(), -amount)
		return add_years(nowdate(), -amount)
	return None


def _expand_relative_dates(filters: dict) -> dict:
	"""Replace relative keywords anywhere in a filter map with real dates."""
	out = {}
	for field, value in (filters or {}).items():
		if isinstance(value, str):
			out[field] = _relative_day(value) or value
		elif isinstance(value, (list, tuple)):
			parts = list(value)
			# ["between", ["Today", "Add 90 days from today"]]
			if len(parts) == 2 and isinstance(parts[1], (list, tuple)):
				inner = [(_relative_day(v) or v) if isinstance(v, str) else v for v in parts[1]]
				out[field] = [parts[0], inner]
			else:
				out[field] = [(_relative_day(v) or v) if isinstance(v, str) else v for v in parts]
		else:
			out[field] = value
	return out


def _metric_modules():
	"""Every module that defines dashboard metrics.

	The Account and HRM dashboards own cards too, and a card with no route is inert -
	so the map has to cover all three or those dashboards are the dead ones. Imported
	inside the function: the two domain modules import from this one, so a top-level
	import would be circular.
	"""
	from sharm_dreams_group.accounts import dashboard as account
	from sharm_dreams_group.hr import dashboard_hrm as hrm

	return (sys.modules[__name__], account, hrm)


# Cards that cannot be drilled down, and why. A card with no route is a number that
# does nothing, so the list is short and every entry earns its place.
# The cards that are deliberately not clickable, and why.
#
# `Coupons Unrecorded` counts people the August coupon workbook paid who are not in
# `SDG Coupon Granted` - a difference between a spreadsheet and a register. There is
# no row to hang a filter on, because the people being counted mostly do not exist as
# employees at all: of the 26 outstanding, 22 came off the workbook with no work ID to
# match them on and two more name a work ID that is not in the system. A list can only
# show rows it holds, so every list this could open is the opposite of what the card
# says. Pointing it at the register would answer the wrong question, which is worse
# than offering no link at all.
#
# The other two used to sit here as well. Both were set differences - employees minus
# the medical register, companies that have posted - and are now filterable, because
# `setup.sync_drilldown_flags` writes the difference onto the row as a flag and the
# metric reads that same flag.
# Every card on the three dashboards now opens a list that agrees with its figure.
# Kept as a structure rather than deleted, because it is the list a future card has to
# be added to deliberately, and an empty dict proves the sweep was finished rather
# than forgotten.
NO_DRILLDOWN: dict[str, str] = {}


# The six penalty cards are a fourth set, from `hr.penalty.headcount` rather than a
# metric dispatcher, so they need their own map. They land on the Employee list
# filtered to people carrying a penalty - the list is the record of *which* people,
# which is the only thing a number cannot tell you.
def _penalty_routes() -> dict:
	return {
		"With a Penalty": {"route": "List/Employee", "route_options": {"sdg_penalty_level": ["is", "set"]}},
		"Critical": {
			"route": "List/Employee",
			"route_options": {"status": "Active", "sdg_penalty_level": "Critical"},
		},
		"Serious": {
			"route": "List/Employee",
			"route_options": {"status": "Active", "sdg_penalty_level": "Serious"},
		},
		"Warning": {
			"route": "List/Employee",
			"route_options": {"status": "Active", "sdg_penalty_level": "Warning"},
		},
		"Minor": {
			"route": "List/Employee",
			"route_options": {"status": "Active", "sdg_penalty_level": "Minor"},
		},
		"Clear": {
			"route": "List/Employee",
			"route_options": {"status": "Active", "sdg_penalty_level": "Clear"},
		},
	}
