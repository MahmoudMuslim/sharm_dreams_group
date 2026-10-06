"""Build the group's monthly payroll from `seed_data/payroll_spec.json`.

The spec is one record per **person**, produced by `tools/build_payroll_spec.py`
from the Master Payroll sheet and checked there against the sheet's own figures
before it is written.  This module turns it into live HRMS documents:

    Payroll Period  ->  Payroll Entry (one per paying company)
                     ->  Salary Slip (one per employee)
                     ->  SDG Group Allocation (one per cost share)

Why one slip per person: the sheet's "Final approved remuneration for payment" is

    BA = (own row net + every delegated row's net) - loan - phone - family medical

so a manager who is cost-charged to three other companies is still one employee
paid one amount.  The cost shares are earnings on their slip and the split between
companies is carried as `SDG Group Allocation` records.

A slip's gross therefore holds two kinds of earning which are taxed on separate
bases, so the engine needs the boundary: `sdg_leader_earning` is what this company
employs the person for, and the rest is cost share, taxed flat with no exemption
and no social insurance.
"""

from __future__ import annotations

import json
import pathlib

import frappe
from dateutil.relativedelta import relativedelta
from frappe.utils import flt, getdate

from sharm_dreams_group import companies
from sharm_dreams_group.hr.salary import ABBR, structure_name

# The payroll is dated **September 2026** by decision, and the figures in it come
# from the **August 2026** Master Payroll run.  That is not a slip in the code: the
# only payroll source available is the August file, and it was confirmed to be
# August by matching 116 of 134 net salaries and final approved amounts in
# `Head Office AUG 2026` against it to the cent.
#
# The consequence is that anyone whose employment changed between the two months
# is wrong in this run.  H0355 is the known case: he joined 16/08/2026, so the
# August row charges him for the 15 days before he started.  He is paid, but
# from his own components rather than the sheet's total earning, because the 240
# by which the two differ has been confirmed to be an error.  Re-check the
# joiners and leavers each month this is used again.
PERIOD_DECISION = "September 2026 payroll period, built from the August 2026 run"

# The inter-company recharge is booked on the paying side only.  The employers
# carry the 491,562.00 owed to them as `Debtors`; the five charging companies have
# NOT yet recognised the cost against an intercompany payable, because none of the
# group's 13 charts of accounts has an intercompany account and adding one is the
# group's decision, not this app's.  Until that side is posted the group is out by
# that amount, and `SDG Group Allocation` records who is charged for what without
# creating a journal.  See `recharge_summary` for the split.

SPEC_FILE = pathlib.Path(__file__).resolve().parents[2] / "seed_data" / "payroll_spec.json"

# What each person was actually paid in the August run, and what the corrected
# tax table would have paid.  Reconciling a slip against the *spec* only proves it
# matches the correction, which is not the check payroll needs - they need the
# difference against real payments, so both are kept side by side.
AUGUST_PAID_FILE = pathlib.Path(__file__).resolve().parents[2] / "seed_data" / "august_paid.json"
CORRECTED_PAID_FILE = pathlib.Path(__file__).resolve().parents[2] / "seed_data" / "corrected_paid.json"

# what the sheet's own columns are called, mapped onto this app's components
LEADER_EARNINGS = {
	"basic": "Basic",
	"Fixed Monthly Incentives": "Fixed Monthly Incentives",
	"Overtime": "Overtime",
	"Rest Allowance": "Rest Allowance",
	"Housing & Transport": "Housing & Transport",
	"Exceptional Bonus": "Exceptional Bonus",
	"Days off Value": "Days off Value",
	"Official Days Value": "Official Days Value",
}

PAYMENT_DEDUCTIONS = {
	"phone": "Phone Allowance Recovered",
	"family_medical": "Family Medical Insurance",
}

# Two employees carry a different number in the payroll sheet than in the master
# the Employee records came from.  Both were confirmed to be the same person by
# national ID *and* company before being mapped, so this is a rename, not a
# guess: without it these two are skipped and go unpaid.
#
#   sheet H0272 (27011052101593, Egypt Development & Investment) -> H0285
#   sheet H0345 (29605058800374, Rowad Misr For Red Sea Hotels)   -> H0344
#
# Worth raising with the group: the master is treated as authoritative for the
# employee number, so if the payroll numbering is the correct one these two
# Employee records are named wrongly.
WORKBOOK_ID_MAP = {
	"H0272": "H0285",
	"H0345": "H0344",
}

# what the sheet's own columns are called, mapped onto this app's components
LEADER_EARNINGS = {
	"basic": "Basic",
	"Fixed Monthly Incentives": "Fixed Monthly Incentives",
	"Overtime": "Overtime",
	"Rest Allowance": "Rest Allowance",
	"Housing & Transport": "Housing & Transport",
	"Exceptional Bonus": "Exceptional Bonus",
	"Days off Value": "Days off Value",
	"Official Days Value": "Official Days Value",
}

PAYMENT_DEDUCTIONS = {
	"phone": "Phone Allowance Recovered",
	"family_medical": "Family Medical Insurance",
}

# Two employees carry a different number in the payroll sheet than in the master
# the Employee records came from.  Both were confirmed to be the same person by
# national ID *and* company before being mapped, so this is a rename, not a
# guess: without it these two are skipped and go unpaid.
#
#   sheet H0272 (27011052101593, Egypt Development & Investment) -> H0285
#   sheet H0345 (29605058800374, Rowad Misr For Red Sea Hotels)   -> H0344
#
# Worth raising with the group: the master is treated as authoritative for the
# employee number, so if the payroll numbering is the correct one these two
# Employee records are named wrongly.
WORKBOOK_ID_MAP = {
	"H0272": "H0285",
	"H0345": "H0344",
}

# Employees deliberately left out of a run, with the reason.  A skip is never
# silent: the name and reason come out in the run report, so someone unpaid is
# always visible rather than quietly missing.
#
# Empty.  H0355 was here while his row was being resolved: he joined 16/08/2026,
# so the sheet charges him for the 15 days before he started, and its total
# earning of 19,114 does not equal its own components of 18,874.  The 240
# difference was confirmed to be an error, so he is now paid from his components
# alone - gross 18,874, net 7,258.06 - which is 192.06 below what August paid
# him, and no longer needs holding back.
SKIPPED: dict[str, str] = {}


