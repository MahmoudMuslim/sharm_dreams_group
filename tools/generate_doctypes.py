#!/usr/bin/env python3
"""Generate the DocType JSON + controller files for sharm_dreams_group.

Run from anywhere:  python3 tools/generate_doctypes.py
Idempotent - safe to re-run after editing the specs below.
"""

import json
import os
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent
MODULE_DISPLAY = "Sharm Dreams Group"
DT_ROOT = APP / "sharm_dreams_group" / "sharm_dreams_group" / "doctype"
NOW = "2026-09-29 12:00:00.000000"

PERM_FULL = {
	"role": "System Manager",
	"read": 1,
	"write": 1,
	"create": 1,
	"delete": 1,
	"email": 1,
	"print": 1,
	"share": 1,
	"export": 1,
	"report": 1,
}


def perms(*extra_roles):
	out = [dict(PERM_FULL)]
	for role in extra_roles:
		out.append(
			{
				"role": role,
				"read": 1,
				"write": 1,
				"create": 1,
				"delete": 1,
				"email": 1,
				"print": 1,
				"share": 1,
				"export": 1,
				"report": 1,
			}
		)
	return out


def F(fieldname, fieldtype="Data", **kw):
	"""Build a field dict with fieldname/fieldtype first (Frappe convention)."""
	d = {"fieldname": fieldname, "fieldtype": fieldtype}
	d.update(kw)
	return d


# ---------------------------------------------------------------------------
# DocType specifications
# ---------------------------------------------------------------------------
SPECS = []


def spec(
	name,
	snake,
	fields,
	*,
	single=False,
	autoname=None,
	naming_rule=None,
	extra=None,
	roles=(),
	controller=True,
	track_changes=False,
	allow_import=False,
	title_field=None,
	description=None,
	istable=False,
):
	SPECS.append(
		dict(
			name=name,
			snake=snake,
			fields=fields,
			single=single,
			autoname=autoname,
			naming_rule=naming_rule,
			extra=extra or {},
			roles=roles,
			controller=controller,
			track_changes=track_changes,
			allow_import=allow_import,
			title_field=title_field,
			description=description,
			istable=istable,
		)
	)


ACTIVE = F("active", "Check", label="Active", default="1")

SOURCE_OPTIONS = "\nSeeded from Master Colleagues Data\nMaintained by HR"

# --- Employment Category ---------------------------------------------------
# "V Employment Category", populated in 176 of 178 master rows.  A Link target so
# the vocabulary is enforced rather than free text.
spec(
	"SDG Employment Category",
	"sdg_employment_category",
	[
		F(
			"category_name",
			"Data",
			label="Category",
			reqd=1,
			unique=1,
			description="e.g. Entry Level, Middle Management, Senior Management",
		),
		F("category_name_ar", "Data", label="Category (Arabic)"),
		F("sort_order", "Int", label="Sort Order", default="0", description="Lower sorts first in dropdowns"),
		F(
			"source",
			"Select",
			label="Source",
			options=SOURCE_OPTIONS,
			default="Seeded from Master Colleagues Data",
		),
		F("active", "Check", label="Active", default="1"),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
	],
	autoname="field:category_name",
	naming_rule="By fieldname",
	title_field="category_name",
	allow_import=True,
	roles=("SDG Payroll Manager",),
	description="Seniority bands used on Employee.employment_category",
)

# --- Contract Type ---------------------------------------------------------
# "W Contract Type", populated in 174 of 178 master rows.
spec(
	"SDG Contract Type",
	"sdg_contract_type",
	[
		F(
			"contract_type_name",
			"Data",
			label="Contract Type",
			reqd=1,
			unique=1,
			description="e.g. Unlimited Contract, Fixed Term",
		),
		F("contract_type_name_ar", "Data", label="Contract Type (Arabic)"),
		F(
			"is_limited_term",
			"Check",
			label="Limited Term",
			default="0",
			description="Tick when the contract needs an end date",
		),
		F(
			"source",
			"Select",
			label="Source",
			options=SOURCE_OPTIONS,
			default="Seeded from Master Colleagues Data",
		),
		F("active", "Check", label="Active", default="1"),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
	],
	autoname="field:contract_type_name",
	naming_rule="By fieldname",
	title_field="contract_type_name",
	allow_import=True,
	roles=("SDG Payroll Manager",),
	description="Contract types used on Employee.contract_type",
)

