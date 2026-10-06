# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Egyptian payroll arithmetic, transcribed from the legacy workbook.

Everything here is a pure function: no database, no document, no settings object.
That is deliberate.  The whole point of this module is that its output can be
compared cell-for-cell against the 207-row workbook it replaces, which is only
possible if the arithmetic does not depend on site state.

The column letters in the docstrings refer to the legacy workbook and are kept
because they are the only way to audit a change against it.

Chain, verified against `Master Payroll (corrected tax + PoD flag).xlsx`:

    AB  Total Earning                       monthly gross
    AG  Total Deductions                    AF (absenteeism) + AD (penalties)
    AK  SI-EE                               employee social insurance
    AL  Tax Base                            (AB - AK - AG - exemption) * 12
    AM  Round to the nearest 10             FLOOR(AL, 10)
    AN  15% of the tax base                 the statutory cap on deductible cover
    AO  Annual insurance premium            (AI medical + AH life) * 12
    AP  Insurance deductible from tax base  MIN(AO, 10000, AN)
    AQ  Net tax base                        MAX(AM - AP, 0)
    AR  Tax                                 bracket(AQ) / 12
    AS  Martyrs fund                        P * 5 / 10000
    AT  Total deductions                    AK + AR + AS + AG + AI + AH
    AU  Net salary                          AB - AT
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MONTHS_PER_YEAR = 12


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------
def floor_to(value: float, multiple: float) -> float:
	"""Excel's FLOOR(x, 10) - round *down* to a multiple, and never below zero.

	`round()` would be wrong here: the workbook deliberately truncates the tax
	base, which keeps the tax marginally lower than a rounded base would give.
	"""
	if multiple <= 0:
		return value
	return math.floor(value / multiple) * multiple


def bracket_tax(annual_taxable: float, bands: list[dict]) -> float:
	"""Progressive tax on `annual_taxable`.

	`bands` is the active `Income Tax Slab` child rows, ascending, each with `up_to`,
	`rate` and `fixed_amount`.  `fixed_amount` is the cumulative tax due at the
	bottom of the band, so the result is

		fixed_amount + (income - lower_bound) * rate

	Income above the table is charged at the final band's rate, matching the
	workbook's `(AQ-1200000)*27.5% + 274750` branch.
	"""
	if annual_taxable <= 0 or not bands:
		return 0.0

	lower = 0.0
	for band in bands:
		upper = float(band["up_to"])
		if annual_taxable <= upper:
			return float(band["fixed_amount"]) + (annual_taxable - lower) * float(band["rate"]) / 100
		lower = upper

	final = bands[-1]
	return float(final["fixed_amount"]) + (annual_taxable - lower) * float(final["rate"]) / 100


# ---------------------------------------------------------------------------
# the tax chain
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaxInputs:
	"""One employee's monthly figures, as the workbook holds them.

	`monthly_total_earning` and `monthly_basic` are deliberately separate.  The tax
	base is built from the *total earning* (AB = basic plus allowances plus
	overtime), while the martyrs fund is a flat percentage of the *basic*
	(P).  Conflating them over-charges martyrs on anyone with allowances.
	"""

	monthly_total_earning: float  # AB  - drives the tax base
	monthly_basic: float  # P  - drives the martyrs fund
	monthly_deductions: float  # AG  = absenteeism + penalties
	employee_social_insurance: float  # AK
	monthly_medical_premium: float  # AI
	monthly_life_premium: float  # AH
	is_delegated: bool = False
	is_person_of_determination: bool = False
	rounding_multiple: float = 10.0


@dataclass(frozen=True)
class TaxResult:
	"""Every intermediate the workbook shows, so a run can be audited."""

	exemption_monthly: float
	annual_tax_base: float  # AL
	annual_tax_base_rounded: float  # AM
	insurance_cap_15pct: float  # AN
	annual_insurance_premium: float  # AO
	insurance_deductible: float  # AP
	annual_taxable: float  # AQ
	annual_tax: float
	monthly_tax: float  # AR
	monthly_martyrs_fund: float  # AS


def personal_exemption(inputs: TaxInputs, standard: float, determination: float) -> float:
	"""Monthly personal exemption, AL/IF(F="",0,IF(BJ="Yes",2500,1666.66)).

	A delegated row gets zero because the statutory employment sits on the leader
	row; applying the exemption twice would understate the group's withholding.
	"""
	if inputs.is_delegated:
		return 0.0
	return determination if inputs.is_person_of_determination else standard


def insurance_deductible(
	annual_base_rounded: float,
	annual_insurance_premium: float,
	cap_percent: float,
	cap_max: float,
) -> float:
	"""AP: the cover premium actually allowed against the tax base.

	AP = MIN(annual premium, 10000, 15% of the tax base).  The workbook spells
	this out as a twelve-case truth table; the three MINs are equivalent to all
	twelve branches and are asserted against the truth table in the tests.
	"""
	if annual_base_rounded <= 0 or annual_insurance_premium <= 0:
		return 0.0
	allowed = min(cap_max, annual_base_rounded * cap_percent / 100)
	return min(annual_insurance_premium, allowed)