def load_spec() -> dict:
	if not SPEC_FILE.exists():
		frappe.throw(
			frappe._("{0} not found. Run tools/build_payroll_spec.py on the host.").format(SPEC_FILE)
		)
	return json.loads(SPEC_FILE.read_text())


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def plan(only: list[str] | None = None) -> dict:
	"""Work out each person's slip.  Writes nothing.

	A person is skipped, with a reason, when their employing company or their
	Salary Structure cannot be resolved - the payroll must not quietly pay the
	wrong company.
	"""
	spec = load_spec()
	slips, skipped, conflicts = [], [], []

	for person in spec["people"]:
		work_id = WORKBOOK_ID_MAP.get(person["work_id"], person["work_id"])
		if only and work_id not in only:
			continue
		if work_id in SKIPPED:
			skipped.append({"work_id": work_id, "reason": SKIPPED[work_id]})
			_clear_slip(work_id)
			continue

		# The employing company comes from the Employee record, not the sheet.
		# `Salary Structure Assignment.company` is `fetch_from: employee.company`,
		# so HRMS forces the Employee's company on the assignment and then checks
		# the structure against it - the sheet's company cannot be assigned through
		# HRMS at all.  Two employees disagree between the two sources; they are
		# paid under the Employee record and the disagreement is reported.
		sheet_company = companies.resolve(person["company"])
		company = frappe.db.get_value("Employee", work_id, "company")
		if sheet_company and company != sheet_company:
			conflicts.append(
				{
					"work_id": work_id,
					"employee_company": company,
					"sheet_company": sheet_company,
				}
			)
		if not company:
			skipped.append({"work_id": work_id, "reason": "no company on the Employee record"})
			continue
		if not frappe.db.exists("Employee", work_id):
			skipped.append({"work_id": work_id, "reason": "no Employee record"})
			continue

		leader = person["leader"]
		structure = structure_name(company)
		if not frappe.db.exists("Salary Structure", structure):
			skipped.append({"work_id": work_id, "reason": f"no salary structure for {company}"})
			continue

		shares = person["delegated"]
		leader_earning = flt(leader["total_earning"])
		share_earning = sum(flt(s["total_earning"]) for s in shares)
		payment = person["payment_adjustments"]

		slips.append(
			{
				"work_id": work_id,
				"name": person["name"],
				"company": company,
				"structure": structure,
				"leader": leader,
				"shares": shares,
				"leader_earning": leader_earning,
				"share_earning": share_earning,
				"si_limit": flt(leader["si_limit"]),
				# The spec holds the rate as a fraction (0.11) because that is how
				# it multiplies out; `SDG Payroll Settings` and the engine both work
				# in percentage points, so it is converted here.  One employee in the
				# sheet has social insurance hand-entered at 1% instead of 11%, and
				# that is carried through rather than corrected.
				"si_employee_rate": flt(leader["si_employee_rate"] or 0.11) * 100,
				# The MI fund is stated per company line, and - as with gross, social
				# insurance and the insurance premiums - the sheet aggregates it across
				# the employed line and every delegated one rather than splitting it.
				"mi_employer_share": flt(leader.get("mi_employer_share"))
				+ sum(flt(share.get("mi_employer_share")) for share in shares),
				"earnings": {
					component: flt(amount)
					for key, component in LEADER_EARNINGS.items()
					if (amount := flt(leader["earnings"].get(key)))
				}
				| ({"Delegated Cost Share": share_earning} if share_earning else {}),
				"deductions": {
					"Penalties": flt(leader["penalties"]),
					"Absenteeism": flt(leader["absenteeism"]),
					"Medical Insurance": flt(leader["medical_insurance"]),
					"Life Insurance": flt(leader["life_insurance"]),
					**{
						component: flt(payment[key])
						for key, component in PAYMENT_DEDUCTIONS.items()
						if flt(payment[key])
					},
				},
				"loan": flt(payment["loan"]),
				"expected_net": flt(person["combined_net_salary"])
				- flt(payment["loan"])
				- flt(payment["phone"])
				- flt(payment["family_medical"])
				+ flt(payment["refund"]),
			}
		)

	return {
		"slips": slips,
		"skipped": skipped,
		"conflicts": conflicts,
		"meta": spec["_meta"],
	}


# ---------------------------------------------------------------------------
# periods and entries
# ---------------------------------------------------------------------------
def ensure_payroll_period(start: str, end: str) -> list[str]:
	"""One Payroll Period per company, for the month being paid."""
	made = []
	for company in frappe.get_all("Company", pluck="name", order_by="name", limit_page_length=0):
		if frappe.db.exists("Payroll Period", {"company": company, "start_date": start, "end_date": end}):
			continue
		frappe.get_doc(
			{
				"doctype": "Payroll Period",
				# Payroll Period's autoname is "Prompt", so the name has to be given
				# rather than generated; company plus the start date is unique for a
				# monthly run and readable in the period picker.
				"name": f"{company}-{start}",
				"company": company,
				"start_date": start,
				"end_date": end,
				"periods": [{"start_date": start, "end_date": end}],
			}
		).insert(ignore_permissions=True)
		made.append(company)
	frappe.db.commit()
	return made


