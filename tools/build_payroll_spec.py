"""Build `payroll_spec.json` from the Master Payroll sheet.

One record per **person**, not per workbook row.  The workbook has 207 rows for
170 people: a manager who is cost-charged to other companies has one extra row per
charge, and the sheet's own "Final approved remuneration for payment" column is

    BA = AV - (loan + phone + family medical) + refunds
    AV = own row net + every delegated row's net

so the remuneration belongs to the person, and the delegated rows are part of it.
This file records both parts per person so the payroll can be built as one slip.

Every figure the sheet computes by formula is recomputed here from the raw inputs
rather than read from the cached cell, because the sheet has stale cached values in
at least one row (H0355).  Run `check()` to prove the recomputation reproduces the
sheet's own net salary for every row before trusting it.

Run on the host, where the workbook lives:

    python3 tools/build_payroll_spec.py
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

import openpyxl

WORKBOOK = pathlib.Path("/home/mahmoud/Master Payroll (corrected tax + PoD flag).xlsx")
OUT = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / "payroll_spec.json"

# 1-based column numbers, read off the sheet's own header row.
COL = {
	"row": 1,
	"work_id": 2,
	"name": 3,
	"company": 5,
	"delegated_company": 6,
	"position": 7,
	"department": 8,
	"national_id": 10,
	"si_number": 11,
	"date_of_joining": 12,
	"date_of_birth": 14,
	"basic": 16,  # P  Gross Salary
	"incentive": 19,  # S  Fixed Monthly Incentives
	"overtime": 20,  # T  Overtime
	"rest_allowance": 21,  # U  Rest Allowance Value
	"housing_transport": 22,  # V  Transportation + Housing
	"exceptional_bonus": 23,  # W  Exceptional bonus
	"days_off_days": 24,  # X  Days off
	"days_off_value": 25,  # Y  Days off V
	"official_days": 26,  # Z  Official Days
	"official_value": 27,  # AA Official Days  V
	"total_earning": 28,  # AB
	"penalty_days": 29,  # AC
	"penalties": 30,  # AD
	"absent_days": 31,  # AE
	"absenteeism": 32,  # AF
	"other_deductions": 33,  # AG
	"life_insurance": 34,  # AH
	"medical_insurance": 35,  # AI
	"si_limit": 36,  # AJ  Final Limit
	"si_employee": 37,  # AK
	"tax_base": 38,  # AL
	"tax_base_rounded": 39,  # AM
	"insurance_cap": 40,  # AN
	"annual_premium": 41,  # AO
	"insurance_deductible": 42,  # AP
	"annual_taxable": 43,  # AQ
	"tax": 44,  # AR
	"martyrs": 45,  # AS
	"total_deductions": 46,  # AT
	"net_salary": 47,  # AU
	"loan": 49,  # AW  company Loan
	"phone": 50,  # AX
	"family_medical": 51,  # AY
	"refund": 52,  # AZ
	"final_approved": 53,  # BA
	"si_employer": 56,  # BD
	# MI employer's share.  Like SI-ER this is an employer-side cost: it is not
	# part of the sheet's "Total Ded" and does not touch "Net Salary", so it only
	# adds to the company's employment cost.  108 of the sheet's rows carry it.
	"mi_employer_share": 55,  # BC
	"emergency": 57,  # BE
	"training_fund": 59,  # BG
	"person_of_determination": 62,  # BJ
}

EARNING_COLUMNS = {
	"basic": COL["basic"],
	"Fixed Monthly Incentives": COL["incentive"],
	"Overtime": COL["overtime"],
	"Rest Allowance": COL["rest_allowance"],
	"Housing & Transport": COL["housing_transport"],
	"Exceptional Bonus": COL["exceptional_bonus"],
	"Days off Value": COL["days_off_value"],
	"Official Days Value": COL["official_value"],
}

# Social insurance is AJ * 11% everywhere except row 56, where the sheet was
# hand-edited to AJ * 1%.  Carried through as an explicit override rather than
# "fixed" in the data, so the exception is visible and reviewable.
SI_EMPLOYEE_RATE_OVERRIDES = {56: 0.01}

# No Total Earning overrides.  There used to be one here, for row 146 (H0355),
# whose cell is a typed-in 19,114 rather than the sheet's own SUM, and it did not
# equal the sum of its components (18,874).  The group confirmed the 240 is an
# error, so the row is rebuilt from its components like every other row.  His net
# is 7,258.06 rather than the 7,450.06 the sheet shows, and that 192.06
# difference is deliberate.

# The sheet's nested IF in AR: (taxable - band_low) * rate + fixed.
# (upper bound, lower bound, rate, fixed) - the fixed amounts already encode the
# tax accumulated in the bands below, so they are not recomputed here.
TAX_BANDS = (
	(40000.0, 0.0, 0.0, 0.0),
	(55000.0, 40000.0, 0.10, 0.0),
	(70000.0, 55000.0, 0.15, 1500.0),
	(200000.0, 70000.0, 0.20, 3750.0),
	(400000.0, 200000.0, 0.225, 29750.0),
	(1200000.0, 400000.0, 0.25, 74750.0),
)
TOP_BAND = (1200000.0, 0.275, 274750.0)
DELEGATED_TAX_RATE = 0.10

EXEMPTION_STANDARD = 1666.66
EXEMPTION_DETERMINATION = 2500.0
SI_EMPLOYEE_RATE = 0.11
SI_EMPLOYER_RATE = 0.1875
TRAINING_RATE = 0.0025
EMERGENCY_RATE = 0.01
EMERGENCY_CAP = 2720.0
MARTYRS_RATE = 0.0005
MONTHS = 12
DIVISOR = 30


def bracket_tax(annual_taxable: float) -> float:
	"""The sheet's nested IF in AR, as a function."""
	for upper, lower, rate, fixed in TAX_BANDS:
		if annual_taxable <= upper:
			return (annual_taxable - lower) * rate + fixed
	lower, rate, fixed = TOP_BAND
	return (annual_taxable - lower) * rate + fixed


