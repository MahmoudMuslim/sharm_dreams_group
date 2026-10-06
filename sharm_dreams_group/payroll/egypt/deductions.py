# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Regional override for Egyptian salary slips.

Registered in hooks.py as the Egypt replacement for
`SalarySlip.apply_regional_deductions`.  HRMS calls it from
`calculate_net_pay()`, after earnings and deductions are resolved and after
lending has run, but before employer contributions are computed and
`set_net_pay()` finalises the net.  Writing our rows here is what makes them
land in `total_deduction`, `gross_pay` and `net_pay` correctly.

The arithmetic itself lives in `calculation.py` as pure functions so it can be
tested against the legacy workbook without a database.  This module is only the
adapter: read the slip, call the calculator, write the rows back.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from sharm_dreams_group.payroll.egypt.calculation import (
	TaxInputs,
	compute_tax,
	cost_share_tax,
	employer_funds,
	social_insurance,
)

# Salary components the app creates on install.  Names are stable identifiers -
# renaming one orphans every historical slip.
COMPONENT_INCOME_TAX = "Income Tax"
COMPONENT_SI_EMPLOYEE = "Social Insurance (Employee)"
COMPONENT_SI_EMPLOYER = "Social Insurance (Employer)"
COMPONENT_MARTYRS_FUND = "Martyrs Fund"
COMPONENT_EMERGENCY_FUND = "Emergency Fund"
COMPONENT_TRAINING_FUND = "Training and Development Fund"
# The MI fund is the one employer contribution the sheet states as a flat
# per-person amount rather than a rate, so the builder carries it on the slip
# and the engine only places it.
COMPONENT_MI_EMPLOYER = "MI Fund (Employer)"
# A cost share is a slice of a manager's salary charged to another group company.
# It is part of what the employee is paid, so it is an earning on their slip, but
# it is not their statutory employment: see the notes in hooks.py.
COMPONENT_COST_SHARE = "Delegated Cost Share"
COMPONENT_COST_SHARE_TAX = "Income Tax on Cost Share"
COMPONENT_MEDICAL_INSURANCE = "Medical Insurance"
COMPONENT_LIFE_INSURANCE = "Life Insurance"
# Taken off the payment rather than off taxable income.
COMPONENT_PHONE = "Phone Allowance Recovered"
COMPONENT_FAMILY_MEDICAL = "Family Medical Insurance"

REQUIRED_COMPONENTS = (
	COMPONENT_INCOME_TAX,
	COMPONENT_SI_EMPLOYEE,
	COMPONENT_SI_EMPLOYER,
	COMPONENT_MARTYRS_FUND,
	COMPONENT_EMERGENCY_FUND,
	COMPONENT_TRAINING_FUND,
)

# the components this module computes.  They are the engine's own output, so they
# must never be read back in as inputs: social insurance is already passed to the
# tax calculation separately, and income tax and the martyrs fund are results.
# Summing the whole `deductions` table subtracted social insurance twice and made
# the tax base depend on how many times the engine had already run.
COMPUTED_COMPONENTS = (
	COMPONENT_SI_EMPLOYEE,
	COMPONENT_INCOME_TAX,
	COMPONENT_MARTYRS_FUND,
)

# The insurance premium is read from the register and written on the slip, so it is
# an input like the pre-tax deductions, not something the engine produces.
REGISTERED_COMPONENTS = (
	COMPONENT_MEDICAL_INSURANCE,
	COMPONENT_LIFE_INSURANCE,
	COMPONENT_PHONE,
	COMPONENT_FAMILY_MEDICAL,
)