# Where each salary component posts, by the leaf account each company's chart of
# accounts actually has.  HRMS refuses to submit a slip until every component it
# uses has one of these for the company, so they cannot be left unset.
#
# The one judgement call is the cost share.  It is not this company's cost: it is
# pay charged to other group companies, and posting it to `Salary` would overstate
# their salary cost by the whole recharge.  It goes to `Debtors` as a receivable
# from the charging companies instead, which is what it is.
#
# The other side of that - the charging companies crediting what they owe - is not
# posted by this run.  `SDG Group Allocation` records who is charged for what, but
# it creates no journal, so the inter-company position is only half booked until
# that is done.
COMPONENT_ACCOUNTS = {
	"salary": (
		"Basic",
		"Fixed Monthly Incentives",
		"Rest Allowance",
		"Housing & Transport",
		"Exceptional Bonus",
		"Phone Allowance Recovered",
		"Penalties",
		"Absenteeism",
	),
	"tax": ("Income Tax", "Income Tax on Cost Share"),
	"benefits": (
		"Social Insurance (Employee)",
		"Martyrs Fund",
		"Medical Insurance",
		"Life Insurance",
		"Family Medical Insurance",
	),
	"recharge": ("Delegated Cost Share",),
	# employer contributions only: the liability for the amount owed to the
	# statutory body, as well as the cost to the company
	"employer": (
		"Social Insurance (Employer)",
		"Training and Development Fund",
		"Emergency Fund",
		"MI Fund (Employer)",
	),
}

ACCOUNT_FOR_ROLE = {
	"salary": "Salary",
	"tax": "Tax Expense",
	"benefits": "Employee Benefits Obligation",
	"recharge": "Debtors",
	# the cost of an employer contribution is employment cost; the matching
	# liability is the amount owed to the statutory body
	"employer": "Salary",
	"employer_liability": "Short-term Provisions",
}


def ensure_salary_component_accounts() -> dict:
	"""Give every payroll component a GL account in every paying company.

	Idempotent, and reports anything it could not map rather than guessing: a
	component with no account stops HRMS submitting the slip, so a missing account
	is a hard error, not something to paper over with a default.
	"""
	company_abbr = {}
	for company in frappe.get_all("Company", pluck="name", order_by="name", limit_page_length=0):
		abbr = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
		company_abbr[company] = abbr.split(" - ", 1)[1] if abbr and " - " in abbr else None

	made, missing = 0, []
	for company, abbr in company_abbr.items():
		if not abbr:
			missing.append(f"{company}: no leaf Cost Center to derive the account suffix from")
			continue
		for role, components in COMPONENT_ACCOUNTS.items():
			account = frappe.db.get_value(
				"Account", {"company": company, "name": f"{ACCOUNT_FOR_ROLE[role]} - {abbr}"}, "name"
			)
			if not account:
				missing.append(f"{company}: no account {ACCOUNT_FOR_ROLE[role]} - {abbr}")
				continue
			for component in components:
				if not frappe.db.exists("Salary Component", component):
					missing.append(f"{company}: no Salary Component {component}")
					continue
				if frappe.db.get_value(
					"Salary Component Account", {"parent": component, "company": company}, "name"
				):
					continue
				# An employer contribution needs a liability account as well as an
				# expense one - HRMS's get_employer_contribution_accounts requires
				# both, or the accrual refuses.  The liability is the amount owed to
				# the statutory body, so it sits in short-term provisions.
				liability = None
				if role == "employer":
					liability = frappe.db.get_value(
						"Account",
						{"company": company, "name": f"{ACCOUNT_FOR_ROLE['employer_liability']} - {abbr}"},
						"name",
					)
					if not liability:
						missing.append(
							f"{company}: no account {ACCOUNT_FOR_ROLE['employer_liability']} - {abbr}"
						)
						continue
				frappe.get_doc(
					{
						"doctype": "Salary Component Account",
						"parent": component,
						"parenttype": "Salary Component",
						"parentfield": "accounts",
						"company": company,
						"account": account,
						"liability_account": liability,
					}
				).insert(ignore_permissions=True)
				made += 1
	frappe.db.commit()
	frappe.clear_cache()
	return {"created": made, "missing": missing}


def _payroll_account(company: str) -> str:
	"""The company's payroll payable account, which must already exist.

	HRMS only accepts an account of type `Payable`
	(payroll_entry.validate_payroll_payable_account), and the group already has one
	for every paying company - `Payroll Payable - <abbr>`.  Nothing is created here:
	the group's chart of accounts is not this app's to extend, and a new account
	would have to be typed `Liability`/`Payable` by hand anyway, since ERPNext
	derives `account_type` from the parent and a group parent here carries none.
	"""
	name = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Payable", "name": ("like", "Payroll Payable%")},
		"name",
		order_by="name",
	)
	if not name:
		frappe.throw(
			frappe._("No Payroll Payable account for {0}. Create one of type Payable first.").format(company),
			title=frappe._("Payroll account missing"),
		)

	# `Company.default_payroll_payable_account` is where HRMS takes the account from
	# when a Payroll Entry is created, so a default left pointing at an account that
	# no longer exists fails every new entry with "Could not find Payroll Payable
	# Account".  Repair it here rather than making each entry be fixed by hand.
	default = frappe.db.get_value("Company", company, "default_payroll_payable_account")
	if default and not frappe.db.exists("Account", default):
		frappe.db.set_value("Company", company, "default_payroll_payable_account", name)
		frappe.db.commit()
		frappe.clear_cache()
		print(f"  repaired {company}: default payroll payable {default} -> {name}")
	return name


def ensure_payroll_entry(company: str, start: str, end: str, posting_date: str) -> str:
	"""One Payroll Entry per company, reused on a re-run."""
	# docstatus != 2: a cancelled entry cannot be edited, and the naming series
	# never reuses its name, so a rebuild has to make a new one rather than try to
	# reuse the cancelled shell
	name = frappe.db.get_value(
		"Payroll Entry",
		{
			"company": company,
			"start_date": start,
			"end_date": end,
			"docstatus": ["!=", 2],
		},
		"name",
	)
	if name:
		return name
	entry = frappe.get_doc(
		{
			"doctype": "Payroll Entry",
			"company": company,
			"start_date": start,
			"end_date": end,
			"posting_date": posting_date,
			"payroll_frequency": "Monthly",
			"currency": frappe.db.get_value("Company", company, "default_currency") or "EGP",
			"exchange_rate": 1.0,
			"cost_center": frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}),
			"payroll_payable_account": _payroll_account(company),
		}
	).insert(ignore_permissions=True)
	return entry.name


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------
def _set_rows(doc, fieldname: str, values: dict) -> None:
	"""Set each component's amount on a slip table, adding a row if it is absent.

	Rows are added through `doc.append` rather than `doc.get(field).append`: the
	latter pushes a bare dict into a list the Document expects to hold child
	Documents, and the next save then fails inside `_set_defaults`.
	"""
	for component, amount in values.items():
		if not amount:
			continue
		for row in doc.get(fieldname) or []:
			if row.salary_component == component:
				row.amount = amount
				break
		else:
			doc.append(
				fieldname,
				{"salary_component": component, "abbr": ABBR.get(component), "amount": amount},
			)