def insurance_deductible(annual_premium: float, cap: float) -> float:
	"""The sheet's 12-case truth table in AP, which is just three minimums."""
	if annual_premium <= 0 or cap <= 0:
		return 0.0
	return min(annual_premium, 10000.0, cap)


def compute_row(ws, r: int) -> dict:
	"""One workbook row, recomputed from the sheet's formulas."""
	num = lambda k: _num(ws.cell(r, COL[k]).value)  # noqa: E731
	at = lambda c: _num(ws.cell(r, c).value)  # noqa: E731
	is_delegated = bool(str(ws.cell(r, COL["delegated_company"]).value or "").strip())
	pod = str(ws.cell(r, COL["person_of_determination"]).value or "").strip().lower() == "yes"

	earnings = {name: at(col) for name, col in EARNING_COLUMNS.items()}
	total_earning = sum(earnings.values())

	penalties = earnings["basic"] / DIVISOR * num("penalty_days")
	absenteeism = earnings["basic"] / DIVISOR * num("absent_days")
	other = penalties + absenteeism

	si_limit = num("si_limit")
	si_rate = SI_EMPLOYEE_RATE_OVERRIDES.get(r, SI_EMPLOYEE_RATE)
	si_employee = si_limit * si_rate

	# the sheet waives the personal exemption on a cost-shared row
	exemption = 0.0 if is_delegated else (EXEMPTION_DETERMINATION if pod else EXEMPTION_STANDARD)

	base = (total_earning - si_employee - other - exemption) * MONTHS
	base_rounded = 0.0 if base <= 0 else math.floor(base / 10) * 10
	cap = base_rounded * 0.15
	annual_premium = (num("medical_insurance") + num("life_insurance")) * MONTHS
	deductible = insurance_deductible(annual_premium, cap)
	annual_taxable = max(base_rounded - deductible, 0.0)

	if is_delegated:
		annual_tax = annual_taxable * DELEGATED_TAX_RATE
	else:
		annual_tax = bracket_tax(annual_taxable)
	tax = annual_tax / MONTHS

	martyrs = earnings["basic"] * MARTYRS_RATE
	total_deductions = si_employee + tax + martyrs + other + num("medical_insurance") + num("life_insurance")
	net = total_earning - total_deductions

	return {
		"workbook_row": r,
		"work_id": str(ws.cell(r, COL["work_id"]).value or "").strip(),
		"company": str(ws.cell(r, COL["company"]).value or "").strip(),
		"delegated_company": str(ws.cell(r, COL["delegated_company"]).value or "").strip(),
		"position": str(ws.cell(r, COL["position"]).value or "").strip(),
		"department": str(ws.cell(r, COL["department"]).value or "").strip(),
		"national_id": str(ws.cell(r, COL["national_id"]).value or "").strip(),
		"si_number": str(ws.cell(r, COL["si_number"]).value or "").strip(),
		"date_of_joining": _date(ws.cell(r, COL["date_of_joining"]).value),
		"date_of_birth": _date(ws.cell(r, COL["date_of_birth"]).value),
		"is_delegated": is_delegated,
		"person_of_determination": pod,
		"earnings": earnings,
		"total_earning": total_earning,
		"si_limit": si_limit,
		"si_employee": si_employee,
		"si_employee_rate": si_rate,
		"si_employer": si_limit * SI_EMPLOYER_RATE,
		"training_fund": si_limit * TRAINING_RATE,
		"emergency": min(earnings["basic"], EMERGENCY_CAP) * EMERGENCY_RATE,
		"penalties": penalties,
		"absenteeism": absenteeism,
		"life_insurance": num("life_insurance"),
		"medical_insurance": num("medical_insurance"),
		"annual_premium": annual_premium,
		"insurance_deductible": deductible,
		"annual_taxable": annual_taxable,
		"tax": tax,
		"martyrs": martyrs,
		"total_deductions": total_deductions,
		"net_salary": net,
		"loan": num("loan"),
		"phone": num("phone"),
		"family_medical": num("family_medical"),
		"refund": num("refund"),
		"mi_employer_share": num("mi_employer_share"),
		# the sheet's own cached figures, for the check
		"sheet": {
			"total_earning": num("total_earning"),
			"si_employee": num("si_employee"),
			"tax": num("tax"),
			"net_salary": num("net_salary"),
			"final_approved": num("final_approved"),
			"training_fund": num("training_fund"),
			"mi_employer_share": num("mi_employer_share"),
		},
	}