def compute_tax(
	inputs: TaxInputs,
	bands: list[dict],
	*,
	exemption_standard: float,
	exemption_determination: float,
	insurance_cap_percent: float,
	insurance_cap_max: float,
	martyrs_fund_rate: float = 0.05,
	delegated_tax_rate: float = 10.0,
) -> TaxResult:
	"""Run the whole chain, workbook order, from monthly gross to monthly tax.

	A delegated row is not taxed on the bracket table.  Because the statutory
	employment sits on the leader row, the group company withholds a flat
	`delegated_tax_rate` on the row's own annual taxable income, which is the
	workbook's `(AQ*10%)/12`.
	"""
	exemption = personal_exemption(inputs, exemption_standard, exemption_determination)

	# AL
	annual_base = (
		inputs.monthly_total_earning
		- inputs.employee_social_insurance
		- inputs.monthly_deductions
		- exemption
	) * MONTHS_PER_YEAR

	# AM - floored, and never negative
	annual_base_rounded = floor_to(annual_base, inputs.rounding_multiple) if annual_base > 0 else 0.0

	# AN
	insurance_cap = annual_base_rounded * insurance_cap_percent / 100

	# AO - the cover that can actually be offset against tax
	annual_premium = (inputs.monthly_medical_premium + inputs.monthly_life_premium) * MONTHS_PER_YEAR

	# AP
	deductible = insurance_deductible(
		annual_base_rounded, annual_premium, insurance_cap_percent, insurance_cap_max
	)

	# AQ
	annual_taxable = max(annual_base_rounded - deductible, 0.0)

	# AR - annual tax, then divided down to a monthly figure
	if inputs.is_delegated:
		annual_tax = annual_taxable * delegated_tax_rate / 100
	else:
		annual_tax = bracket_tax(annual_taxable, bands)

	return TaxResult(
		exemption_monthly=exemption,
		annual_tax_base=annual_base,
		annual_tax_base_rounded=annual_base_rounded,
		insurance_cap_15pct=insurance_cap,
		annual_insurance_premium=annual_premium,
		insurance_deductible=deductible,
		annual_taxable=annual_taxable,
		annual_tax=annual_tax,
		monthly_tax=annual_tax / MONTHS_PER_YEAR,
		monthly_martyrs_fund=inputs.monthly_basic * martyrs_fund_rate / 100,
	)


def cost_share_tax(monthly_earnings: float, *, rounding_multiple: float, rate_percent: float) -> float:
	"""**Monthly** tax on a delegated cost share: a flat rate on the annualised share.

	The cost share is not the person's statutory employment, so it gets no
	personal exemption and no social insurance, and it is not run through the
	bracket table - the group company withholds a flat `rate_percent` on it.  The
	annual figure is floored to `rounding_multiple` first, exactly as the leader
	portion is, then divided back down to a month like every other tax figure on
	the slip.  Returning the annual figure here charged H0039 39,717.00 of cost
	share tax instead of 3,309.67.
	"""
	if monthly_earnings <= 0:
		return 0.0
	annual = floor_to(monthly_earnings * MONTHS_PER_YEAR, rounding_multiple)
	return annual * rate_percent / 100 / MONTHS_PER_YEAR


# ---------------------------------------------------------------------------
# social insurance and the small employer-side funds
# ---------------------------------------------------------------------------
def social_insurance(
	si_base: float,
	*,
	employee_rate: float,
	employer_rate: float,
	cap: float = 0.0,
) -> tuple[float, float, float]:
	"""Employee and employer social insurance.

	Returns `(base, employee_amount, employer_amount)`.  The workbook takes the
	base from the gross-band `Final Limit` (AJ) rather than deriving a cap from
	gross, so `cap` is only a safety net against a hand-entered base.
	"""
	base = max(float(si_base), 0.0)
	if cap > 0:
		base = min(base, cap)
	return base, base * employee_rate / 100, base * employer_rate / 100


def employer_funds(
	monthly_basic: float,
	si_base: float,
	*,
	training_fund_rate: float,
	emergency_fund_rate: float,
	emergency_base_cap: float,
) -> dict[str, float]:
	"""Employer-side funds that are shown on the slip but never touch net pay.

	Emergency is charged on basic (P) but capped at a base
	(`Emergency = min(P,2720)*1%`), which is why it has its own cap parameter.
	"""
	emergency_base = min(monthly_basic, emergency_base_cap) if emergency_base_cap > 0 else monthly_basic
	return {
		"social_insurance_employer": 0.0,  # filled in by the caller, needs the rate
		"training_fund": si_base * training_fund_rate / 100,
		"emergency_fund": emergency_base * emergency_fund_rate / 100,
	}