# --- Insured Person --------------------------------------------------------
# Holds medical cover and statutory identity only.
#
# This doctype used to carry the 18 document flags from the master's EMPLOYOYEE
# FILE INDEX block as Check fields.  A medical record is the wrong home for a
# document checklist, nothing was ever written to them, and they left the
# checklist with no usable home.  They now live in the `SDG Employee File Item`
# child table on Employee, where HR sees them on the employee they belong to.
spec(
	"SDG Insured Person",
	"sdg_insured_person",
	[
		F("employee", "Link", label="Employee", options="Employee", reqd=1, unique=1),
		F(
			"employee_number",
			"Data",
			label="Employee Number",
			read_only=1,
			description="Copied from the employee for searching",
		),
		F("section_cover", "Section Break", label="Medical Cover"),
		F(
			"medical_band",
			"Link",
			label="Coverage Band",
			options="SDG Medical Band",
			description="Band from the insurer, e.g. 100k B",
		),
		F(
			"family_band",
			"Link",
			label="Family Band",
			options="SDG Family Medical Band",
			description="Resolved from band, spouse and children count",
		),
		F("insurer_name", "Data", label="Insurer"),
		F("card_number", "Data", label="Card Number"),
		F("card_expiry", "Date", label="Card Expiry"),
		F(
			"dependants_count",
			"Int",
			label="Dependants",
			description="From the Medical register, not the master (which has 25 of 178)",
		),
		F("has_spouse", "Check", label="Covered Spouse", default="0"),
		F("section_identity", "Section Break", label="Statutory Identity"),
		F(
			"national_id",
			"Data",
			label="National ID",
			description="Primary statutory identifier; the join key between sources",
		),
		F("national_id_expiry", "Date", label="National ID Expiry"),
		F("si_number", "Data", label="Social Insurance Number"),
		F("si_status", "Data", label="Social Insurance Status"),
		F("si_date", "Date", label="Social Insurance Date"),
		F("section_footer", "Section Break"),
		F("active", "Check", label="Active", default="1"),
		F("remarks", "Small Text", label="Remarks"),
	],
	autoname="field:employee",
	naming_rule="By fieldname",
	title_field="employee",
	allow_import=True,
	track_changes=True,
	roles=("SDG Payroll Manager", "SDG Payroll User"),
	description="Medical cover, statutory identity and the document checklist",
)

# --- Employee File Item (child table on Employee) --------------------------
# One row per document in the employee's personnel file, from the master's
# "EMPLOYEE FILE INDEX" block (columns CO..DE: HRA, LOI, ID, 6 PIC, MILITARY,
# MEDICAL 111, SOCIAL S1, CRIMINAL RECORD, EDUCATION CERTIFICATE, EMPLOYMENT
# PERMT, Security check, BANK DETAILS, EMPLOYMENT OF RELATIVES, BENEFICIARY,
# CODE OF BUSINESS CONDUCT, UPDATED CV / INTERVIEW PAPERS, JOB DESCRIPTION,
# Refernce Check).
#
# A child table rather than 18 Check fields because the master's column set is
# not open-ended, a flat list of ticks cannot record *why* a document is missing
# or who chased it, and a new document type would need a schema change rather
# than a new row.
#
# The source column is the master header text; the fieldname is a stable slug so
# a rename in a later workbook does not orphan the rows.
FILE_INDEX_ITEMS = [
	("hra", "HRA"),
	("loi", "LOI"),
	("id", "ID"),
	("six_pic", "6 PIC"),
	("military", "Military"),
	("medical_111", "Medical 111"),
	("social_s1", "Social S1"),
	("criminal_record", "Criminal Record"),
	("education_certificate", "Education Certificate"),
	("employment_permit", "Employment Permit"),
	("security_check", "Security Check"),
	("bank_details", "Bank Details"),
	("employment_of_relatives", "Employment of Relatives"),
	("beneficiary", "Beneficiary"),
	("code_of_business_conduct", "Code of Business Conduct"),
	("updated_cv", "Updated CV / Interview Papers"),
	("job_description", "Job Description"),
	("reference_check", "Reference Check"),
]

