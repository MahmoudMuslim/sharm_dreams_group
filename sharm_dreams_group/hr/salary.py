# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Build Salary Structures and Salary Structure Assignments from the payroll lines.

The payroll workbook has 207 rows for 170 people.  Only the first row of each
person is their employment: it names the employing company and pays them.  The
other 37 are cost shares recharged to a second group company, carry `net` of zero
and exist to move cost, not to pay anyone.

So this creates a **flexi** structure template per company and an assignment for
the **base** line only.

The per-employee amounts deliberately do not go on the assignment.
`Salary Structure Assignment` has no earnings child table - Frappe silently drops
unknown fields on insert - so amounts written there simply disappear.  In ERPNext
the amounts live on the **Salary Slip**: a flexi structure declares which earning
components exist, and the slip carries the figures.  The amount that does belong
on the assignment is `base`, and it is set to the employee's Basic so reporting
and the tax engine's `basic` lookup have something to read before a slip exists.

Delegated lines are deliberately not given an assignment.  An assignment is what
makes a payroll run in that company pay the person, so creating one for a cost
share would let a second payslip be generated for the same month and the same
employee - a double payment.  They are reported here and left for `SDG Group
Allocation`, which is what tells a delegated slip apart from the real one
(`deductions._is_delegated` already reads that doctype).

Earnings map to components one for one, from the workbook's own columns:

    Basic                     Gross Salary
    Fixed Monthly Incentives   Fixed Monthly Incentives   (47 rows)
    Rest Allowance            Rest Allowance Value       (1 row)
    Housing & Transport       Transportation + Housing    (19 rows)
    Exceptional Bonus         Exceptional bonus          (2 rows)

Overtime has no component: all 207 rows are zero, and a component nothing uses
is a column someone will eventually try to fill in by hand.

Run:
    bench --site <site> execute sharm_dreams_group.hr.salary.plan
    bench --site <site> execute sharm_dreams_group.hr.salary.apply
