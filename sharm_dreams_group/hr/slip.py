# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Generate Salary Slips for the current payroll period.

This is where the tax engine first runs against real employees.  `apply_egypt_deductions`
reads a slip, not a spreadsheet, so everything it needs has to be on the document:

    doc.gross_pay            -> monthly_total_earning   (the sum of the earnings)
    the Basic earning row    -> monthly_basic
    the existing deduction rows -> monthly_deductions
    SDG Gross Insurance Band -> SI base and life premium
    SDG Insured Person       -> medical premium
    Employee.sdg_is_person_of_determination

The amounts come from the payroll line for each employee, mapped to the same
components the salary structures declare.  Everything else - income tax, social
insurance, martyrs fund, the three employer funds - is left at zero for
`apply_egypt_deductions` to fill in.

Two things deliberately not done here:

*   **Delegated cost shares are not generated.**  37 payroll lines are recharges
    to another group company, not employment.  A slip for one would be a second
    payslip for the same person and month.  They belong to `SDG Group Allocation`,
    which is also what tells the engine a slip is a cost share so it takes the flat
    10% rather than the progressive brackets.
*   **No Payroll Entry is created.**  A Payroll Entry is what ERPNext uses to drive
    `make_salary_slip` across a company for a period; this generates the slips
    directly so the figures can be checked against the workbook before a run is
    committed to.

Run:
    bench --site <site> execute sharm_dreams_group.hr.slip.plan
    bench --site <site> execute sharm_dreams_group.hr.slip.apply