FILE_ITEM_OPTIONS = "\n" + "\n".join(label for _slug, label in FILE_INDEX_ITEMS)
# The master writes these three shapes: Y (on file), N (missing) and NA (not
# applicable, plus "N/A").  "Copy" on EDUCATION CERTIFICATE is a stray value on
# one row and is reported rather than folded into a status.
FILE_STATUS_OPTIONS = "\nHeld\nMissing\nNot Applicable"

spec(
	"SDG Employee File Item",
	"sdg_employee_file_item",
	[
		F(
			"document_item",
			"Select",
			label="Document",
			options=FILE_ITEM_OPTIONS,
			reqd=1,
			in_list_view=1,
			columns=3,
			description="The document this row is about",
		),
		F(
			"status",
			"Select",
			label="Status",
			options=FILE_STATUS_OPTIONS,
			in_list_view=1,
			columns=2,
			description=(
				"Held = on file, Missing = not yet received, Not Applicable = not "
				"required. Deliberately not mandatory: 57 of the master's cells are "
				"blank and three carry a stray 'Copy', and an empty status means "
				"'not recorded'. Making it mandatory would make every save of an "
				"employee holding such a row fail."
			),
		),
		F(
			"notes",
			"Small Text",
			label="Notes",
			description="Why it is missing, who chased it, or where the paper copy is",
		),
	],
	istable=True,
	controller=True,
	description="One row per document in an employee's personnel file",
)

# --- Sub Department --------------------------------------------------------
# "L Sub Department", populated in 178 of 178 master rows, 21 distinct values,
# exactly one per employee and with no list separators - so it is a vocabulary,
# not a per-employee list.
#
# NOTE on the source data: the column conflates two ideas.  Several values are
# really locations rather than sub-departments ("Head Office", "Sharm El Sheikh",
# "Marsa Alam", "@40", "Villa 644"), and "Vacation Club" appears as both a
# Department and a Sub Department.  Modelling it as a vocabulary keeps that
# visible and correctable in the UI instead of baking it in.
spec(
	"SDG Sub Department",
	"sdg_sub_department",
	[
		F("sub_department_name", "Data", label="Sub Department", reqd=1, unique=1),
		F("sub_department_name_ar", "Data", label="Sub Department (Arabic)"),
		F(
			"looks_like_a_location",
			"Check",
			label="Appears To Be A Location",
			default="0",
			description="Tick where the master value is a place rather than a sub-department, "
			"e.g. Head Office, Sharm El Sheikh, @40",
		),
		F(
			"source",
			"Select",
			label="Source",
			options=SOURCE_OPTIONS,
			default="Seeded from Master Colleagues Data",
		),
		F("active", "Check", label="Active", default="1"),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
	],
	autoname="field:sub_department_name",
	naming_rule="By fieldname",
	title_field="sub_department_name",
	allow_import=True,
	roles=("SDG Payroll Manager",),
	description="Third level of the org structure, used on Employee.sdg_sub_department",
)