def apply_egypt_deductions(doc) -> None:
	"""Compute the Egyptian statutory chain and write it onto the slip.

	`doc` is the HRMS SalarySlip.  It is mutated in place; the caller saves.
	"""
	if doc.doctype != "Salary Slip":
		frappe.throw(frappe._("Expected a Salary Slip, got {0}.").format(doc.doctype))

	settings = frappe.get_cached_doc("SDG Payroll Settings")
	_require_bands()
	_require_components()

	bands = _active_bands()
	if not bands:
		frappe.throw(
			frappe._("No active slab rows on {0}. Run the seeder before a payroll run.").format(TAX_SLAB)
		)

	monthly_deductions = _pre_tax_deductions(doc)

	# A slip carries two kinds of earning: what this company employs the person
	# for, and cost shares charged to other group companies.  The workbook taxes
	# them on separate bases, so they cannot be pooled into one gross:
	#   - social insurance and the personal exemption apply to the employed
	#     portion only, and the insurance band is read on that portion's gross,
	#     not on the combined total;
	#   - the cost share is taxed at a flat rate with neither.
	# `sdg_leader_earning` and `sdg_si_limit` are written when the slip is built
	# from the payroll spec.  A slip without them is a plain single-company one.
	leader_earning = flt(doc.sdg_leader_earning) or flt(doc.gross_pay)
	cost_share_earning = max(flt(doc.gross_pay) - leader_earning, 0.0)
	band = _band_for(doc, leader_earning)
	si_limit = flt(doc.sdg_si_limit) or band.si_base

	# `sdg_si_employee_rate` lets the builder carry the sheet's own rate, which is
	# 11% everywhere except one employee hand-entered at 1%.
	# Both insurance premiums are per-person inputs, read from what the builder put
	# on the slip.  They are NOT taken from the gross insurance band: the band is a
	# scale on social insurance, and the premium varies between people whose pay is
	# the same (H0160 has no life cover at all, H0051 has 26.57), so a per-band
	# figure is wrong for most of them.
	life_premium = _row_amount(doc, "deductions", COMPONENT_LIFE_INSURANCE)
	si_employee_rate = flt(doc.sdg_si_employee_rate) or settings.si_employee_rate
	si_base, si_employee, si_employer = social_insurance(
		si_limit,
		employee_rate=si_employee_rate,
		employer_rate=settings.si_employer_rate,
		cap=0.0,
	)

	result = compute_tax(
		TaxInputs(
			monthly_total_earning=leader_earning,
			monthly_basic=_basic_of(doc),
			monthly_deductions=monthly_deductions,
			employee_social_insurance=si_employee,
			monthly_medical_premium=_medical_premium(doc),
			monthly_life_premium=life_premium,
			is_delegated=_is_delegated(doc),
			is_person_of_determination=_is_person_of_determination(doc),
			rounding_multiple=settings.tax_rounding_multiple,
		),
		bands,
		exemption_standard=settings.exemption_standard,
		exemption_determination=settings.exemption_determination,
		insurance_cap_percent=settings.insurance_cap_percent,
		insurance_cap_max=settings.insurance_cap_max,
		martyrs_fund_rate=settings.martyrs_fund_rate,
		delegated_tax_rate=settings.delegated_tax_rate,
	)

	share_tax = cost_share_tax(
		cost_share_earning,
		rounding_multiple=settings.tax_rounding_multiple,
		rate_percent=settings.delegated_tax_rate,
	)
	share_martyrs = cost_share_earning * flt(settings.martyrs_fund_rate) / 100

	_set(doc, "deductions", COMPONENT_SI_EMPLOYEE, si_employee)
	_set(doc, "deductions", COMPONENT_INCOME_TAX, result.monthly_tax)
	_set(doc, "deductions", COMPONENT_MARTYRS_FUND, result.monthly_martyrs_fund + share_martyrs)
	if cost_share_earning:
		_set(doc, "deductions", COMPONENT_COST_SHARE_TAX, share_tax)
	# the workbook takes the insurance premium off the payment as well as using it
	# to reduce the tax base, so it has to appear on the slip
	medical = _medical_premium(doc)
	if medical:
		_set(doc, "deductions", COMPONENT_MEDICAL_INSURANCE, medical)
	# the life premium is an input, so it is left as the builder wrote it

	funds = employer_funds(
		monthly_basic=_basic_of(doc),
		si_base=si_base,
		training_fund_rate=settings.training_fund_rate,
		emergency_fund_rate=settings.emergency_fund_rate,
		emergency_base_cap=settings.emergency_base_cap,
	)
	_set(doc, "employer_contributions", COMPONENT_SI_EMPLOYER, si_employer)
	_set(doc, "employer_contributions", COMPONENT_TRAINING_FUND, funds["training_fund"])
	_set(doc, "employer_contributions", COMPONENT_EMERGENCY_FUND, funds["emergency_fund"])
	# The MI fund is not a rate, so it arrives as an input like the insurance
	# premiums.  It is an employer cost, so it must not touch net pay.
	mi = flt(doc.sdg_mi_employer_share)
	if mi:
		_set(doc, "employer_contributions", COMPONENT_MI_EMPLOYER, mi)

	# Keep the slip self-describing: these are the numbers the tax was built on.
	doc.sdg_annual_tax_base = result.annual_tax_base_rounded
	doc.sdg_annual_taxable = result.annual_taxable
	doc.sdg_annual_income_tax = result.annual_tax
	doc.sdg_insurance_deductible = result.insurance_deductible
	doc.sdg_personal_exemption = result.exemption_monthly