"""

from __future__ import annotations

import json
import pathlib

import frappe
from frappe.utils import getdate

from sharm_dreams_group import companies as companies_mod
from sharm_dreams_group.hr.importer import load_fixture

# The earning and deduction components a slip carries, mapped to the fixture keys.
EARNING_MAP = {
	"Basic": "Basic",
	"Fixed Monthly Incentives": "Fixed Monthly Incentives",
	"Rest Allowance": "Rest Allowance",
	"Housing & Transport": "Housing & Transport",
	"Exceptional Bonus": "Exceptional Bonus",
}
DEDUCTION_MAP = {
	"Penalties": "penalties",
	"Absenteeism": "absenteeism",
}


def holiday_list() -> str:
	"""A Holiday List that covers the payroll period.

	HRMS refuses to make a slip for an employee with no Holiday List Assignment,
	"for Employee X or their company Y for date Z".  This picks the list that spans
	the posting date.
	"""
	on_date = getdate(posting_date())
	for name in frappe.get_all("Holiday List", pluck="name", limit_page_length=0):
		span = frappe.db.get_value("Holiday List", name, ["from_date", "to_date"], as_dict=True)
		if span and getdate(span["from_date"]) <= on_date <= getdate(span["to_date"]):
			return name
	frappe.throw(
		frappe._("No Holiday List covers {0}. Create one before running payroll.").format(on_date),
		title=frappe._("Holiday List required"),
	)


def ensure_holiday_assignments() -> dict:
	"""Assign the payroll Holiday List to every paying company.

	HRMS refuses to make a slip for an employee with no Holiday List Assignment.
	In this version an assignment is made to a **role or a company**, not to an
	individual, so this creates one row per company the group actually employs
	through rather than 182 rows.

	A prerequisite, not part of the employee import: the master workbook carries no
	holiday data and the group already has a Holiday List on the site, so only the
	assignment is missing.  An existing assignment for the period is left alone.

	Note the assignment is **submitted**: HRMS only reads assignments at docstatus 1
	(see hrms.utils.holiday_list.get_holiday_list_assignments), so a plain insert
	leaves the employee still looking unassigned and the slip still fails.
	"""
	list_name = holiday_list()
	on_date = posting_date()

	companies = sorted(
		{
			frappe.db.get_value("Employee", name, "company")
			for name in frappe.get_all("Employee", pluck="name", limit_page_length=0)
		}
		- {None, ""}
	)
	existing = {
		row["employee_company"]
		for row in frappe.get_all(
			"Holiday List Assignment",
			filters={
				"holiday_list": list_name,
				"applicable_for": "Company",
				"from_date": ("<=", on_date),
				"docstatus": 1,
			},
			fields=["employee_company"],
			limit_page_length=0,
		)
	}
	made = 0
	for company in companies:
		if company in existing:
			continue
		frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"holiday_list": list_name,
				"applicable_for": "Company",
				"assigned_to": company,
				"employee_company": company,
				"from_date": on_date,
			}
		).insert(ignore_permissions=True).submit()
		made += 1
	frappe.db.commit()
	frappe.clear_cache()
	return {"holiday_list": list_name, "created": made, "already": len(existing)}


def posting_date() -> str:
	"""The date the slips are posted on, which decides their period.

	HRMS's `make_salary_slip` takes a posting date and derives the period from the
	structure's payroll frequency, so the period is not chosen here.  The date is
	clamped inside the current Fiscal Year because ERPNext refuses a slip outside
	one, and the site has a single Fiscal Year (2026-07-01 to 2027-06-30).
	"""
	from frappe.utils import nowdate

	from sharm_dreams_group.hr.salary import current_fiscal_year

	start, end = current_fiscal_year()
	today = str(getdate(nowdate()))
	if today < start:
		return start
	if today > end:
		return end
	return today


def default_period() -> tuple[str, str]:
	"""The Fiscal Year a slip can be dated in."""
	from sharm_dreams_group.hr.salary import current_fiscal_year

	return current_fiscal_year()


def _base_lines() -> dict[str, dict]:
	"""employee_number -> their undelegated payroll line.

	People with more than one undelegated line would be two employments, which the
	workbook does not express, and they are refused by the salary build.
	"""
	out: dict[str, dict] = {}
	for record in load_fixture()["employees"]:
		who = record["employee_number"]
		base = next((l for l in record.get("payroll_lines", []) if not l.get("is_delegated")), None)
		if base:
			out[who] = base
	return out


def _golden_by_national_id() -> dict[str, dict]:
	"""National ID -> the workbook's own expected figures, for comparison.

	Only the **leader** rows are indexed.  The workbook has 207 rows for 170
	people: the extra 37 are delegated cost shares, identified by
	`delegated_company` being set, and a manager who is cost-charged to other
	companies appears once per charge.  Those rows repeat the same national ID, so
	indexing all of them silently kept whichever came last - a cost share of
	12,165.50 instead of the 104,443.00 the slip actually carries, which read as a
	gross pay error on 17 employees when nothing was wrong.
	"""
	path = pathlib.Path(__file__).resolve().parent.parent / "tests" / "golden_payroll.json"
	if not path.exists():
		return {}
	data = json.loads(path.read_text())
	return {
		row["national_id"]: row for row in data["rows"] if not str(row.get("delegated_company") or "").strip()
	}


def build_plan(start_date: str, end_date: str, only: list[str] | None = None) -> dict:
	"""Work out the slips.  Writes nothing."""
	lines = _base_lines()
	golden = _golden_by_national_id()

	slips: list[dict] = []
	skipped: list[dict] = []
	errors: list[str] = []

	for who, line in sorted(lines.items()):
		if only and who not in only:
			continue
		if not frappe.db.exists("Employee", who):
			errors.append(f"{who}: not an Employee")
			continue

		company = companies_mod.resolve(line["company"])
		employee_company = frappe.db.get_value("Employee", who, "company")
		if not company:
			errors.append(f"{who}: payroll company {line['company']!r} does not resolve")
			continue
		if employee_company != company:
			# the same two records the salary build refuses to assign
			skipped.append(
				{
					"employee_number": who,
					"reason": (
						f"Employee.company is {employee_company!r} but the payroll books the "
						f"salary under {company!r}"
					),
				}
			)
			continue

		assignment = frappe.get_all(
			"Salary Structure Assignment",
			filters={"employee": who, "company": company},
			pluck="name",
			limit_page_length=1,
		)
		if not assignment:
			skipped.append({"employee_number": who, "reason": "no salary structure assignment"})
			continue

		earnings = {
			component: float((line.get("earnings") or {}).get(key) or 0.0)
			for component, key in EARNING_MAP.items()
		}
		deductions = {component: float(line.get(key) or 0.0) for component, key in DEDUCTION_MAP.items()}
		loan = float(line.get("loan") or 0.0)
		gross_pay = sum(earnings.values())

		# the workbook's own Total Earning, to flag a row that does not reconcile
		book_total = line.get("total_earning")
		reconciles = book_total is None or abs(gross_pay - float(book_total)) <= 0.51

		slips.append(
			{
				"employee_number": who,
				"company": company,
				"structure": frappe.db.get_value(
					"Salary Structure Assignment", assignment[0], "salary_structure"
				),
				"earnings": earnings,
				"deductions": deductions,
				"loan": loan,
				"gross_pay": gross_pay,
				"workbook_total_earning": book_total,
				"reconciles_with_workbook": reconciles,
				"absent_days": line.get("absent_days") or 0.0,
				"penalty_days": line.get("penalty_days") or 0.0,
				"golden": golden.get(frappe.db.get_value("Employee", who, "sdg_national_id")),
			}
		)

	return {
		"slips": slips,
		"skipped": skipped,
		"errors": errors,
		"start_date": str(getdate(start_date)),
		"end_date": str(getdate(end_date)),
	}


def report(result: dict) -> None:
	slips = result["slips"]
	gross = sum(s["gross_pay"] for s in slips)
	pre_tax = sum(sum(s["deductions"].values()) for s in slips)
	loans = sum(s["loan"] for s in slips)
	bad = [s for s in slips if not s["reconciles_with_workbook"]]
	has_golden = [s for s in slips if s["golden"]]

	print("=== salary slip plan ===")
	print(f"  period                : {result['start_date']} to {result['end_date']}")
	print(f"  slips                 : {len(slips)}")
	print(f"  gross pay total       : {gross:,.2f}")
	print(f"  pre-tax deductions    : {pre_tax:,.2f}")
	print(f"  loans (post-tax)      : {loans:,.2f}")
	print(f"  with a workbook row   : {len(has_golden)}")
	if bad:
		print(f"\n  earnings do not reconcile with Total Earning ({len(bad)}):")
		for s in bad:
			print(
				f"     {s['employee_number']:8} components {s['gross_pay']:,.2f}"
				f"  workbook {float(s['workbook_total_earning']):,.2f}"
			)
	if result["skipped"]:
		print(f"\n  skipped ({len(result['skipped'])}):")
		for row in result["skipped"]:
			print(f"     {row['employee_number']:8} {row['reason']}")
	if result["errors"]:
		print(f"\n  ERRORS ({len(result['errors'])}) - nothing written")
		for error in result["errors"][:15]:
			print(f"     {error}")
	else:
		print("\nno errors: ready to generate")


def plan() -> None:
	result = build_plan(*default_period())
	report(result)
	frappe.db.rollback()


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
def _make_slip(item: dict, on_date: str):
	"""One slip, with the statutory chain left for the engine to fill.

	Built through HRMS's own `make_salary_slip` rather than assembled by hand, so
	the usual machinery - leave encashment, attendance, LWP, the period from the
	posting date - all behave as they would in a real Payroll Entry.
	"""
	from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

	# re-running has to replace this app's own drafts: HRMS refuses a second slip
	# for the same employee and period.  Only unsubmitted slips this run owns are
	# touched, so a submitted payroll is never silently rewritten.
	for stale in frappe.get_all(
		"Salary Slip",
		filters={"employee": item["employee_number"], "docstatus": 0},
		pluck="name",
		limit_page_length=0,
	):
		frappe.delete_doc("Salary Slip", stale, ignore_permissions=True)

	slip = make_salary_slip(item["structure"], employee=item["employee_number"], posting_date=on_date)
	if isinstance(slip, str):
		slip = frappe.get_doc("Salary Slip", slip)

	# make_salary_slip copies the structure's zeroed rows; put the figures on them
	_amt(slip, "earnings", EARNING_MAP, item["earnings"])
	_amt(slip, "deductions", DEDUCTION_MAP, item["deductions"])

	# the loan is post-tax: HRMS subtracts it after total_deduction
	slip.total_loan_repayment = item["loan"]
	slip.save()
	slip.reload()
	# the engine runs from calculate_net_pay(), so this is the step that fills in
	# income tax, social insurance, martyrs fund and the employer funds
	slip.calculate_net_pay()
	slip.save()
	return slip


def _amt(slip, table: str, mapping: dict, values: dict) -> None:
	"""Set amounts on the rows make_salary_slip already created."""
	for component, amount in values.items():
		if not amount:
			continue
		for row in slip.get(table) or []:
			if row.salary_component == component:
				row.amount = amount
				break
		else:
			slip.append(table, {"salary_component": component, "abbr": component, "amount": amount})


def apply(only: list[str] | None = None) -> None:
	"""Generate the slips.  One transaction: a failure leaves none behind."""
	ensure_holiday_assignments()
	on_date = posting_date()
	result = build_plan(*default_period(), only=only)
	if result["errors"]:
		report(result)
		frappe.throw(
			f"{len(result['errors'])} error(s) in the slip plan; nothing was written.",
			title=frappe._("Salary slip generation blocked"),
		)

	made = 0
	for item in result["slips"]:
		_make_slip(item, on_date)
		made += 1
		if made % 25 == 0:
			print(f"  ... {made}/{len(result['slips'])}")
	frappe.db.commit()
	frappe.clear_cache()

	print(f"\ngenerated {made} salary slips posted on {on_date}")
	report_totals()


def apply_one(employee_number: str) -> None:
	"""Generate one slip and print it beside the workbook's own figures.

	The spot check.  Generating all 168 at once and finding a mapping wrong leaves
	168 slips to unpick; one slip, compared against the row it came from, does not.
	"""
	ensure_holiday_assignments()
	on_date = posting_date()
	result = build_plan(*default_period(), only=[employee_number])
	if result["errors"]:
		report(result)
		frappe.throw(result["errors"][0], title=frappe._("Cannot generate this slip"))
	if not result["slips"]:
		why = result["skipped"][0]["reason"] if result["skipped"] else "no payroll line"
		frappe.throw(
			frappe._("No slip for {0}: {1}").format(employee_number, why),
			title=frappe._("Cannot generate this slip"),
		)

	item = result["slips"][0]
	slip = _make_slip(item, on_date)
	frappe.db.commit()
	frappe.clear_cache()

	print(f"=== {slip.name} ===")
	print(f"  employee   : {item['employee_number']} ({item['company']})")
	print(f"  period     : {slip.start_date} to {slip.end_date}  (posted {on_date})")
	print()
	print("  earnings")
	for row in slip.earnings:
		print(f"     {row.salary_component:<28} {float(row.amount or 0):>12,.2f}")
	print(f"  {'gross_pay':<31} {float(slip.gross_pay or 0):>12,.2f}")
	print()
	print("  deductions")
	for row in slip.deductions:
		print(f"     {row.salary_component:<28} {float(row.amount or 0):>12,.2f}")
	print("  employer contributions")
	for row in slip.employer_contributions:
		print(f"     {row.salary_component:<28} {float(row.amount or 0):>12,.2f}")
	print(f"  {'total_deduction':<31} {float(slip.total_deduction or 0):>12,.2f}")
	if item["loan"]:
		print(f"  {'total_loan_repayment':<31} {float(item['loan']):>12,.2f}  (post-tax)")
	print(f"  {'net_pay':<31} {float(slip.net_pay or 0):>12,.2f}")

	golden = item.get("golden")
	if golden:
		print()
		print(f"  against the workbook row for {golden.get('work_id')}:")
		compare = [
			("gross", float(slip.gross_pay or 0), float(golden["total_earning"])),
			("income tax", float(slip.sdg_annual_income_tax or 0) / 12.0, float(golden["expected_tax"])),
			(
				"SI employee",
				_component(slip, "deductions", "Social Insurance (Employee)"),
				float(golden["expected_si_employee"]),
			),
			(
				"SI employer",
				_component(slip, "employer_contributions", "Social Insurance (Employer)"),
				float(golden["expected_si_employer"]),
			),
			("martyrs", _component(slip, "deductions", "Martyrs Fund"), float(golden["expected_martyrs"])),
			(
				"training",
				_component(slip, "employer_contributions", "Training and Development Fund"),
				float(golden["expected_training_fund"]),
			),
			(
				"emergency",
				_component(slip, "employer_contributions", "Emergency Fund"),
				float(golden["expected_emergency"]),
			),
		]
		for label, got, want in compare:
			ok = "ok  " if abs(got - want) <= 0.01 else "DIFF"
			print(
				f"     {ok} {label:<14} slip {got:>14,.2f}  workbook {want:>14,.2f}  delta {got - want:+,.2f}"
			)


def _component(slip, table: str, component: str) -> float:
	for row in slip.get(table) or []:
		if row.salary_component == component:
			return float(row.amount or 0)
	return 0.0


def reconcile(tolerance: float = 0.01) -> dict:
	"""Compare every generated slip with its workbook row, component by component.

	Reports the totals rather than a pass or fail, because a known gap is expected:
	`SDG Insured Person` is empty, so the insurance deduction is missing and income
	tax comes out high for anyone with medical cover.  When that register is
	imported, the income tax line should fall to zero and this is the check.
	"""
	golden = _golden_by_national_id()
	rows = {
		"gross": ("total_earning", None),
		"si employee": ("expected_si_employee", ("deductions", "Social Insurance (Employee)")),
		"income tax": ("expected_tax", ("deductions", "Income Tax")),
		"si employer": ("expected_si_employer", ("employer_contributions", "Social Insurance (Employer)")),
		"martyrs fund": ("expected_martyrs", ("deductions", "Martyrs Fund")),
		"training fund": (
			"expected_training_fund",
			("employer_contributions", "Training and Development Fund"),
		),
		"emergency fund": ("expected_emergency", ("employer_contributions", "Emergency Fund")),
	}
	stats = {label: {"exact": 0, "off": 0, "delta": 0.0, "worst": 0.0} for label in rows}
	compared = 0
	slips = frappe.get_all("Salary Slip", fields=["name", "employee"], limit_page_length=0)
	for row in slips:
		national_id = frappe.db.get_value("Employee", row.employee, "sdg_national_id")
		want_row = golden.get(national_id)
		if not want_row:
			continue
		compared += 1
		slip = frappe.get_doc("Salary Slip", row.name)
		for label, (key, source) in rows.items():
			want = float(want_row.get(key) or 0.0)
			got = float(slip.gross_pay or 0.0) if source is None else _component(slip, *source)
			delta = got - want
			if abs(delta) <= tolerance:
				stats[label]["exact"] += 1
			else:
				stats[label]["off"] += 1
			stats[label]["delta"] += delta
			if abs(delta) > abs(stats[label]["worst"]):
				stats[label]["worst"] = delta

	print(f"  slips compared with the workbook: {compared}")
	for label, st in stats.items():
		flag = "ok  " if not st["off"] else "DIFF"
		print(
			f"  {flag} {label:<16} exact {st['exact']:>4}  off {st['off']:>4}"
			f"  total {st['delta']:>12,.2f}  worst {st['worst']:>+10,.2f}"
		)
	return {"compared": compared, "stats": stats}


def report_totals() -> None:
	"""The slip figures, next to the workbook where a row can be compared."""
	slips = frappe.get_all(
		"Salary Slip",
		fields=["name", "employee", "company", "gross_pay", "net_pay", "total_deduction"],
		limit_page_length=0,
	)
	print(f"  slips in the site      : {len(slips)}")
	if slips:
		print(f"  gross pay total        : {sum(float(s.gross_pay or 0) for s in slips):,.2f}")
		print(f"  total deduction total  : {sum(float(s.total_deduction or 0) for s in slips):,.2f}")
		print(f"  net pay total          : {sum(float(s.net_pay or 0) for s in slips):,.2f}")
	untaxed = frappe.db.count("Salary Slip", {"sdg_annual_income_tax": ("is", "not set")})
	print(f"  slips the engine did not run on: {untaxed}")