# --- 1. Medical band --------------------------------------------------------
spec(
	"SDG Medical Band",
	"sdg_medical_band",
	[
		F(
			"band",
			"Data",
			label="Coverage Band",
			reqd=1,
			unique=1,
			description="Band code exactly as issued by the insurer, e.g. 75k C",
		),
		F(
			"coverage_limit",
			"Currency",
			label="Coverage Limit",
			description="Sum insured as printed on the policy",
		),
		F("monthly_premium", "Currency", label="Monthly Premium", reqd=1),
		F(
			"source",
			"Select",
			label="Source",
			options="\nSeeded from payroll workbook\nInsurer schedule",
			default="Seeded from payroll workbook",
		),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
		ACTIVE,
	],
	autoname="field:band",
	naming_rule="By fieldname",
	roles=("HR Manager",),
	allow_import=True,
	title_field="band",
)

# --- 2. Gross insurance band ------------------------------------------------
spec(
	"SDG Gross Insurance Band",
	"sdg_gross_insurance_band",
	[
		F(
			"from_gross",
			"Currency",
			label="From Gross (>=)",
			reqd=1,
			description="Inclusive lower bound of the monthly gross salary range",
		),
		F(
			"to_gross",
			"Currency",
			label="To Gross (<)",
			reqd=1,
			description="Exclusive upper bound. Use a very large number for the top band",
		),
		F(
			"life_insurance",
			"Currency",
			label="Life Insurance (monthly)",
			description="Deducted from the employee. Zero where there is no life cover",
		),
		F(
			"si_base",
			"Currency",
			label="Social Insurance Base",
			description="Final Limit: the ceiling SI, training fund and employer SI are charged on",
		),
		F(
			"source",
			"Select",
			label="Source",
			options="\nSeeded from payroll workbook\nInsurer schedule\nPay scale",
			default="Seeded from payroll workbook",
		),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
		ACTIVE,
	],
	autoname="format:SDG-GIB-{#####}",
	naming_rule='By "Naming Series" field',
	roles=("HR Manager", "Accounts Manager"),
	allow_import=True,
	track_changes=True,
)

# --- 3. Family medical band -------------------------------------------------
spec(
	"SDG Family Medical Band",
	"sdg_family_medical_band",
	[
		F("medical_band", "Link", options="SDG Medical Band", label="Medical Band", reqd=1),
		F("has_spouse", "Check", label="Covers Spouse", reqd=1, default="0"),
		F("children_count", "Int", label="Children Covered", reqd=1, default="0", non_negative=1),
		F("monthly_premium", "Currency", label="Monthly Family Premium", reqd=1),
		F(
			"member_multiplier",
			"Float",
			label="Per Member Multiplier",
			read_only=1,
			description="premium / annual per member rate. Read only, derived",
		),
		F(
			"source",
			"Select",
			label="Source",
			options="\nSeeded from payroll workbook\nBroker schedule",
			default="Seeded from payroll workbook",
		),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
		ACTIVE,
	],
	autoname="format:SDG-FMB-{#####}",
	naming_rule='By "Naming Series" field',
	roles=("HR Manager",),
	allow_import=True,
	track_changes=True,
)

# --- 5. (removed) Tax bracket ------------------------------------------------
# `SDG Tax Bracket` is gone: the Egyptian bracket lives in the stock
# `Income Tax Slab` now, with the cumulative amount on a custom field of the
# `slabs` child row (see `setup.create_slab_fixed_amount`).  Its spec is deleted
# rather than left commented out, because this file is the source of truth and a
# commented spec gets re-created the next time somebody runs the generator - which
# is how a deleted table came back during this work.
#
# `SDG Leave Type`, `SDG Loan Settings`, `SDG Penalty Settings` and the original
# `SDG Coupon Band` went for the same reason: stock HRMS holds what they held, or
# nothing read them.
#
# The `SDG Coupon Band` at spec 8 is a *different* table, not a revival. The bands
# are reference data HR looks up when handing a coupon over; the old one was never
# stored at all.