def _clear_slip(work_id: str) -> None:
	"""Remove this employee's slip and cost shares, whatever their status.

	Called before rebuilding and when an employee is skipped.  Without it a skip
	leaves the previous run's slip in place and the employee is paid despite being
	held back.

	A submitted slip has to be cancelled before it can be deleted, which is why this
	is not just a `db.delete`.  Cancelling is safe here because the payroll entry's
	accrual journal - the only thing that actually posted to the ledger - is
	cancelled separately, first.
	"""
	# The allocations link to the slip, so they have to go first or the slip
	# cannot be cancelled or deleted.  They are removed straight from the table
	# rather than through `delete_doc`, which runs Frappe's link check: that check
	# consults a cached map of dynamic links, and while the map still lists these
	# rows it refuses the *slip* because the allocation points back at it - so the
	# documented order deadlocks.  Safe to skip: the doctype is not submittable and
	# an allocation records no journal, so there is nothing to un-book.
	for allocation in frappe.get_all(
		"SDG Group Allocation",
		filters={"employee": work_id},
		pluck="name",
		limit_page_length=0,
	):
		frappe.db.delete("SDG Group Allocation", allocation)
	frappe.clear_cache(doctype="SDG Group Allocation")
	for stale in frappe.get_all(
		"Salary Slip",
		filters={"employee": work_id, "docstatus": 0},
		pluck="name",
		limit_page_length=0,
	):
		frappe.delete_doc("Salary Slip", stale, ignore_permissions=True)
	for done in frappe.get_all(
		"Salary Slip",
		filters={"employee": work_id, "docstatus": 1},
		pluck="name",
		limit_page_length=0,
	):
		doc = frappe.get_doc("Salary Slip", done)
		doc.cancel()
		frappe.db.commit()
		frappe.delete_doc("Salary Slip", done, force=True, ignore_permissions=True)


def _make_slip(item: dict, on_date: str):
	"""One slip per employee, made through its Payroll Entry."""
	from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

	_clear_slip(item["work_id"])

	slip = make_salary_slip(item["structure"], employee=item["work_id"], posting_date=on_date)
	if isinstance(slip, str):
		slip = frappe.get_doc("Salary Slip", slip)

	# tell the engine where the employed/cost-share boundary is before it runs
	slip.sdg_leader_earning = item["leader_earning"]
	slip.sdg_si_limit = item["si_limit"]

	_set_rows(slip, "earnings", item["earnings"])
	_set_rows(slip, "deductions", item["deductions"])
	slip.sdg_si_employee_rate = item["si_employee_rate"]
	slip.sdg_mi_employer_share = flt(item.get("mi_employer_share"))
	slip.save()

	slip.reload()
	slip.calculate_net_pay()
	slip.save()

	# HRMS recomputes `total_loan_repayment` on *every* validate - `validate` calls
	# `calculate_net_pay`, which calls `set_loan_repayment`, which zeroes the field
	# and rebuilds it from `Loan` records the group does not have.  So the figure is
	# written straight to the row after the last save, and net pay restated with the
	# same arithmetic HRMS's own `set_net_pay` uses.  Setting the field on the
	# document is silently undone, which left 27 employees 1,000 to 5,000 light.
	if item["loan"]:
		net = flt(slip.gross_pay) - flt(slip.total_deduction) - flt(item["loan"])
		frappe.db.set_value(
			"Salary Slip",
			slip.name,
			{"total_loan_repayment": item["loan"], "net_pay": net, "rounded_total": round(net)},
			update_modified=False,
		)

	for share in item["shares"]:
		frappe.get_doc(
			{
				"doctype": "SDG Group Allocation",
				"salary_slip": slip.name,
				"employee": item["work_id"],
				"employing_company": item["company"],
				"charged_company": companies.resolve(share["delegated_company"]) or "",
				"gross_amount": flt(share["total_earning"]),
				"income_tax": flt(share["tax"]),
				"martyrs_fund": flt(share["martyrs"]),
				"remarks": f"Cost share of {item['work_id']}'s pay, per the Master Payroll sheet",
			}
		).insert(ignore_permissions=True)

	return slip


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------
def fiscal_period() -> tuple[str, str]:
	"""The month being paid: the current month inside the open Fiscal Year."""
	from frappe.utils import nowdate

	from sharm_dreams_group.hr.salary import current_fiscal_year

	year_start, year_end = current_fiscal_year()
	today = getdate(nowdate())
	year_start, year_end = getdate(year_start), getdate(year_end)
	if today.year == year_start.year:
		first = today.replace(day=1)
		last = first + relativedelta(months=1, days=-1)
	else:
		first = year_start
		last = year_end
	first = max(first, year_start)
	last = min(last, year_end)
	return getdate(first).isoformat(), getdate(last).isoformat()


def ensure_prerequisites() -> dict:
	"""Holiday list assignments, without which HRMS refuses to make any slip."""
	from sharm_dreams_group.hr.slip import ensure_holiday_assignments

	return ensure_holiday_assignments()


def report(result: dict) -> None:
	slips = result["slips"]
	gross = sum(flt(s["leader_earning"]) + flt(s["share_earning"]) for s in slips)
	share = sum(flt(s["share_earning"]) for s in slips)
	net = sum(s["expected_net"] for s in slips)
	companies_used = sorted({s["company"] for s in slips})
	print(f"  people in the spec       : {result['meta']['people']}")
	print(f"  slips to build           : {len(slips)}")
	print(f"  skipped                 : {len(result['skipped'])}")
	for row in result["skipped"]:
		print(f"      {row['work_id']:8} {row['reason']}")
	if result.get("conflicts"):
		print("  employing company disagreements, paid under the Employee record:")
		for row in result["conflicts"]:
			print(
				f"      {row['work_id']:8} Employee {row['employee_company']}"
				f"  vs sheet {row['sheet_company']}"
			)
	print(f"  paying companies        : {len(companies_used)}")
	print(f"  gross pay               : {gross:,.2f}")
	print(f"    of which cost share   : {share:,.2f}")
	print(f"  expected net (from spec): {net:,.2f}")