# ---------------------------------------------------------------------------
# reading the slip
# ---------------------------------------------------------------------------
def _band_for(doc, gross: float | None = None):
	"""The `SDG Gross Insurance Band` for this month's employed gross.

	`gross` defaults to the slip's own gross.  A slip carrying cost shares passes
	the employed portion only, because the band is a scale on what the company
	pays the person, not on what other group companies are charged for them.
	"""
	gross = flt(doc.gross_pay) if gross is None else flt(gross)
	name = frappe.db.get_value(
		"SDG Gross Insurance Band",
		{"active": 1, "from_gross": ("<=", gross), "to_gross": (">", gross)},
		"name",
		order_by="from_gross desc",
	)
	if not name:
		name = frappe.db.get_value(
			"SDG Gross Insurance Band", {"active": 1}, "name", order_by="from_gross asc"
		)
	if not name:
		frappe.throw(
			frappe._("No active SDG Gross Insurance Band rows. Run the seeder before a payroll run.")
		)
	return frappe.get_cached_doc("SDG Gross Insurance Band", name)


def _is_delegated(doc) -> bool:
	"""Always False: a Salary Slip is never itself a cost share.

	A cost share is an earning line on the employing company's slip for the
	person, with `SDG Group Allocation` records saying who is charged.  So the
	slip is always the statutory employment and always gets the exemption, and
	only the cost-share portion of its gross is taxed flat - see `apply_egypt_deductions`.
	"""
	return False


def _is_person_of_determination(doc) -> bool:
	if not doc.employee:
		return False
	return bool(
		frappe.db.get_value(
			"Custom Field",
			{"dt": "Employee", "fieldname": "sdg_is_person_of_determination"},
			"name",
		)
	) and bool(frappe.db.get_value("Employee", doc.employee, "sdg_is_person_of_determination"))


def _basic_of(doc) -> float:
	for row in doc.get("earnings") or []:
		if row.salary_component == "Basic":
			return flt(row.amount)
	return 0.0


def _medical_premium(doc) -> float:
	"""The employee's monthly medical premium.

	Whatever the builder has already put on the slip wins, because that is the
	per-person figure the Master Payroll sheet charged, and the band table cannot
	replace it: `SDG Medical Band` is flagged `needs_review` and its own data shows
	57 people on one band paying 232.50 while others on the same band pay 310 or
	775.  Preferring the band silently overwrote every employee's cover with the
	band price and moved the payroll by 5,839.30.

	The register is still the record of *who* is covered, which is what it is good
	for; the price comes from the payroll until the insurer's rate schedule exists.
	"""
	existing = _row_amount(doc, "deductions", COMPONENT_MEDICAL_INSURANCE)
	if existing:
		return existing
	if doc.employee and frappe.db.exists("DocType", "SDG Insured Person"):
		band = frappe.db.get_value("SDG Insured Person", {"employee": doc.employee}, "medical_band")
		if band:
			premium = frappe.db.get_value("SDG Medical Band", {"band": band, "active": 1}, "monthly_premium")
			if premium:
				return flt(premium)
	return 0.0


