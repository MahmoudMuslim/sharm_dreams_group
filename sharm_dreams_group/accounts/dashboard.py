"""The `SDG Account` dashboard: what the payroll and the ledger actually did.

Kept apart from `hr/dashboard.py` on purpose.  That module answers *who works
here*; this one answers *what it cost and where it landed in the books*, and
mixing the two is how a dashboard stops answering anything.

Everything here is read from submitted documents - `Salary Slip` for the cost,
`GL Entry` and `Journal Entry` for the ledger - so the figures are the ones the
accounts are, not a re-derivation of them.

Note on the ledger figures: every `GL Entry` on this site is a `Journal Entry`,
because the payroll posted its accruals and the group has no other transactions.
The dashboard says so in the card descriptions rather than implying a fuller set
of books than exists.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from sharm_dreams_group.hr.dashboard import can_view_dashboard, require_dashboard_access

METRIC_METHOD = "sharm_dreams_group.accounts.dashboard.metric"
MONEY = "EGP"

# Every key is (label, colour, kind, currency).  `kind` is `sum` for money and
# `count` for everything else; the card's currency follows the kind, so a
# headcount can never be recorded as "EGP 12 people" - see the same rule in
# `hr/dashboard.py`, where it was a bug before it was a rule.
METRICS: dict[str, dict] = {
	"payroll_gross": {
		"label": "Payroll Gross",
		"color": "Blue",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"payroll_net": {
		"label": "Payroll Net",
		"color": "Blue",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"total_deductions": {
		"label": "Total Deductions",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"income_tax_deducted": {
		"label": "Income Tax Deducted",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"employer_si_share": {
		"label": "Employer Social Insurance",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"employee_si_share": {
		"label": "Employee Social Insurance",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"insurance_deducted": {
		"label": "Insurance Deducted",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"loan_repayments": {
		"label": "Loan Repayments",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"training_fund": {
		"label": "Training Fund",
		"color": "Orange",
		"kind": "sum",
		"route": "List/Salary Slip",
		"route_options": {"docstatus": "1"},
	},
	"journal_entries": {
		"label": "Journal Entries",
		"color": "Green",
		"kind": "count",
		"route": "List/Journal Entry",
		"route_options": {"docstatus": "1"},
	},
	"journal_value": {
		"label": "Journal Value",
		"color": "Green",
		"kind": "sum",
		"route": "List/GL Entry",
		"route_options": {"voucher_type": "Journal Entry"},
	},
	"gl_entries": {
		"label": "Ledger Entries",
		"color": "Green",
		"kind": "count",
		"route": "List/GL Entry",
	},
	# The card counts the *distinct* companies that have posted - 13 of the 14 on
	# the site, our own app entity being the one that has not.
	"companies_posting": {
		"label": "Companies Posting",
		"color": "Green",
		"kind": "count",
		# "Has at least one GL Entry" is not a filterable property of a company - the
		# Company list would show all of them and the GL Entry list would show every
		# entry, neither of which is the number on the card.  `sync_drilldown_flags`
		# writes the answer onto the company row as a flag, and the count below reads
		# the same flag, so the list and the card cannot drift apart.
		"route": "List/Company",
		"route_options": {"sdg_posting": 1},
	},
}


def _is_money(key: str) -> bool:
	return METRICS[key]["kind"] == "sum"


def metric_definitions() -> list[dict]:
	"""The cards, in the shape `setup.create_domain_cards` expects."""
	return [
		dict(key=key, currency=(MONEY if _is_money(key) else None), **spec) for key, spec in METRICS.items()
	]


def _slip_sum(field: str) -> float:
	"""Sum one field on `Salary Slip` itself.

	Only `docstatus = 1`: a draft slip is a guess, and a dashboard that moves when
	somebody opens a form is worse than one that is a day stale.
	"""
	return flt(
		frappe.db.sql(
			f"select sum(`{field}`) from `tabSalary Slip` where docstatus = 1",
			as_list=True,
		)[0][0]
	)


# A component is a row on the slip, not a field on it, so these cannot be read
# off `Salary Slip` at all.  `Income Tax` and `Income Tax on Cost Share` are
# separate components on the payroll and both are tax, and a card that showed
# only the first would understate the tax by 49,155.66 a month.
_COMPONENT_TOTALS = {
	"income_tax_deducted": ("Income Tax", "Income Tax on Cost Share"),
	"employer_si_share": ("Social Insurance (Employer)", "MI Fund (Employer)"),
	"employee_si_share": ("Social Insurance (Employee)",),
	"insurance_deducted": ("Medical Insurance", "Family Medical Insurance", "Life Insurance"),
	"training_fund": ("Training and Development Fund",),
}


def _component_total(components: tuple[str, ...]) -> float:
	"""Sum the named components across every submitted slip.

	Joined to `Salary Slip` rather than read from `Salary Detail` alone: the child
	rows exist for draft slips too, and counting those would mean the figure
	changed the moment somebody opened a payroll entry.
	"""
	placeholders = ", ".join("%s" for _ in components)
	return flt(
		frappe.db.sql(
			f"""select sum(sd.amount) from `tabSalary Detail` sd
				join `tabSalary Slip` s on s.name = sd.parent and sd.parenttype = 'Salary Slip'
				where s.docstatus = 1 and sd.salary_component in ({placeholders})""",
			components,
			as_list=True,
		)[0][0]
	)


@frappe.whitelist()
def metric(filters: str | dict | None = None) -> int | float:
	"""One card's figure, for the card's stored `metric` filter."""
	require_dashboard_access()

	filters = frappe.parse_json(filters) or {}
	key = filters.get("metric")
	if key not in METRICS:
		frappe.throw(frappe._("Unknown account dashboard metric {0}.").format(key))

	if key in _COMPONENT_TOTALS:
		return _round(_component_total(_COMPONENT_TOTALS[key]))
	if key == "payroll_gross":
		return _round(_slip_sum("gross_pay"))
	if key == "payroll_net":
		return _round(_slip_sum("net_pay"))
	if key == "total_deductions":
		return _round(_slip_sum("total_deduction"))
	if key == "loan_repayments":
		return _round(_slip_sum("total_loan_repayment"))

	if key == "journal_entries":
		# Submitted only. A draft is not part of the books, and every other figure on
		# this dashboard counts submitted documents - counting drafts here made the
		# card read 79 while the list it opened showed the 27 that are actually
		# posted, which is a worse number than either.
		return frappe.db.count("Journal Entry", {"docstatus": 1})
	if key == "companies_posting":
		return frappe.db.count("Company", {"sdg_posting": 1})
	if key == "gl_entries":
		return frappe.db.count("GL Entry")
	if key == "journal_value":
		# Debit side only. Debit and credit are equal on every journal entry, so
		# either side gives the same figure; the debit side is named so the label
		# cannot be read as a net movement, which it is not.
		return _round(
			frappe.db.sql(
				"select sum(debit) from `tabGL Entry` where voucher_type = 'Journal Entry'", as_list=True
			)[0][0]
		)
	raise AssertionError(key)  # pragma: no cover - the key was checked above


MONEY_PRECISION = 2


def _round(value) -> float:
	return flt(round(flt(value), MONEY_PRECISION))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
# Three sections, read as a sentence: what it cost, what came out of it, and
# where the books ended up.  Only `SDG Account`'s own cards appear here - the
# headcount and benefits cards belong to the other dashboards, and a figure
# appearing on three pages is a figure nobody trusts.
ACCOUNT_SECTIONS = (
	(
		"Payroll Cost",
		("Payroll Gross", "Payroll Net"),
		# Per company on the first row, and by designation and department on the
		# second: the same money two ways.  `... by Company` answers "which company",
		# and nothing else on this dashboard answers "which job" - which for a group
		# running hotels, a warehouse and a head office under one set of accounts is
		# the more useful of the two questions.  Both are stock HRMS charts, reused
		# rather than rebuilt; see `setup.tune_reused_charts` for the payroll window
		# they are scoped to, which HRMS ships as a calendar month and is not.
		(
			"Payroll Gross by Company",
			"Payroll Net by Company",
			"Designation Wise Salary(Last Month)",
			"Department Wise Salary(Last Month)",
		),
	),
	(
		"What Came Out",
		(
			"Total Deductions",
			"Income Tax Deducted",
			"Employee Social Insurance",
			"Employer Social Insurance",
			"Insurance Deducted",
			"Loan Repayments",
			"Training Fund",
		),
		(
			"Deductions by Company",
			"MI Fund by Company",
			"Loans Repaid by Company",
		),
	),
	(
		"The Ledger",
		("Journal Entries", "Journal Value", "Ledger Entries", "Companies Posting"),
		(
			"Journal Entries by Company",
			"Ledger Debit by Company",
			"Ledger Credit by Company",
		),
	),
)

# How each band is arranged, and how wide.  The format, and the reason it is a table
# rather than a change to the shape of ACCOUNT_SECTIONS, are in `hr/dashboard.py`'s
# `SECTION_LAYOUT`; this is the Account dashboard's own copy, passed to
# `section_layout` by `create_domain_dashboards`.
ACCOUNT_SECTION_LAYOUT: dict[str, tuple] = {
	# Two cards, two charts.  The cards side by side above their charts rather than
	# beside them: payroll cost is read as a total and then as a spread, not as four
	# things of equal weight, and two across is all these two need.
	"Payroll Cost": (
		("cards", ("Payroll Gross", "Payroll Net"), {"columns": 2}),
		# Per company first, because it is the same thirteen names every other chart on
		# this dashboard uses and the eye already knows them. Then the same money by
		# job, which has 102 and 55 categories against it and is capped at twenty bars
		# by `tune_reused_charts` - so a third of them, and a `Driver` bar says more
		# than the ninety hairline designations behind it.
		(
			"charts",
			("Payroll Gross by Company", "Payroll Net by Company"),
			{"columns": 2, "height": 260},
		),
		(
			"charts",
			("Designation Wise Salary(Last Month)", "Department Wise Salary(Last Month)"),
			{"columns": 2, "height": 280},
		),
	),
	# Seven deduction figures then three charts.  Four across, so the band reads as a
	# table of what came out rather than as a long list; "Employee Social Insurance"
	# and "Employer Social Insurance" sit next to each other on purpose, which is the
	# comparison this band exists for.
	"What Came Out": (
		(
			"cards",
			(
				"Total Deductions",
				"Income Tax Deducted",
				"Employee Social Insurance",
				"Employer Social Insurance",
			),
			{"columns": 4},
		),
		(
			"charts",
			("Deductions by Company", "MI Fund by Company"),
			{"columns": 2, "height": 260},
		),
		(
			"cards",
			("Insurance Deducted", "Loan Repayments", "Training Fund"),
			{"columns": 3},
		),
		("charts", ("Loans Repaid by Company",), {"columns": 1, "height": 260}),
	),
	# Four figures and three per-company charts.  The three charts three across and
	# 280 tall: each has thirteen companies on its axis, and at 240 the labels
	# overlapped enough to be unreadable.
	"The Ledger": (
		(
			"cards",
			("Journal Entries", "Journal Value", "Ledger Entries", "Companies Posting"),
			{"columns": 4},
		),
		(
			"charts",
			(
				"Journal Entries by Company",
				"Ledger Debit by Company",
				"Ledger Credit by Company",
			),
			{"columns": 3, "height": 280},
		),
	),
}

# (chart_name, doctype, group-by field, value field, filters, options)
# Declarative, like the workforce charts: a Dashboard Chart builds its query from
# `document_type`, so a bespoke Python method is not reachable from a chart - only
# from a Number Card, which does forward its method.
ACCOUNT_CHARTS = (
	(
		"Payroll Gross by Company",
		"Salary Slip",
		"company",
		"gross_pay",
		[["Salary Slip", "docstatus", "=", 1]],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
	(
		"Payroll Net by Company",
		"Salary Slip",
		"company",
		"net_pay",
		[["Salary Slip", "docstatus", "=", 1]],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
	(
		"Deductions by Company",
		"Salary Slip",
		"company",
		"total_deduction",
		[["Salary Slip", "docstatus", "=", 1]],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
	(
		# Named for the field it sums, which is *not* the whole employer
		# contribution: `sdg_mi_employer_share` is the MI Fund alone (66,660),
		# while employer social insurance is 300,317 and lives only on the slip's
		# child rows - a chart cannot join to the parent to filter on docstatus.
		# The card above does show the real total, from the child rows.
		"MI Fund by Company",
		"Salary Slip",
		"company",
		"sdg_mi_employer_share",
		[["Salary Slip", "docstatus", "=", 1]],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
	(
		"Journal Entries by Company",
		"Journal Entry",
		"company",
		None,
		[["Journal Entry", "docstatus", "=", 1]],
		{"type": "Bar", "number_of_groups": 20},
	),
	(
		"Ledger Debit by Company",
		"GL Entry",
		"company",
		"debit",
		[],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
	(
		"Ledger Credit by Company",
		"GL Entry",
		"company",
		"credit",
		[],
		{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
	),
)

# "Loans Repaid by Company" is defined with the workforce charts and reused here
# rather than declared twice; the workforce module owns it because the loan cards
# live on that dashboard.
REUSED_CHARTS = ("Loans Repaid by Company",)


def can_view() -> bool:
	"""Whether this user may see the cost figures.

	The same roles as the workforce dashboard, plus Accounts Manager: a book that
	cannot be seen by the people who keep it is not much use.  Delegated to
	`hr.dashboard.can_view_dashboard` so there is one answer, not two.
	"""
	return can_view_dashboard() or "Accounts Manager" in frappe.get_roles()