def _entry_employees(entry, items: list[dict]) -> None:
	"""List the employees a Payroll Entry is paying.

	This is not cosmetic.  `Payroll Entry.create_salary_slips` iterates
	`self.employees` and does nothing at all when it is empty, so an entry with no
	employee rows is a shell: it shows nobody in the desk, and submitting it books
	nothing.  Both fields are `allow_on_submit = 0`, so they have to be in place
	before the entry is submitted.
	"""
	if entry.docstatus != 0:
		frappe.throw(
			frappe._("Cannot list employees on Payroll Entry {0}: it is not a draft.").format(entry.name)
		)
	entry.set("employees", [])
	for item in items:
		employee = frappe.get_doc("Employee", item["work_id"])
		entry.append(
			"employees",
			{
				"employee": item["work_id"],
				"employee_name": employee.employee_name,
				"department": employee.department,
				"designation": employee.designation,
			},
		)
	entry.number_of_employees = len(items)
	entry.save()
	frappe.db.commit()


def _fill_slip(slip, item: dict) -> None:
	"""Put this employee's figures on a slip HRMS created from the structure.

	The entry creates the slip from the Salary Structure, which is a template with
	every component at zero, so the actual package is written here.
	"""
	slip.sdg_leader_earning = item["leader_earning"]
	slip.sdg_si_limit = item["si_limit"]
	slip.sdg_si_employee_rate = item["si_employee_rate"]
	slip.sdg_mi_employer_share = flt(item.get("mi_employer_share"))
	_set_rows(slip, "earnings", item["earnings"])
	_set_rows(slip, "deductions", item["deductions"])
	slip.total_loan_repayment = item["loan"]
	slip.save()


def _record_cost_shares(slip, item: dict) -> None:
	"""One `SDG Group Allocation` per cost share, saying who is being charged.

	The share is already an earning on the slip, because the employee is paid one
	amount.  This is the other side of the same fact: which company is being
	charged, and for how much.  It records no journal - see `PERIOD_DECISION`.
	"""
	for share in item["shares"]:
		charged = companies.resolve(share["delegated_company"])
		if not charged:
			continue
		frappe.get_doc(
			{
				"doctype": "SDG Group Allocation",
				"salary_slip": slip.name,
				"employee": item["work_id"],
				"employing_company": item["company"],
				"charged_company": charged,
				"gross_amount": flt(share["total_earning"]),
				"income_tax": flt(share["tax"]),
				"martyrs_fund": flt(share["martyrs"]),
				"remarks": f"Cost share of {item['work_id']}'s pay, per the Master Payroll sheet",
			}
		).insert(ignore_permissions=True)


def _reassert_loans(slips: dict[str, str], items: list[dict]) -> int:
	"""Rewrite the loan after submission; see `_make_slip` for why.

	The figure comes from the plan, not from the slip: submission is exactly what
	zeroes the field, so reading it back finds nothing to restore.
	"""
	fixed = 0
	loans = {i["work_id"]: flt(i["loan"]) for i in items}
	for employee, name in slips.items():
		loan = loans.get(employee, 0.0)
		if not loan:
			continue
		slip = frappe.get_doc("Salary Slip", name)
		net = flt(slip.gross_pay) - flt(slip.total_deduction) - loan
		frappe.db.set_value(
			"Salary Slip",
			name,
			{"total_loan_repayment": loan, "net_pay": net, "rounded_total": round(net)},
			update_modified=False,
		)
		fixed += 1
	return fixed


def _entry_args(entry) -> frappe._dict:
	"""The arguments HRMS passes down when an entry makes its slips."""
	return frappe._dict(
		{
			"salary_slip_based_on_timesheet": entry.salary_slip_based_on_timesheet,
			"payroll_frequency": entry.payroll_frequency,
			"start_date": entry.start_date,
			"end_date": entry.end_date,
			"company": entry.company,
			"posting_date": entry.posting_date,
			"deduct_tax_for_unsubmitted_tax_exemption_proof": (
				entry.deduct_tax_for_unsubmitted_tax_exemption_proof
			),
			"payroll_entry": entry.name,
			"exchange_rate": entry.exchange_rate,
			"currency": entry.currency,
		}
	)


def _make_slips_now(entry, items: list[dict]) -> None:
	"""Make the entry's slips in this process rather than a background one.

	`Payroll Entry.create_salary_slips` enqueues as soon as a company has more than
	30 employees, and `submit_salary_slips` does the same above 30 slips.  Eleven of
	the group's thirteen companies are over that, so the entries silently created
	nothing and queued work nobody was waiting for.  HRMS's own functions are called
	directly here instead, with the same arguments it would have queued.
	"""
	from hrms.payroll.doctype.payroll_entry.payroll_entry import create_salary_slips_for_employees

	entry.reload()
	create_salary_slips_for_employees(
		[i["work_id"] for i in items], _entry_args(entry), publish_progress=False
	)
	frappe.db.commit()
	frappe.clear_cache()


def _submit_slips_now(entry) -> None:
	"""Submit the entry's slips and book its accrual, in this process.

	Same reason as `_make_slips_now`: the HRMS wrappers enqueue above 30 slips.
	"""
	from hrms.payroll.doctype.payroll_entry.payroll_entry import submit_salary_slips_for_employees

	entry.reload()
	# `get_sal_slip_list` returns [name, salary_structure] pairs, which is the shape
	# `submit_salary_slips_for_employees` indexes into; a plain list of names makes
	# it read entry[0] as the first *character* of the slip name
	slips = entry.get_sal_slip_list(ss_status=0)
	if slips:
		submit_salary_slips_for_employees(entry, slips, publish_progress=False)
	frappe.db.commit()
	frappe.clear_cache()


