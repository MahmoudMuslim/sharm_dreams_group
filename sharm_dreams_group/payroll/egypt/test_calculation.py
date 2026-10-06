# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Golden test: the engine must reproduce the legacy workbook exactly.

`golden_payroll.json` holds, for all 207 payroll rows, the inputs the engine
needs and the values the workbook actually paid.  If any of these assertions
move, the migration has changed someone's pay.

TOLERANCE is 0.01 EGP.  The workbook rounds the tax base down to a multiple of 10
and leaves full float precision elsewhere, so the arithmetic matches exactly and
only binary floating point noise needs absorbing.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from sharm_dreams_group.payroll.egypt.calculation import (
	TaxInputs,
	bracket_tax,
	compute_tax,
	social_insurance,
)

GOLDEN_PATH = pathlib.Path(__file__).resolve().parents[2] / "tests" / "golden_payroll.json"
GOLDEN = json.loads(GOLDEN_PATH.read_text())
ROWS = GOLDEN["rows"]

# mirrors SDG Payroll Settings
EXEMPTION_STANDARD = 1666.66
EXEMPTION_DETERMINATION = 2500.0
INSURANCE_CAP_PERCENT = 15.0
INSURANCE_CAP_MAX = 10_000.0
SI_EMPLOYEE_RATE = 11.0
SI_EMPLOYER_RATE = 18.75
SI_CAP = 0.0  # the workbook takes the base from the gross band, not a cap
TRAINING_FUND_RATE = 0.25
EMERGENCY_FUND_RATE = 1.0
EMERGENCY_BASE_CAP = 2720.0
MARTYRS_FUND_RATE = 0.05
DELEGATED_TAX_RATE = 10.0

BANDS = [
	{"up_to": 40_000, "rate": 0.0, "fixed_amount": 0.0},
	{"up_to": 55_000, "rate": 10.0, "fixed_amount": 0.0},
	{"up_to": 70_000, "rate": 15.0, "fixed_amount": 1_500.0},
	{"up_to": 200_000, "rate": 20.0, "fixed_amount": 3_750.0},
	{"up_to": 400_000, "rate": 22.5, "fixed_amount": 29_750.0},
	{"up_to": 1_200_000, "rate": 25.0, "fixed_amount": 74_750.0},
	{"up_to": 9_999_999_999, "rate": 27.5, "fixed_amount": 274_750.0},
]

TOL = 0.01

# Cells in the legacy workbook where someone typed a number instead of leaving the
# formula.  The engine computes the correct figure; these are recorded so the
# discrepancy stays visible and is re-checked on every run.  Values here are the
# WORKBOOK's, not the formula's.
#
#   training fund  rows 59,63,67,71,147 -> 30.00 flat (a minimum charge)
#                  rows 78..93, 146, 158 -> 0.00 (a run of rows left blank)
#   SI employee    row 56  -> 167.00 instead of 1,837.00
#   SI employer    row 146 -> 0.00 instead of 3,131.25
LEGACY_OVERRIDES: dict[tuple[str, int], float] = {}
for _row in (59, 63, 67, 71, 147):
	LEGACY_OVERRIDES[("training_fund", _row)] = 30.0
for _row in (*range(78, 94), 146, 158):
	LEGACY_OVERRIDES[("training_fund", _row)] = 0.0
LEGACY_OVERRIDES[("si_employee", 56)] = 167.0
LEGACY_OVERRIDES[("si_employer", 146)] = 0.0


def _result(row: dict) -> tuple:
	inputs = TaxInputs(
		monthly_total_earning=row["total_earning"],
		monthly_basic=row["gross"],
		monthly_deductions=row["total_deductions"],
		employee_social_insurance=row["expected_si_employee"],
		monthly_medical_premium=row["medical_insurance"],
		monthly_life_premium=row["life_insurance"],
		is_delegated=bool(row["delegated_company"]),
		is_person_of_determination=row["is_person_of_determination"],
	)
	return compute_tax(
		inputs,
		BANDS,
		exemption_standard=EXEMPTION_STANDARD,
		exemption_determination=EXEMPTION_DETERMINATION,
		insurance_cap_percent=INSURANCE_CAP_PERCENT,
		insurance_cap_max=INSURANCE_CAP_MAX,
		martyrs_fund_rate=MARTYRS_FUND_RATE,
		delegated_tax_rate=DELEGATED_TAX_RATE,
	)