# --- 7. Payroll settings (Single) -------------------------------------------
spec(
	"SDG Payroll Settings",
	"sdg_payroll_settings",
	[
		F("section_si", "Section Break", label="Social Insurance & Statutory"),
		F("si_employee_rate", "Percent", label="SI Employee Rate", default="11", reqd=1),
		F("si_employer_rate", "Percent", label="SI Employer Rate", default="18.75", reqd=1),
		F("training_fund_rate", "Percent", label="Training & Employment Fund", default="0.25", reqd=1),
		F(
			"training_fund_fixed",
			"Currency",
			label="Training Fund Fixed Fallback",
			default="0",
			description="Used when the rate cannot be applied",
		),
		F(
			"martyrs_fund_rate",
			"Percent",
			label="Martyrs Fund Rate",
			default="0.05",
			reqd=1,
			description="0.05% of gross = P * 5/10000",
		),
		F("emergency_fund_rate", "Percent", label="Emergency Fund Rate", default="1", reqd=1),
		F("emergency_base_cap", "Currency", label="Emergency Fund Base Cap", default="2720", reqd=1),
		F("column_break_tax", "Column Break"),
		F("section_tax", "Section Break", label="Income Tax"),
		F("exemption_standard", "Currency", label="Exemption - Normal Employee", default="1666.66", reqd=1),
		F(
			"exemption_determination",
			"Currency",
			label="Exemption - People of Determination",
			default="2500",
			reqd=1,
		),
		F(
			"delegated_tax_rate",
			"Percent",
			label="Delegated Row Tax Rate",
			default="10",
			reqd=1,
			description="Flat rate applied to a row that has a Delegated Company",
		),
		F(
			"tax_rounding_multiple",
			"Int",
			label="Annual Base Rounding",
			default="10",
			reqd=1,
			description="Annual tax base is floored to a multiple of this",
		),
		F("insurance_cap_percent", "Percent", label="Insurance Cap (% of base)", default="15", reqd=1),
		F("insurance_cap_max", "Currency", label="Insurance Cap (max)", default="10000", reqd=1),
		F("section_earnings", "Section Break", label="Earnings"),
		F(
			"monthly_days_divisor",
			"Int",
			label="Days Divisor",
			default="30",
			reqd=1,
			description="Monthly gross is divided by this to get a daily rate",
		),
		F(
			"official_day_multiplier",
			"Int",
			label="Official Day Multiplier",
			default="2",
			reqd=1,
			description="Official days are valued at this multiple of the daily rate",
		),
		F(
			"family_member_annual_rate",
			"Currency",
			label="Family Member Annual Rate",
			default="3100",
			reqd=1,
			description="Per covered family member per year. Monthly = this / 12",
		),
	],
	single=True,
	controller=False,
	allow_import=True,
)

# --- 8. Cost-of-living coupon band -----------------------------------------
# Four bands from Cost of living coupon.xlsx.  This is a *handout*: the HR desk
# counts notes out of the drawer and the employee signs for them, so it is a cash
# expense and deliberately NOT a payroll earning - see `hr/coupon.py` for why a
# stock Employee Benefit Claim cannot record it.
spec(
	"SDG Coupon Band",
	"sdg_coupon_band",
	[
		F(
			"category_name",
			"Data",
			label="Category",
			reqd=1,
			unique=1,
			description="e.g. Single, Married + 1 Child",
		),
		F("category_name_ar", "Data", label="Category (Arabic)"),
		F(
			"coupon_amount",
			"Currency",
			label="Coupon Amount",
			reqd=1,
			default="0",
			description="Value of one coupon in this band",
		),
		F("children_count", "Int", label="Children Count", default="0"),
		F("sort_order", "Int", label="Sort Order", default="0"),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
		F("active", "Check", label="Active", default="1"),
	],
	autoname="field:category_name",
	naming_rule="By fieldname",
	title_field="category_name",
	allow_import=True,
	roles=("SDG Payroll Manager",),
	description="Cost-of-living coupon bands, by family size",
)