def _num(value) -> float:
	if value in (None, "", 0):
		return 0.0
	return float(value)


def _date(value) -> str | None:
	return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else None


def build(period: str | None = None) -> dict:
	ws = openpyxl.load_workbook(WORKBOOK, data_only=True)["Master Payroll"]
	rows = [compute_row(ws, r) for r in range(3, ws.max_row + 1) if ws.cell(r, COL["work_id"]).value]
	people: dict[str, dict] = {}
	for row in rows:
		wid = row["work_id"]
		person = people.setdefault(
			wid,
			{
				"work_id": wid,
				"name": str(ws.cell(row["workbook_row"], COL["name"]).value or "").strip(),
				"national_id": row["national_id"],
				"si_number": row["si_number"],
				"date_of_joining": row["date_of_joining"],
				"date_of_birth": row["date_of_birth"],
				"company": row["company"],
				"position": row["position"],
				"department": row["department"],
				"person_of_determination": row["person_of_determination"],
				"leader": None,
				"delegated": [],
			},
		)
		if row["is_delegated"]:
			person["delegated"].append(row)
		else:
			person["leader"] = row

	for person in people.values():
		leader, shares = person["leader"], person["delegated"]
		total_net = sum([leader["net_salary"], *[s["net_salary"] for s in shares]])
		person["total_earning"] = leader["total_earning"] + sum(s["total_earning"] for s in shares)
		person["combined_net_salary"] = total_net
		person["payment_adjustments"] = {
			"loan": leader["loan"] + sum(s["loan"] for s in shares),
			"phone": leader["phone"] + sum(s["phone"] for s in shares),
			"family_medical": leader["family_medical"] + sum(s["family_medical"] for s in shares),
			"refund": leader["refund"] + sum(s["refund"] for s in shares),
		}
		person["final_approved"] = (
			total_net
			- person["payment_adjustments"]["loan"]
			- person["payment_adjustments"]["phone"]
			- person["payment_adjustments"]["family_medical"]
			+ person["payment_adjustments"]["refund"]
		)
		person["sheet_final_approved"] = leader["sheet"]["final_approved"]

	people = [p for p in people.values() if p["leader"]]
	people.sort(key=lambda p: p["work_id"])
	return {
		"_meta": {
			"source": WORKBOOK.name,
			"sheet": "Master Payroll",
			# Which month this sheet actually pays for, as "YYYY-MM".  Declared by
			# whoever builds the spec rather than inferred: the sheet's own "Years
			# of Work" column fits 2026-08 on 29 rows and 2026-09 on 177, so nothing
			# in the file settles it.  The payroll engine compares this against the
			# Payroll Entry's month and refuses to fill an entry from a different
			# month's figures, so an unlabelled or wrong label is a real risk.
			"period": period,
			"workbook_rows": len(rows),
			"people": len(people),
			"delegated_rows": sum(len(p["delegated"]) for p in people),
		},
		"people": people,
	}


def check(spec: dict) -> int:
	"""Prove the recomputation reproduces the sheet, before anyone trusts it."""
	rows = [p["leader"] for p in spec["people"]] + [s for p in spec["people"] for s in p["delegated"]]
	bad_net = [
		(r["work_id"], r["workbook_row"], round(r["net_salary"], 2), r["sheet"]["net_salary"])
		for r in rows
		if abs(r["net_salary"] - r["sheet"]["net_salary"]) > 0.01
	]
	bad_final = [
		(p["work_id"], round(p["final_approved"], 2), p["sheet_final_approved"])
		for p in spec["people"]
		if abs(p["final_approved"] - p["sheet_final_approved"]) > 0.01
	]
	print(f"rows recomputed {len(rows)}: net salary differs on {len(bad_net)}")
	for item in bad_net:
		print(f"    {item}")
	print(f"people {len(spec['people'])}: final approved differs on {len(bad_final)}")
	for item in bad_final:
		print(f"    {item}")
	return len(bad_net) + len(bad_final)