def _row_amount(doc, table: str, component: str) -> float:
	"""What a component is currently set to on a slip table."""
	for row in doc.get(table) or []:
		if row.salary_component == component:
			return flt(row.amount)
	return 0.0


# The only deductions that come off the package before tax.  This is an explicit
# list rather than "everything the engine does not compute": the slip also carries
# the insurance premium, the phone and family medical recoveries, and the cost-share
# tax, none of which reduce taxable income.  Summing those into the tax base
# understated income tax badly - on H0039 it took 12,827.92 down to 2,697.79.
PRE_TAX_COMPONENTS = ("Penalties", "Absenteeism")


# The tax table.  Stock `Income Tax Slab` holds the bands in its `slabs` child table;
# this app's own `SDG Tax Bracket` did the same job with a shape Frappe could not read,
# which is why the table is written and queried through the stock one.  `sdg_fixed_amount`
# on the child row carries the cumulative tax at the bottom of the band - see
# `setup.create_slab_fixed_amount` for why the stock shape needs it.
TAX_SLAB = "Income Tax Slab"


def _active_bands() -> list[dict]:
	"""The live bracket, in the shape `calculation.bracket_tax` expects.

	One slab is used for the whole group: the Egyptian table is national, and the 13
	companies are all taxed on it.  A slab scoped to a company still wins for that
	company, so a group that later needs per-entity brackets can set one up without a
	code change.

	`to_amount` is the band's ceiling and maps to `up_to`; the highest band is stored
	open-ended, and `bracket_tax` already charges anything above the last figure at
	that band's rate, so the value is only a very large number.
	"""
	# The group-wide slab wins: the Egyptian table is national, so one record with no
	# company serves all thirteen.  A slab scoped to a company is used only if there
	# is no group-wide one, which is what lets a group with per-entity brackets set
	# them up without a code change.
	row = frappe.db.sql(
		"""
		select name
		from `tabIncome Tax Slab`
		where ifnull(company, '') = '' and docstatus < 2
		order by modified desc
		limit 1
		""",
		as_list=True,
	)
	if not row:
		return []
	rows = frappe.get_all(
		"Taxable Salary Slab",
		filters={"parent": row[0][0], "parenttype": TAX_SLAB},
		fields=["to_amount", "percent_deduction", "sdg_fixed_amount"],
		order_by="to_amount asc",
	)
	return [
		{
			"up_to": r["to_amount"],
			"rate": r["percent_deduction"],
			"fixed_amount": r["sdg_fixed_amount"],
		}
		for r in rows
	]


def _pre_tax_deductions(doc) -> float:
	"""The workbook's own deduction columns: penalties and absenteeism."""
	return sum(
		flt(row.amount) for row in (doc.get("deductions") or []) if row.salary_component in PRE_TAX_COMPONENTS
	)


# ---------------------------------------------------------------------------
# writing the slip
# ---------------------------------------------------------------------------
def _set(doc, table: str, component: str, amount: float) -> None:
	"""Set a component's amount on a slip table, adding the row if it is absent.

	Re-running a payroll must not append duplicate rows, so an existing row for
	the component is updated in place.
	"""
	for row in doc.get(table) or []:
		if row.salary_component == component:
			row.amount = flt(amount)
			return

	doc.append(
		table,
		{"salary_component": component, "abbr": component, "amount": flt(amount)},
	)


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------
def _require_bands() -> None:
	for band in (TAX_SLAB, "SDG Gross Insurance Band", "SDG Payroll Settings"):
		if not frappe.db.exists("DocType", band):
			frappe.throw(frappe._("{0} is missing. Is sharm_dreams_group installed?").format(band))


def _require_components() -> None:
	missing = [name for name in REQUIRED_COMPONENTS if not frappe.db.exists("Salary Component", name)]
	if missing:
		frappe.throw(
			frappe._("Missing salary component(s): {0}").format(", ".join(missing)),
			title=frappe._("Run the seeder"),
		)