# --- 9. Coupon granted -----------------------------------------------------
# One row per employee per month: who was handed a coupon, how much, and how the
# notes were counted.  Deliberately its own DocType rather than a stock
# `Employee Benefit Claim`, because that submits an `Additional Salary` and would
# pay the coupon through payroll - the two systems must not both pay for it.
spec(
	"SDG Coupon Granted",
	"sdg_coupon_granted",
	[
		F(
			"period_month",
			"Date",
			label="Period Month",
			reqd=1,
			description="First day of the month the coupon was handed over",
		),
		F("employee", "Link", label="Employee", options="Employee", reqd=1),
		F("band", "Link", label="Band", options="SDG Coupon Band"),
		F("amount", "Currency", label="Amount", reqd=1, default="0"),
		F("notes_100", "Int", label="100 Notes", default="0"),
		F("notes_200", "Int", label="200 Notes", default="0"),
		F("notes_250", "Int", label="250 Notes", default="0"),
		F(
			"notes_total",
			"Currency",
			label="Notes Total",
			read_only=1,
			default="0",
			description="100 x n100 + 200 x n200 + 250 x n250, must equal Amount",
		),
		F("section_break_src", "Section Break", label="Source"),
		F("workbook_row", "Int", label="Workbook Row", read_only=1),
		F("source_sheet", "Data", label="Source Sheet", read_only=1),
		F("signature_received", "Check", label="Signature Received", default="0"),
		F("section_break_note", "Section Break"),
		F("remarks", "Small Text", label="Remarks"),
	],
	# The name is built by the controller's `autoname()`, not by an autoname
	# expression: Frappe's `set_name_from_naming_options` only understands
	# `field:`, `naming_series:`, `prompt`, `format:` and anything containing `#`,
	# so `{employee} - {period_month}` is quietly ignored and the row gets a hash
	# name.  `hash` is stated here as the fallback that actually applies.
	autoname="hash",
	title_field="employee",
	track_changes=True,
	allow_import=True,
	roles=("SDG Payroll Manager",),
	description="A cost-of-living coupon handed to an employee, and the notes counted",
)


# ---------------------------------------------------------------------------
# Emit files
# ---------------------------------------------------------------------------
def class_name(name):
	return "".join(w for w in name.replace("-", " ").title().split() if w.isalnum())


def build(s):
	dt = {
		"actions": [],
		"creation": NOW,
		"doctype": "DocType",
		"editable_grid": 1,
		"engine": "InnoDB",
		"field_order": [f["fieldname"] for f in s["fields"]],
		"fields": s["fields"],
		"index_web_pages_for_search": 1,
		"links": [],
		"modified": NOW,
		"modified_by": "Administrator",
		"module": MODULE_DISPLAY,
		"name": s["name"],
		"owner": "Administrator",
		"permissions": perms(*s["roles"]),
		"row_format": "Dynamic",
		"sort_field": "modified",
		"sort_order": "DESC",
		"states": [],
	}
	if s["istable"]:
		# A child table: no autoname, no list view, and permissions come from the
		# parent.  Getting istable wrong produces a doctype the desk will not let
		# you attach as a table.
		dt["istable"] = 1
	elif s["single"]:
		dt["issingle"] = 1
		dt["default_view"] = "List"
	else:
		if s["autoname"]:
			dt["autoname"] = s["autoname"]
		if s["naming_rule"]:
			dt["naming_rule"] = s["naming_rule"]
		if s["allow_import"]:
			dt["allow_import"] = 1
		if s["track_changes"]:
			dt["track_changes"] = 1
	if s["title_field"]:
		dt["title_field"] = s["title_field"]
	if s["description"]:
		dt["description"] = s["description"]
	dt.update(s["extra"])
	return dt


def main():
	written = []
	for s in SPECS:
		d = DT_ROOT / s["snake"]
		d.mkdir(parents=True, exist_ok=True)
		(d / "__init__.py").touch()
		(d / f"{s['snake']}.json").write_text(json.dumps(build(s), indent=1, sort_keys=True) + "\n")
		# controllers are hand-written next to the generated json
		written.append(s["name"])
	print(f"wrote {len(written)} doctypes:")
	for w in written:
		print("  -", w)


if __name__ == "__main__":
	main()