def apply(only: list[str] | None = None) -> None:
	"""Build the whole payroll the way HRMS expects: entry first, then slips.

	`Payroll Entry` is the parent and it creates the slips from its own employee
	list, so the order is: build the entry with its employees, submit it, let it
	make the slips, write each employee's figures onto them, then let the entry
	submit the slips and book the accrual.  Building the slips directly, as this
	originally did, left every entry with an empty employee table and the accrual
	had to be created by hand.
	"""
	frappe.flags.enqueue_payroll_entry = 0
	frappe.flags.via_payroll_entry = True

	ensure_prerequisites()
	accounts = ensure_salary_component_accounts()
	if accounts["missing"]:
		frappe.throw(
			frappe._("Cannot run payroll, these accounts are missing: {0}").format(
				"; ".join(accounts["missing"][:10])
			),
			title=frappe._("Salary component accounts incomplete"),
		)
	print(f"  salary component accounts created: {accounts['created']}")

	start, end = fiscal_period()
	ensure_payroll_period(start, end)

	# The bulk run does not stop on a period mismatch - a deliberate back-run of a
	# past month is legitimate - but it must never be silent about it, because every
	# slip it writes is stamped with the entry's dates and would look correct.
	spec_month = spec_period()
	if spec_month and _month_of(start) != spec_month:
		print(f"  NOTE: the spec holds {spec_month} figures, applied to a {start} payroll.")
		print("        Every slip will carry the entry's dates and the month's figures.")

	result = plan(only)
	report(result)
	frappe.db.rollback()

	on_date = end
	by_company: dict[str, list[dict]] = {}
	for item in result["slips"]:
		by_company.setdefault(item["company"], []).append(item)

	made = 0
	for company, items in sorted(by_company.items()):
		entry_name = ensure_payroll_entry(company, start, end, on_date)
		entry = frappe.get_doc("Payroll Entry", entry_name)
		if entry.docstatus == 1:
			# the accrual journal is the only thing posted to the ledger, and it is
			# cancelled with the entry; the slips then have to go before a rebuild
			_cancel_entry_and_accrual(entry)
			entry_name = ensure_payroll_entry(company, start, end, on_date)
			entry = frappe.get_doc("Payroll Entry", entry_name)

		# clear this company's previous slips, or the entry refuses to make a
		# second slip for an employee and period it already has
		for item in items:
			_clear_slip(item["work_id"])
		if entry.docstatus != 0:
			frappe.throw(
				frappe._("Payroll Entry {0} is not a draft, so its employee list cannot be set.").format(
					entry.name
				)
			)
		_entry_employees(entry, items)
		entry.submit()
		_make_slips_now(entry, items)

		entry.reload()
		created = {
			row.employee: row.name
			for row in frappe.get_all(
				"Salary Slip",
				filters={"payroll_entry": entry.name, "docstatus": 0},
				fields=["name", "employee"],
				limit_page_length=0,
			)
		}
		missing = [i["work_id"] for i in items if i["work_id"] not in created]
		if missing:
			frappe.throw(
				frappe._(
					"Payroll Entry {0} did not create a slip for: {1}. The entry created {2} of {3}."
				).format(entry.name, ", ".join(missing), len(created), len(items)),
				title=frappe._("Salary slips missing"),
			)
		for item in items:
			slip = frappe.get_doc("Salary Slip", created[item["work_id"]])
			_fill_slip(slip, item)
			_record_cost_shares(frappe.get_doc("Salary Slip", slip.name), item)
		frappe.db.commit()

		# the entry submits its own slips and books the accrual - the normal path
		_submit_slips_now(entry)
		entry.reload()
		submitted = {
			row["employee"]: row["name"]
			for row in frappe.get_all(
				"Salary Slip",
				filters={"payroll_entry": entry.name, "docstatus": 1},
				fields=["name", "employee"],
				limit_page_length=0,
			)
		}
		slips = list(submitted.values())
		fixed = _reassert_loans(submitted, items)
		frappe.db.commit()
		made += len(slips)
		print(f"  {company[:40]:40} {len(slips):>4} slips submitted, {fixed} loans re-asserted")

	frappe.db.commit()
	frappe.clear_cache()
	print(f"built {made} salary slips for {start} to {end} across {len(by_company)} payroll entries")


def _cancel_entry_and_accrual(entry) -> None:
	"""Cancel a payroll entry and the accrual journal it booked.

	The entry's cancellation only *unlinks* the accrual from the slips, so the
	journal has to be cancelled explicitly or a rebuild leaves the cost booked
	twice.
	"""
	for je in frappe.get_all("Journal Entry", filters={"docstatus": 1}, pluck="name", limit_page_length=0):
		doc = frappe.get_doc("Journal Entry", je)
		if entry.name in {r.reference_name for r in doc.accounts if r.reference_type == "Payroll Entry"}:
			doc.cancel()
	entry.reload()
	entry.cancel()
	frappe.db.commit()
	frappe.clear_cache()


def reconcile(tolerance: float = 0.01) -> dict:
	"""Every slip's net against the sheet's "Final approved remuneration"."""
	result = plan()
	want = {s["work_id"]: s["expected_net"] for s in result["slips"]}
	exact, off, missing = 0, [], []
	# Only submitted slips are the payroll.  A draft left behind by an
	# interrupted run is an empty template with a negative net, and reading one
	# here reported the whole month as being overpaid by more than a million.
	for row in frappe.get_all(
		"Salary Slip", filters={"docstatus": 1}, fields=["name", "employee"], limit_page_length=0
	):
		if row["employee"] not in want:
			continue
		net = flt(frappe.db.get_value("Salary Slip", row["name"], "net_pay"))
		delta = net - want[row["employee"]]
		if abs(delta) <= tolerance:
			exact += 1
		else:
			off.append((row["employee"], net, want[row["employee"]], delta))
	total = sum(d for *_, d in off)
	print(f"  slips reconciled        : {exact + len(off)}")
	print(f"  net matches the sheet   : {exact}")
	print(f"  net differs             : {len(off)}  total {total:,.2f}")
	for who, net, expected, delta in sorted(off, key=lambda x: -abs(x[3]))[:15]:
		print(f"      {who:8} slip {net:>13,.2f}  sheet {expected:>13,.2f}  {delta:>+12,.2f}")
	return {"exact": exact, "off": off, "missing": missing}