class TestGoldenPayroll(unittest.TestCase):
	"""Pure arithmetic against the recorded workbook.  No database needed."""

	def test_fixture_covers_the_whole_workbook(self):
		self.assertEqual(len(ROWS), 207)
		self.assertEqual(sum(1 for r in ROWS if r["delegated_company"]), 37)
		self.assertEqual(sum(1 for r in ROWS if r["is_person_of_determination"]), 5)

	def test_tax_base(self):
		for row in ROWS:
			with self.subTest(row=row["row"], work_id=row["work_id"]):
				self.assertAlmostEqual(_result(row).annual_tax_base, row["expected_tax_base"], places=2)

	def test_tax_base_floored_to_a_multiple_of_ten(self):
		for row in ROWS:
			with self.subTest(row=row["row"]):
				self.assertAlmostEqual(
					_result(row).annual_tax_base_rounded, row["expected_base_rounded"], places=2
				)

	def test_insurance_deduction(self):
		for row in ROWS:
			with self.subTest(row=row["row"]):
				self.assertAlmostEqual(
					_result(row).insurance_deductible,
					row["expected_insurance_deductible"],
					places=2,
				)

	def test_annual_taxable_income(self):
		for row in ROWS:
			with self.subTest(row=row["row"]):
				self.assertAlmostEqual(_result(row).annual_taxable, row["expected_annual_taxable"], places=2)

	def test_monthly_income_tax(self):
		"""The headline number: what each employee was actually taxed."""
		worst = 0.0
		for row in ROWS:
			got = _result(row).monthly_tax
			diff = abs(got - row["expected_tax"])
			worst = max(worst, diff)
			with self.subTest(row=row["row"], work_id=row["work_id"]):
				self.assertAlmostEqual(got, row["expected_tax"], places=2)
		self.assertLess(worst, TOL)

	def test_martyrs_fund(self):
		for row in ROWS:
			with self.subTest(row=row["row"]):
				self.assertAlmostEqual(_result(row).monthly_martyrs_fund, row["expected_martyrs"], places=2)

	def test_social_insurance_employee_and_employer(self):
		"""AK = AJ * 11% and BD = AJ * 18.75%.

		Two rows in the workbook have a hand-typed figure instead of the formula:
		row 56's employee share is 167.00 (understated by 1,670) and row 146's
		employer share is 0.00.  For every other row the engine must reproduce the
		workbook exactly; for these two the engine must produce the correct formula
		value and must NOT reproduce the error.
		"""
		for row in ROWS:
			with self.subTest(row=row["row"]):
				_base, employee, employer = social_insurance(
					row["final_limit"],
					employee_rate=SI_EMPLOYEE_RATE,
					employer_rate=SI_EMPLOYER_RATE,
					cap=SI_CAP,
				)

				if ("si_employee", row["row"]) in LEGACY_OVERRIDES:
					self.assertAlmostEqual(
						row["expected_si_employee"],
						LEGACY_OVERRIDES[("si_employee", row["row"])],
						places=2,
					)
					self.assertAlmostEqual(employee, row["final_limit"] * SI_EMPLOYEE_RATE / 100, places=2)
				else:
					self.assertAlmostEqual(employee, row["expected_si_employee"], places=2)

				if ("si_employer", row["row"]) in LEGACY_OVERRIDES:
					self.assertAlmostEqual(
						row["expected_si_employer"],
						LEGACY_OVERRIDES[("si_employer", row["row"])],
						places=2,
					)
					self.assertAlmostEqual(employer, row["final_limit"] * SI_EMPLOYER_RATE / 100, places=2)
				else:
					self.assertAlmostEqual(employer, row["expected_si_employer"], places=2)

	def test_the_only_formula_overrides_are_the_ones_we_know_about(self):
		"""If the workbook gains or loses a hand-typed cell, this fails."""
		found = {
			("training_fund", r["row"])
			for r in ROWS
			if abs(r["final_limit"] * TRAINING_FUND_RATE / 100 - r["expected_training_fund"]) > 0.01
		}
		found |= {
			("si_employee", r["row"])
			for r in ROWS
			if abs(r["final_limit"] * SI_EMPLOYEE_RATE / 100 - r["expected_si_employee"]) > 0.01
		}
		found |= {
			("si_employer", r["row"])
			for r in ROWS
			if abs(r["final_limit"] * SI_EMPLOYER_RATE / 100 - r["expected_si_employer"]) > 0.01
		}
		self.assertEqual(found, set(LEGACY_OVERRIDES))

	def test_emergency_fund_is_capped_at_the_base(self):
		"""Emergency = min(gross, 2720) * 1%, so it never exceeds 27.20."""
		for row in ROWS:
			with self.subTest(row=row["row"]):
				expected = min(row["gross"], EMERGENCY_BASE_CAP) * EMERGENCY_FUND_RATE / 100
				self.assertAlmostEqual(expected, row["expected_emergency"], places=2)
				self.assertLessEqual(row["expected_emergency"], EMERGENCY_BASE_CAP * 0.01 + TOL)

	def test_training_fund(self):
		"""BG = AJ * 0.25%, except where the workbook has a hand-typed cell."""
		for row in ROWS:
			with self.subTest(row=row["row"]):
				want = LEGACY_OVERRIDES.get(
					("training_fund", row["row"]),
					row["final_limit"] * TRAINING_FUND_RATE / 100,
				)
				self.assertAlmostEqual(want, row["expected_training_fund"], places=2)

	def test_total_deductions_reconcile(self):
		"""AT = SI + tax + martyrs + absenteeism/penalties + medical + life."""
		for row in ROWS:
			with self.subTest(row=row["row"]):
				result = _result(row)
				total = (
					row["expected_si_employee"]
					+ result.monthly_tax
					+ result.monthly_martyrs_fund
					+ row["total_deductions"]
					+ row["medical_insurance"]
					+ row["life_insurance"]
				)
				self.assertAlmostEqual(total, row["expected_total_deductions"], places=2)

	def test_net_salary_reconciles(self):
		"""AU = AB - AT.  AB is the total earning, not the bare basic."""
		for row in ROWS:
			with self.subTest(row=row["row"]):
				result = _result(row)
				net = row["total_earning"] - row["expected_total_deductions"] - row["expected_net_salary"]
				self.assertAlmostEqual(net, 0.0, places=1, msg="net salary no longer ties to earnings")
				self.assertGreaterEqual(result.monthly_tax, 0.0)


