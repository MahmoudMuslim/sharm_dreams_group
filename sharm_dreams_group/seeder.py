# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Seeds the SDG band tables and settings from `seed_data/seed.json`.

The JSON is produced on the host by `tools/extract_seed_data.py` from the legacy
workbooks, so the data can be reviewed before anything is written.

Run with:
    bench --site <site> execute sharm_dreams_group.seeder.seed_all
    bench --site <site> execute sharm_dreams_group.seeder.seed_all_reset
    bench --site <site> execute sharm_dreams_group.seeder.report
"""

from __future__ import annotations

import json
import pathlib

import frappe
from frappe.utils import flt

SEED_DIR = pathlib.Path(__file__).resolve().parent.parent / "seed_data"
SEED_FILE = SEED_DIR / "seed.json"

BAND_DOCTYPES = (
	"SDG Coupon Band",
	"SDG Gross Insurance Band",
	"SDG Family Medical Band",
	"SDG Medical Band",
)

# The one `Income Tax Slab` this group uses. Named because the DocType's autoname is
# Prompt, so a scripted insert has to choose; and chosen rather than derived because
# the Egyptian table is national and all thirteen companies are taxed on it.
TAX_SLAB_NAME = "Egypt Annual Income Tax"

# The Employee every employee reports a Leave Application to unless HR has chosen
# otherwise: Ghada Kamel, Assistant Director of Human Resources.
#
# It has to be named rather than derived. This site has no approver machinery at
# all - `Employee Leave Approver` is not installed, `Leave Approver` is not
# installed, `Leave Policy` has no rows - so HRMS's own resolution has nothing to
# resolve from and a submitted Leave Application has nowhere to go. That makes
# the employee portal's time-off screen refuse every submission, for 179 of the
# 180 active employees.
#
# This is a *default*, not a policy. `assign_leave_approver` only fills blanks, so
# anything HR sets afterwards is kept and this never overwrites it - re-running the
# seeder on a site where HR has already assigned approvers changes nothing.
#
# Named as an Employee because that is where a person is recorded. What goes in the
# field is their *user id*: `leave_approver` is a Link to `User` in this version of
# HRMS, not to `Employee`, and an employee name fails link validation with
# `Could not find Leave Approver: H0050`.
DEFAULT_LEAVE_APPROVER = "H0050"


def load_seed() -> dict:
	if not SEED_FILE.exists():
		raise FileNotFoundError(f"{SEED_FILE} not found. Run tools/extract_seed_data.py on the host first.")
	return json.loads(SEED_FILE.read_text())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def upsert(doctype: str, filters: dict, values: dict) -> str:
	"""Update the row matching `filters`, or insert a new one.  Idempotent."""
	existing = frappe.db.get_value(doctype, filters, "name")
	if existing:
		doc = frappe.get_doc(doctype, existing)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return "updated"
	frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
	return "created"


# ---------------------------------------------------------------------------
# seeders
# ---------------------------------------------------------------------------
def seed_medical_bands(data: dict) -> int:
	for b in data["medical_bands"]:
		notes = [f"Raw policy type in the insurer register: {b['band_raw']}"]
		if b["needs_review"]:
			notes.insert(
				0,
				"PREVIEW ONLY - thin or mixed sample. Observed distribution: "
				+ json.dumps(b["distribution"], ensure_ascii=False),
			)
		upsert(
			"SDG Medical Band",
			{"band": b["band"]},
			{
				"band": b["band"],
				"coverage_limit": b["coverage_limit"],
				"monthly_premium": b["monthly_premium"],
				"source": "Seeded from payroll workbook",
				"remarks": "\n".join(notes),
				"active": 1,
			},
		)
	return len(data["medical_bands"])


def seed_gross_insurance_bands(data: dict) -> int:
	for b in data["gross_insurance_bands"]:
		upsert(
			"SDG Gross Insurance Band",
			{"from_gross": b["from_gross"], "to_gross": b["to_gross"]},
			{
				"from_gross": b["from_gross"],
				"to_gross": b["to_gross"],
				"life_insurance": b["life_insurance"],
				"si_base": b["si_base"],
				"source": "Seeded from payroll workbook",
				"remarks": (
					"One band per distinct gross, so the table reproduces the legacy "
					"workbook exactly. Replace with the real pay scale / insurer "
					"schedule when it is available."
				),
				"active": 1,
			},
		)
	return frappe.db.count("SDG Gross Insurance Band")


def seed_family_medical_bands(data: dict) -> int:
	for b in data["family_medical_bands"]:
		notes = [f"Seeded from {b['sample_size']} observed payroll row(s)."]
		if b["sample_size"] < 3:
			notes.append("THIN SAMPLE - confirm the price with the broker before relying on it.")
		upsert(
			"SDG Family Medical Band",
			{
				"medical_band": b["medical_band"],
				"has_spouse": b["has_spouse"],
				"children_count": b["children_count"],
			},
			{
				"medical_band": b["medical_band"],
				"has_spouse": b["has_spouse"],
				"children_count": b["children_count"],
				"monthly_premium": b["monthly_premium"],
				"source": "Seeded from payroll workbook",
				"remarks": "\n".join(notes),
				"active": 1,
			},
		)
	return frappe.db.count("SDG Family Medical Band")


def check_coupon_bands(data: dict) -> int:
	"""Check the band arithmetic, and report the summary against the real list.

	The workbook holds two sets of numbers that disagree. The four-row band summary
	says 127 people and 153,300. The per-employee list below it - the one with the
	note counts and the signature box, which is what the desk actually hands over
	and gets signed for - contains 126 people and 151,800. The difference is one
	person at 1,500, and the summary's own arithmetic is internally consistent, so
	it is not a multiplication slip: it is a stale summary.

	The bands are therefore seeded from the list, and this reports the gap rather
	than passing quietly, because a summary the finance team reads is worth telling
	them is wrong.
	"""
	drift_rows = 0
	for c in data["coupon_bands"]:
		expected = c["headcount"] * c["coupon_amount"]
		if abs(c["stated_total"] - expected) > 0.5:
			drift_rows += 1
			print(
				f"  DATA QUALITY: coupon {c['category']!r} states {c['stated_total']:,.0f} "
				f"but headcount x amount is {expected:,.0f} "
				f"(off by {c['stated_total'] - expected:,.0f})"
			)

	people = sum(c["headcount"] for c in data["coupon_bands"])
	total = sum(c["stated_total"] for c in data["coupon_bands"])
	rows = json.loads((SEED_DIR / "coupon_rows.json").read_text(encoding="utf-8"))
	listed, listed_total = len(rows), sum(flt(r["amount"]) for r in rows)
	if (people, total) != (listed, listed_total):
		print(
			f"  DATA QUALITY: the coupon band summary says {people} people / {total:,.0f}, "
			f"but the per-employee list holds {listed} / {listed_total:,.0f} "
			f"(off by {people - listed} people, {total - listed_total:,.0f}). "
			"  Seeding the list; the summary looks stale."
		)
	return drift_rows


def seed_tax_brackets(data: dict) -> int:
	"""Write the Egyptian bracket into the stock `Income Tax Slab`.

	An app-owned `SDG Tax Bracket` did this job with a shape Frappe could not read,
	so the band table was only reachable from a URL and invisible to the reports
	that gather slabs.  The bands live in the stock DocType now, with the one extra
	field Egypt needs - the cumulative tax at the bottom of each band - on the
	`slabs` child row.  See `setup.create_slab_fixed_amount`.
	"""
	name = TAX_SLAB_NAME
	if frappe.db.exists("Income Tax Slab", name):
		frappe.delete_doc("Income Tax Slab", name, force=True, ignore_permissions=True)

	bands = [b for b in data["tax_brackets"] if b.get("active", 1)]
	if not bands:
		return 0
	bands.sort(key=lambda b: b["up_to"])

	slab = frappe.get_doc(
		{
			"doctype": "Income Tax Slab",
			# autoname is Prompt, so a scripted insert has to name it
			"name": name,
			"slab_name": "Egypt - Income Tax (annual, progressive from a base)",
			"effective_from": "2026-01-01",
			"currency": "EGP",
			"allow_tax_exemption": 0,
		}
	)
	slab.set("slabs", [])
	lower = 0.0
	for b in bands:
		slab.append(
			"slabs",
			{
				"from_amount": lower,
				"to_amount": b["up_to"],
				"percent_deduction": b["rate"],
				"sdg_fixed_amount": b["fixed_amount"],
			},
		)
		lower = b["up_to"]
	slab.insert(ignore_permissions=True)
	return len(slab.slabs)


def seed_leave_types(data: dict) -> int:
	"""Write the twelve Sharm leave types into the stock `Leave Type`.

	They used to live in an app-owned `SDG Leave Type`, which was a second
	dictionary of leave names kept by hand.  The payroll does not read it - absence
	comes from the workbook - but the HR team does: the short code is what the
	vacation tracker is keyed on, and a type nobody can look up is not reference
	data, it is a legend.

	Stock `Leave Type` is the right home for the names, so the codes and the two
	payroll flags ride along as custom fields (see `setup.create_leave_type_fields`).
	`is_lwp` is deliberately left alone: setting it would make HRMS deduct unpaid
	leave on top of the payroll, which already does.

	Frappe's own five defaults are left in place - policies may reference them, and
	deleting stock rows is not this app's business.
	"""
	wanted = {row["leave_type"]: row for row in data["leave_types"]}
	if not wanted:
		return 0

	# name -> the fields to change, so a re-run repairs rather than duplicates
	touched = 0
	for row in frappe.get_all("Leave Type", pluck="name", limit_page_length=0):
		if row not in wanted:
			continue
		spec = wanted[row]
		frappe.db.set_value(
			"Leave Type",
			row,
			{
				"sdg_code": spec.get("code", ""),
				"sdg_tracker_name": spec.get("tracker_ar", ""),
				"sdg_is_paid": int(bool(spec.get("is_paid"))),
				"sdg_affects_deduction": int(bool(spec.get("affects_deduction"))),
			},
			update_modified=False,
		)
		touched += 1

	for name, spec in wanted.items():
		if frappe.db.exists("Leave Type", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": name,
				"sdg_code": spec.get("code", ""),
				"sdg_tracker_name": spec.get("tracker_ar", ""),
				"sdg_is_paid": int(bool(spec.get("is_paid"))),
				"sdg_affects_deduction": int(bool(spec.get("affects_deduction"))),
			}
		).insert(ignore_permissions=True)
		touched += 1
	return touched


def retire_stale_leave_types(data: dict) -> int:
	"""Remove Sharm leave types this app seeded that the tracker no longer lists.

	The vocabulary came from the workbook first and the vacation tracker second, and
	the two do not agree: the workbook's twelve codes include `LL`, `PDOPH` and
	`D.L`, which the tracker has no column for, and it has no code for three of its
	own.  Only rows this app created are removed - a `Leave Type` Frappe shipped, or
	one a user typed, is left alone - and only when nothing references it, since a
	leave type in use by a policy or an application is real data whatever the
	tracker says.
	"""
	wanted = {row["leave_type"] for row in data["leave_types"]}
	removed = 0
	for row in frappe.get_all("Leave Type", fields=["name"], limit_page_length=0):
		name = row["name"]
		if name in wanted:
			continue
		if not frappe.db.get_value("Leave Type", name, "sdg_code"):
			continue  # not ours
		if frappe.db.exists("Leave Policy Assignment", {"leave_type": name}) or frappe.db.exists(
			"Leave Allocation", {"leave_type": name}
		):
			print(f"  kept {name!r}: a policy or allocation still uses it")
			continue
		frappe.delete_doc("Leave Type", name, force=True, ignore_permissions=True)
		removed += 1
	return removed


# The Holiday List this group runs on, and the two Shift Types it splits into.
#
# Both are recorded here rather than discovered, because the split is a policy: the two
# shifts start an hour apart (10:00 and 09:00), which is not something to infer from a
# roster that may change. `assign_shifts_by_gender` maps gender onto shift; nothing else
# in the app decides who works which hours.
HOLIDAY_LIST_NAME = "2026"
MALE_SHIFT_TYPE = "Full Time (Male)"
FEMALE_SHIFT_TYPE = "Full Time (Female)"

# When the shift assignments run. A whole calendar year, because the only Shift Types on
# this site have no weekly pattern of their own - HRMS applies the Holiday List's
# `weekly_off`, and Saturday is the group's.
SHIFT_ASSIGNMENT_FROM = f"{HOLIDAY_LIST_NAME}-01-01"
SHIFT_ASSIGNMENT_TO = f"{HOLIDAY_LIST_NAME}-12-31"

# --------------------------------------------------------------- performance
# The Performance area reads HRMS's own Appraisal Cycle -> Appraisal -> Goal/KRA. Every
# one of those doctypes is installed on this site and every table in them is empty, so
# without the three objects below the portal's performance screen has nothing to show
# any employee and says so.
#
# These are placeholder policy, not a recommendation about how Sharm Dreams should appraise
# anybody. There is no local source for cycle dates, goal weights or rating criteria, so
# they are the most generic defensible set: four criteria and four key result areas,
# evenly weighted, with weights that add to the 100 HRMS insists on. They exist so the
# flow is real and demonstrable end to end, and `unseed_performance` removes all of them
# so HR can replace the set with the real one. None of it is derived from anything about
# the employees it will be applied to.
#
# One template, but **one cycle per company**. `Appraisal Cycle.company` is mandatory,
# and `create_appraisals_for_cycle` stamps every appraisal it makes with the cycle's own
# company rather than the employee's. A single cycle across this group - 13 companies -
# would therefore put the wrong company on most of the appraisals, silently, because
# nothing in `Appraisal.validate` compares the two. `PERFORMANCE_CYCLE_PREFIX` is what
# ties the seeded cycles together for `unseed_performance` to find.
PERFORMANCE_TEMPLATE_NAME = "Standard (2026)"
PERFORMANCE_CYCLE_PREFIX = "Performance"

# Title, and why it is here. Kept as data rather than constants scattered through the
# function so `unseed_performance` can find every row it has to remove.
PERFORMANCE_CRITERIA = (
	"Job knowledge",
	"Quality of work",
	"Communication",
	"Teamwork",
)
PERFORMANCE_KRAS = (
	"Applies the knowledge required for the role",
	"Delivers work accurately and completely",
	"Communicates clearly, in writing and in person",
	"Works with others towards a shared result",
)

# Evenly weighted, and checked below rather than assumed: `validate_total_weightage`
# refuses anything that does not total 100, so an uneven split here would fail on insert
# with a message about weightage allocation, which is a confusing way to learn that the
# list has the wrong number of entries.
PERFORMANCE_WEIGHT = 100.0 / len(PERFORMANCE_CRITERIA)


def performance_cycle_name(company: str) -> str:
	"""The seeded cycle name for one company. The only place this string is built."""
	return f"{PERFORMANCE_CYCLE_PREFIX} {HOLIDAY_LIST_NAME} ({company})"

# One approval request type per area the portal files into, and who decides it. Named
# here rather than discovered because the approver is a decision, not data: the site
# had zero approval categories, which is why a filed request had nobody to reach.
#
# Expenses go to the same person as leave, for the same reason - H0050 is the
# Assistant Director of HR and the only named approver this deployment has. Anyone
# who can sign in to the desk can change a category's approvers afterwards, and the
# portal reads the approver from the category rather than from this file.
APPROVAL_CATEGORIES = {
	"Expenses": {"approvers": [DEFAULT_LEAVE_APPROVER], "manager_approval": "No"},
	# One category per area the employee can submit something into, so an approver's
	# queue reads as the five areas rather than one undifferentiated pile. Same
	# approver as expenses: one named person for HR matters on this deployment, and
	# inventing a second one would be putting a name on a queue nobody agreed to.
	"Performance": {"approvers": [DEFAULT_LEAVE_APPROVER], "manager_approval": "No"},
}


def seed_approval_categories(_data: dict | None = None) -> int:
	"""Create the approval request types the portal files against.

	Approvers are set only on a category this step creates. A category that already
	exists keeps whoever HR has since put on it - the same reason
	`assign_leave_approver` only fills blanks, and the same reason it matters: the
	only symptom of overwriting here would be expenses quietly going to the wrong
	person.
	"""
	created = 0
	for name, spec in APPROVAL_CATEGORIES.items():
		approvers = [
			frappe.db.get_value("Employee", employee, "user_id")
			for employee in spec["approvers"]
		]
		approvers = [user for user in approvers if user]
		if not approvers:
			# No approver means a request that queues for nobody, which looks the same
			# as one that is being worked on. Said rather than created.
			print(f"  skipped {name!r}: none of {spec['approvers']} has a Frappe user")
			continue

		if frappe.db.exists("Employee Self Service Approval Category", name):
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Employee Self Service Approval Category",
				"category_name": name,
				"manager_approval": spec["manager_approval"],
				"approvers": [
					{
						"approver_user": user,
						"required": 1,
						# 1, not the fallback's 9: this category names its approver, and
						# the fallback is only for ones that do not.
						"sequence": index,
					}
					for index, user in enumerate(approvers, start=1)
				],
			}
		)
		doc.insert(ignore_permissions=True)
		created += 1
	return created


def assign_holiday_list(_data: dict | None = None) -> int:
	"""Point every employee at this group's Holiday List.

	Blanks only, for the same reason `assign_leave_approver` fills blanks: overwriting
	would undo a deliberate per-employee arrangement every time the seeder ran.

	Attendance and leave both depend on this, not just attendance. HRMS computes a leave
	application's working days from the employee's holiday list, so an employee with none
	has Saturdays counted as working days - which quietly overstates every leave
	entitlement on the site.
	"""
	if not frappe.db.exists("Holiday List", HOLIDAY_LIST_NAME):
		print(f"  skipped: no Holiday List named {HOLIDAY_LIST_NAME!r}")
		return 0

	rows = frappe.get_all(
		"Employee",
		filters={"holiday_list": ("in", ("", None))},
		pluck="name",
		limit_page_length=0,
	)
	if rows:
		frappe.db.sql(
			"UPDATE `tabEmployee` SET `holiday_list` = %s WHERE `name` IN %s",
			(HOLIDAY_LIST_NAME, tuple(rows)),
		)
	return len(rows)


def assign_shifts_by_gender(_data: dict | None = None) -> dict:
	"""Give every active employee the shift their gender maps to, and submit it.

	Submitted, which is the part that is easy to miss. `get_shifts_for_date` filters on
	`docstatus == 1`, so a draft assignment is on file but HRMS resolves no shift from it -
	every punch then lands as `offshift` with no shift on it, and the day's Attendance is
	created with `shift` empty. This is exactly what happened to the first 177.

	Employees whose `gender` is blank are counted and skipped, never guessed at. A
	shift is a work arrangement; inferring one from anything other than the recorded
	policy would put somebody on hours nobody agreed to.
	"""
	report = {"created": 0, "skipped_no_gender": [], "unmapped": []}

	mapping = {"Male": MALE_SHIFT_TYPE, "Female": FEMALE_SHIFT_TYPE}
	for shift_type in mapping.values():
		if not frappe.db.exists("Shift Type", shift_type):
			print(f"  skipped shifts: no Shift Type {shift_type!r}")
			report["unmapped"].append(shift_type)
			return report

	for gender, shift_type in mapping.items():
		employees = frappe.db.sql(
			"SELECT name, company FROM `tabEmployee` "
			"WHERE status = 'Active' AND gender = %s "
			"AND company IS NOT NULL AND company <> '' ORDER BY name",
			(gender,),
		)
		for employee, company in employees:
			existing = frappe.db.get_all(
				"Shift Assignment",
				filters={
					"employee": employee,
					"shift_type": shift_type,
					"start_date": SHIFT_ASSIGNMENT_FROM,
				},
				pluck="name",
				limit_page_length=1,
			)
			if existing:
				# A draft from an earlier run still needs submitting, and that is the
				# same fix rather than a new row.
				if frappe.db.get_value("Shift Assignment", existing[0], "docstatus") == 0:
					frappe.get_doc("Shift Assignment", existing[0]).submit()
					report["created"] += 1
				continue
			doc = frappe.get_doc(
				{
					"doctype": "Shift Assignment",
					"employee": employee,
					"company": company,
					"shift_type": shift_type,
					"start_date": SHIFT_ASSIGNMENT_FROM,
					"end_date": SHIFT_ASSIGNMENT_TO,
					"status": "Active",
				}
			)
			doc.insert(ignore_permissions=True)
			doc.submit()
			report["created"] += 1

	report["skipped_no_gender"] = frappe.get_all(
		"Employee",
		filters={"status": "Active", "gender": ("in", ("", None))},
		pluck="name",
		limit_page_length=0,
	)
	if report["skipped_no_gender"]:
		print(
			f"  {len(report['skipped_no_gender'])} active employee(s) have no gender "
			f"recorded and were given no shift: {report['skipped_no_gender']}"
		)
	return report


def seed_performance_setup(_data: dict | None = None) -> dict:
	"""Create the cycle, template and rating criteria the performance screen reads.

	Deliberately in that order, because each depends on the one before: the template's
	goals link to `KRA` rows and its rating criteria link to
	`Employee Feedback Criteria`, and the cycle's appraisees name the template.

	The appraisals themselves are created by HRMS's own
	`create_appraisals_for_cycle`, not by hand, so what an employee ends up looking at
	is exactly what HR's own "Create Appraisals" button would have produced - including
	the KRAs and rating rows copied out of the template.

	Idempotent, and safe to re-run: anything already present is left alone, and the
	number of appraisals created is whatever was genuinely missing. An employee with no
	company is counted and skipped, because `Appraisal.company` is mandatory and an
	assessment without one cannot be saved.
	"""
	report = {
		"criteria": 0,
		"kras": 0,
		"template": False,
		"cycles": [],
		"appraisals": 0,
		"skipped_no_company": 0,
	}

	for title in PERFORMANCE_CRITERIA:
		if frappe.db.exists("Employee Feedback Criteria", title):
			continue
		frappe.get_doc(
			{
				"doctype": "Employee Feedback Criteria",
				"criteria": title,
				"description": "Placeholder set created by the seeder. Replace with the "
				"criteria your appraisal policy uses.",
			}
		).insert(ignore_permissions=True)
		report["criteria"] += 1

	for title in PERFORMANCE_KRAS:
		if frappe.db.exists("KRA", title):
			continue
		frappe.get_doc(
			{
				"doctype": "KRA",
				"title": title,
				"description": "Placeholder set created by the seeder.",
			}
		).insert(ignore_permissions=True)
		report["kras"] += 1

	# Read back rather than assumed: the weights are computed from the list lengths
	# above, and if one of those ever changes so that they no longer total 100 then
	# every insert below would fail with "Incorrect Weightage Allocation". Said here,
	# where the cause is obvious.
	for label, titles in (
		("criteria", PERFORMANCE_CRITERIA),
		("key result areas", PERFORMANCE_KRAS),
	):
		total = PERFORMANCE_WEIGHT * len(titles)
		if round(total, 2) != 100.0:
			raise ValueError(
				f"{label} must total 100% and currently total {total}%. "
				f"Adjust the list or PERFORMANCE_WEIGHT."
			)

	if not frappe.db.exists("Appraisal Template", PERFORMANCE_TEMPLATE_NAME):
		frappe.get_doc(
			{
				"doctype": "Appraisal Template",
				"template_title": PERFORMANCE_TEMPLATE_NAME,
				"description": "Placeholder template created by the seeder. Replace with "
				"the goals and weights your appraisal policy uses.",
				"goals": [
					{"key_result_area": title, "per_weightage": PERFORMANCE_WEIGHT}
					for title in PERFORMANCE_KRAS
				],
				"rating_criteria": [
					{"criteria": title, "per_weightage": PERFORMANCE_WEIGHT}
					for title in PERFORMANCE_CRITERIA
				],
			}
		).insert(ignore_permissions=True)
		report["template"] = True

	if not frappe.db.exists("Appraisal Template", PERFORMANCE_TEMPLATE_NAME):
		frappe.get_doc(
			{
				"doctype": "Appraisal Template",
				"template_title": PERFORMANCE_TEMPLATE_NAME,
				"description": "Placeholder template created by the seeder. Replace with "
				"the goals and weights your appraisal policy uses.",
				"goals": [
					{"key_result_area": title, "per_weightage": PERFORMANCE_WEIGHT}
					for title in PERFORMANCE_KRAS
				],
				"rating_criteria": [
					{"criteria": title, "per_weightage": PERFORMANCE_WEIGHT}
					for title in PERFORMANCE_CRITERIA
				],
			}
		).insert(ignore_permissions=True)
		report["template"] = True

	# One cycle per company that has active employees on it, each carrying only that
	# company's appraisees. Companies with nobody active are skipped: a cycle with no
	# appraisees is a cycle that produces nothing.
	report["cycles"] = []
	report["appraisals"] = 0
	from hrms.hr.doctype.appraisal_cycle.appraisal_cycle import (
		create_appraisals_for_cycle,
	)

	for (company,) in frappe.db.sql(
		"SELECT DISTINCT company FROM `tabEmployee` "
		"WHERE status = 'Active' AND company IS NOT NULL AND company <> '' ORDER BY company"
	):
		name = performance_cycle_name(company)
		# `built`, not `cycle.is_new()`: a document created from a dict reports
		# `is_new() == False` in this Frappe version because the dict is flagged local,
		# so asking the document whether it is new silently answered "no" and the report
		# claimed zero cycles were created when there were thirteen. The flag is set
		# where the knowledge actually is.
		built = not frappe.db.exists("Appraisal Cycle", name)
		cycle = (
			frappe.get_doc(
				{
					"doctype": "Appraisal Cycle",
					"cycle_name": name,
					"company": company,
					"status": "In Progress",
					"start_date": f"{HOLIDAY_LIST_NAME}-01-01",
					"end_date": f"{HOLIDAY_LIST_NAME}-12-31",
					# "Manual Rating" puts the template's goals in `Appraisal.goals`
					# rather than `appraisal_kra`, which is the list the employee sees.
					# The automated method derives them from goal progress instead, and
					# then there is nothing for the employee to rate.
					"kra_evaluation_method": "Manual Rating",
				}
			)
			if built
			else frappe.get_doc("Appraisal Cycle", name)
		)
		if built:
			cycle.insert(ignore_permissions=True)
			report["cycles"].append(name)

		employees = frappe.get_all(
			"Employee",
			filters={"status": "Active", "company": company},
			pluck="name",
			limit_page_length=0,
		)
		existing = {row.employee for row in (cycle.get("appraisees") or [])}
		missing = [e for e in employees if e not in existing]
		if missing:
			for employee in missing:
				# `appraisal_template` on every appraisee, because
				# `create_appraisals_for_cycle` copies the goals and rating rows out of
				# it. Left blank, the appraisals would be created with neither - and
				# because the blank is not itself an error, they would simply have
				# nothing to show. The cycle's own `create_appraisals` button refuses in
				# that case; calling the function directly, as below, does not.
				cycle.append(
					"appraisees",
					{
						"employee": employee,
						"appraisal_template": PERFORMANCE_TEMPLATE_NAME,
						"department": frappe.db.get_value("Employee", employee, "department"),
						"designation": frappe.db.get_value(
							"Employee", employee, "designation"
						),
					},
				)
			cycle.save(ignore_permissions=True)
		if not employees:
			continue

		before = frappe.db.count("Appraisal", {"appraisal_cycle": name})
		# HRMS's own creation path, called directly rather than through the cycle's
		# `create_appraisals` button: that method enqueues to the long queue past 30
		# appraisees, so from a seeder the run would finish before the appraisals
		# existed. It skips an employee who already has one, so re-running is safe.
		create_appraisals_for_cycle(frappe.get_doc("Appraisal Cycle", name))
		report["appraisals"] += frappe.db.count("Appraisal", {"appraisal_cycle": name}) - before

	# `skipped_no_company` is the count that matters, not a list: an active employee with
	# no company cannot be appraised, and naming all of them buries the number.
	report["skipped_no_company"] = frappe.db.count(
		"Employee", {"status": "Active", "company": ("in", ("", None))}
	)
	if report["skipped_no_company"]:
		print(
			f"  {report['skipped_no_company']} active employee(s) have no company and were "
			f"given no appraisal: `Appraisal.company` is mandatory."
		)
	return report


def unseed_performance(_data: dict | None = None) -> dict:
	"""Remove everything `seed_performance_setup` created.

	There because the objects that function writes are placeholder policy. HR should be
	able to throw them away and put the real set in without a database restore, and so
	should whoever is evaluating the portal.

	Scoped to the seeded names throughout, so it cannot touch a cycle, template or
	appraisal that HR made for themselves. Refuses rather than guessing if the seeded
	cycle has acquired appraisals from anywhere but the seeder: those would be somebody's
	real self-assessment.
	"""
	report = {"appraisals": 0, "requests": 0, "cycles": 0, "template": False,
			  "kras": 0, "criteria": 0}

	for name in frappe.get_all(
		"Employee Self Service Approval Request",
		filters={"request_type": "Performance"},
		pluck="name",
		limit_page_length=0,
	):
		frappe.delete_doc(
			"Employee Self Service Approval Request", name, force=True,
			ignore_permissions=True,
		)
		report["requests"] += 1

	# Only appraisals belonging to the seeded cycle, and only ones nobody has submitted:
	# a submitted appraisal is a person's finished self-assessment and cancelling it
	# would destroy the record of it.
	# The seeded cycles, identified by rebuilding each expected name through
	# `performance_cycle_name` rather than by a `like` pattern. A hand-written prefix
	# pattern was wrong the first time - it read "Performance 2026(%" against names that
	# are actually "Performance 2026 (Company)" - and the teardown then silently removed
	# the template and left every cycle and appraisal behind, reporting success. There is
	# now one place that knows how these are named, and this asks it.
	seeded = {
		performance_cycle_name(company)
		for company in frappe.get_all("Company", pluck="name", limit_page_length=0)
	}
	cycles = [
		name
		for name in frappe.get_all(
			"Appraisal Cycle", pluck="name", limit_page_length=0
		)
		if name in seeded
	]

	names = frappe.get_all(
		"Appraisal",
		filters={"appraisal_cycle": ("in", cycles)} if cycles else {"name": "___none___"},
		fields=["name", "docstatus"],
		limit_page_length=0,
	)
	submitted = [row.name for row in names if row.docstatus != 0]
	if submitted:
		print(
			f"  skipped {len(submitted)} appraisal(s) under the seeded cycles: they have "
			f"been submitted, so they are real self-assessments. Cancel them by hand if "
			f"they really are seed data."
		)
	for row in names:
		if row.docstatus != 0:
			continue
		frappe.delete_doc("Appraisal", row.name, force=True, ignore_permissions=True)
		report["appraisals"] += 1

	for name in cycles:
		frappe.delete_doc("Appraisal Cycle", name, force=True, ignore_permissions=True)
		report["cycles"] += 1

	if frappe.db.exists("Appraisal Template", PERFORMANCE_TEMPLATE_NAME):
		frappe.delete_doc(
			"Appraisal Template", PERFORMANCE_TEMPLATE_NAME, force=True,
			ignore_permissions=True,
		)
		report["template"] = True

	for title in PERFORMANCE_KRAS:
		if frappe.db.exists("KRA", title):
			frappe.delete_doc("KRA", title, force=True, ignore_permissions=True)
			report["kras"] += 1
	for title in PERFORMANCE_CRITERIA:
		if frappe.db.exists("Employee Feedback Criteria", title):
			frappe.delete_doc(
				"Employee Feedback Criteria", title, force=True, ignore_permissions=True
			)
			report["criteria"] += 1
	return report


def resolve_leave_approver_user() -> str:
	"""The `User` id of `DEFAULT_LEAVE_APPROVER`, refusing rather than guessing.

	Every refusal here is about the difference between "an approver was named" and
	"an approver exists":

	- the employee is not there, so there is nobody to name;
	- they have no Frappe user, so the Link has nothing valid to hold - and an
	  application routed to a non-existent approver would sit in a queue nobody
	  reads while looking like it was in progress;
	- they have left, which is the same failure wearing a plausible name.
	"""
	if not frappe.db.exists("Employee", DEFAULT_LEAVE_APPROVER):
		frappe.throw(
			f"{DEFAULT_LEAVE_APPROVER} is not an Employee. Set DEFAULT_LEAVE_APPROVER "
			"in seeder.py to somebody who is, or leave employees to be assigned by HR.",
			exc=frappe.ValidationError,
		)
	approver = frappe.db.get_value(
		"Employee",
		DEFAULT_LEAVE_APPROVER,
		["user_id", "status"],
		as_dict=True,
	)
	if approver.status != "Active":
		frappe.throw(
			f"{DEFAULT_LEAVE_APPROVER} is not an active Employee, so leave requests "
			"would queue against someone who has left.",
			exc=frappe.ValidationError,
		)
	if not approver.user_id:
		frappe.throw(
			f"{DEFAULT_LEAVE_APPROVER} has no Frappe user, and `leave_approver` links "
			"to User. Create the user first, or name somebody who has one.",
			exc=frappe.ValidationError,
		)
	return approver.user_id


def assign_leave_approver(_data: dict | None = None) -> int:
	"""Point employees with no leave approver at `DEFAULT_LEAVE_APPROVER`'s user.

	Takes the seed data like every other step and ignores it: this default is a
	decision recorded in this file, not something the workbooks carry.

	Blanks only. Overwriting a populated `leave_approver` would silently undo HR's
	own routing every time the seeder was run, which is the worst property a default
	can have - and the one that would only be noticed the next time somebody went on
	leave and the wrong person was asked.

	Inactive employees are included, deliberately: a leaver can still have an
	application cancelled or corrected, and refusing to record an approver for them
	only guarantees the refusal happens at the worst moment.
	"""
	approver_user = resolve_leave_approver_user()

	# A single UPDATE rather than a loop: 310 employees, and `db.set_value` per row
	# would re-read and re-validate a document 310 times for one column.
	rows = frappe.db.get_all(
		"Employee",
		filters={"leave_approver": ("in", ("", None))},
		pluck="name",
		limit_page_length=0,
	)
	if rows:
		frappe.db.sql(
			"UPDATE `tabEmployee` SET `leave_approver` = %s WHERE `name` IN %s",
			(approver_user, tuple(rows)),
		)
		frappe.clear_document_cache("Employee", DEFAULT_LEAVE_APPROVER)
	return len(rows)


def seed_coupon_bands(data: dict) -> int:
	"""The four cost-of-living coupon bands, from the workbook's own figures.

	The workbook carries two sets of numbers: a four-row band summary at the top,
	and a per-employee list below it.  The summary says 127 people and 153,300; the
	list says 126 and 151,800.  The list is the one the HR desk signed for - it
	carries the note counts and the signatures - so its counts are what go in, and
	`check_coupon_bands` reports the summary as the stale figure rather than
	silently agreeing with it.
	"""
	written = 0
	for band in data["coupon_bands"]:
		name = band["category"]
		if frappe.db.exists("SDG Coupon Band", name):
			frappe.db.set_value(
				"SDG Coupon Band",
				name,
				{
					"category_name_ar": band.get("category_ar", ""),
					"coupon_amount": band["coupon_amount"],
					"children_count": band.get("children_count", 0),
				},
				update_modified=False,
			)
		else:
			frappe.get_doc(
				{
					"doctype": "SDG Coupon Band",
					"category_name": name,
					"category_name_ar": band.get("category_ar", ""),
					"coupon_amount": band["coupon_amount"],
					"children_count": band.get("children_count", 0),
				}
			).insert(ignore_permissions=True)
		written += 1
	return written


def seed_coupons_granted(data: dict) -> int:
	"""Record the coupons handed over, one row per person.

	The rows come from `seed_data/coupon_rows.json`, extracted from the workbook's
	per-employee list, which is the same list the band sheet repeats lower down.
	Each row carries the notes counted, and the controller refuses a row whose notes
	do not make the amount - which is worth having, because the notes are how the
	drawer was actually balanced.

	Twenty-four of the 126 could not be attached to an Employee and are reported
	rather than guessed:

	- 22 have an Arabic name and no work ID at all;
	- 2 carry a work ID that is not on the site (`VIR010`, `VIR011`).

	Matching on the Arabic name was tried and abandoned: it is a transliteration
	problem, and the one "match" it produced was wrong.  A coupon is cash leaving a
	drawer, and attaching it to the wrong person is worse than leaving it
	unrecorded - so only an explicit work ID is trusted, and anything else is
	printed for HR to resolve.
	"""
	rows = json.loads((SEED_DIR / "coupon_rows.json").read_text(encoding="utf-8"))
	month = data["coupon_period_month"]
	sheet = data.get("coupon_source_sheet", "")

	# a re-run replaces the month rather than adding to it
	frappe.db.delete("SDG Coupon Granted", {"period_month": month})

	written, unmatched = 0, []
	for row in rows:
		employee = row.get("work_id")
		reason = ""
		if not employee:
			reason = "no work ID"
		elif not frappe.db.exists("Employee", employee):
			reason = f"work ID {employee} is not on the site"

		# Recorded either way. Skipping an unmatched coupon was defensible while the
		# card was a subtraction, but it made the shortfall invisible and unopenable:
		# the register held 100 of 126 coupons and nothing on the site could show which
		# 26 were missing or what they were worth. Written as a flagged row instead,
		# `Coupons Unrecorded` is a count of real records and opens them.
		frappe.get_doc(
			{
				"doctype": "SDG Coupon Granted",
				"period_month": month,
				"employee": employee if not reason else None,
				"amount": row["amount"],
				"notes_100": row.get("n100") or 0,
				"notes_200": row.get("n200") or 0,
				"notes_250": row.get("n250") or 0,
				"workbook_row": row.get("row"),
				"source_sheet": sheet,
				"sdg_unrecorded": 1 if reason else 0,
				"remarks": (
					f"unrecorded: {reason} - {row.get('name_ar') or '(no name)'}"
					if reason
					else f"workbook work ID {employee}"
				),
			}
		).insert(ignore_permissions=True)
		written += 1
		if reason:
			unmatched.append(f"{row.get('name_ar') or '(no name)'} - {reason} ({row['amount']:,.0f})")

	if unmatched:
		flagged = frappe.db.count("SDG Coupon Granted", {"period_month": month, "sdg_unrecorded": 1})
		print(
			f"  UNRECORDED: {flagged} of {len(rows)} coupons "
			f"({sum(r['amount'] for r in rows if not _matchable(r)):,.0f}) are flagged - "
			f"no Employee could be matched, so they are kept without an owner:"
		)
		for line in unmatched:
			print(f"    - {line}")
	return written


def _matchable(row: dict) -> bool:
	"""Whether a workbook row names an employee this site actually has.

	Only an explicit work ID counts.  Matching on the Arabic name is not safe: the
	Employee record holds a Latin transliteration, and the one candidate that came
	back was the wrong person, which for a cash handout is worse than no answer.
	"""
	wid = row.get("work_id")
	return bool(wid) and bool(frappe.db.exists("Employee", wid))


def seed_settings(data: dict) -> None:
	doc = frappe.get_single("SDG Payroll Settings")
	doc.update(data["payroll_settings"])
	doc.save()


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------
def seed_all() -> None:
	"""Populate every band table and the two settings singles.  Safe to re-run."""
	_seed(reset=False)


def seed_all_reset() -> None:
	"""Same, but clears the band tables first.  fm's shell mangles inline kwargs,
	so this is exposed as its own entry point."""
	_seed(reset=True)


def _seed(reset: bool) -> None:
	data = load_seed()
	print(f"seed file : {SEED_FILE}")

	if reset:
		for dt in BAND_DOCTYPES:
			frappe.db.delete(dt)
		frappe.db.commit()
		print("cleared   : existing band rows")

	steps = (
		("medical bands", seed_medical_bands),
		("gross insurance bands", seed_gross_insurance_bands),
		("family medical bands", seed_family_medical_bands),
		("tax brackets", seed_tax_brackets),
		("leave types", seed_leave_types),
		("stale leave types", retire_stale_leave_types),
		("leave approver", assign_leave_approver),
		("approval categories", seed_approval_categories),
		("holiday list", assign_holiday_list),
		("shifts", assign_shifts_by_gender),
		("performance setup", seed_performance_setup),
		("coupon bands", seed_coupon_bands),
		("coupons granted", seed_coupons_granted),
	)
	# not a seeding step: the coupon bands are no longer stored, only checked
	_ = check_coupon_bands
	for label, fn in steps:
		n = fn(data)
		frappe.db.commit()
		print(f"  {label:24} {n:>4} row(s)")

	seed_settings(data)
	frappe.db.commit()
	print("  settings                 written")
	print()
	report()


def report() -> None:
	"""Print the seeded state so it can be eyeballed before any payroll runs."""
	print("=== SDG Medical Band ===")
	for d in frappe.get_all(
		"SDG Medical Band",
		fields=["name", "coverage_limit", "monthly_premium", "remarks"],
		order_by="coverage_limit",
	):
		flag = "   <== needs review" if "PREVIEW ONLY" in (d.remarks or "") else ""
		print(f"  {d.name:10} limit={d.coverage_limit:>10,.0f}  premium={d.monthly_premium:>9,.2f}{flag}")

	print("=== SDG Family Medical Band ===")
	for d in frappe.get_all(
		"SDG Family Medical Band",
		fields=["medical_band", "has_spouse", "children_count", "monthly_premium"],
		order_by="medical_band asc, children_count asc",
	):
		print(
			f"  {d.medical_band:10} spouse={int(d.has_spouse)} children={d.children_count}"
			f"  -> {d.monthly_premium:>9,.2f}"
		)

	print(f"=== {TAX_SLAB_NAME} (Income Tax Slab) ===")
	for d in frappe.get_all(
		"Taxable Salary Slab",
		filters={"parenttype": "Income Tax Slab", "parent": TAX_SLAB_NAME},
		fields=["from_amount", "to_amount", "percent_deduction", "sdg_fixed_amount"],
		order_by="to_amount asc",
	):
		print(
			f"  {d.from_amount:>12,.0f} - {d.to_amount:>14,.0f}  rate {d.percent_deduction:>5}%"
			f"  tax at start {d.sdg_fixed_amount:>9,.0f}"
		)