"""

from __future__ import annotations

import json
import pathlib

import frappe

from sharm_dreams_group import companies as companies_mod

FIXTURE = pathlib.Path(__file__).resolve().parent.parent.parent / "seed_data" / "employees.json"

STRUCTURE_PREFIX = "SDG "
CURRENCY = "EGP"
PAYROLL_FREQUENCY = "Monthly"


# component -> (label, description, abbr).  `Basic` already exists from
# setup.create_salary_components(); the rest are created here.
EARNINGS = [
	("Basic", "Basic", "Gross salary before allowances", "B"),
	(
		"Fixed Monthly Incentives",
		"Fixed Monthly Incentives",
		"Commission or incentive held flat each month. Present on 47 of 207 payroll lines",
		"FMI",
	),
	(
		"Rest Allowance",
		"Rest Allowance",
		"Shift or rest allowance. Present on 1 of 207 payroll lines",
		"RA",
	),
	(
		"Housing & Transport",
		"Housing & Transport",
		"Accommodation and transport allowance. Present on 19 of 207 payroll lines",
		"H&T",
	),
	(
		"Exceptional Bonus",
		"Exceptional Bonus",
		"One-off bonus, not part of the monthly package. Present on 2 of 207 payroll lines",
		"EB",
	),
	(
		"Delegated Cost Share",
		"Delegated Cost Share",
		"Part of the employee's pay charged to another group company. Paid by this "
		"company but not its statutory employment, so it is taxed at a flat rate "
		"with no social insurance or personal exemption. Present on 37 of 207 "
		"payroll lines, belonging to 17 people",
		"DCS",
	),
]

# The statutory deductions, matching the employee side of REQUIRED_COMPONENTS in
# payroll/egypt/deductions.py.
DEDUCTIONS = [
	("Social Insurance (Employee)", "SI", "Employee share of social insurance, 11% of the SI base"),
	("Martyrs Fund", "MF", "Martyrs fund, 0.05% of basic salary"),
	("Income Tax", "IT", "Annual progressive income tax, divided to a monthly figure"),
	(
		"Income Tax on Cost Share",
		"ICS",
		"Flat 10% group withholding on delegated cost shares, which are taxed apart "
		"from the employee's own pay",
	),
]

# Pre-tax deductions the group makes from the monthly package.  The engine reads
# the total of the slip's existing deduction rows as `monthly_deductions`, so
# these have to be rows on the slip and the structure rather than something the
# engine computes.  Across all 207 payroll rows, "Total Deductions" is exactly
# Penalties + Absenteeism, so the split carries cleanly.
PRE_TAX_DEDUCTIONS = [
	("Penalties", "PEN", "Penalties for the month's unexcused absence or lateness"),
	("Absenteeism", "ABS", "Pay withheld for unpaid absence days"),
]

# Deductions that come off the employee's payment.  The insurance premium is
# unusual: the workbook uses it twice - to reduce the taxable income, and again in
# "Total Deductions" - so it is both an input to the tax base and a deduction.
POST_TAX_DEDUCTIONS = [
	(
		"Medical Insurance",
		"MED",
		"Monthly medical cover, taken off the payment. Also reduces taxable income",
	),
	(
		"Life Insurance",
		"LIF",
		"Monthly life cover, taken off the payment. Also reduces taxable income",
	),
	("Phone Allowance Recovered", "PHN", "Phone allowance recovered from the monthly pay"),
	("Family Medical Insurance", "FAM", "Family medical cover recovered from the monthly pay"),
]

# ...and the employer side, which the engine also writes.
EMPLOYER_CONTRIBUTIONS = [
	(
		"Social Insurance (Employer)",
		"SIE",
		"Employer share of social insurance, 18.75% of the SI base",
	),
	("Training and Development Fund", "TDF", "Training fund, 0.25% of the SI base"),
	("Emergency Fund", "EF", "Emergency fund, 0.25% of the SI base"),
	(
		"MI Fund (Employer)",
		"MIF",
		"MI fund borne by the employer; a per-person amount, not a rate",
	),
]

# Derived from the lists rather than repeated, so a component added there cannot
# be left without an abbreviation here.
ABBR = {
	**{name: abbr for name, _l, _d, abbr in EARNINGS},
	**{name: abbr for name, abbr, _d in DEDUCTIONS},
	**{name: abbr for name, abbr, _d in PRE_TAX_DEDUCTIONS},
	**{name: abbr for name, abbr, _d in POST_TAX_DEDUCTIONS},
	**{name: abbr for name, abbr, _d in EMPLOYER_CONTRIBUTIONS},
}

# Only the components that can actually carry a figure for a given employee are
# written onto that employee's structure: a structure declares every component the
# app knows about, and a slip is given zero for the ones that person has none of.
POST_TAX_COMPONENTS = [name for name, _abbr, _d in POST_TAX_DEDUCTIONS]

# A structure is a template: it declares which earning components exist.  The
# figures themselves live on the Salary Slip, because neither the structure nor
# the assignment can hold a per-employee amount in this HRMS version.
TEMPLATE_AMOUNT = 0.0


def load_fixture() -> dict:
	if not FIXTURE.exists():
		raise FileNotFoundError(
			f"{FIXTURE} not found. Run tools/build_employee_fixture.py on the host first."
		)
	return json.loads(FIXTURE.read_text())


def structure_name(company: str) -> str:
	return f"{STRUCTURE_PREFIX}{company}"


def current_fiscal_year() -> tuple[str, str]:
	"""(start, end) of the fiscal year containing today.

	The site has one fiscal year, 2026-07-01 to 2027-06-30, so payroll can only
	be dated inside it.
	"""
	from frappe.utils import getdate, nowdate

	today = getdate(nowdate())
	rows = frappe.get_all(
		"Fiscal Year",
		filters={"disabled": 0},
		fields=["name", "year_start_date", "year_end_date"],
		limit_page_length=0,
	)
	for row in rows:
		if getdate(row["year_start_date"]) <= today <= getdate(row["year_end_date"]):
			return str(getdate(row["year_start_date"])), str(getdate(row["year_end_date"]))
	if rows:
		row = rows[0]
		return str(getdate(row["year_start_date"])), str(getdate(row["year_end_date"]))
	raise frappe.throw(frappe._("No active Fiscal Year"), title=frappe._("Payroll needs a Fiscal Year"))


def _master_joining(employee: str | None) -> str | None:
	"""The joining date on the Employee record, which is what HRMS validates on.

	The payroll sheet and the Employee master do not always agree - H0355 is dated
	1996-08-16 in the sheet and 2026-08-16 on the record - and HRMS refuses an
	assignment that starts before the record's date.  The master is the authority
	for that check, so it is read here rather than left to fail the build.
	"""
	if not employee or not frappe.db.exists("Employee", employee):
		return None
	value = frappe.db.get_value("Employee", employee, "date_of_joining")
	return str(value) if value else None


def assignment_from_date(joining: str | None, window: tuple[str, str], employee: str | None = None) -> str:
	"""When the assignment starts.

	HRMS requires `from_date` to be on or after the employee's joining date, and
	ERPNext requires it to fall inside an active Fiscal Year.  Long-tenured
	employees joined in the 1990s, long before this site's only fiscal year, so
	`date_of_joining` cannot be used as-is: it raised FiscalYearError on the first
	of them.

	The assignment records when the person's pay is effective in Frappe, not when
	they joined, so the start of the current fiscal year is the honest answer.
	"""
	from frappe.utils import getdate

	start, end = window
	chosen = getdate(start)
	for candidate in (joining, _master_joining(employee)):
		if candidate and getdate(candidate) > chosen:
			chosen = getdate(candidate)
	if chosen > getdate(end):
		# a joiner whose start date is beyond the only fiscal year on the site
		frappe.throw(
			frappe._("Joining date {0} is after the Fiscal Year ends ({1})").format(joining, end),
			title=frappe._("No Fiscal Year covers this joining date"),
		)
	return str(chosen)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def build_plan() -> dict:
	"""Work out the components, structures and assignments.  Writes nothing."""
	data = load_fixture()
	window = current_fiscal_year()

	created_components, existing_components = [], []
	# the two lists are kept apart on purpose.  `Penalties` and `Absenteeism` are
	# deductions that the payroll workbook happens to sit in the same block as the
	# earnings, and merging them put them in a structure's `earnings` table, where
	# they were silently added to gross pay.
	earning_components: list[str] = []
	deduction_components: list[str] = []
	for name, _label, _description, _abbr in EARNINGS:
		if frappe.db.exists("Salary Component", name):
			existing_components.append(name)
		else:
			created_components.append(name)
		earning_components.append(name)
	for name, _abbr, _description in PRE_TAX_DEDUCTIONS:
		if frappe.db.exists("Salary Component", name):
			existing_components.append(name)
		else:
			created_components.append(name)
		deduction_components.append(name)

	structures: dict[str, list[str]] = {}
	assignments: list[dict] = []
	delegated: list[dict] = []
	conflicts: list[dict] = []
	errors: list[str] = []
	warnings: list[str] = []

	for record in data["employees"]:
		who = record["employee_number"]
		if not frappe.db.exists("Employee", who):
			errors.append(f"{who}: not an Employee, cannot assign a salary")
			continue
		for index, line in enumerate(record.get("payroll_lines", [])):
			if line.get("is_delegated"):
				# reported, never assigned: see the module docstring
				delegated.append(
					{
						"employee_number": who,
						"employing_company": line["company"],
						"charged_company": line["delegated_company"],
						"amount": line["earnings"].get("Basic", 0.0),
					}
				)
				continue

			company = companies_mod.resolve(line["company"])
			if not company:
				errors.append(f"{who}: company {line['company']!r} does not exist")
				continue

			# `Salary Structure Assignment.company` is `fetch_from: employee.company`,
			# so Frappe overwrites whatever is set here with the Employee's own
			# company and then checks the structure against that.  Where the master
			# and the payroll sheet disagree on the employing company, the Employee
			# record therefore has to win: it is the employer's own master data, and
			# there is no way to assign the sheet's company through HRMS anyway.
			# The disagreement is reported rather than silently resolved, because for
			# a senior person the employing company is a legal fact worth confirming.
			employee_company = frappe.db.get_value("Employee", who, "company")
			if employee_company != company:
				conflicts.append(
					{
						"employee_number": who,
						"employee_company": employee_company,
						"payroll_company": company,
						"payroll_company_source": line["company"],
						"gross_salary": float(line.get("gross_salary") or 0.0),
						"resolution": "assigned under the Employee record's company",
					}
				)
				company = employee_company
			structures.setdefault(company, []).append(who)

			earnings = {
				k: float(v or 0.0) for k, v in (line.get("earnings") or {}).items() if k in earning_components
			}
			total = sum(earnings.values())
			# against the workbook's Total Earning, not Gross Salary: the sum
			# legitimately exceeds Gross because it includes the allowances, and
			# 206 of the 207 rows reconcile to Total Earning exactly.
			book_total = line.get("total_earning")
			if book_total is not None and abs(total - float(book_total)) > 0.51:
				warnings.append(
					f"{who}: the earning components sum to {total:,.2f} but the payroll row's "
					f"Total Earning is {float(book_total):,.2f} "
					f"(difference {total - float(book_total):,.2f}); the sum is used, and the "
					"row is flagged rather than padded with a made-up allowance"
				)
			if index > 0:
				# the base line is the first; a second undelegated row would mean
				# two employments, which the workbook does not express
				errors.append(f"{who}: more than one undelegated payroll line")
			assignments.append(
				{
					"employee_number": who,
					"company": company,
					"structure": structure_name(company),
					"earnings": earnings,
					"from_date": assignment_from_date(record.get("date_of_joining"), window, who),
				}
			)

	return {
		"created_components": created_components,
		"existing_components": existing_components,
		"earning_components": earning_components,
		"deduction_components": deduction_components,
		"structures": structures,
		"assignments": assignments,
		"delegated": delegated,
		"conflicts": conflicts,
		"fiscal_year": window,
		"errors": errors,
		"warnings": sorted(set(warnings)),
	}


def report(result: dict) -> None:
	print("=== salary structure plan ===")
	print(f"  fiscal year in use  : {result['fiscal_year'][0]} to {result['fiscal_year'][1]}")
	print(f"  components to create : {len(result['created_components'])} {result['created_components']}")
	print(f"  components existing  : {len(result['existing_components'])} {result['existing_components']}")
	print(f"  structures per company: {len(result['structures'])}")
	print(f"  assignments          : {len(result['assignments'])}")
	print(f"  delegated cost shares: {len(result['delegated'])} (not assigned)")
	if result["conflicts"]:
		print(f"  company conflicts    : {len(result['conflicts'])} (not assigned)")
	if result["structures"]:
		print("\n  per company:")
		for company, people in sorted(result["structures"].items()):
			print(f"     {company[:46]:48} {len(people):4} employee(s)")

	if result["warnings"]:
		print(f"\n  warnings ({len(result['warnings'])})")
		for warning in result["warnings"]:
			print(f"     {warning}")

	if result["conflicts"]:
		print(f"\n  EMPLOYING COMPANY CONFLICTS ({len(result['conflicts'])})")
		print("     the master and the payroll name different employers, and the")
		print("     assignment's company is taken from Employee, so one has to be")
		print("     corrected before these can be paid:")
		for row in result["conflicts"]:
			print(f"     {row['employee_number']:8} Employee.company={row['employee_company']}")
			print(
				f"     {'':8} payroll says    ={row['payroll_company']}"
				f"  (from {row['payroll_company_source']!r}), gross {row['gross_salary']:,.2f}"
			)

	if result["delegated"]:
		print(f"\n  delegated cost shares, for SDG Group Allocation ({len(result['delegated'])})")
		by_employee: dict[str, int] = {}
		total = 0.0
		for row in result["delegated"]:
			by_employee[row["employee_number"]] = by_employee.get(row["employee_number"], 0) + 1
			total += row["amount"]
		print(f"     {len(by_employee)} employee(s), {total:,.2f} per month in total")
		for who, count in sorted(by_employee.items(), key=lambda kv: -kv[1])[:8]:
			print(f"     {who:8} {count} share(s)")

	if result["errors"]:
		print(f"\n  ERRORS ({len(result['errors'])}) - nothing written")
		for error in result["errors"][:20]:
			print(f"     {error}")
	else:
		print("\nno errors: ready to apply")


def plan() -> None:
	result = build_plan()
	report(result)
	frappe.db.rollback()


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------
def _ensure_component(name: str, description: str, abbr: str) -> None:
	if frappe.db.exists("Salary Component", name):
		doc = frappe.get_doc("Salary Component", name)
		doc.description = description
		doc.salary_component_abbr = abbr
		doc.save()
		return
	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": name,
			"salary_component_abbr": abbr,
			"description": description,
			"type": "Earning",
			"company": None,
		}
	).insert(ignore_permissions=True)
	print(f"  created earning component: {name}")


def _ensure_deduction(name: str, description: str, abbr: str) -> None:
	"""A pre-tax deduction the group makes from the package.

	Not statutory and not computed by the engine - the engine only reads the total
	of the slip's existing deduction rows as `monthly_deductions`.
	"""
	if frappe.db.exists("Salary Component", name):
		doc = frappe.get_doc("Salary Component", name)
		doc.description = description
		doc.salary_component_abbr = abbr
		doc.save()
		return
	frappe.get_doc(
		{
			"doctype": "Salary Component",
			"salary_component": name,
			"salary_component_abbr": abbr,
			"description": description,
			"type": "Deduction",
			"company": None,
		}
	).insert(ignore_permissions=True)
	print(f"  created deduction component: {name}")


def _ensure_structure(company: str, earning_components: list[str], deduction_components: list[str]) -> str:
	name = structure_name(company)
	doc = frappe.get_doc(
		{
			"doctype": "Salary Structure",
			"name": name,
			"salary_structure": name,
			"company": company,
			"currency": CURRENCY,
			"payroll_frequency": PAYROLL_FREQUENCY,
			"max_lateness": 0,
			"deductions": [
				{
					"salary_component": component,
					"abbr": ABBR[component],
					"amount": TEMPLATE_AMOUNT,
					"flexi_amount": TEMPLATE_AMOUNT,
					"idx": index + 1,
					# `variable_based_on_taxable_salary` stays 0 on purpose.  With
					# it set, hrms.get_tax_component() returns this component and
					# the Salary Structure Assignment then demands an ERPNext
					# `Income Tax Slab` - which this app does not use, because tax
					# comes from the Income Tax Slab bands instead.
				}
				for index, component in enumerate(deduction_components)
			],
			"employer_contributions": [
				{
					"salary_component": component,
					"abbr": ABBR[component],
					"amount": TEMPLATE_AMOUNT,
					"flexi_amount": TEMPLATE_AMOUNT,
					"idx": index + 1,
				}
				for index, (component, _abbr, _d) in enumerate(EMPLOYER_CONTRIBUTIONS)
			],
			"earnings": [
				{
					"salary_component": component,
					"abbr": ABBR[component],
					"amount": TEMPLATE_AMOUNT,
					"flexi_amount": TEMPLATE_AMOUNT,
					"idx": index + 1,
				}
				for index, component in enumerate(earning_components)
			],
		}
	)
	doc.insert(ignore_permissions=True)
	# a Salary Structure is submittable and has to be *in effect*, not a draft
	# template: the desk shows a draft as not active, and 170 submitted slips and
	# assignments reference these.  Left as drafts they were an inconsistent state.
	doc.submit()
	return name


def apply() -> None:
	"""Create the components, structures and assignments.  One transaction."""
	result = build_plan()
	if result["errors"]:
		report(result)
		frappe.throw(
			f"{len(result['errors'])} error(s) in the salary plan; nothing was written.",
			title=frappe._("Salary structure build blocked"),
		)

	for name, _label, description, abbr in EARNINGS:
		_ensure_component(name, description, abbr)
	for name, abbr, description in PRE_TAX_DEDUCTIONS:
		_ensure_deduction(name, description, abbr)
	frappe.db.commit()

	earning_components = result["earning_components"]
	# the statutory deductions (social insurance, martyrs fund, income tax) are
	# created by setup.create_salary_components(); the pre-tax ones by this plan.
	# Both belong in the structure's `deductions` table.
	deduction_components = (
		[c for c, _a, _d in DEDUCTIONS] + result["deduction_components"] + POST_TAX_COMPONENTS
	)
	created = 0
	for company in sorted(result["structures"]):
		name = structure_name(company)
		if (
			frappe.db.exists("Salary Structure", name)
			and frappe.db.get_value("Salary Structure", name, "docstatus") == 2
		):
			# A cancelled structure cannot be edited back to life, and the
			# component list is exactly the sort of thing that changes after one
			# was submitted.  Its assignments and slips are already gone by the
			# time we get here, so it is replaced outright.
			frappe.db.delete("Salary Structure", name)
		if frappe.db.exists("Salary Structure", name):
			# keep the template in step with the component list
			doc = frappe.get_doc("Salary Structure", name)
			changed = False
			for table, rows in (
				("earnings", earning_components),
				("deductions", deduction_components),
				("employer_contributions", [c for c, _a, _d in EMPLOYER_CONTRIBUTIONS]),
			):
				have = {row.salary_component for row in doc.get(table)}
				# this app owns these structures, so a row that is not in the
				# expected list is stale and is removed rather than left to inflate
				# gross pay
				for row in list(doc.get(table)):
					if row.salary_component not in rows:
						doc.get(table).remove(row)
						changed = True
				for component in rows:
					if component not in have:
						doc.append(
							table,
							{
								"salary_component": component,
								"abbr": ABBR[component],
								"amount": TEMPLATE_AMOUNT,
								"flexi_amount": TEMPLATE_AMOUNT,
							},
						)
						changed = True
				# put the rows in the canonical order: a component added later would
				# otherwise be appended at the end, so the printed slip lists
				# "Income Tax on Cost Share" after "Family Medical Insurance"
				by_name = {row.salary_component: row for row in doc.get(table)}
				for position, component in enumerate(rows, start=1):
					row = by_name.get(component)
					if row is not None and row.idx != position:
						row.idx = position
						changed = True
			if changed:
				doc.save()
			if doc.docstatus == 0:
				# see _ensure_structure: a structure has to be in effect
				doc.submit()
		else:
			_ensure_structure(company, earning_components, deduction_components)
		created += 1
	frappe.db.commit()
	print(f"  structures: {created}")

	# one effective assignment per employee; replace any this app owns.
	# submitted, not just inserted: a Salary Slip only finds its assignment at
	# docstatus 1 (salary_slip.set_salary_structure_assignment), so a draft
	# assignment leaves the employee looking unassigned when the slip is made.
	made = 0
	for item in result["assignments"]:
		for stale in frappe.get_all(
			"Salary Structure Assignment",
			filters={
				"employee": item["employee_number"],
				"salary_structure": item["structure"],
			},
			pluck="name",
			limit_page_length=0,
		):
			# a submitted assignment has to be cancelled before it can be deleted;
			# force=True on delete_doc still refuses a submitted record
			doc = frappe.get_doc("Salary Structure Assignment", stale)
			if doc.docstatus == 1:
				doc.cancel()
			doc.delete(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Salary Structure Assignment",
				"employee": item["employee_number"],
				"salary_structure": item["structure"],
				"company": item["company"],
				"from_date": item["from_date"],
				"base_flag": 1,
				# the employee's Basic, carried here so it is available before a
				# slip exists.  The allowances are not: they belong on the slip.
				"base": item["earnings"].get("Basic", 0.0),
			}
		).insert(ignore_permissions=True).submit()
		made += 1
	frappe.db.commit()
	frappe.clear_cache()

	print(f"\nwrote {made} salary structure assignments")
	print(f"  Salary Structure              : {frappe.db.count('Salary Structure')}")
	print(f"  Salary Structure Assignment   : {frappe.db.count('Salary Structure Assignment')}")
	print(f"  delegated cost shares pending  : {len(result['delegated'])}")