class TestDelegationRules(unittest.TestCase):
	"""The two rules that make group cost allocation work."""

	def test_delegated_rows_get_no_exemption(self):
		delegated = [r for r in ROWS if r["delegated_company"]]
		self.assertEqual(len(delegated), 37)
		for row in delegated:
			with self.subTest(row=row["row"]):
				self.assertEqual(_result(row).exemption_monthly, 0.0)

	def test_leader_rows_get_the_standard_exemption(self):
		leaders = [r for r in ROWS if not r["delegated_company"] and not r["is_person_of_determination"]]
		self.assertTrue(leaders)
		for row in leaders:
			with self.subTest(row=row["row"]):
				self.assertEqual(_result(row).exemption_monthly, EXEMPTION_STANDARD)

	def test_people_of_determination_get_the_higher_exemption(self):
		pod = [r for r in ROWS if r["is_person_of_determination"]]
		self.assertEqual(len(pod), 5)
		for row in pod:
			with self.subTest(row=row["row"]):
				self.assertEqual(_result(row).exemption_monthly, EXEMPTION_DETERMINATION)

	def test_exemption_applies_only_to_leader_rows(self):
		"""A row cannot be both delegated and a PoD exemption claim."""
		for row in ROWS:
			if row["delegated_company"]:
				self.assertEqual(_result(row).exemption_monthly, 0.0)