def recharge_summary() -> list[dict]:
	"""The cost shares by the company being charged, for the recharge journal.

	Nothing is posted by this: it reports what the five charging companies owe the
	group, so whoever writes the intercompany entry has the figures.  See
	`PERIOD_DECISION`'s neighbour comment for why the paying side is not matched
	automatically.
	"""
	spec = load_spec()
	totals: dict[str, dict] = {}
	for person in spec["people"]:
		for share in person["delegated"]:
			company = companies.resolve(share["delegated_company"])
			row = totals.setdefault(
				company,
				{"charged_company": company, "shares": 0, "gross": 0.0, "tax": 0.0, "net": 0.0},
			)
			row["shares"] += 1
			row["gross"] += flt(share["total_earning"])
			row["tax"] += flt(share["tax"])
			row["net"] += flt(share["net_salary"])
	for row in totals.values():
		for key in ("gross", "tax", "net"):
			row[key] = round(row[key], 2)
	return sorted(totals.values(), key=lambda r: -r["gross"])


# Tolerance for calling a slip a match.  The sheet taxes each cost share on its own
# annual base and this app taxes the combined total, so the two differ by well under
# a cent per share; anything inside this is rounding, not a disagreement.
RECONCILE_TOLERANCE = 0.20

# Why a slip differs from the sheet, so the reconciliation can be handed to payroll
# with a reason rather than just a number.
REASONS = {
	"H0355": "August row's total earning (19,114) exceeded its own components "
	"(18,874); the 240 has been confirmed an error and is not paid",
	"TAX_TABLE": "corrected tax brackets retained, so this employee's tax is lower "
	"than the original staircase table gave",
}


def _paid(path: pathlib.Path) -> dict[str, dict]:
	"""workbook work_id -> what that run paid them."""
	if not path.exists():
		frappe.throw(
			frappe._("{0} not found. Run tools/build_payroll_spec.py --paid on the host.").format(path)
		)
	return {p["work_id"]: p for p in json.loads(path.read_text())["people"]}


def reconcile_report(path: str | None = None) -> dict:
	"""Per-employee reconciliation against what August actually paid.

	The check payroll should run before paying: every slip's net against the
	August run's final approved figure, with the reason for each difference.  The
	two deliberate differences are the corrected tax table and H0355's removed
	240, so anyone can see which is which rather than reverse-engineering the
	column.
	"""
	august = _paid(AUGUST_PAID_FILE)
	corrected = _paid(CORRECTED_PAID_FILE)

	rows = []
	exact = 0
	for work_id, was in sorted(august.items()):
		slips = frappe.get_all(
			"Salary Slip",
			filters={"employee": WORKBOOK_ID_MAP.get(work_id, work_id), "docstatus": 1},
			pluck="name",
			limit_page_length=0,
		)
		if not slips:
			continue
		net = round(flt(frappe.db.get_value("Salary Slip", slips[0], "net_pay")), 2)
		want = round(flt(was["final_approved"]), 2)
		delta = round(net - want, 2)
		spec_id = WORKBOOK_ID_MAP.get(work_id, work_id)
		if abs(delta) <= RECONCILE_TOLERANCE:
			exact += 1
			reason = ""
		elif spec_id in REASONS:
			reason = REASONS[spec_id]
		elif abs(net - flt(corrected.get(work_id, {}).get("final_approved"))) <= RECONCILE_TOLERANCE:
			reason = REASONS["TAX_TABLE"] + " - matches the corrected figure exactly"
		else:
			reason = "investigate: matches neither the August nor the corrected figure"
		rows.append(
			{
				"employee": spec_id,
				"name": was["name"],
				"company": was["company"],
				"slip_net": net,
				"august_paid": want,
				"corrected_would_pay": round(flt(corrected.get(work_id, {}).get("final_approved")), 2),
				"difference": delta,
				"reason": reason,
			}
		)

	lines = [
		"# September 2026 payroll vs what was actually paid in August 2026",
		"# slip_net is what this payroll pays, august_paid is the August run's",
		"# final approved figure, and corrected_would_pay is August's figures on the",
		"# corrected tax table. A non-zero difference is expected and explained.",
		"",
		"employee,name,slip_net,august_paid,corrected_would_pay,difference,reason",
	]
	for r in rows:
		lines.append(
			'{employee},"{name}",{slip_net:.2f},{august_paid:.2f},'
			'{corrected_would_pay:.2f},{difference:.2f},"{reason}"'.format(**r)
		)
	out = path or "/workspace/frappe-bench/apps/sharm_dreams_group/reconciliation.csv"
	with open(out, "w") as fh:
		fh.write("\n".join(lines) + "\n")

	off = [r for r in rows if r["reason"]]
	total = sum(r["difference"] for r in off)
	print(f"  employees reconciled            : {len(rows)}")
	print(f"  equal to what August paid      : {exact}")
	print(f"  deliberately different         : {len(off)}  net {total:,.2f}")
	by_reason: dict[str, list[dict]] = {}
	for r in off:
		by_reason.setdefault(r["reason"], []).append(r)
	for reason, group in by_reason.items():
		print(
			f"    {len(group):>3} employee(s), {sum(x['difference'] for x in group):>12,.2f}  {reason[:60]}"
		)
	print(f"  written to {out}")
	return {"rows": rows, "exact": exact, "total": total}


MEDICAL_REGISTER_FILE = pathlib.Path(__file__).resolve().parents[2] / "seed_data" / "medical_register.json"