def main() -> int:
	parser = argparse.ArgumentParser(description="Build the payroll spec from the master workbook.")
	parser.add_argument(
		"--period",
		required=True,
		help='the month this sheet pays for, "YYYY-MM" - stored in the spec and checked against the Payroll Entry',
	)
	args = parser.parse_args()
	if not re.fullmatch(r"\d{4}-\d{2}", args.period):
		parser.error(f"--period must look like 2026-08, got {args.period!r}")

	spec = build(args.period)
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(spec, indent=1, ensure_ascii=False))
	meta = spec["_meta"]
	print(f"wrote {OUT}")
	print(f"  {meta['people']} people from {meta['workbook_rows']} rows, {meta['delegated_rows']} delegated")
	print(f"  period {args.period}")
	print(f"  total final approved {sum(p['final_approved'] for p in spec['people']):,.2f}")
	failures = check(spec)
	if failures:
		print(f"  {failures} rows do not match the sheet - see the exceptions above")
	return 0


# ---------------------------------------------------------------------------
# what was actually paid
# ---------------------------------------------------------------------------
def build_paid(source: pathlib.Path) -> dict:
	"""Per-person net salary and final approved, from one of the workbooks.

	`payroll_spec.json` is built from the corrected workbook, so comparing a slip
	against it only proves the slip matches the correction.  Payroll needs the
	other comparison: what people were actually paid.  This reads column AU and BA
	straight out of whichever workbook is given, and the report checks the slips
	against `Master Payroll.xlsx`, which is the run the August payslips came from.
	"""
	ws = openpyxl.load_workbook(source, data_only=True)["Master Payroll"]
	people: dict[str, dict] = {}
	for r in range(3, ws.max_row + 1):
		wid = ws.cell(r, COL["work_id"]).value
		if not wid:
			continue
		wid = str(wid).strip()
		row = people.setdefault(
			wid,
			{
				"work_id": wid,
				"name": str(ws.cell(r, COL["name"]).value or "").strip(),
				"company": str(ws.cell(r, COL["company"]).value or "").strip(),
				"own_net_salary": 0.0,
				"delegated_net": 0.0,
				"loan": 0.0,
				"phone": 0.0,
				"family_medical": 0.0,
				"refund": 0.0,
			},
		)
		if str(ws.cell(r, COL["delegated_company"]).value or "").strip():
			row["delegated_net"] += _num(ws.cell(r, COL["net_salary"]).value)
			continue
		row["own_net_salary"] = _num(ws.cell(r, COL["net_salary"]).value)
		for key in ("loan", "phone", "family_medical", "refund"):
			row[key] += _num(ws.cell(r, COL[key]).value)
	for row in people.values():
		row["final_approved"] = round(
			row["own_net_salary"]
			+ row["delegated_net"]
			- row["loan"]
			- row["phone"]
			- row["family_medical"]
			+ row["refund"],
			2,
		)
		for key in ("own_net_salary", "delegated_net", "loan", "phone", "family_medical", "refund"):
			row[key] = round(row[key], 2)
	# the sheet's own BA, so a hand-edited cell still shows up
	for r in range(3, ws.max_row + 1):
		wid = ws.cell(r, COL["work_id"]).value
		if wid and not str(ws.cell(r, COL["delegated_company"]).value or "").strip():
			people[str(wid).strip()]["sheet_final_approved"] = round(
				_num(ws.cell(r, COL["final_approved"]).value), 2
			)
	return {
		"_meta": {"source": source.name, "people": len(people)},
		"people": sorted(people.values(), key=lambda p: p["work_id"]),
	}


def paid() -> int:
	target = pathlib.Path(
		"/home/mahmoud/Master Payroll.xlsx"
		if "--original" in sys.argv
		else "/home/mahmoud/Master Payroll (corrected tax + PoD flag).xlsx"
	)
	name = "august_paid.json" if "--original" in sys.argv else "corrected_paid.json"
	out = pathlib.Path(__file__).resolve().parent.parent / "seed_data" / name
	payload = build_paid(target)
	out.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
	total = sum(p["final_approved"] for p in payload["people"])
	print(f"wrote {out}")
	print(f"  {payload['_meta']['people']} people from {payload['_meta']['source']}")
	print(f"  total final approved {total:,.2f}")
	return 0


if __name__ == "__main__":
	sys.exit(paid() if "--paid" in sys.argv else main())