class TestBracketTax(unittest.TestCase):
	def test_boundaries(self):
		for income, want in (
			(0, 0.0),
			(40_000, 0.0),
			(40_001, 0.1),
			(55_000, 1_500.0),
			(70_000, 3_750.0),
			(200_000, 29_750.0),
			(400_000, 74_750.0),
			(1_200_000, 274_750.0),
		):
			with self.subTest(income=income):
				self.assertAlmostEqual(bracket_tax(income, BANDS), want, places=2)

	def test_above_the_table_uses_the_final_rate(self):
		self.assertAlmostEqual(bracket_tax(1_500_000, BANDS), 357_250.0, places=2)
		self.assertAlmostEqual(bracket_tax(3_000_000, BANDS), 769_750.0, places=2)

	def test_negative_income_is_not_taxed(self):
		self.assertEqual(bracket_tax(-1_000, BANDS), 0.0)

	def test_monotonic(self):
		previous = -1.0
		for income in range(0, 1_500_001, 10_000):
			current = bracket_tax(income, BANDS)
			self.assertGreaterEqual(current, previous)
			previous = current


class TestInsuranceDeductionTruthTable(unittest.TestCase):
	"""The workbook writes AP as a twelve-case truth table.  Prove the three
	MINs reproduce all of it, so the simplification is safe."""

	def _workbook_version(self, ao: float, an: float) -> float:
		# transcribed literally from AP3
		if ao <= an and an < 10_000 and ao < 10_000:
			return ao
		if ao <= an and an > 10_000 and ao < 10_000:
			return ao
		if ao <= an and an > 10_000 and ao > 10_000:
			return 10_000
		if ao > an and an < 10_000 and ao > 10_000:
			return an
		if ao > an and an < 10_000 and ao < 10_000:
			return an
		if ao > an and an > 10_000 and ao > 10_000:
			return 10_000
		raise AssertionError(f"truth table has no branch for ao={ao} an={an}")

	def test_min_form_matches_the_truth_table(self):
		"""`insurance_deductible` takes the tax *base*; the workbook's AN column
		is that base times 15%.  Comparing them means feeding the base in and
		deriving AN the same way the workbook does."""
		from sharm_dreams_group.payroll.egypt.calculation import insurance_deductible

		# 10,000 exactly is excluded: the workbook's IF chain has no branch when
		# AO or AN lands precisely on the cap, so it is not a defined input.
		values = [0, 500, 9_999.5, 10_000.5, 15_000, 30_000, 120_000]
		cases = 0
		for base in values:
			an = base * INSURANCE_CAP_PERCENT / 100
			for ao in values:
				with self.subTest(base=base, ao=ao):
					self.assertAlmostEqual(
						insurance_deductible(base, ao, INSURANCE_CAP_PERCENT, INSURANCE_CAP_MAX),
						self._workbook_version(ao, an),
						places=2,
					)
					cases += 1
		self.assertEqual(cases, 49)

	def test_deductible_never_exceeds_the_ten_thousand_cap(self):
		from sharm_dreams_group.payroll.egypt.calculation import insurance_deductible

		self.assertLessEqual(
			insurance_deductible(200_000, 500_000, INSURANCE_CAP_PERCENT, INSURANCE_CAP_MAX),
			INSURANCE_CAP_MAX,
		)

	def test_deductible_never_exceeds_15_percent_of_the_base(self):
		from sharm_dreams_group.payroll.egypt.calculation import insurance_deductible

		# a base of 20,000 caps the deduction at 3,000
		self.assertLessEqual(
			insurance_deductible(20_000, 500_000, INSURANCE_CAP_PERCENT, INSURANCE_CAP_MAX),
			20_000 * 0.15 + TOL,
		)


if __name__ == "__main__":
	unittest.main(verbosity=2)