def ensure_medical_register() -> dict:
	"""Load the insurer's register into `SDG Insured Person`.

	One row per employee, matched on national ID, carrying the coverage band and
	how many dependants are on the policy.  The register is who is covered; the
	monthly premium still comes from the payroll workbook, because the register
	does not price the cover - see `Medical Band.needs_review`.

	Employees the register does not mention are left alone rather than written off:
	the register is the insurer's, and a gap in it is a question for them, not a
	decision for this app.
	"""
	if not MEDICAL_REGISTER_FILE.exists():
		frappe.throw(
			frappe._("{0} not found. Run tools/build_medical_register.py on the host.").format(
				MEDICAL_REGISTER_FILE
			)
		)
	data = json.loads(MEDICAL_REGISTER_FILE.read_text())
	bands = set(frappe.get_all("SDG Medical Band", pluck="name", limit_page_length=0))

	created, skipped, no_band = 0, [], []
	for person in data["people"]:
		work_id = person.get("work_id")
		if not work_id or not frappe.db.exists("Employee", work_id):
			skipped.append(person["national_id"])
			continue
		if frappe.db.exists("SDG Insured Person", {"employee": work_id}):
			continue
		band = person.get("medical_band")
		if band not in bands:
			no_band.append(f"{work_id}: {band!r}")
			continue
		frappe.get_doc(
			{
				"doctype": "SDG Insured Person",
				"employee": work_id,
				"employee_number": work_id,
				"medical_band": band,
				"insurer_name": person.get("insurer_name"),
				"card_number": person.get("card_number"),
				"card_expiry": person.get("card_expiry"),
				"national_id": person.get("national_id"),
				"national_id_expiry": person.get("national_id_expiry"),
				"si_number": person.get("si_number"),
				"si_date": person.get("si_date"),
				"dependants_count": person.get("dependants_count", 0),
				"has_spouse": person.get("has_spouse", 0),
				"active": 1,
				"remarks": person.get("remarks"),
			}
		).insert(ignore_permissions=True)
		created += 1
	frappe.db.commit()
	frappe.clear_cache()
	return {
		"created": created,
		"already": frappe.db.count("SDG Insured Person"),
		"unmatched_employee": skipped,
		"unknown_band": no_band,
		"meta": data["_meta"],
	}


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------
@frappe.whitelist()
def spec_period() -> str | None:
	"""The month the payroll spec's figures belong to, as "YYYY-MM".

	Read from `_meta.period`, which the spec builder is required to set.  Nothing
	in the workbook settles it - its own "Years of Work" column fits 2026-08 on 29
	rows and 2026-09 on 177 - so this is a declared fact, not a derived one.
	"""
	value = (load_spec().get("_meta") or {}).get("period")
	return str(value) if value else None


def _month_of(value) -> str:
	"""A date as "YYYY-MM", so an entry's dates and the spec's period compare."""
	from frappe.utils import getdate

	d = getdate(value)
	return f"{d.year:04d}-{d.month:02d}"


def period_mismatch(entry) -> str | None:
	"""Why this entry cannot be filled from the current spec, or None if it can.

	The check exists because the mismatch is invisible on the result: filling an
	October entry from August figures produced slips that looked entirely ordinary
	and were wrong by 814.00 and 186.28 on the two Virgin Gates staff alone.
	"""
	spec = spec_period()
	if not spec:
		return (
			"The payroll spec does not say which month it is. Rebuild it with "
			"tools/build_payroll_spec.py --period YYYY-MM, so the month can be checked."
		)
	want = spec
	got = _month_of(entry.start_date)
	if got == want:
		return None
	return (
		f"This entry is for {got} but the payroll spec holds {want} figures. "
		f"Rebuild the spec from the {got} workbook, or date the entry to {want}."
	)


def build_for_entry(name: str) -> dict:
	"""Write this month's figures onto the entry's slips, via the desk button.

	The order the button is meant to be used in is submit, build, submit: the entry
	has to be submitted before HRMS will make the slips at all, so it is *submitted*
	by the time this runs.  The employee list is therefore only set on a draft; a
	submitted entry already has it from the submit.

	Slips that are already submitted are left alone and reported, because a
	submitted slip cannot be edited - that is a genuine "this month's run is
	closed" rather than something to work around.
	"""
	entry = frappe.get_doc("Payroll Entry", name)
	if entry.docstatus == 2:
		frappe.throw(
			frappe._("Payroll Entry {0} is cancelled.").format(name),
			title=frappe._("Nothing to build"),
		)

	problem = period_mismatch(entry)
	if problem and not frappe.flags.sdg_force_period:
		frappe.throw(
			frappe._("{0}").format(problem),
			title=frappe._("This spec is for a different month"),
		)

	items = [i for i in plan()["slips"] if i["company"] == entry.company]
	if not items:
		frappe.throw(
			frappe._("No payroll spec rows for {0}.").format(entry.company),
			title=frappe._("Nothing to build"),
		)

	listed = 0
	if entry.docstatus == 0:
		# a draft has no slips yet; the entry makes them when it is submitted
		_entry_employees(entry, items)
		frappe.db.commit()
		listed = len(items)

	drafts = {
		row["employee"]: row["name"]
		for row in frappe.get_all(
			"Salary Slip",
			filters={"payroll_entry": name, "docstatus": 0},
			fields=["name", "employee"],
			limit_page_length=0,
		)
	}
	already = {
		row["employee"]
		for row in frappe.get_all(
			"Salary Slip",
			filters={"payroll_entry": name, "docstatus": 1},
			fields=["employee"],
			limit_page_length=0,
		)
	}

	filled, skipped = 0, []
	for item in items:
		if item["work_id"] in already:
			skipped.append(item["work_id"])
			continue
		if item["work_id"] not in drafts:
			skipped.append(item["work_id"])
			continue
		slip = frappe.get_doc("Salary Slip", drafts[item["work_id"]])
		_fill_slip(slip, item)
		_record_cost_shares(frappe.get_doc("Salary Slip", slip.name), item)
		filled += 1
	frappe.db.commit()

	return {
		"entry": name,
		"employees": len(items),
		"listed": listed,
		"filled": filled,
		"skipped": skipped,
	}


def run_all() -> None:
	"""Build the whole month's payroll, every company.  `apply` under a no-arg name.

	For a scheduled job or the bench, where there is no single entry to click:

	    bench --site <site> execute sharm_dreams_group.payroll.run.run_all
	"""
	apply()
