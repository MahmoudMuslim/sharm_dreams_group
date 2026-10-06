# Copyright (c) 2026, Sharm Dreams Group and contributors
# For license information, please see license.txt

"""Post-install setup, diagnostics and seed entry points.

Run with:  bench --site <site> execute sharm_dreams_group.setup.<function>
"""

from __future__ import annotations

import json
import os
import sys
import zlib

import frappe
from frappe.utils import cint, get_link_to_form, nowdate

from sharm_dreams_group.payroll.egypt.deductions import REQUIRED_COMPONENTS

# Colours for the group-by charts, taken from the same family the number cards use
# so the strip and the charts read as one thing. They are picked by a stable hash of
# the chart name rather than by position in the spec list: `hash()` is salted per
# process, and an index would repaint every chart after one is inserted in the
# middle, which turns each migrate into a diff nobody can review.
CHART_COLOURS = (
	"#3d5a80",  # slate blue
	"#2f6f4e",  # green
	"#e07b39",  # orange
	"#7048e8",  # purple
	"#0ca678",  # teal
	"#c2255c",  # rose
	"#e8a33d",  # amber
	"#8d99ae",  # grey
)


def chart_colour(name: str) -> str:
	"""Pick this chart's colour, deterministically and independent of the run."""
	return CHART_COLOURS[zlib.crc32(name.encode("utf-8")) % len(CHART_COLOURS)]


def after_install():
	create_roles()
	create_salary_components()
	create_custom_fields()
	create_slab_fixed_amount()
	create_leave_type_fields()
	create_self_service_fields()
	create_self_service_defaults()
	create_penalty_levels()
	create_penalty_cards()
	set_app_logo()
	set_favicon()
	create_dashboard_cards()
	delete_retired_dashboard()
	create_custom_chart_sources()
	# Before `create_dashboard`, because the drilldown has to be on the chart
	# document before the dashboard names it - and it is the whole of what makes a
	# reused `Custom` chart clickable, a `Group By` being clickable from its own
	# fields.
	create_reused_custom_chart_drilldowns()
	create_dashboard()
	create_domain_cards_and_charts()
	sync_drilldown_flags()
	create_domain_dashboards()
	apply_app_sidebar()
	apply_workspace()
	set_default_workspace()
	apply_desk_entry_points()


def set_favicon() -> None:
	"""Give the browser tab this app's mark instead of Frappe's favicon.

	`Website Settings.favicon` was empty, and `frappe/www/desk.html` falls back to
	Frappe's favicon when it is, so the tab showed the framework icon on every
	desk page. The file has to be a File record rather than the app asset: the
	Attach field stores a path under /files, not an /assets URL.
	"""
	if frappe.db.get_single_value("Website Settings", "favicon"):
		return
	existing = frappe.db.get_value(
		"File",
		{"file_name": FAVICON_FILE_NAME, "attached_to_doctype": "Website Settings"},
		"name",
	)
	if not existing:
		source = os.path.join(frappe.get_app_path(APP), "public", "images", "logo.png")
		if not os.path.exists(source):
			return
		with open(source, "rb") as handle:
			inserted = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": FAVICON_FILE_NAME,
					"attached_to_doctype": "Website Settings",
					"attached_to_name": "Website Settings",
					"is_private": 0,
					"content": handle.read(),
				}
			).insert(ignore_permissions=True, ignore_if_duplicate=True)
		# `.name`, not the document: the two branches otherwise leave a Document in
		# one and a name string in the other, and a Document passed as `filters`
		# breaks the query rather than failing loudly.
		existing = inserted.name if not isinstance(inserted, str) else inserted

	ws = frappe.get_doc("Website Settings", "Website Settings")
	ws.favicon = frappe.db.get_value("File", existing, "file_url")
	ws.save(ignore_permissions=True)
	print(f"set favicon: {ws.favicon}")


def set_app_logo() -> None:
	"""Point the desk navbar at this app's own mark.

	`Navbar Settings` is used rather than the `app_logo_url` hook because that hook
	is aggregated across every installed app and `get_app_logo` only prefers the
	last entry when there are exactly two. With frappe, erpnext, hrms and lending
	all defining one, the hook resolves to Frappe's logo however it is ordered -
	so the setting, which is checked first, is the only thing that actually wins.
	"""
	logo = "/assets/sharm_dreams_group/images/logo.png"
	if frappe.db.get_single_value("Navbar Settings", "app_logo") == logo:
		return
	frappe.db.set_single_value("Navbar Settings", "app_logo", logo)


def after_migrate() -> None:
	"""Re-apply everything this app owns, on every migrate.

	All three halves matter.  `create_custom_fields` covers a new field in the
	specs above being added to an already-installed site - without this, the
	field simply never appears until someone re-runs it by hand.  It is also the
	only thing that creates the self-service page's columns, so a site installed before the
	self-service existed gets them here rather than needing a reinstall.
	`create_self_service_defaults` re-asserts the same defaults, and `create_leave_type_fields`
	has to run in the same pass because `create_self_service_defaults` writes the day
	multiplier onto Leave Types that field has to exist on.
	`apply_workspace` restores the Link Cards an HRMS upgrade overwrites, and
	`remove_company_filters` strips the per-user Company filter that an ERPNext
	or HRMS upgrade would otherwise put back on the Dashboard Charts and Number
	Cards.
	"""
	create_custom_fields()
	create_slab_fixed_amount()
	create_leave_type_fields()
	create_self_service_fields()
	create_self_service_defaults()
	create_penalty_levels()
	create_penalty_cards()
	set_app_logo()
	set_favicon()
	create_dashboard_cards()
	delete_retired_dashboard()
	create_custom_chart_sources()
	# Before `create_dashboard`, because the drilldown has to be on the chart
	# document before the dashboard names it - and it is the whole of what makes a
	# reused `Custom` chart clickable, a `Group By` being clickable from its own
	# fields.
	create_reused_custom_chart_drilldowns()
	create_dashboard()
	create_domain_cards_and_charts()
	sync_drilldown_flags()
	create_domain_dashboards()
	apply_app_sidebar()
	apply_workspace()
	set_default_workspace()
	apply_desk_entry_points()

	from sharm_dreams_group.dashboard_widgets import remove_company_filters
	from sharm_dreams_group.upstream_patches import apply as apply_upstream_patches

	# No commit: `post_schema_updates` is @atomic and commits the whole migrate
	# itself, so committing here would end that transaction early.
	remove_company_filters(commit=False)

	# Six Dashboard Charts are backed by reports that reject a blank Company
	# inside their own Python.  An ERPNext or HRMS upgrade rewrites those files,
	# so this has to be re-applied rather than done once.
	apply_upstream_patches()

	# The penalty level is only computed when an Employee is saved, so anyone who
	# has not been edited since the feature was installed would carry no level
	# and no colour.  Idempotent and cheap when nothing has changed.
	from sharm_dreams_group.hr.penalty import backfill as backfill_penalties

	if backfill_penalties():
		print("employee penalty levels: backfilled")

	# The Employee list needs the level as a column, not only as a colour.
	from sharm_dreams_group.hr.penalty import apply_list_view_columns

	if apply_list_view_columns():
		print("employee list: penalty columns added")


def apply_workspace() -> None:
	"""Create the SDG workspace and (re)add the links from the stock workspaces.

	Called after install and after every migrate.  Migrate is what makes this
	necessary: the links live in the content of HRMS's own workspaces, and an
	HRMS upgrade rewrites those documents, which would drop the entry points
	without any error.  Re-applying is idempotent.
	"""
	from sharm_dreams_group import workspace

	workspace.apply()


def apply_desk_entry_points() -> None:
	"""Put this app on the desk: a `Desktop Icon`, and a `Workspace Sidebar`.

	Called after install and after every migrate.  Both are needed and neither is
	enough on its own.

	`Desktop Icon` is what appears on the desk itself.  It is NOT created from
	`modules.txt` or from a `config/desktop.py` - in this Frappe version nothing
	reads a `desktop.py` at all, so writing one changes nothing.  The icon is a
	document, created from the `add_to_apps_screen` hook by
	`frappe.utils.install.auto_generate_icons_and_sidebar`.

	That helper is registered on `after_app_install`, so it runs exactly once, at
	install time.  Here it was too early to be useful: `create_desktop_icons` is
	guarded by an existence check and skips an app that already has an icon, and
	the Workspace `after_app_install` goes on to create only exists afterwards.
	So the hook fired before the workspace did, no icon was made, and there is no
	re-run of it on a normal migrate - which leaves an app that is installed and
	has a public workspace but is simply absent from the desk.

	`Workspace Sidebar` is the left-hand list inside the workspace, and is
	generated from the workspace's own shortcuts, so it has to exist before the
	icon resolves to something a user can actually see.

	Re-applied on every migrate rather than only at install, and idempotent:
	`create_desktop_icons` only fills in what is missing, and the sidebar builder
	skips any workspace that already has one.  Safe to run against a site where
	both already exist.
	"""
	from frappe.desk.doctype.desktop_icon.desktop_icon import create_desktop_icons
	from frappe.desk.doctype.workspace_sidebar.workspace_sidebar import (
		create_workspace_sidebar_for_workspaces,
	)

	# Order matters and mirrors frappe's own helper: the sidebar is what makes the
	# icon resolvable, so build it first.
	create_workspace_sidebar_for_workspaces()
	create_desktop_icons()

	icon_name = frappe.get_hooks("app_title", app_name=APP)[0]
	sidebar = frappe.db.exists("Workspace Sidebar", "Sharm Dreams Group")

	# `create_desktop_icons` leaves `standard` at its default of 0, and
	# `get_desktop_icons` selects `standard == 1 OR owner in (Administrator, user)`.
	# So the icon is created visible to the installing user alone and the
	# `has_permission` hook never gets to decide anything - every other user is
	# simply not shown it.  The workspace is public and `has_app_permission`
	# returns True unconditionally, so the icon is made standard and that hook
	# becomes the thing that governs visibility, which is what it is there for.
	# This also matches how Frappe, ERPNext and HRMS ship their own app icons.
	icon_exists = frappe.db.exists("Desktop Icon", icon_name)
	if icon_exists:
		icon = frappe.get_doc("Desktop Icon", icon_name)
		if not icon.standard:
			icon.standard = 1
			icon.save()
			print(f"  desk icon {icon_name!r}: set standard (was visible to its owner only)")

	icon = frappe.db.get_value("Desktop Icon", icon_name, "link") if icon_exists else None
	print(f"  desk icon: {icon_name!r} -> {icon or 'MISSING'}")
	print(f"  desk sidebar: {'ok' if sidebar else 'MISSING'}")


def set_default_workspace() -> None:
	"""Land users on this app's workspace rather than ERPNext's Home.

	Called after install and after every migrate so it covers both the users who
	already exist and any added later, which the `User` after_insert hook covers
	on its own.  Idempotent: a user who has chosen a workspace keeps it.
	"""
	from sharm_dreams_group import workspace

	changed = workspace.set_default_workspace()
	if changed:
		print(f"  default workspace set for {changed} user(s)")


def create_roles() -> None:
	"""SDG payroll roles, used by the band tables and operational doctypes."""
	for role, desc in (
		("SDG Payroll Manager", "Maintains band tables, settings and staff loans"),
		("SDG Payroll User", "Runs payroll and views reconciliation"),
	):
		if frappe.db.exists("Role", role):
			print(f"role exists : {role}")
			continue
		role_doc = frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role,
				"desk_access": 1,
				"is_custom": 1,
				"description": desc,
			}
		).insert(ignore_permissions=True)
		print(f"created role: {role} ({get_link_to_form('Role', role_doc.name)})")


# The Egyptian statutory lines.  `type` is Salary Component's own field name
# (not `component_type`).  Amounts are written by the regional override, so these
# carry no amount or formula of their own.
COMPONENTS = (
	# name, type, is_income_tax_component, description
	("Social Insurance (Employee)", "Deduction", 0, "Employee share of social insurance, 11% of the SI base"),
	("Martyrs Fund", "Deduction", 0, "Martyrs fund, 0.05% of basic salary"),
	(
		"Social Insurance (Employer)",
		"Employer Contribution",
		0,
		"Employer share of social insurance, 18.75% of the SI base",
	),
	("Training and Development Fund", "Employer Contribution", 0, "Training fund, 0.25% of the SI base"),
	("Emergency Fund", "Employer Contribution", 0, "Emergency fund, 1% of basic capped at 2,720"),
	(
		"MI Fund (Employer)",
		"Employer Contribution",
		0,
		"MI fund borne by the employer. Stated per person in the payroll sheet "
		"rather than as a rate, and not deducted from the employee",
	),
	# Taken off the payment rather than off taxable income, so they are not
	# `variable_based_on_taxable_salary` deductions and must not be flagged as
	# income tax components.  The insurance pair is read from the register and
	# also reduces the tax base - see payroll/egypt/deductions.py.
	("Medical Insurance", "Deduction", 0, "Monthly medical cover, taken off the payment"),
	("Life Insurance", "Deduction", 0, "Monthly life cover, taken off the payment"),
	("Phone Allowance Recovered", "Deduction", 0, "Phone allowance recovered from monthly pay"),
	("Family Medical Insurance", "Deduction", 0, "Family medical cover recovered from monthly pay"),
	(
		"Income Tax on Cost Share",
		"Deduction",
		0,
		"Flat group withholding on delegated cost shares, 10% of the annualised share. "
		"Separate from `Income Tax` so a slip shows both bases",
	),
)


def create_salary_components() -> None:
	"""Create the deduction and employer-contribution lines the engine writes to.

	`Income Tax` is created by HRMS itself and is deliberately not in this list.
	Names are stable identifiers - renaming one orphans every historical slip.
	"""
	created = 0
	for name, type_, is_tax_component, description in COMPONENTS:
		if frappe.db.exists("Salary Component", name):
			continue
		frappe.get_doc(
			{
				"doctype": "Salary Component",
				"salary_component": name,
				"salary_component_abbr": _abbr(name),
				"type": type_,
				"is_income_tax_component": is_tax_component,
				"description": description,
			}
		).insert(ignore_permissions=True)
		created += 1
		print(f"created component: {name} ({type_})")
	print(f"salary components: {created} created")


def _abbr(name: str) -> str:
	"""Short code shown on the printed slip, e.g. 'Social Insurance (EE)'."""
	initials = "".join(word[0] for word in name.replace("(", " ").replace(")", " ").split())
	return initials.upper()[:5]


# Read-only echo of the tax chain, so a printed slip can be audited against the
# workbook columns without re-running anything.
SLIP_FIELDS = (
	(
		"sdg_annual_tax_base",
		"SDG Annual Tax Base",
		"Currency",
		"Annual tax base after the personal exemption, floored to a multiple of 10",
	),
	(
		"sdg_annual_taxable",
		"SDG Annual Taxable Income",
		"Currency",
		"Tax base less the deductible insurance premium",
	),
	(
		"sdg_annual_income_tax",
		"SDG Annual Income Tax",
		"Currency",
		"Income tax for the year, before dividing to a monthly figure",
	),
	(
		"sdg_insurance_deductible",
		"SDG Insurance Deductible",
		"Currency",
		"Insurance premium allowed against the tax base, capped at 15% of it and 10,000",
	),
	(
		"sdg_personal_exemption",
		"SDG Personal Exemption",
		"Currency",
		"Monthly exemption applied; zero for a delegated cost share",
	),
	# --- inputs the payroll builder writes, and the engine reads back -------
	# A slip may carry the person's own pay plus cost shares charged to other
	# group companies.  The two are taxed on separate bases, so the engine needs
	# to be told where the boundary is rather than inferring it from gross.
	(
		"sdg_leader_earning",
		"SDG Employed Earning",
		"Currency",
		"Gross earned by this company. The remainder of gross pay is cost share, "
		"which is taxed at a flat rate with no exemption or social insurance.",
	),
	(
		"sdg_si_employee_rate",
		"SDG Social Insurance Rate",
		"Float",
		"Employee share of social insurance, 11% unless the payroll sheet says "
		"otherwise. Cost shares are not insured.",
	),
	# Written by `hr/slip.py` when the payroll builder carries a loan repayment, and
	# read back by three places in `hr/dashboard.py` - the `Loan Repayment` card, the
	# `Employees On A Loan` card's filter, and its sum query.  It was in all four and
	# declared in none of them, so it existed only for as long as somebody had added it
	# to the site by hand; `after_migrate` could not restore it, and the two cards
	# failed once it was gone.  Declared here so migrate owns it.
	(
		"total_loan_repayment",
		"Total Loan Repayment",
		"Currency",
		"Loan repayment deducted from this slip, after tax.",
	),
	(
		"sdg_si_limit",
		"SDG Social Insurance Limit",
		"Currency",
		"Social insurance base for this company, from the gross insurance band "
		"for the employed earning. Cost shares are not insured.",
	),
	(
		"sdg_mi_employer_share",
		"SDG MI Employer Share",
		"Currency",
		"MI fund contribution the employer bears, from the payroll sheet. Like "
		"the employer side of social insurance it is a company cost that is not "
		"deducted from the employee, so it leaves net pay alone.",
	),
)

EMPLOYEE_FIELDS = (
	# --- identity and names ------------------------------------------------
	(
		"sdg_name_arabic",
		"Name (Arabic)",
		"Data",
		"Arabic name exactly as it appears on the national ID",
		"Personal Details",
	),
	# --- employment terms --------------------------------------------------
	(
		"sdg_employment_category",
		"Employment Category",
		"Link",
		"Seniority band, from the master workbook",
		"Joining",
	),
	("sdg_contract_type", "Contract Type", "Link", "Contract type, from the master workbook", "Joining"),
	(
		"sdg_contract_date",
		"Contract Date",
		"Date",
		"Date the contract was signed; distinct from date_of_joining",
		"Joining",
	),
	("sdg_sub_department", "Sub Department", "Link", "Third level of the org structure", "Overview"),
	# --- statutory identity ------------------------------------------------
	(
		"sdg_national_id",
		"National ID",
		"Data",
		"Egyptian national ID. The join key between the master, payroll and medical files",
		"Personal Details",
	),
	("sdg_national_id_expiry", "National ID Expiry", "Date", "", "Personal Details"),
	("sdg_si_number", "Social Insurance Number", "Data", "", "Personal Details"),
	(
		"sdg_si_status",
		"Social Insurance Status",
		"Select",
		"Whether the employee is covered by social insurance",
		"Personal Details",
	),
	("sdg_si_date", "Social Insurance Date", "Date", "", "Personal Details"),
	(
		"sdg_military_status",
		"Military Status",
		"Select",
		"Required for Egyptian employment records",
		"Personal Details",
	),
	(
		"sdg_military_document",
		"Military Status Document",
		"Select",
		"Whether the military service certificate is on file",
		"Personal Details",
	),
	# --- personal ----------------------------------------------------------
	("sdg_religion", "Religion", "Select", "Carried over from the master workbook", "Personal Details"),
	(
		"sdg_dependants_count",
		"Number of Dependants",
		"Int",
		"From the master workbook. Sparse (25 of 178); Medical.xlsx is the better source",
		"Personal Details",
	),
	("sdg_governorate", "Governorate", "Data", "Home governorate", "Address & Contacts"),
	# Profile tab, with the other narrative text and above the history tables
	(
		"sdg_job_profile",
		"Current Job Profile (Tasks)",
		"Text Editor",
		"What this person actually does, from the master's Current Job Profile column. "
		"Populated for 95 of the 178 master rows; the rest are blank in the source",
		"Profile",
	),
	# --- onboarding --------------------------------------------------------
	("sdg_offer_status", "Offer Status", "Select", "", "Joining"),
	("sdg_status_date", "Status Date", "Date", "Date the current offer status took effect", "Joining"),
	(
		"sdg_starting_designation",
		"Starting Designation",
		"Link",
		"The role held on joining, from the master's Starting Position column.  Differs "
		"from Designation for anyone who has moved role since",
		"Joining",
	),
	(
		"sdg_is_person_of_determination",
		"SDG People of Determination",
		"Check",
		"Qualifies for the higher personal exemption",
		"Salary",
	),
	(
		"sdg_file_index",
		"Employee File Index",
		"Table",
		"One row per document in the personnel file, from the master's EMPLOYOYEE FILE INDEX",
		"Personal Details",
	),
	# --- penalties ----------------------------------------------------------
	# The table is the record; everything else here is derived from that table by
	# hr/penalty.py on every save, and is read-only so it cannot drift from the
	# rows it summarises.  `sdg_penalty_level` is a Link rather than text, which
	# is what makes "show me everyone at Warning" and a per-level headcount
	# possible without writing a report.
	(
		"sdg_penalty",
		"Penalties",
		"Table",
		"Recorded against the employee. Feeds the level HR acts on; never affects salary",
		"PENALTIES",
	),
	(
		"sdg_penalty_points",
		"Penalty Points",
		"Int",
		"Weight of the penalties that still count, waived and expired ones excluded. Derived",
		"PENALTIES",
	),
	(
		"sdg_penalty_count",
		"Penalty Count",
		"Int",
		"How many penalties still count. Derived",
		"PENALTIES",
	),
	(
		"sdg_penalty_level",
		"Penalty Level",
		"Link",
		"The band the points fall into, from SDG Penalty Level. Derived",
		"PENALTIES",
	),
	(
		"sdg_penalty_indicator",
		"Penalty Indicator",
		"Select",
		"Colours the Employee list row. Derived from the level",
		"PENALTIES",
	),
	(
		"sdg_penalty_colour",
		"Penalty Colour",
		"Color",
		"For the employee card and the dashboard tile. Derived from the level",
		"PENALTIES",
	),
	(
		"sdg_penalty_summary",
		"Penalty Summary",
		"Data",
		"The level and the action it requires, so the rule travels with the person. Derived",
		"PENALTIES",
	),
	(
		"sdg_children",
		"Children",
		"Table",
		"One row per child, with the company school fee that applies to each",
		"CHILDREN",
	),
	(
		"sdg_children_count",
		"Children",
		"Int",
		"Number of child rows recorded. Derived",
		"CHILDREN",
	),
	(
		"sdg_school_fee_total",
		"School Fees / Month",
		"Currency",
		"Sum of the fees for eligible children only. Derived - change a child's fee, not this",
		"CHILDREN",
	),
	(
		"sdg_school_fee_children",
		"Children Claiming Fees",
		"Int",
		"How many of the children are marked eligible. Derived",
		"CHILDREN",
	),
)

OFFER_STATUS_OPTIONS = "\nOn board\nOff board"

# Computed on save so they can never go stale.  These all derive from
# date_of_birth / date_of_joining, but the master workbook carries them and HR
# reports on them, so they are stored for querying rather than recalculated live.
DERIVED_EMPLOYEE_FIELDS = (
	("sdg_age", "Age", "Int", "Recomputed from date_of_birth on every save", "Personal Details"),
	(
		"sdg_years_of_tenure",
		"Years of Tenure",
		"Float",
		"Recomputed from date_of_joining on every save",
		"Joining",
	),
	(
		"sdg_days_with_company",
		"Days with Company",
		"Int",
		"Recomputed from date_of_joining on every save",
		"Joining",
	),
)

# The days each employee is *owed*, for the self-service page's Time Off balances screen.
#
# Retired.  These six fields were the app's own entitlement model - one bucket per
# leave type, summed against Leave Applications - and every one of them is zero on
# every employee, so the screen showed everyone owing nothing.
#
# Entitlement now comes from `SharmDreamVacation Tracker.xlsx`, extracted by
# `tools/build_vacation_spec.py` and read through `self_service/vacation_spec.py`.
# The workbook models **one pooled entitlement** (21 or 30 days a year by age and
# service, plus hand-entered sums for staff who do not work a 5-day week) and one
# carried-forward figure, not six buckets.  "Remote Work Days" in particular has no
# counterpart in the workbook at all.
#
# Kept as an empty tuple rather than deleted outright: it is spliced into
# `EMPLOYEE_FIELDS`, and an empty sequence there is a statement that the app holds
# no per-employee leave figures of its own.  The Custom Fields already created in
# existing databases are left in place - Frappe does not drop them - so nothing in
# the form layout breaks; they are simply no longer written or read.
SELF_SERVICE_EMPLOYEE_FIELDS = ()

COMPANY_FIELDS = (
	(
		"company_name_ar",
		"Company Name (Arabic)",
		"Data",
		"Arabic legal name, from the master workbook's bilingual Company column",
	),
)

DESIGNATION_FIELDS = (
	(
		"sdg_designation_name_ar",
		"Designation (Arabic)",
		"Data",
		"Arabic job title, shared by every employee holding this designation",
	),
)

# Set by the Employee hook, never by the importer.
# Fields the Employee hook owns outright.  Read-only in the desk, because a
# hand-edited penalty total is a penalty total that disagrees with the rows
# underneath it and nobody notices until a level is wrong.
# The two derived fields a manager reads off a list or an image tile.  `in_list_view`
# on the Custom Field is what puts a field in the desk's default column set AND in
# the image view's per-tile bullets; the colour, indicator and summary are display
# dressing and stay off both.
LISTED = {"sdg_penalty_level", "sdg_penalty_points"}

_COMPUTED = {
	"sdg_age",
	"sdg_years_of_tenure",
	"sdg_days_with_company",
	"sdg_penalty_points",
	"sdg_penalty_count",
	"sdg_penalty_level",
	"sdg_penalty_indicator",
	"sdg_penalty_colour",
	"sdg_penalty_summary",
	"sdg_children_count",
	"sdg_school_fee_total",
	"sdg_school_fee_children",
}

_LINK_OPTIONS = {
	"sdg_employment_category": "SDG Employment Category",
	"sdg_contract_type": "SDG Contract Type",
	"sdg_sub_department": "SDG Sub Department",
	"sdg_starting_designation": "Designation",
	"sdg_penalty_level": "SDG Penalty Level",
}

# A Table's `options` names the child doctype.  Without it the field is created
# but the desk cannot attach a table to it, so this has to be set alongside the
# fieldtype rather than left to the default.
_TABLE_OPTIONS = {
	"sdg_file_index": "SDG Employee File Item",
	"sdg_penalty": "SDG Penalty",
	"sdg_children": "SDG Children",
}

# A dedicated tab for the Egyptian statutory record.  It has no home on stock
# ERPNext, and burying national ID / SI details among the address fields would
# make them easy to miss.  The tab is created on demand and named so the label
# cannot collide with a stock tab.
STATUTORY_TAB_FIELD = "sdg_statutory_section"
STATUTORY_TAB_LABEL = "SDG Statutory"

# Social insurance details sit in the Personal Details tab, inside their own
# section immediately after the stock "Health Insurance" section.  Created as a
# custom field because a Custom Field cannot otherwise introduce a section.
# Stock tab break fieldnames, used as anchors so an SDG field can be placed at
# the top of a tab rather than only relative to a sibling field.
JOINING_TAB = "employment_details"
SALARY_TAB = "salary_information"

# The document checklist gets its own tab.  It is 18 rows per employee and HR
# works through it as a task rather than as one more field among the personal
# details, and a Custom Field cannot otherwise introduce a tab.
FILE_INDEX_TAB_FIELD = "sdg_file_index_section"
FILE_INDEX_TAB_LABEL = "Employee File Index"
# The tab is placed immediately before this one, so the checklist sits with the
# employment record rather than after Connections.  Matched on the stock Tab
# Break's fieldname, with its label as a fallback, because "Exit" is what HR
# calls the tab it sits next to.
FILE_INDEX_TAB_BEFORE = "exit"

# A Custom Field cannot introduce a section, so the section is itself a
# zero-width custom field that the penalty fields chain off.  It is anchored to
# the Connections tab: the penalty fields are chained off it (see _INSERT_AFTER),
# and a section break in one tab cannot open a section in the next, so anchoring
# it to the file-index checklist stranded it there with nothing under it.
# Penalties get their own tab rather than a collapsible section inside another
# one.  It is a different kind of record - dated entries with their own status and
# an action attached - and burying it in Personal Details made it look like one
# more attribute of the person rather than a log against them.
#
# Anchored after the file-index table, which puts the tab between "Employee File
# Index" and "Exit": both are the record-keeping tabs, and neither is part of the
# employee's day-to-day record.
PENALTY_TAB_FIELD = "sdg_penalty_tab"
PENALTY_TAB_LABEL = "Penalties"
PENALTY_TAB_ANCHOR = "sdg_file_index"

# The field this replaced.  Dropped on migrate so a site that has it does not end
# up with a stray section break ahead of the new tab.
PENALTY_LEGACY_SECTION = "sdg_penalty_section"

# Children and their school fees, mirroring `hr.employment.children` and
# `hr.employment.school.fee` in the Odoo module this was ported from.  Anchored
# after the penalty table so it lands between "Penalties" and "Exit", keeping
# the record-keeping tabs together and away from the employee's daily record.
CHILDREN_TAB_FIELD = "sdg_children_tab"
CHILDREN_TAB_LABEL = "Children"
CHILDREN_TAB_ANCHOR = "sdg_penalty"

APP = "sharm_dreams_group"
FAVICON_FILE_NAME = "sdg-logo.png"

SOCIAL_SECTION_FIELD = "sdg_social_insurance_section"
SOCIAL_SECTION_LABEL = "Social Insurance"
PERSONAL_DETAILS_TAB = "personal_details"  # the stock tab break fieldname
HEALTH_INSURANCE_ANCHOR = "health_insurance_no"

# Where each SDG field is chained within a tab: (fieldname -> previous field).
# Frappe lays out a tab top-to-bottom, so this controls the reading order.
# The anchors are taken from the *live* Employee form, which HRMS overrides with
# its own layout - it does not match the base ERPNext employee.json.
_INSERT_AFTER = {
	# Joining tab - contract and employment terms
	"sdg_contract_date": "sdg_contract_type",
	# Joining tab - the role the employee was hired into, as distinct from the
	# role they hold now
	# Joining tab - onboarding, at the end of the tab
	# Salary tab - drives the personal exemption in the tax engine, so it belongs
	# with the pay figures rather than buried on a statutory tab
	"sdg_is_person_of_determination": "ctc",
	# Overview tab, Company Details section - org placement
	"sdg_sub_department": "branch",
	# Overview tab, basic_information - tenure, next to date_of_joining
	"sdg_years_of_tenure": "date_of_joining",
	"sdg_days_with_company": "sdg_years_of_tenure",
	# Address & Contacts tab
	"sdg_governorate": "current_address",
	# Profile tab, beside the other narrative text and above the history tables
	"sdg_job_profile": "bio",
	# its own tab, created by _ensure_file_index_tab() before this runs
	"sdg_file_index": FILE_INDEX_TAB_FIELD,
	# Personal Details tab, at the very top - the statutory identity an Egyptian
	# employment record is keyed on, so it is read before anything else
	# Personal Details tab - the rest
	# Personal Details tab, new section after the stock Health Insurance section
	# Connections tab - the penalty block, under its own section break
	"sdg_penalty": PENALTY_TAB_FIELD,
	"sdg_penalty_points": "sdg_penalty",
	"sdg_penalty_count": "sdg_penalty_points",
	"sdg_penalty_colour": "sdg_penalty_count",
	"sdg_penalty_summary": "sdg_penalty_colour",
	"sdg_penalty_indicator": "sdg_penalty_summary",
	"sdg_penalty_level": "sdg_penalty_indicator",
	# Children tab
	"sdg_children": CHILDREN_TAB_FIELD,
	"sdg_children_count": "sdg_children",
	"sdg_school_fee_total": "sdg_children_count",
	"sdg_school_fee_children": "sdg_school_fee_total",
}

# The master's Religion column holds exactly these two values (Muslim 170,
# Christian 8 across the 178 employees), so a Select fits it exactly and stops
# free text accumulating.
# The default penalty scale.  Seeded rather than left empty so the feature is
# usable on install, and because a level with no colour is a level nobody can
# see.  HR edits these records - the thresholds are data, not code.
#
# Points, not penalty counts: escalating somebody means adding a heavier penalty,
# never re-weighting the ones already recorded against them.  The top band is
# open-ended so there is no ceiling to maintain.
PENALTY_LEVELS = (
	# level_name, from_points, to_points, indicator, colour, action_required, sort_order
	("Clear", 0, None, "Green", "#4d9d4a", "", 0),
	("Minor", 1, 2, "Yellow", "#e8a33d", "Verbal warning, noted on the record", 1),
	("Warning", 3, 5, "Orange", "#e07b39", "HR review, supervisor notified", 2),
	("Serious", 6, 9, "Red", "#d64545", "Formal written warning issued", 3),
	("Critical", 10, None, "Purple", "#7d3c98", "Suspension review with HR director", 4),
)


RELIGION_OPTIONS = "\nMuslim\nChristian"
MILITARY_STATUS_OPTIONS = "\nCompleted\nExempted\nNot Required\nPostponed"
MILITARY_DOCUMENT_OPTIONS = "\nMissing\nOn File\nOut and not Returned"
# The master's "Social Insurance Status" column holds exactly these two states
# (Insured 147, Uninsured 31), but writes them in mixed case: "uninsured" on 31
# rows and "insured" on 1.  Both are normalised on the way in, so the Select is an
# exact fit rather than a set the data has to be coerced into.
SI_STATUS_OPTIONS = "\nUninsured\nInsured"

# How the self-service page's geofence judged a punch, as stored on `Employee Checkin`.  The
# same four values as `self_service.geo.STATUS_LABELS`, spelled out rather than imported:
# `self_service.geo` imports Frappe at module scope, and these option tables have to be
# readable without a site connection.  `self_service.test_self_service` asserts the two agree.
SELF_SERVICE_GEO_STATUS_OPTIONS = "\nVerified\nOutside allowed zone\nNo zone configured\nLocation unavailable"

# Leave codes that charge two days of the self-service page's vacation entitlement for one day
# taken.  Working a weekly rest day is the only such case in the Sharm vocabulary:
# you spend a rest day, and it is worth two.  Everything else is 1:1.  Named here
# rather than imported from `self_service.vacation` for the same reason as above, and
# `self_service.test_self_service` asserts the two agree.
SELF_SERVICE_DOUBLE_DAY_LEAVE_CODES = frozenset({"WD"})

# Derived from PENALTY_LEVELS rather than written out, so a band HR retunes
# cannot leave the Select rejecting its own indicator.
PENALTY_INDICATOR_OPTIONS = "\n" + "\n".join(dict.fromkeys(level[3] for level in PENALTY_LEVELS))

# Values the master writes, mapped to the Select's spelling.  Anything not listed
# here is reported rather than guessed at.
SI_STATUS_CANONICAL = {
	"insured": "Insured",
	"uninsured": "Uninsured",
}

_SELECT_OPTIONS = {
	"sdg_offer_status": OFFER_STATUS_OPTIONS,
	"sdg_religion": RELIGION_OPTIONS,
	# The four statuses below are the exact set present in the master's
	# "Military Status" column (Exempted 76, Completed 70, Not Required 24,
	# Postponed 1), so nothing has to be coerced into a fifth bucket.
	"sdg_military_status": MILITARY_STATUS_OPTIONS,
	# The master's "Military Staus Document" column has the same four values, but
	# writes "on file" in lower case on 35 of 175 rows, so the importer
	# normalises case before setting it.
	"sdg_military_document": MILITARY_DOCUMENT_OPTIONS,
	"sdg_si_status": SI_STATUS_OPTIONS,
	# Derived from PENALTY_LEVELS rather than written out, so a band that HR
	# retunes cannot leave the Select rejecting its own indicator.
	"sdg_penalty_indicator": PENALTY_INDICATOR_OPTIONS,
	# The same four values as `self_service.geo.STATUS_LABELS`, in the same order.  The
	# mapping is duplicated rather than imported because `_ensure_custom_field`
	# reads these dicts while `self_service.geo` imports Frappe at module scope; keeping
	# the option list as data here means setup.py stays importable on its own.
	"sdg_self_service_geo_status": SELF_SERVICE_GEO_STATUS_OPTIONS,
}


def normalise_si_status_values() -> dict:
	"""Bring existing `sdg_si_status` values into the Select's spelling.

	Must run before the field becomes a Select.  The master writes the column in
	mixed case ("uninsured" on 31 rows, "insured" on 1), and a Select rejects any
	value outside its options, so changing the fieldtype first would leave those
	employees unable to save at all.  Done while the field is still free text, the
	rewrite is unconstrained.

	`Data` to `Select` is an allowed in-place change in Frappe
	(`ALLOWED_FIELDTYPE_CHANGE` includes ("Data", "Select")) and both store in the
	same column, so no data is lost.  The drop-and-recreate path used for
	`MISTYPED_EMPLOYEE_FIELDS` is only correct while the DocType is empty, and
	Employee is not empty any more.

	Returns the values changed and any that are not in the option set, so an
	unrecognised value is reported rather than silently dropped.
	"""
	changed: dict[str, int] = {}
	unknown: dict[str, int] = {}
	rows = frappe.get_all("Employee", fields=["name", "sdg_si_status"], limit_page_length=0)
	for row in rows:
		value = (row.sdg_si_status or "").strip()
		if not value:
			continue
		canonical = SI_STATUS_CANONICAL.get(value.lower())
		if canonical is None:
			unknown[value] = unknown.get(value, 0) + 1
			continue
		if canonical != value:
			frappe.db.set_value("Employee", row.name, "sdg_si_status", canonical)
			changed[value] = changed.get(value, 0) + 1
	if changed:
		frappe.db.commit()
	for value, count in sorted(changed.items()):
		print(f"  sdg_si_status: {count} {value!r} -> canonical spelling")
	for value, count in sorted(unknown.items()):
		print(
			f"  sdg_si_status: WARNING {count} employees hold {value!r}, "
			"which is not one of the Select's options; left as they are",
		)
	return {"changed": changed, "unknown": unknown}


def sync_drilldown_flags() -> dict:
	"""Maintain the flags that let a set-difference be filtered on in a List View.

	Two cards count something a stock list filter cannot express:

	    active employees minus the medical register
	    companies that have a GL entry

	Each is a set held in *another* table, and Frappe has no operator for "not
	present in another doctype".  So both are reduced to a Checkbox on the row
	itself, and the card and its route then filter on exactly the same thing -
	which is the whole point.  A drill-down that lands on a different number than
	the card it came from is worse than no drill-down, because it reads as "there
	is nobody here" rather than as a broken filter.

	The August coupon card counts a workbook minus a register and has no row to
	hang a flag on - the people it is counting do not exist as rows at all.  It
	stays unrouted, and says so in `hr/dashboard_hrm.py`.

	Every row is written, not just the ones that change, so a flag cannot outlive
	the reason it was set.  Returns the counts, for the tests.
	"""
	out = {}
	if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": "sdg_no_medical_record"}):
		return out

	covered = {
		r[0] for r in frappe.db.sql("select employee from `tabSDG Insured Person` where employee is not null")
	}
	active = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit_page_length=0)
	out["employee_no_medical_record"] = _set_checkbox(
		"Employee", "sdg_no_medical_record", [n for n in active if n not in covered]
	)

	posting = {
		r[0] for r in frappe.db.sql("select distinct company from `tabGL Entry` where company is not null")
	}
	companies = frappe.get_all("Company", pluck="name", limit_page_length=0)
	out["company_posting"] = _set_checkbox("Company", "sdg_posting", [c for c in companies if c in posting])
	return out


def _set_checkbox(doctype: str, fieldname: str, ones: list) -> int:
	"""Set `fieldname` to 1 on `ones` and to 0 on every other row. Returns rows written.

	`update_modified=False` because nothing edits these by hand: bumping `modified`
	on every employee in the company on every migrate would make the flag look
	like a change of substance, and would leave a trail of edits nobody made.
	"""
	flagged = set(ones)
	rest = [n for n in frappe.get_all(doctype, pluck="name", limit_page_length=0) if n not in flagged]
	written = 0
	for names, value in ((flagged, 1), (rest, 0)):
		for name in names:
			frappe.db.set_value(doctype, name, fieldname, value, update_modified=False)
			written += 1
	return written


def create_custom_fields() -> None:
	"""Add the Employee/Designation fields the import needs and the audit fields
	the regional override writes to."""
	_check_no_duplicate_anchors()

	# before the field becomes a Select, while its values are still unconstrained
	normalise_si_status_values()

	created = 0
	for fieldname, label, fieldtype, description in SLIP_FIELDS:
		if _ensure_custom_field("Salary Slip", fieldname, label, fieldtype, description, read_only=True):
			created += 1

	if _ensure_custom_field(
		"Payroll Entry",
		"sdg_build_salary_slips",
		"Build SDG Salary Slips",
		"Button",
		"List this month's employees from the payroll spec and write their figures "
		"onto the slips. Run it after the entry is submitted and before Submit, so "
		"the figures can be checked before anything is posted.",
	):
		created += 1

	# Flags that exist only so a set-difference can be filtered on.  See
	# `sync_drilldown_flags` for why the card needs one.
	if _ensure_custom_field(
		"Employee",
		"sdg_no_medical_record",
		"No Medical Record",
		"Check",
		"Set by Sharm Dreams Group: active staff absent from the medical register "
		"(SDG Insured Person). Maintained by migrate - do not edit by hand.",
		in_list_view=True,
	):
		created += 1
	if _ensure_custom_field(
		"Company",
		"sdg_posting",
		"Posting",
		"Check",
		"Set by Sharm Dreams Group: this company has at least one GL Entry. "
		"Maintained by migrate - do not edit by hand.",
		in_list_view=True,
		# not the default Company slot: `company_name_ar` already holds it, and two
		# fields claiming one slot are ordered by whichever was created last
		insert_after="abbr",
	):
		created += 1

	_ensure_social_insurance_section()
	# Order matters: the penalties tab anchors to the file-index table, so that
	# table has to exist before it.
	_ensure_file_index_tab()
	_ensure_penalty_tab()
	# After the penalty tab: the children tab anchors to the penalty table, so
	# that table has to exist first.
	_ensure_children_tab()
	_ensure_passport_section_collapsible()
	_drop_obsolete_fields()
	_drop_mistyped_fields()

	for fieldname, label, fieldtype, description, _tab in (
		*EMPLOYEE_FIELDS,
		*DERIVED_EMPLOYEE_FIELDS,
		*SELF_SERVICE_EMPLOYEE_FIELDS,
	):
		if _ensure_custom_field(
			"Employee",
			fieldname,
			label,
			fieldtype,
			description,
			read_only=fieldname in _COMPUTED,
			in_list_view=fieldname in LISTED,
		):
			created += 1
	for fieldname, label, fieldtype, description in DESIGNATION_FIELDS:
		if _ensure_custom_field("Designation", fieldname, label, fieldtype, description):
			created += 1
	for fieldname, label, fieldtype, description in COMPANY_FIELDS:
		if _ensure_custom_field("Company", fieldname, label, fieldtype, description):
			created += 1

	# Last, because the field loop above sets insert_after from _INSERT_AFTER and
	# these are what ultimately decide the reading order of their tabs.
	reorder_joining_tab()
	reorder_personal_details_tab()

	print(f"custom fields: {created} created")


# Fields that were created and then judged to duplicate a field that already
# exists elsewhere.  Kept as a list so re-running cleans up rather than leaving
# an orphan column behind.
OBSOLETE_EMPLOYEE_FIELDS = (
	# The Social Insurance section was renamed from Medical Insurance when the
	# label changed.  The old Custom Field has to go, or the form keeps a second,
	# empty section break pointing at fields that have moved.
	"sdg_medical_insurance_section",
	# The SDG Statutory tab existed only to hold national ID, social insurance and
	# military status.  All of those moved to the Personal Details tab and the
	# remaining three moved to Joining and Salary, leaving the tab empty.
	STATUTORY_TAB_FIELD,
	# Grade: Employee already carries a stock `grade` Link to the Employee Grade
	# table, so a free-text `sdg_grade_level` was a second place for one value.
	"sdg_grade_level",
	# The Arabic job title belongs on Designation (sdg_designation_name_ar):
	# there are 108 distinct titles across 178 people, so storing it per employee
	# duplicated the same string and let the two copies drift.
	"sdg_position_arabic",
)


def _drop_obsolete_fields() -> None:
	"""Remove custom fields that have been superseded.

	Only safe while the table is empty.  Once employees are imported the column
	holds data, so the list should be emptied rather than a drop performed.
	"""
	for fieldname in OBSOLETE_EMPLOYEE_FIELDS:
		# Custom Field names are "<DocType>-<fieldname>", and delete_doc needs that
		# real name.  Passing a filters dict here reports success but deletes
		# nothing, so the name is resolved explicitly and the result verified.
		name = frappe.db.get_value("Custom Field", {"dt": "Employee", "fieldname": fieldname}, "name")
		if not name:
			continue
		if frappe.db.count("Employee"):
			frappe.throw(
				frappe._(
					"Cannot drop {0}: Employee records exist. Clear OBSOLETE_EMPLOYEE_FIELDS "
					"instead so no data is lost."
				).format(fieldname)
			)
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		frappe.clear_cache()
		if frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": fieldname}):
			frappe.throw(frappe._("Failed to remove Employee.{0}").format(fieldname))
		print(f"dropped custom field: Employee.{fieldname}")


# Fields created with the wrong fieldtype.  Frappe refuses to change a
# fieldtype in place because that would reinterpret existing data, so a field
# created as Data and later wanted as a Link has to be dropped and recreated.
# Only correct while the DocType is empty.
MISTYPED_EMPLOYEE_FIELDS = {
	# Sub Department became a vocabulary table (SDG Sub Department) once the
	# source column turned out to hold 21 fixed values, one per employee.
	"sdg_sub_department": "Link",
	# Military Status was free text and is now one of four statutory statuses.
	"sdg_military_status": "Select",
	# Religion was free text and is one of two values in the master.
	"sdg_religion": "Select",
}


def _drop_mistyped_fields() -> None:
	"""Remove fields whose fieldtype has to change, so they can be recreated."""
	for fieldname, (_label, fieldtype, _description, _tab) in _employee_field_index().items():
		if fieldname not in MISTYPED_EMPLOYEE_FIELDS:
			continue
		row = frappe.db.get_value(
			"Custom Field",
			{"dt": "Employee", "fieldname": fieldname},
			["name", "fieldtype"],
			as_dict=True,
		)
		if not row or row.fieldtype == fieldtype:
			continue
		if frappe.db.count("Employee"):
			frappe.throw(
				frappe._(
					"Employee.{0} is {1} but should be {2}, and Employee records exist. "
					"Convert the data by hand, then clear MISTYPED_EMPLOYEE_FIELDS."
				).format(fieldname, row.fieldtype, fieldtype)
			)
		frappe.delete_doc("Custom Field", row.name, ignore_permissions=True, force=True)
		frappe.clear_cache()
		print(f"dropped mistyped field: Employee.{fieldname} ({row.fieldtype} -> {fieldtype})")


def _check_no_duplicate_anchors() -> None:
	"""Fail loudly if _INSERT_AFTER lists a field twice.

	A duplicate key in a dict literal is legal Python and silently keeps the last
	one, so a field intended for one tab ends up anchored to another with no
	error at all.  That happened when Military Status was moved out of the SDG
	Statutory block and its old entry was left behind.
	"""
	import ast
	import inspect

	for node in ast.walk(ast.parse(inspect.getsource(sys.modules[__name__]))):
		if not isinstance(node, ast.Assign):
			continue
		names = [t.id for t in node.targets if isinstance(t, ast.Name)]
		if names != ["_INSERT_AFTER"]:
			continue
		if not isinstance(node.value, ast.Dict):
			continue
		keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
		duplicates = {k for k in keys if keys.count(k) > 1}
		if duplicates:
			frappe.throw(
				frappe._("Duplicate _INSERT_AFTER anchor(s): {0}").format(", ".join(sorted(duplicates))),
				title=frappe._("Layout definition is ambiguous"),
			)


def _employee_field_index() -> dict:
	"""fieldname -> (label, fieldtype, description, tab) for Employee fields."""
	index = {}
	for entry in (*EMPLOYEE_FIELDS, *DERIVED_EMPLOYEE_FIELDS):
		if len(entry) == 5:
			index[entry[0]] = entry[1:]
	return index


def _ensure_social_insurance_section() -> None:
	"""Create the social Insurance section on Employee, once.

	Social insurance number, status and date belong beside the stock Health
	Insurance section rather than on the SDG Statutory tab: they describe the same
	thing, and splitting them across two tabs meant neither was complete on its
	own.  A Custom Field cannot otherwise introduce a section, so the section is
	itself a zero-width custom field that the real fields chain off.

	It is collapsible, matching the stock Health Insurance section above it, so
	the three fields can be folded away on a form that has a lot of them.
	"""
	if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": SOCIAL_SECTION_FIELD}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Employee",
				"fieldname": SOCIAL_SECTION_FIELD,
				"label": SOCIAL_SECTION_LABEL,
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": HEALTH_INSURANCE_ANCHOR,
				"module": "Sharm Dreams Group",
			}
		).insert(ignore_permissions=True)
		print(f"created section: {SOCIAL_SECTION_LABEL}")
	else:
		doc = frappe.get_doc("Custom Field", {"dt": "Employee", "fieldname": SOCIAL_SECTION_FIELD})
		doc.label = SOCIAL_SECTION_LABEL
		doc.collapsible = 1
		doc.insert_after = HEALTH_INSURANCE_ANCHOR
		doc.save()


def _ensure_penalty_tab() -> None:
	"""Create the "Penalties" tab on Employee and put the log on it.

	Same constraint as the file-index tab: a Custom Field can introduce a tab only
	where the merged field list already has a tab to follow, and an anchor that
	does not exist on the doctype is silently appended instead. So the tab is
	anchored to a field that is definitely there - the file-index table - and the
	penalty fields are then chained off the tab.
	"""
	if frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": PENALTY_LEGACY_SECTION}):
		# It was a section break before it was a tab.  Left in place it would sit
		# ahead of the tab as an empty heading on whichever tab the fields moved to.
		frappe.delete_doc(
			"Custom Field",
			frappe.db.get_value(
				"Custom Field", {"dt": "Employee", "fieldname": PENALTY_LEGACY_SECTION}, "name"
			),
			force=True,
			ignore_permissions=True,
		)
		print(f"dropped superseded field: Employee.{PENALTY_LEGACY_SECTION}")

	if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": PENALTY_TAB_FIELD}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Employee",
				"fieldname": PENALTY_TAB_FIELD,
				"label": PENALTY_TAB_LABEL,
				"fieldtype": "Tab Break",
				"insert_after": PENALTY_TAB_ANCHOR,
				"module": "Sharm Dreams Group",
			}
		).insert(ignore_permissions=True)
		print(f"created tab: {PENALTY_TAB_LABEL} (after {PENALTY_TAB_ANCHOR})")
		return

	# re-apply the placement: an HRMS upgrade can move the anchor out from under it
	doc = frappe.get_doc("Custom Field", {"dt": "Employee", "fieldname": PENALTY_TAB_FIELD})
	doc.label = PENALTY_TAB_LABEL
	doc.insert_after = PENALTY_TAB_ANCHOR
	doc.save()


def _ensure_children_tab() -> None:
	"""Create the "Children" tab and anchor it after the penalty table.

	Same reasoning as the penalties tab: a Custom Field can introduce a tab only
	where the merged field list already has one to follow, so the anchor is
	explicit and re-applied on every migrate - an HRMS upgrade can move the
	penalties table out from under it.
	"""
	if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": CHILDREN_TAB_FIELD}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Employee",
				"fieldname": CHILDREN_TAB_FIELD,
				"label": CHILDREN_TAB_LABEL,
				"fieldtype": "Tab Break",
				"insert_after": CHILDREN_TAB_ANCHOR,
				"module": "Sharm Dreams Group",
			}
		).insert(ignore_permissions=True)
		print(f"created tab: {CHILDREN_TAB_LABEL} (after {CHILDREN_TAB_ANCHOR})")
		return

	doc = frappe.get_doc("Custom Field", {"dt": "Employee", "fieldname": CHILDREN_TAB_FIELD})
	doc.label = CHILDREN_TAB_LABEL
	doc.insert_after = CHILDREN_TAB_ANCHOR
	doc.save()


def _ensure_file_index_tab() -> None:
	"""Create the "Employee File Index" tab and put the checklist table on it.

	A Custom Field *can* introduce a tab, but only where the merged field list
	already has a tab to follow - Frappe refuses one as the first field, and an
	anchor that does not exist on the doctype silently appends to the end
	instead.  So the tab is anchored explicitly to the last stock field, and the
	table is then anchored to the tab.

	The checklist previously ended up in the Connections tab, which is where a
	field with no valid anchor lands.
	"""
	last = _last_field_before_tab("Employee", FILE_INDEX_TAB_BEFORE)
	if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": FILE_INDEX_TAB_FIELD}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Employee",
				"fieldname": FILE_INDEX_TAB_FIELD,
				"label": FILE_INDEX_TAB_LABEL,
				"fieldtype": "Tab Break",
				"insert_after": last,
				"module": "Sharm Dreams Group",
			}
		).insert(ignore_permissions=True)
		print(f"created tab: {FILE_INDEX_TAB_LABEL} (after {last})")
		return

	doc = frappe.get_doc("Custom Field", {"dt": "Employee", "fieldname": FILE_INDEX_TAB_FIELD})
	doc.label = FILE_INDEX_TAB_LABEL
	doc.insert_after = last
	doc.save()


def _last_field_before_tab(doctype: str, tab: str) -> str:
	"""The last real field before `tab`, so a new tab can be placed just above it.

	Anchoring to a named field is how a Custom Field is positioned, and Frappe
	silently appends instead when the anchor does not exist - so the anchor has
	to be the last field of the preceding tab, found by walking the merged
	metadata.

	Our own fields are excluded: the checklist table was once the last field on
	Employee, so anchoring to "the last field" put the tab *after* the table it
	was meant to contain.
	"""
	# pluck returns plain strings, not rows
	ours = set(
		frappe.get_all("Custom Field", filters={"dt": doctype}, pluck="fieldname", limit_page_length=0)
	)
	last = ""
	for field in frappe.get_meta(doctype).fields:
		is_ours = field.fieldname in ours
		if field.fieldtype == "Tab Break":
			if field.fieldname == tab or (field.label or field.fieldname) == tab:
				return last
			# Our own tab breaks are transparent here.  This tab is anchored
			# relative to a stock one, and once it is in place the walk passes
			# through it on the way to "exit" - treating it as a boundary wiped
			# `last`, the anchor came back empty, and Frappe put the tab first.
			if is_ours:
				continue
			last = ""
			continue
		if is_ours:
			continue
		if field.fieldtype in ("Column Break", "HTML", "Button", "Fold", "Flex Space"):
			continue
		last = field.fieldname
	return last


def create_slab_fixed_amount() -> None:
	"""The one field the Egyptian bracket needs and `Income Tax Slab` lacks.

	Egypt charges a progressive table where each band starts from a fixed amount
	already due at the bottom of the band, not from zero: 22.5% applies only to the
	portion above 200,000, and the 29,750 below it is carried forward.  Stock
	`Taxable Salary Slab` is `from / to / percent`, which cannot express that, so
	`sdg_fixed_amount` holds the cumulative tax at `from_amount`.

	Named with the app prefix because it lives on a stock DocType's child table:
	an unprefixed name on someone else's table is a collision waiting to happen.
	"""
	_ensure_custom_field(
		"Taxable Salary Slab",
		"sdg_fixed_amount",
		"Tax At Band Start",
		"Currency",
		"Cumulative tax due at the bottom of this band. Egypt's bracket is "
		"progressive from a base, not from zero: tax = this + (income - "
		"from_amount) x percent. Leave at 0 for a table that starts at zero.",
	)


def create_leave_type_fields() -> None:
	"""The three fields the Sharm leave vocabulary needs and `Leave Type` lacks.

	The group's twelve leave types are keyed by a short code the HR team writes on
	the vacation tracker - `V`, `LL`, `CL`, `SL`, `DO` - and two of them are not in
	the DocType at all:

	- `sdg_code` is the short code. Stock `Leave Type` is autonamed by
	  `leave_type_name`, so the code has nowhere to live, and the code is what the
	  vacation tracker and the payroll spec both use.
	- `sdg_is_paid` and `sdg_affects_deduction` are how the payroll decides a day
	  of absence. Stock has `is_lwp`, but that is a *rule* - set it and HRMS starts
	  deducting unpaid leave itself - and the payroll computes absence from the
	  workbook's own columns.  Filling in `is_lwp` would make two systems deduct
	  for the same day, so the flags are stored and deliberately not wired to it.

	These are reference data, not behaviour: after this, the twelve types are
	visible and reportable in the desk, and the payroll still reads the workbook.
	"""
	_ensure_custom_field(
		"Leave Type",
		"sdg_code",
		"Sharm Code",
		"Data",
		"Short code the HR team uses on the vacation tracker (V, LL, CL, SL, DO...). "
		"Stock Leave Type is named by its full name, so the code needs a field.",
		in_list_view=True,
	)
	_ensure_custom_field(
		"Leave Type",
		"sdg_is_paid",
		"Paid Leave (Sharm)",
		"Check",
		"Whether this type is paid, per the payroll workbook. Stored only: the "
		"payroll reads the workbook, so this does not drive a deduction.",
	)
	_ensure_custom_field(
		"Leave Type",
		"sdg_tracker_name",
		"Tracker Name (Arabic)",
		"Data",
		"The column heading this type carries in SharmDreamVacation Tracker.xlsx. "
		"The HR team reads the tracker in Arabic, so the desk should show the same "
		"word they will be looking for.",
	)
	_ensure_custom_field(
		"Leave Type",
		"sdg_affects_deduction",
		"Reduces Pay (Sharm)",
		"Check",
		"Whether a day of this type reduces pay, per the payroll workbook. Stored "
		"only, and deliberately not mapped to is_lwp: HRMS deducting unpaid leave "
		"on top of the payroll would double-count the same day.",
	)
	_ensure_custom_field(
		"Leave Type",
		"sdg_vacation_day_multiplier",
		"Vacation Days Multiplier",
		"Float",
		"Paid days this type charges against the self-service page's vacation balances. 2 for "
		"Working Rest Day and Working Public Holiday, which burn two days of "
		"allowance for one day off; 1 for everything else. Read by "
		"`self_service.vacation.get_vacation_used_by_code`.",
	)


# ------------------------------------------------------------------------------
# Employee self-service
# ------------------------------------------------------------------------------
# `Employee Checkin` is HRMS's own attendance record, so the geofence outcome is
# stored on the checkin rather than in a self-service page table: it has to be readable
# alongside the attendance it qualifies, and it has to survive the self-service page app being
# removed. Read-only - the self-service page writes them, nobody edits them.


def create_self_service_fields() -> None:
	"""The two Employee Checkin columns the self-service page's punch writes."""
	created = 0
	if _ensure_custom_field(
		"Employee Checkin",
		"sdg_self_service_geo_status",
		"Self Service Geofence Result",
		"Select",
		"How the self-service page geofence judged this punch. Written by "
		"`self_service.api.punch`, read-only. Empty for a checkin made from the desk or "
		"a device, which HRMS records without a location check.",
		in_list_view=True,
	):
		created += 1
	if _ensure_custom_field(
		"Employee Checkin",
		"sdg_self_service_geo_message",
		"Self Service Geofence Detail",
		"Data",
		"Why the geofence judged it that way, in the words shown to the employee. "
		"Written by `self_service.api.punch`, read-only.",
	):
		created += 1
	if created:
		print(f"self-service checkin fields: {created} created")


def create_self_service_defaults() -> None:
	"""Seed `Employee Self Service Settings` and the leave-type day multipliers.

	Geofence enforcement starts **off**, and that is the one default here that is a
	judgement rather than a copy of source data.  It cannot start on: a punch zone
	is a centre point and a radius, `Company` has no latitude or longitude field,
	and no coordinate for any of the thirteen group companies exists in the master
	workbook or the seed data.  Seeding a zone would mean inventing a number, and
	enforcement on with a wrong centre refuses every punch at the real office.

	So the self-service page installs usable - coordinates are recorded on every checkin
	whatever the outcome - and reports what is left to do.  HR adds a `Self Service
	Location` per site and turns `geo_punch_enforce` on, after which
	`self_service.geo.is_enforced` takes over.  Note the asymmetry, which is deliberate:
	that helper fails *closed* to enforced when the setting cannot be read at all,
	so an unreachable database refuses punches, but an explicitly-off setting is an
	instruction and is obeyed.

	The default leave approver is left blank.  Several people hold the payroll roles
	and picking one silently would route a worker's leave to a colleague; with it
	blank, a self-service page employee who has no approver of their own fails with a message
	telling them to contact HR, which is the honest outcome.

	The multipliers are seeded from the leave vocabulary: a working rest day burns
	two days of allowance, everything else one.  Without them the self-service page would
	understate usage on exactly the bucket that is easiest to get wrong.
	"""
	# A Single's values live as rows in tabSingles, so `set_single_value` creates
	# what it needs - but only *unset* values are written, or a re-run would undo
	# whatever HR has since turned off.
	if frappe.db.get_single_value("Employee Self Service Settings", "geo_punch_enforce") is None:
		frappe.db.set_single_value("Employee Self Service Settings", "geo_punch_enforce", 0)

	seeded = 0
	for leave_type in frappe.get_all(
		"Leave Type", fields=["name", "sdg_code"], limit_page_length=0
	):
		if not leave_type.sdg_code:
			continue
		wanted = 2.0 if leave_type.sdg_code in SELF_SERVICE_DOUBLE_DAY_LEAVE_CODES else 1.0
		current = frappe.db.get_value("Leave Type", leave_type.name, "sdg_vacation_day_multiplier")
		if cint(current or 0) == cint(wanted):
			continue
		frappe.db.set_value(
			"Leave Type",
			leave_type.name,
			"sdg_vacation_day_multiplier",
			wanted,
			update_modified=False,
		)
		seeded += 1

	if frappe.db.get_single_value("Employee Self Service Settings", "geo_punch_enforce"):
		zones = frappe.db.count("Employee Self Service Location", {"active": 1})
		print(
			f"self-service: geofencing is ON with {zones} active punch zone(s) - "
			"a company with no zone cannot be punched from"
		)
	else:
		print(
			"self-service: geofencing is off (no per-company coordinates exist to seed a "
			"punch zone from). Punches are recorded with their coordinates but not "
			"checked - add a Employee Self Service Location per site, then turn on "
			"'Enforce Punch Location' in Employee Self Service Settings"
		)
	print(f"self-service leave multipliers: {seeded} set")


def _ensure_custom_field(
	dt: str,
	fieldname: str,
	label: str,
	fieldtype: str,
	description: str,
	read_only: bool = False,
	in_list_view: bool = False,
	insert_after: str | None = None,
) -> bool:
	"""Create or refresh one Custom Field. True if it was created.

	`insert_after` wins over every rule below it. The blanket `dt == "Company"`
	rule exists only because that is where a Company field has to sit, but two
	fields claiming the same slot are ordered by whichever was created last - which
	is how `sdg_posting` took `company_name`'s place and pushed `company_name_ar`
	off the row directly beneath it. Pass a slot explicitly when the default would
	collide with another field's.
	"""
	values = {
		"doctype": "Custom Field",
		"dt": dt,
		"fieldname": fieldname,
		"label": label,
		# The image view builds each tile from `meta.fields` where `in_list_view`
		# is set - not from List View Settings - so a field only ever reaches the
		# tiles if this is on.  It also puts the field in the desk's default column
		# set, which is why only the level and the points carry it and the colour,
		# summary and indicator do not.
		"in_list_view": 1 if in_list_view else 0,
		"fieldtype": fieldtype,
		"description": description,
		"read_only": 1 if read_only else 0,
		"module": "Sharm Dreams Group",
	}
	if fieldtype == "Link":
		values["options"] = _LINK_OPTIONS.get(fieldname, "")
	if fieldtype == "Select":
		values["options"] = _SELECT_OPTIONS.get(fieldname, "")
	if fieldtype == "Table":
		values["options"] = _TABLE_OPTIONS.get(fieldname, "")
	if fieldtype == "Button":
		values["options"] = "build_for_entry"

	if insert_after:
		values["insert_after"] = insert_after
	elif dt == "Company":
		values["insert_after"] = "company_name"
	elif fieldname in REORDER_OWNED and dt == "Employee":
		# positioned by reorder_fields_in_tab; leave insert_after to it
		values.pop("insert_after", None)
	elif fieldname in _INSERT_AFTER:
		after = _INSERT_AFTER[fieldname]
		# a fieldname maps to a Custom Field; the stock target is an Employee field
		if after and not frappe.db.exists("Custom Field", {"dt": dt, "fieldname": after}):
			if not frappe.get_meta(dt).has_field(after):
				after = None
		if dt == "Employee":
			values["insert_after"] = after or "date_of_joining"
		else:
			values["insert_after"] = after or "designation_name"
	elif dt == "Salary Slip":
		values["insert_after"] = "total_deduction"
	elif frappe.get_meta(dt).has_field("description"):
		# Designation has only designation_name and description
		values["insert_after"] = "description"
	else:
		# Append.  The old fallback named `designation_name`, which Employee does
		# not have, so the field was silently appended and landed in the last tab
		# (Connections) instead of where its spec said.
		values["insert_after"] = "append"

	if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
		# Relayout has to be a real save.  `db.set_value` writes `insert_after`
		# but never recomputes `idx`, so the desk would keep rendering the old
		# position.  Only `doc.save()` re-orders the merged field list.
		doc = frappe.get_doc("Custom Field", {"dt": dt, "fieldname": fieldname})
		doc.update({k: v for k, v in values.items() if k != "doctype"})
		doc.save()
		return False

	frappe.get_doc(values).insert(ignore_permissions=True)
	print(f"created custom field: {dt}.{fieldname}")
	return True


def verify_hook_key() -> None:
	"""Prove the Egypt regional override actually matches, and points at real code.

	This exists because getting the key wrong fails *silently*.  `hrms.allow_regional`
	builds it as `module.function` with no class name and compares verbatim, so a
	typo means the override never fires and no tax is calculated - with no error
	anywhere.  Rebuilding the key the same way HRMS does turns that into a loud
	failure.
	"""
	import inspect

	from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip

	# unwrap the decorator to reach the undecorated function
	inner = getattr(SalarySlip.apply_regional_deductions, "__wrapped__", SalarySlip.apply_regional_deductions)
	expected = f"{inspect.getmodule(inner).__name__}.{inner.__name__}"

	overrides = frappe.get_hooks("regional_overrides", {}).get("Egypt", {})
	print(f"expected key : {expected}")
	print(f"hook keys    : {list(overrides)}")

	if expected not in overrides:
		stale = [k for k in overrides if k != expected]
		frappe.throw(
			frappe._(
				"The Egypt regional override will not fire. HRMS looks up {0}, but hooks.py "
				"registers {1}. Income tax would be silently zero."
			).format(expected, stale or "nothing"),
			title=frappe._("Regional override key does not match"),
		)

	target = overrides[expected][-1]
	module_name, func_name = target.rsplit(".", 1)
	resolved = getattr(__import__(module_name, fromlist=[func_name]), func_name)
	print(f"target       : {target}")
	print(f"resolved     : {resolved}")
	print("OK           : the override fires")

	for stale in (k for k in overrides if k != expected):
		print(f"WARNING      : stale key still registered, {stale}")


def probe() -> None:
	"""Low level check of what actually landed in the database."""
	print("installed_apps:", frappe.get_installed_apps())
	print()
	print("DocType 'Module' exists:", bool(frappe.db.exists("DocType", "Module")))
	print()
	# NB: the Module DocType was removed in Frappe v16 - modules are folder names
	# from modules.txt, tracked per app by the app itself.
	cols = set(frappe.db.get_table_columns("DocType"))
	print("tabDocType has 'module' column:", "module" in cols)
	print("tabDocType has 'app' column    :", "app" in cols)
	print()
	dts = frappe.db.sql(
		"select name, module, issingle, istable from tabDocType where module = %s order by name",
		"Sharm Dreams Group",
		as_dict=True,
	)
	print(f"tabDocType in 'Sharm Dreams Group': {len(dts)}")
	for d in dts:
		print(f"   {d['name']:32} single={d['issingle']} istable={d['istable']}")


def verify() -> None:
	"""Smoke test: the app is installed, the module and doctypes resolve, and
	the Egypt regional override is wired up."""
	print("installed:", "sharm_dreams_group" in frappe.get_installed_apps())
	apps = {
		d.app
		for d in frappe.get_all(
			"DocType", filters={"module": "Sharm Dreams Group"}, fields=["app"], distinct=True
		)
	}
	print(f"app      : {sorted(apps)}")
	print()

	doctypes = frappe.get_all(
		"DocType",
		filters={"module": "Sharm Dreams Group"},
		fields=["name", "istable", "issingle"],
		order_by="name",
	)
	print(f"doctypes : {len(doctypes)}")
	for d in doctypes:
		kind = "Single" if d.issingle else ("Child" if d.istable else "Doc")
		meta = frappe.get_meta(d.name)
		real = [
			f
			for f in meta.fields
			if f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML")
		]
		print(f"   {d.name:32} {kind:6} {len(real):2} fields")
	print()

	import hrms

	print(f"region   : {hrms.get_region('Concord For Touristic Development')}")
	print()
	print("salary components written by the tax engine:")
	for name in sorted(REQUIRED_COMPONENTS):
		exists = bool(frappe.db.exists("Salary Component", name))
		kind = frappe.db.get_value("Salary Component", name, "type") if exists else "-"
		print(f"   {'yes' if exists else 'NO '}  {name:34} {kind}")
	print()
	verify_hook_key()


def describe_company_layout() -> None:
	"""Print the Company form's field order, marking the SDG fields.

	`company_name_ar` has to sit directly under `company_name`; the desk renders
	fields in idx order, so the only way to confirm that is to read it back.
	"""
	names = [
		f.fieldname
		for f in frappe.get_meta("Company").fields
		if f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button")
	]
	print("=== Company form layout ===")
	for name in names[:24]:
		mark = "   <== SDG" if name.endswith("_ar") else ""
		print(f"    {name}{mark}")
	if "company_name" in names and "company_name_ar" in names:
		adjacent = names.index("company_name_ar") == names.index("company_name") + 1
		print(f"\n  company_name_ar directly under company_name: {adjacent}")
		print(
			f"  companies with an Arabic name: "
			f"{frappe.db.count('Company', {'company_name_ar': ('is', 'set')})} of "
			f"{frappe.db.count('Company')}"
		)


def describe_employee_layout() -> None:
	"""Print the Employee form's tab/section layout, marking the SDG fields.

	Useful after changing `insert_after` in `_ensure_custom_field`, because the
	only way to confirm a field landed on the intended tab is to read back the
	merged metadata order that the desk actually renders.
	"""
	meta = frappe.get_meta("Employee")
	tab = "(no tab)"
	print("=== Employee form layout ===")
	for f in meta.fields:
		if f.fieldtype == "Tab Break":
			tab = f.label or f.fieldname
			print(f"\n=== TAB: {tab} ===")
		elif f.fieldtype in ("Section Break", "Table"):
			extra = ""
			if f.fieldtype == "Section Break" and f.collapsible:
				extra = "   (collapsible)"
			print(f"    -- {f.fieldtype}: {f.label or f.fieldname}{extra}")
		elif f.fieldtype in ("Column Break", "HTML", "Button", "Fold"):
			continue
		else:
			if not f.fieldname.startswith("sdg_"):
				print(f"    {f.fieldname}")
				continue
			after = frappe.db.get_value(
				"Custom Field", {"dt": "Employee", "fieldname": f.fieldname}, "insert_after"
			)
			print(f"    {f.fieldname}   <== SDG  (insert_after={after})")

	print("\n=== SDG Statutory tab contents ===")
	statutory = [f.fieldname for f in meta.fields if f.fieldname.startswith("sdg_")]
	seen = []
	in_tab = False
	for f in meta.fields:
		if f.fieldtype == "Tab Break":
			in_tab = (f.label or f.fieldname) == STATUTORY_TAB_LABEL
			continue
		if in_tab and f.fieldname.startswith("sdg_"):
			seen.append(f.fieldname)
	print("  " + (", ".join(seen) if seen else "(none)"))
	print(f"  all {len(statutory)} sdg_ fields on Employee: {len(statutory)}")

	print("\n=== Select fields on Employee ===")
	for row in frappe.get_all(
		"Custom Field",
		filters={"dt": "Employee", "fieldtype": "Select"},
		fields=["fieldname", "options"],
		limit_page_length=0,
	):
		options = [o for o in (row.options or "").split("\n") if o]
		print(f"  {row.fieldname:24} {options}")


def _ensure_passport_section_collapsible() -> None:
	"""Make the stock Passport Details section collapsible.

	This one is not a custom field, so it cannot be created the way the Medical
	Insurance section was.  The Employee DocType's own field list has to be edited.

	That is a customization of a doctype owned by ERPNext, which means an ERPNext
	upgrade can put it back.  It is therefore re-applied here on every
	`create_custom_fields` run, and `test_employee` asserts the result, so a
	revert is visible rather than silent.
	"""
	dt = frappe.get_doc("DocType", "Employee")
	target = next((f for f in dt.fields if f.fieldname == "passport_details_section"), None)
	if target is None:
		frappe.throw(
			frappe._(
				"Employee has no passport_details_section. ERPNext's layout has "
				"changed and this customization needs revisiting."
			)
		)
	if target.collapsible:
		return
	target.collapsible = 1
	dt.save()
	frappe.clear_cache()
	print("made the stock Passport Details section collapsible")


# Reading order for the Joining tab (DocType fieldname `employment_details`).
# Grouped so the form is filled in the order a hire actually progresses:
#   are they on board -> confirmation milestones -> contract terms -> the role
#   they were hired into -> how the relationship can end.
# Stock fields are listed alongside the SDG ones because reordering a tab means
# moving the whole block, not just the fields this app added.
JOINING_TAB_ORDER = (
	"sdg_offer_status",
	"sdg_status_date",
	"job_applicant",
	"scheduled_confirmation_date",
	"final_confirmation_date",
	"sdg_contract_type",
	"sdg_contract_date",
	"contract_end_date",
	"notice_number_of_days",
	"sdg_employment_category",
	"sdg_starting_designation",
	"date_of_retirement",
)


def reorder_fields_in_tab(doctype: str, tab_fieldname: str, order: tuple) -> None:
	"""Put the fields of one tab into `order`, leaving other tabs untouched.

	Two mechanisms are needed, because a tab's fields live in two places:

	*  Stock fields are listed on the DocType itself, so their order is set by
	   rewriting that list and letting Frappe recompute `idx`.
	*  Custom fields are not on the DocType at all - they are merged in at read
	   time and positioned by their own `insert_after`.  So each one is anchored
	   to whichever field precedes it in `order`, which is the tab break when it
	   comes first.

	Column and section breaks carry no data and are left where they are; moving
	them would change the form's visual grouping for no benefit.

	The tab's data fields must match `order` exactly.  A partial or surplus match
	is refused rather than applied, because silently losing a field off a payroll
	form is far worse than leaving the order alone.
	"""
	ignore = ("Column Break", "Section Break", "Tab Break", "HTML", "Button", "Fold")

	dt = frappe.get_doc("DocType", doctype)
	fields = list(dt.fields)
	names = [f.fieldname for f in fields]
	if tab_fieldname not in names:
		frappe.throw(frappe._("{0} has no tab called {1}.").format(doctype, tab_fieldname))

	start = names.index(tab_fieldname) + 1
	end = len(fields)
	for index in range(start, len(fields)):
		if fields[index].fieldtype == "Tab Break":
			end = index
			break

	# A custom field may be a layout field (a Section Break).  Those are not part
	# of the data-field comparison, but they do have to appear in `order` so the
	# chain that positions each field does not jump a field above the section it
	# belongs to.
	layout = {
		name
		for name in order
		if _custom_fieldtype(doctype, name) in ("Section Break", "Column Break", "Tab Break")
	}

	# the tab's data fields: stock ones are on the DocType, custom ones are not
	stock_block = [f for f in fields[start:end] if f.fieldtype not in ignore]
	actual = [f.fieldname for f in stock_block]
	custom = [
		name
		for name in order
		if name not in layout and frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": name})
	]
	actual_all = actual + custom

	# A layout entry is listed in `order` but is not a data field, so it is
	# excluded from both sides of the comparison rather than counted as unknown.
	ordered_data = set(order) - layout
	missing = set(actual_all) - ordered_data
	unknown = ordered_data - set(actual_all)
	if missing or unknown or len(actual_all) != len(set(actual_all)):
		frappe.throw(
			frappe._(
				"Cannot reorder {0}: the tab holds {1} data fields but {2} were listed. "
				"Not in the list: {3}. Not in the tab: {4}."
			).format(
				tab_fieldname,
				len(actual_all),
				len(ordered_data),
				", ".join(sorted(missing)) or "none",
				", ".join(sorted(unknown)) or "none",
			),
			title=frappe._("Field list does not match the tab"),
		)

	# 1. rewrite the stock order, keeping the layout breaks in their existing slots
	stock_positions = [i for i, f in enumerate(fields[start:end]) if f.fieldtype not in ignore]
	by_name = {f.fieldname: f for f in stock_block}
	desired_stock = [name for name in order if name in by_name]
	changed = [
		n
		for slot, n in zip(stock_positions, desired_stock, strict=False)
		if fields[start + slot].fieldname != n
	]
	if changed:
		for slot, name in zip(stock_positions, desired_stock, strict=False):
			fields[start + slot] = by_name[name]
		dt.fields = fields
		dt.save()

	# 2. anchor each custom field to its predecessor in the desired order.
	#    The current value is read straight from the table rather than off a
	#    loaded document: get_doc can serve a pre-save cached copy, which made
	#    every run believe it had work to do and rewrote the same rows forever.
	previous = tab_fieldname
	for name in order:
		if name in layout:
			# a section break anchors the section, and the field after it anchors
			# to the break so it renders inside the section
			previous = name
			continue
		rows = frappe.get_all(
			"Custom Field",
			filters={"dt": doctype, "fieldname": name},
			pluck="insert_after",
			limit=1,
		)
		if rows:
			# get_all + pluck is used rather than db.get_value, which on this
			# doctype returned insert_after values belonging to other DocTypes -
			# a Custom Field lookup that ignored the dt filter.
			if rows[0] != previous:
				field = frappe.get_doc("Custom Field", {"dt": doctype, "fieldname": name})
				field.insert_after = previous
				field.save()
				changed.append(name)
		previous = name

	frappe.clear_cache()
	if changed:
		print(f"{doctype}.{tab_fieldname}: reordered {len(changed)} field(s) {changed}")
	else:
		print(f"{doctype}.{tab_fieldname}: already in order")


# Reading order for the Personal Details tab (DocType fieldname `personal_details`).
# Identity first, because that is what an Egyptian employment record is keyed on;
# then the personal facts in the order they are usually asked for; then health;
# then the three document sections.  The two SDG section breaks appear in the list
# so the social-insurance fields stay inside the Social Insurance section.
PERSONAL_DETAILS_TAB = "personal_details"

PERSONAL_DETAILS_ORDER = (
	# identity
	"sdg_national_id",
	"sdg_national_id_expiry",
	"sdg_name_arabic",
	# personal facts
	"marital_status",
	"sdg_dependants_count",
	"family_background",
	"sdg_religion",
	"sdg_military_status",
	"sdg_military_document",
	"sdg_age",
	# health
	"blood_group",
	"health_details",
	# insurance, then identity documents
	"health_insurance_provider",
	"health_insurance_no",
	SOCIAL_SECTION_FIELD,
	"sdg_si_number",
	"sdg_si_status",
	"sdg_si_date",
	"passport_number",
	"valid_upto",
	"date_of_issue",
	"place_of_issue",
)


def _custom_fieldtype(doctype: str, fieldname: str) -> str:
	"""Fieldtype of a custom field, or "" when there is not one.

	`frappe.db.get_value` is avoided throughout this module's layout code: on the
	Custom Field doctype it returns values belonging to a different DocType, so a
	filter on `dt` is not honoured.  get_all + pluck is reliable.
	"""
	rows = frappe.get_all(
		"Custom Field",
		filters={"dt": doctype, "fieldname": fieldname},
		pluck="fieldtype",
		limit=1,
	)
	return rows[0] if rows else ""


# Fields whose position is owned by a tab reorder rather than by _INSERT_AFTER.
# Two mechanisms writing the same value just undo each other on every run, so
# each anchor is written in exactly one place.
REORDER_OWNED = frozenset(JOINING_TAB_ORDER) | frozenset(PERSONAL_DETAILS_ORDER)


def reorder_joining_tab() -> None:
	reorder_fields_in_tab("Employee", JOINING_TAB, JOINING_TAB_ORDER)


def reorder_personal_details_tab() -> None:
	reorder_fields_in_tab("Employee", PERSONAL_DETAILS_TAB, PERSONAL_DETAILS_ORDER)


# The indicators are the level names' colours, so the Select's options are the
# distinct indicators the scale actually uses.


def create_penalty_levels() -> None:
	"""Seed the default penalty bands.  Idempotent, and never overwrites an edit.

	A level that HR has already retuned is left exactly as it is: this only fills
	in the bands that do not exist yet, so running it after install cannot undo a
	threshold somebody changed.
	"""
	created = 0
	existing = set(frappe.get_all("SDG Penalty Level", pluck="level_name", limit_page_length=0))
	for name, from_points, to_points, indicator, colour, action, order in PENALTY_LEVELS:
		if name in existing:
			continue
		values = {
			"doctype": "SDG Penalty Level",
			"level_name": name,
			"from_points": from_points,
			"indicator": indicator,
			"colour": colour,
			"action_required": action,
			"sort_order": order,
		}
		# An open-ended band leaves `to_points` out, which the table stores as 0;
		# hr/penalty.py reads 0 as "no upper bound" rather than a real ceiling.
		if to_points is not None:
			values["to_points"] = to_points
		frappe.get_doc(values).insert(ignore_permissions=True)
		created += 1
	if created:
		print(f"  penalty levels: {created} created")


# ---------------------------------------------------------------------------
# the penalty desk: list columns and one Number Card per level
# ---------------------------------------------------------------------------
# The level has to be visible somewhere other than the employee's own form: a
# manager does not open 182 employees to find out who is at Serious.  Two places -
# a column on the Employee list, and a card per level on a dashboard.
#
# The cards are created from the levels that exist rather than from PENALTY_LEVELS,
# so a level HR adds later gets its own card on the next migrate instead of having
# to be coded in.
PENALTY_CARDS_METHOD = "sharm_dreams_group.hr.penalty.headcount"
# The card that is not a level: everybody with at least one penalty on record.
PENALTY_TOTAL_CARD = ("With a Penalty", {"total": True})


def create_penalty_cards() -> None:
	"""One Number Card per configured level, plus a total.  Idempotent.

	A card's colour comes from its level, so the dashboard strip and the Employee
	list are coloured from one place and cannot drift apart.  A level that no
	longer exists has its card removed, rather than being left showing zero -
	which would read as "nobody is at this level" rather than "this level is gone".
	"""
	from sharm_dreams_group.hr.penalty import bands, level_colour

	wanted = [(lv.name, lv.level_name, level_colour(lv.name)) for lv in bands()]
	wanted.append((None, PENALTY_TOTAL_CARD[0], "#8d99ae"))

	existing = {
		row.name: row
		for row in frappe.get_all(
			"Number Card",
			filters={"method": PENALTY_CARDS_METHOD},
			fields=["name", "label", "color"],
			limit_page_length=0,
		)
	}
	wanted_labels = {label for _n, label, _c in wanted}

	created = 0
	for level, label, colour in wanted:
		filters = {"level": level} if level else PENALTY_TOTAL_CARD[1]
		if label in {row.label for row in existing.values()}:
			doc = frappe.get_doc("Number Card", existing[label].name)
			changed = False
			if not cint(doc.is_public):
				doc.is_public = 1
				changed = True
			# These are headcounts - one card per level, plus a total. `currency`
			# is the field the card form and the API read to say whether a figure is
			# an amount, and `Number Card.currency` defaults to EGP, so a card that
			# does not say otherwise comes out recorded as money. Passing
			# `currency: None` is not enough on insert, because `_set_defaults` fills
			# a default into any field left falsy, so it is cleared once the doc
			# exists - exactly as `_upsert_card` does, and for the same reason.
			#
			# Nothing on screen changes either way: a Custom card whose method returns
			# a bare number never reaches `set_formatted_number`, so no card on this
			# site draws a symbol. This is about the record being right.
			if doc.currency:
				doc.currency = None
				changed = True
			if changed:
				doc.save(ignore_permissions=True)
			continue
		card = frappe.get_doc(
			{
				"doctype": "Number Card",
				"label": label,
				"type": "Custom",
				"method": PENALTY_CARDS_METHOD,
				"filters_json": json.dumps(filters),
				"color": colour,
				# Public: the workspace is public, and a card no other role can
				# see answers to nobody.
				"is_public": 1,
				"is_standard": 0,
			}
		)
		card.insert(ignore_permissions=True)
		if card.currency:
			card.currency = None
			card.save(ignore_permissions=True)
		created += 1

	removed = 0
	for name, row in existing.items():
		if row.label not in wanted_labels:
			frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)
			removed += 1

	if created or removed:
		print(f"penalty cards: {created} created, {removed} removed, {len(wanted)} wanted")
	else:
		print(f"penalty cards: all {len(wanted)} present")


def create_dashboard_cards() -> None:
	"""The workforce Number Cards and Charts for the Employee dashboard.

	Idempotent, and it removes what it no longer wants: a card for a metric that
	has been deleted would keep answering with a figure nothing on the desk can
	explain, which is worse than no card.

	Charts are declarative `Group By` / `Sum` rather than `Custom`.  A Dashboard
	Chart in this Frappe version builds its query from `document_type` even for
	`chart_type = Custom`, so a bespoke Python method is not reachable from a
	chart - only from a Number Card, which does forward its method.
	"""
	import json

	from sharm_dreams_group.hr import dashboard

	# --- Stock charts this app reuses rather than re-declares -------------
	#
	# A `Group By` chart of ours is one line in the list below, and there is no
	# reason for it to be ours: HRMS already ships
	# `Designation Wise Employee Count`, scoped to active staff. Declaring our own
	# would be a second definition of a figure HRMS owns, free to drift from it.
	#
	# Reused charts are *named by a section*, not written - `create_dashboard` puts a
	# named chart on the dashboard's `charts` child table if the document exists, so
	# naming one is enough - and `_prune_our_charts` only deletes charts this module
	# owns, so a reused chart cannot be pruned by accident.
	#
	# Every entry was checked for data before being listed, and by fill rate rather
	# than by row count: a `Group By` on a field that is null on every row draws one
	# empty slice, which reads as a fault rather than as "not yet". That is why
	# `Designation Wise Employee Count` is here (designation filled on 179 of 180
	# active staff) and `Employees by Grade` is not (grade is null on all 310).
	#
	# `Employees by Age` is the only `Custom` chart on this list, and a Custom chart
	# has no `group_by_based_on` to filter a list on - so it needs `custom_options`
	# written before it is clickable at all. See
	# `create_reused_custom_chart_drilldowns`.
	#
	# `dashboard.REUSED_CHARTS`, not a bare name: the constant lives in
	# `hr/dashboard.py` with the rest of the workforce dashboard spec, and this
	# module imports that module as `dashboard` above.
	tune_reused_charts()

	# --- Number Cards, one per metric -----------------------------------
	existing_cards = {
		row.label: row.name
		for row in frappe.get_all(
			"Number Card",
			filters={"method": dashboard.METRIC_METHOD},
			fields=["name", "label"],
			limit_page_length=0,
		)
	}
	created_cards, removed_cards = 0, 0
	for spec in dashboard.metric_definitions():
		filters = json.dumps({"metric": spec["key"]})
		# keyed by label, so it must be compared with the label - comparing the
		# metric key never matched and every run created a fresh set of cards.
		if spec["label"] in existing_cards:
			# keep the label, colour and filters in step with the definition
			doc = frappe.get_doc("Number Card", existing_cards[spec["label"]])
			changed = False
			if doc.label != spec["label"]:
				doc.label = spec["label"]
				changed = True
			if doc.color != spec["color"]:
				doc.color = spec["color"]
				changed = True
			if doc.filters_json != filters:
				doc.filters_json = filters
				changed = True
			if not cint(doc.is_public):
				doc.is_public = 1
				changed = True
			# `currency` says a figure is an amount, and nothing checks what the
			# number is, so leaving it on a headcount records a count of people as
			# money.  Only the money metrics carry one.
			if (doc.currency or None) != spec.get("currency"):
				doc.currency = spec.get("currency")
				changed = True
			if changed:
				doc.save(ignore_permissions=True)
			continue
		frappe.get_doc(
			{
				"doctype": "Number Card",
				"label": spec["label"],
				"type": "Custom",
				"method": dashboard.METRIC_METHOD,
				"filters_json": filters,
				"color": spec["color"],
				"module": "Sharm Dreams Group",
				"currency": spec.get("currency"),
				# Public so every role sees it; the workspace is public, and a
				# card that is invisible to payroll users answers to nobody.
				"is_public": 1,
				"is_standard": 0,
			}
		).insert(ignore_permissions=True)
		# The currency default, which `_upsert_card` also has to undo - see
		# `_clear_defaulted_currency`. It is duplicated here rather than shared
		# because this loop and `_sync_cards` are two copies of one thing: same
		# insert-or-update keyed by label, same fields compared. That was true
		# before this line and it is how the workforce copy lost the currency fix
		# the domain copy had. The one-line call is the cheaper of the two
		# remedies; collapsing the loops is the right one and is not this change.
		_clear_defaulted_currency(frappe.get_doc("Number Card", spec["label"]), spec.get("currency"))
		created_cards += 1

	# --- Charts, declarative ---------------------------------------------
	# (chart_name, doctype, chart_type, group-by/count field, value field, filters)
	charts = [
		(
			"Headcount by Company",
			"Employee",
			"Group By",
			"company",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Leavers by Company",
			"Employee",
			"Group By",
			"company",
			None,
			[["Employee", "status", "=", "Left"]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Headcount by Department",
			"Employee",
			"Group By",
			"department",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Social Insurance Status",
			"Employee",
			"Group By",
			"sdg_si_status",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Donut"},
		),
		(
			"Employment Category",
			"Employee",
			"Group By",
			"sdg_employment_category",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Donut"},
		),
		(
			"Medical Cover by Band",
			"SDG Insured Person",
			"Group By",
			"medical_band",
			None,
			[],
			{"type": "Donut"},
		),
		(
			"Medical Cover by Family Band",
			"SDG Insured Person",
			"Group By",
			"family_band",
			None,
			[],
			{"type": "Donut"},
		),
		(
			"Payroll Gross by Company",
			"Salary Slip",
			"Group By",
			"company",
			"gross_pay",
			[["Salary Slip", "docstatus", "=", 1]],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"Payroll Net by Company",
			"Salary Slip",
			"Group By",
			"company",
			"net_pay",
			[["Salary Slip", "docstatus", "=", 1]],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"People of Determination by Company",
			"Employee",
			"Group By",
			"company",
			None,
			[
				["Employee", "status", "=", "Active"],
				["Employee", "sdg_is_person_of_determination", "=", 1],
			],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Employer Cost by Company",
			"Salary Slip",
			"Group By",
			"company",
			None,
			[["Salary Slip", "docstatus", "=", 1]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Loans Repaid by Company",
			"Salary Slip",
			"Group By",
			"company",
			"total_loan_repayment",
			[["Salary Slip", "docstatus", "=", 1]],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"Compliance Gaps by Company",
			"Employee",
			"Group By",
			"company",
			None,
			[
				["Employee", "status", "=", "Active"],
				# A `between` from a fixed floor, never a `< today`. `DatabaseQuery`
				# renders a Date comparison as `ifnull(field, '0001-01-01') < value`, so
				# the short form also matches everyone whose ID expiry was never filled
				# in - and the list a bar opens would then carry a barful of staff the
				# bar does not count. The same reason, and the same remedy, as
				# `test_no_drilldown_uses_a_bare_date_cutoff`; see `DATED_FLOOR`.
				# `1900-01-01` rather than that constant because a national ID cannot
				# have expired in the first millennium, and a floor nobody will reach
				# reads better here than a floor borrowed from the null substitute.
				["Employee", "sdg_national_id_expiry", "between", ["1900-01-01", nowdate()]],
			],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Uninsured by Company",
			"Employee",
			"Group By",
			"company",
			None,
			[
				["Employee", "status", "=", "Active"],
				["Employee", "sdg_si_status", "in", ["Uninsured", "None"]],
			],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Life Insurance Band Split",
			"SDG Gross Insurance Band",
			"Group By",
			"from_gross",
			"life_insurance",
			[],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"Recharged Net by Charging Company",
			"SDG Group Allocation",
			"Group By",
			"charged_company",
			"net_amount",
			[],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"Recharged Gross by Paying Company",
			"SDG Group Allocation",
			"Group By",
			"employing_company",
			"gross_amount",
			[],
			{"type": "Bar", "group_by_type": "Sum", "number_of_groups": 20},
		),
		(
			"Contract Type by Company",
			"Employee",
			"Group By",
			"sdg_contract_type",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Donut"},
		),
		# --- the Workforce Profile band -------------------------------------
		# Six charts of who the group is made of.  Each field was chosen by measuring
		# how many active employees have it filled in and how many distinct values it
		# takes, because a `Group By` on a field that is null everywhere draws one
		# empty slice - which on a dashboard reads as a fault rather than as "not yet
		# recorded".  `grade` and `employment_type` are null on all 310 employees, so
		# the two charts the desk ships for them are deliberately not used here.
		#
		# Counts are spread over `Bar` where there are more than about six slices and
		# over `Donut` below that, because a donut with 23 segments is a ring of
		# hairlines and a bar with two segments is two bars.
		(
			"Headcount by Governorate",
			"Employee",
			"Group By",
			"sdg_governorate",
			None,
			[["Employee", "status", "=", "Active"]],
			# 23 governorates, so a donut would be 23 hairlines.  Twenty groups
			# rather than all of them: the long tail is Cairo, and dropping it from
			# the axis is what makes the others readable.
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Headcount by Sub Department",
			"Employee",
			"Group By",
			"sdg_sub_department",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Headcount by Branch",
			"Employee",
			"Group By",
			"branch",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Bar", "number_of_groups": 20},
		),
		(
			"Headcount by Gender",
			"Employee",
			"Group By",
			"gender",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Donut"},
		),
		(
			"Military Status Split",
			"Employee",
			"Group By",
			"sdg_military_status",
			None,
			[["Employee", "status", "=", "Active"]],
			# Five values including the blank, which `Group By` folds into its own
			# slice - so this also says how many staff have no status recorded, which
			# is the other card on the band.
			{"type": "Donut"},
		),
		(
			"Headcount by Marital Status",
			"Employee",
			"Group By",
			"marital_status",
			None,
			[["Employee", "status", "=", "Active"]],
			{"type": "Donut"},
		),
	]

	existing_charts = {
		row.chart_name: row.name
		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": "Sharm Dreams Group", "is_standard": 0},
			fields=["name", "chart_name"],
			limit_page_length=0,
		)
	}
	created_charts = 0
	for name, doctype, chart_type, field, value, filters, options in charts:
		values = {
			"doctype": "Dashboard Chart",
			"chart_name": name,
			"chart_type": chart_type,
			"document_type": doctype,
			"filters_json": json.dumps(filters),
			"module": "Sharm Dreams Group",
			"is_standard": 0,
			"timespan": "Select Date Range",
			# Only a Sum has money in it.  `chart_widget.js` reads `currency` in
			# exactly one place - `formatTooltipY` - so a count chart with it set
			# formats its hover value as "EGP 70 people".  Not the axis, which is
			# where it is often assumed to appear.
			"currency": "EGP" if value else None,
		}
		if chart_type == "Group By":
			values["group_by_based_on"] = field
			values["group_by_type"] = options.get("group_by_type", "Count")
			values["number_of_groups"] = options.get("number_of_groups", 10)
			values["aggregate_function_based_on"] = value or ""
		values["type"] = options.get("type", "Bar")
		# A Line or Bar chart with no colour fails to build in Chart.js, so it never
		# appears on the page at all.  See `chart_colour`.
		values["color"] = chart_colour(name)

		if name in existing_charts:
			doc = frappe.get_doc("Dashboard Chart", existing_charts[name])
			for key, item in values.items():
				if key != "doctype" and doc.get(key) != item:
					doc.set(key, item)
			doc.save(ignore_permissions=True)
			# Asked of the document rather than trusted to `values`. An explicit set
			# outlives the DocType default, so the update path above already needs no
			# help - but a record that picked up `EGP` on some earlier insert is only
			# corrected because this loop writes the field on every pass. Said here
			# because it is the third insert path in this module that needed the same
			# fix, and the second that had to be found by a test rather than by reading
			# it: the comment on `currency` says `None` means "no currency", and on
			# insert it does not.
			_clear_defaulted_currency(
				frappe.get_doc("Dashboard Chart", existing_charts[name]), values["currency"]
			)
			continue
		frappe.get_doc(values).insert(ignore_permissions=True)
		# The same default, undone on the insert path. `Dashboard Chart.currency` is a
		# Link to Currency and `_set_defaults` fills a default into any falsy field, so
		# the `None` written above does not survive as "no currency": a `Group By` that
		# counts rows comes out carrying EGP and hovers as "EGP 70 people". This is
		# what happened to `Headcount by Marital Status`, and it is invisible on a site
		# where the chart predates the field - only a fresh insert shows it, which is
		# why it survived a good many migrates before anything asserted on it.
		_clear_defaulted_currency(frappe.get_doc("Dashboard Chart", name), values["currency"])
		created_charts += 1

	for label, name in existing_cards.items():
		if label not in {spec["label"] for spec in dashboard.metric_definitions()}:
			frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)
			removed_cards += 1

	if created_cards or removed_cards or created_charts:
		print(
			f"  dashboard: {created_cards} cards created, {removed_cards} removed, "
			f"{created_charts} charts created"
		)
	else:
		print("  dashboard: all present")


# The dashboard's own name, and the sidebar that reaches it.  In this Frappe
# version a dashboard is a `Dashboard` document reached through a
# `Workspace Sidebar` item - which is what `/desk/dashboard-view/<name>` resolves.
# Putting the cards on the workspace as well is deliberate: a workspace shows them
# on the way in, the dashboard shows them full width with room for the charts.
DASHBOARD_NAME = "SDG Workforce"
# Built here once, then retired: every card it showed was already on the workforce
# dashboard.  Kept as a name so `delete_retired_dashboard` can find the document a
# site already has and take it away.
RETIRED_DASHBOARD_NAME = "SDG Analytics"
# The one Income Tax Slab this group uses; see seeder.TAX_SLAB_NAME
TAX_SLAB_NAME = "Egypt Annual Income Tax"
DASHBOARD_MODULE = "Sharm Dreams Group"

# The currency every money figure on a dashboard is in.  A `Dashboard Chart` with a
# currency set hovers its bars with it (`formatTooltipY`), so a count chart carrying
# one reads "EGP 180 people" - the same reason `hr/dashboard.py` has its own `MONEY`
# for the Number Cards.  Kept here rather than imported from there because this is
# the module that writes chart documents, and importing the metric module's constant
# to spell a chart's currency would couple two things for a string.
MONEY = "EGP"

# The one side panel for the app.  The panel is chosen by `app`, so a second
# sidebar carrying the same app is not a second panel - it is a shadowed
# duplicate, and which one shows is decided by the site cache rather than by us.
APP_SIDEBAR = "Sharm Dreams Group"


# The two domain dashboards, added after the workforce pair.  Same `Dashboard`
# doctype, same route, same factory; only the sections differ.
ACCOUNT_DASHBOARD_NAME = "SDG Account"
HRM_DASHBOARD_NAME = "SDG HRM"

# (dashboard name, module, sections, card definitions, chart specs)
DOMAIN_DASHBOARDS = (
	(
		ACCOUNT_DASHBOARD_NAME,
		"sharm_dreams_group.accounts.dashboard",
		"ACCOUNT_SECTIONS",
		"ACCOUNT_CHARTS",
		("Loans Repaid by Company",),
		"ACCOUNT_SECTION_LAYOUT",
	),
	(
		HRM_DASHBOARD_NAME,
		"sharm_dreams_group.hr.dashboard_hrm",
		"HRM_SECTIONS",
		"HRM_CHARTS",
		(
			"Uninsured by Company",
			"Compliance Gaps by Company",
			"Social Insurance Status",
			"Leavers by Company",
		),
		"HRM_SECTION_LAYOUT",
	),
)


def create_domain_cards_and_charts() -> None:
	"""Build the cards and charts for the domain dashboards.

	Same job as `create_dashboard_cards`, and the same insert-or-update keyed by
	name, but for the modules that own the `SDG Account` and `SDG HRM` cards.  The
	loop is shared with the workforce one through `_upsert_card` / `_upsert_chart`
	so a card behaves the same whichever dashboard it is on - the earlier
	version had one inline copy of this logic and adding a second dashboard
	meant copying it, which is how the two would have drifted.

	Only this module's own cards are created.  A chart reused from the workforce
	module is referenced, not re-declared, so it stays defined in one place.
	"""
	import importlib

	for _name, module_path, _sections_attr, charts_attr, _reused, _layout_attr in DOMAIN_DASHBOARDS:
		module = importlib.import_module(module_path)
		created_cards, removed_cards, created_charts = _sync_cards(module)
		created_charts += _sync_charts(getattr(module, charts_attr))
		if created_cards or removed_cards or created_charts:
			print(
				f"  dashboard: {_name} {created_cards} cards created, {removed_cards} removed, "
				f"{created_charts} charts created"
			)
		else:
			print(f"  dashboard: {_name} all present")


def _sync_cards(module) -> tuple[int, int, int]:
	"""Create, update and retire the Number Cards a dashboard module defines.

	Existing cards are found by *label*, which is what the card is called on the
	page, and compared by the fields that decide how it renders.  Cards this
	module no longer defines are removed: a card left behind answers with a
	figure nothing on the desk can explain, which is worse than no card.
	"""
	import json

	existing = {
		row.label: row.name
		for row in frappe.get_all(
			"Number Card",
			filters={"method": module.METRIC_METHOD},
			fields=["name", "label"],
			limit_page_length=0,
		)
	}
	created = removed = 0
	wanted = module.metric_definitions()
	for spec in wanted:
		name = _upsert_card(
			spec, module.METRIC_METHOD, json.dumps({"metric": spec["key"]}), existing.get(spec["label"])
		)
		if name is None:
			created += 1
	for label, name in existing.items():
		if label not in {spec["label"] for spec in wanted}:
			frappe.delete_doc("Number Card", name, force=True, ignore_permissions=True)
			removed += 1
	return created, removed, 0


def _clear_defaulted_currency(doc, wanted) -> None:
	"""Undo the EGP that `_set_defaults` puts on a freshly inserted widget.

	Both `Number Card.currency` and `Dashboard Chart.currency` default to EGP, and
	`_set_defaults` fills a default into any field left falsy - so writing `None` in
	the insert is not the same as saying "no currency", and the record comes out
	claiming a headcount is an amount.  The update path is unaffected: an explicit
	set outlives the default, which is why this only ever went wrong for a widget
	added since the last migrate, and why it survived a suite that checks every
	card on every dashboard.

	It reads the value back off the inserted document rather than assuming, because
	the only thing worth saving is a difference and re-saving an unchanged document
	buys nothing.

	Nothing on screen changes either way for a `Custom` card: `get_number_for_custom_card`
	only reads `currency` when the method returns an object, and every metric here
	returns a bare number.  For a chart it does reach the screen - `currency` is read
	by `formatTooltipY`, so a count chart hovers as "EGP 70 people".  So this is about
	the record being right, and about a count not being displayed as money.
	"""
	if (doc.currency or None) == (wanted or None):
		return
	doc.currency = wanted or None
	doc.save(ignore_permissions=True)


def _upsert_card(spec: dict, method: str, filters: str, existing_name: str | None) -> str | None:
	"""Write one Number Card. Returns None when it was created, else its name.

	`currency` is the field the card form and the API read to say whether the
	figure is an amount, and it is taken from the metric's kind rather than typed
	in, so a headcount cannot end up recorded as money.  Note that a Custom card
	whose method returns a bare number never reads it - `get_number_for_custom_card`
	only calls `set_formatted_number` for an object return - so this is about the
	record, not about a symbol on the card.  See
	`test_a_scalar_metric_never_reaches_the_currency_branch`.
	"""
	values = {
		"label": spec["label"],
		"type": "Custom",
		"method": method,
		"filters_json": filters,
		"color": spec["color"],
		"module": DASHBOARD_MODULE,
		"currency": spec.get("currency"),
		# Public so every role sees it: a card invisible to a payroll user
		# answers to nobody.
		"is_public": 1,
		"is_standard": 0,
	}
	if existing_name:
		doc = frappe.get_doc("Number Card", existing_name)
		changed = False
		for key, item in values.items():
			if key in ("is_public", "is_standard"):
				continue
			if doc.get(key) != item:
				doc.set(key, item)
				changed = True
		if not cint(doc.is_public):
			doc.is_public = 1
			changed = True
		if changed:
			doc.save(ignore_permissions=True)
		return existing_name
	card = frappe.get_doc(dict(doctype="Number Card", **values))
	card.insert(ignore_permissions=True)
	_clear_defaulted_currency(card, spec.get("currency"))
	return None


def _sync_charts(specs) -> int:
	"""Create and update a tuple of Dashboard Chart specs."""
	import json

	created = 0
	existing = {
		row.chart_name: row.name
		for row in frappe.get_all(
			"Dashboard Chart",
			filters={"module": DASHBOARD_MODULE, "is_standard": 0},
			fields=["name", "chart_name"],
			limit_page_length=0,
		)
	}
	for name, doctype, field, value, filters, options in specs:
		if _write_chart(name, doctype, field, value, filters, options, existing):
			created += 1
	return created


def _write_chart(name, doctype, field, value, filters, options, existing) -> bool:
	"""Write one Dashboard Chart. Returns True when it was created."""
	import json

	values = {
		"chart_name": name,
		"chart_type": "Group By",
		"document_type": doctype,
		"filters_json": json.dumps(filters),
		"module": DASHBOARD_MODULE,
		"is_standard": 0,
		"timespan": "Select Date Range",
		# Only a Sum has money in it.  A chart's `currency` reaches the tooltip
		# (`formatTooltipY`) and nowhere else, so a count chart with it set hovers as
		# "EGP 70 people".
		"currency": "EGP" if value else None,
		"group_by_based_on": field,
		"group_by_type": options.get("group_by_type", "Count"),
		"number_of_groups": options.get("number_of_groups", 10),
		"aggregate_function_based_on": value or "",
		"type": options.get("type", "Bar"),
		# Mandatory in practice. A Line or Bar chart with no colour makes Frappe
		# hand Chart.js `[[]]`, which it rejects as `"" is not a valid color`, and
		# the widget fails to build - so the chart is simply not there.
		"color": chart_colour(name),
	}
	if name in existing:
		doc = frappe.get_doc("Dashboard Chart", existing[name])
		for key, item in values.items():
			if doc.get(key) != item:
				doc.set(key, item)
		doc.save(ignore_permissions=True)
		return False
	frappe.get_doc(dict(doctype="Dashboard Chart", **values)).insert(ignore_permissions=True)
	# Same defaulted-currency trap as `_upsert_card`, and here it reaches the screen:
	# a count chart inserted with `currency: None` came out as EGP and hovered its
	# bars as "EGP 70 people" until a later migrate fixed it on the update path.
	_clear_defaulted_currency(frappe.get_doc("Dashboard Chart", name), values["currency"])
	return True


# How many bars a reused chart may draw, where HRMS ships no limit.
#
# `number_of_groups` of 0 means "all of them", and every reused chart on this site
# has a field with far more values than an axis can carry: 111 distinct
# designations across 179 active staff. Twenty is what this app's own charts use
# for the same reason, and 20 bars is as many as a company name stays readable at.
REUSED_CHART_GROUP_LIMITS = {
	"Designation Wise Employee Count": 20,
	"Designation Wise Salary(Last Month)": 20,
	"Department Wise Salary(Last Month)": 20,
}

# The reused charts that have to become a Bar, and why.
#
# A Pie is the wrong shape for a chart with more values than a pie can show, and
# the desk makes that worse rather than better: `chart_widget.js` caps a Pie or a
# Donut at **6** slices unless `number_of_groups` overrides it, and drops the rest
# without a word. A pie whose slices are the six largest of a hundred and four is a
# chart of the six, whatever its title says.
#
# Two of the four reused charts ship as Pies:
#
#   * **`Employees by Age`** - fourteen five-year bands, truncated to six. This one
#     is not merely ugly, it is *dead*. The pie draws six slices while the drilldown
#     declares fourteen bands, and a slice click is resolved by its position in the
#     drawn store, which is length-checked against the label list. Six against
#     fourteen fails that check, so every click refuses - and the wiring report
#     still counts the chart clickable, because a chart with a drilldown declared
#     looks identical to a working one from where it stands.
#   * **`Designation Wise Employee Count`** - 111 designations, truncated to 20
#     wedges. Clickable, and unreadable: twenty slices around a circle, labelled
#     "Marketing Coordinator".
#
# A Bar draws every value and stamps `data-point-index` onto each one, which is
# exactly what the click reads. So both become Bars, and all fourteen age bands then
# draw - which is also the better picture of an age distribution than a pie of it.
#
# `is_standard = 1`, so an HRMS migrate puts the Pie back. That is what makes this a
# function called from after_migrate rather than a one-off edit.
REUSED_CHART_TYPES = {
	"Employees by Age": "Bar",
	"Designation Wise Employee Count": "Bar",
}


def latest_payroll_window() -> tuple[str, str] | None:
	"""The `(start_date, end_date)` of the newest submitted payroll, or None.

	Deliberately *not* a `Timespan`. HRMS ships these two salary charts with
	`["Salary Slip", "start_date", "Timespan", "last month"]`, which the desk
	resolves to the calendar month before today - 2026-09-01..30 on the 4th of
	October. That happens to be the one payroll run on this site, so the chart looks
	right and stops being right the moment a second month is run, or the first is run
	late: on the 20th of October "last month" is September again and the chart shows
	the previous run twice.

	Written as explicit dates instead, from the payroll itself. That is the same
	rule `hr/dashboard.py`'s money metrics follow - they sum the newest submitted
	`Payroll Entry` rather than a named month - so the chart and the cards beside it
	are always talking about the same run.
	"""
	row = frappe.db.sql(
		"select start_date, end_date from `tabPayroll Entry` "
		"where docstatus = 1 order by end_date desc, name desc limit 1",
		as_dict=True,
	)
	if not row or not row[0].start_date:
		return None
	return str(row[0].start_date), str(row[0].end_date)


def tune_reused_charts() -> None:
	"""Put the four things HRMS omits onto each reused chart.

	All four are small and each is the difference between a chart that reads and one
	that does not:

	  * **a group limit.** `number_of_groups` is 0 on every reused chart, which means
	    every value, and 111 designations do not fit on an axis.

	  * **the payroll window, as dates.** See `latest_payroll_window`.

	  * **a shape that can hold the values.** See `REUSED_CHART_TYPES` - two of these
	    ship as Pies, and the desk caps a Pie at six slices and drops the rest, which
	    for `Employees by Age` leaves a chart with fourteen declared bands and six
	    drawn slices, and therefore a click that resolves to nothing.

	  * **a currency only where it is money.** A Sum chart hovers its bars with it; a
	    Count chart does not, and EGP on one reads "EGP 12 people".

	Only these fields are touched, and only on charts `dashboard.REUSED_CHARTS`
	names. They are HRMS documents - `is_standard = 1` - and this app is not in the
	business of rewriting them beyond what it puts on a dashboard of its own; the
	company widening that belongs to *every* chart lives in `upstream_patches` for
	the same reason.

	Idempotent, and each field only saved when it actually changes.
	"""
	from sharm_dreams_group.hr import dashboard as workforce

	window = latest_payroll_window()
	for name in workforce.REUSED_CHARTS:
		if not frappe.db.exists("Dashboard Chart", name):
			print(f"  dashboard: REUSED CHART MISSING: {name}")
			continue

		doc = frappe.get_doc("Dashboard Chart", name)
		values: dict = {}

		limit = REUSED_CHART_GROUP_LIMITS.get(name)
		if limit and doc.number_of_groups != limit:
			values["number_of_groups"] = limit

		shape = REUSED_CHART_TYPES.get(name)
		if shape and doc.type != shape:
			values["type"] = shape

		if window and doc.document_type == "Salary Slip":
			# The `Timespan` row is replaced by the two dates. Replacing rather than
			# adding: a `Timespan` filter and a `between` on the same field are ANDed,
			# so leaving it would narrow the chart to nothing the moment the payroll
			# period and the calendar month disagreed.
			rows = frappe.parse_json(doc.filters_json)
			if not isinstance(rows, list):
				rows = []
			kept = [r for r in rows if not (isinstance(r, list) and len(r) > 2 and r[2] == "Timespan")]
			kept.append(["Salary Slip", "start_date", "between", [window[0], window[1]]])
			# `timespan` itself has to go too, or the desk resolves it to the calendar
			# month and ANDs that on top of the dates.
			values["filters_json"] = json.dumps(kept)
			values["timespan"] = "Select Date Range"

		# A Sum chart hovers its bars with the currency, and these are money. A Count
		# chart does not, and leaving EGP on one puts "EGP 12 people" in the tooltip.
		wants_money = doc.group_by_type == "Sum"
		wanted_currency = MONEY if wants_money else None
		if (doc.currency or None) != wanted_currency:
			values["currency"] = wanted_currency

		if not values:
			continue
		for field, value in values.items():
			doc.set(field, value)
		doc.save(ignore_permissions=True)
		summary = ", ".join(sorted(values))
		print(f"  dashboard: {name} tuned ({summary})")


# The `Custom` charts this app reuses, and how each bar becomes a filter.
#
# A `Group By` chart is clickable because its bars *are* values of a field: tapping
# `Bautex Misr` means `company = "Bautex Misr"`. A `Custom` chart's bars are computed
# - "25-29" is an age band nobody stores on the row - so the bar cannot be the
# filter and the chart has to say what each one means. That declaration is
# `custom_options`, and without it the chart is a picture: no pointer, and the
# wiring report counting it as an unclickable chart.
#
# `Employees by Age` is the one reused `Custom` chart on this site with real data
# behind it: fourteen age bands off `date_of_birth` over 180 active staff. The other
# four `Custom` charts HRMS and ERPNext ship cannot run here at all - `Top 10 Pledged
# Loan Securities` and both Stock charts need doctypes this site does not have, and
# `Bank Balance` has one GL entry against 28 bank accounts.
#
# HRMS's `Hiring vs Attrition Count` used to be the second entry, and is not any more.
# It drew joiners and leavers on one axis, and 130 leavers in September set that axis
# so hard that a month with nine joiners drew nine pixels tall - a chart about hiring
# showing no variation in hiring. It also could not be clicked: frappe-charts keeps no
# per-series identity on a Line, so the widget infers it from pixel distance, and at
# 0.95px per person eleven of thirteen months were too close to tell apart. It is
# replaced by `Monthly Joiners` and `Monthly Attrition` in `CUSTOM_CHART_SOURCES` -
# this app's own, one line each, one axis each, every month clickable. Its drilldown
# is not removed, because the stock chart keeps its `custom_options` on the site and
# it is harmless there: no section places it, so nothing draws it.
#
# Buckets are *day offsets* from today, never dates, for the reason
# `contract_end_dates.window_specs` gives: these live on a chart document, so a date
# written here would be next month's answer when the bar was tapped. The widget adds
# them to today. The exception says so in its own module, because it is
# calendar-relative rather than today-relative: an age band moves with the calendar
# year, so it carries absolute dates and is rewritten on every migrate
# (`employees_by_age`).
REUSED_CUSTOM_CHARTS = {
	"Employees by Age": (
		"sharm_dreams_group.hr.dashboard_chart_source.employees_by_age"
		".employees_by_age.drilldown_options"
	),
	"Hiring vs Attrition Count": (
		"sharm_dreams_group.hr.dashboard_chart_source.hiring_vs_attrition"
		".hiring_vs_attrition.drilldown_options"
	),
}


def create_reused_custom_chart_drilldowns() -> None:
	"""Write the drilldown onto each reused `Custom` chart, so it is clickable.

	Only `custom_options` is touched. These are HRMS documents - `is_standard = 1`,
	module `HR` - and rewriting anything else on them would make this app the owner
	of a chart an HRMS upgrade rewrites underneath it, which is the same reason the
	company widening lives in `upstream_patches` rather than here.

	Idempotent, and keyed by chart name. `Dashboard Chart.currency` also defaults to
	EGP on this DocType and `_set_defaults` fills it into a falsy field on insert, but
	these charts are inserted by HRMS's fixtures rather than by this function, so
	there is no insert here to correct - the value is cleared on update like any
	other, which does outlive the default.
	"""
	import json
	import importlib

	for chart_name, options_path in REUSED_CUSTOM_CHARTS.items():
		if not frappe.db.exists("Dashboard Chart", chart_name):
			print(f"  dashboard: REUSED CUSTOM CHART MISSING: {chart_name}")
			continue
		module_path, _dot, attribute = options_path.rpartition(".")
		# Belt and braces: an unreachable drilldown module is a chart that renders and
		# does nothing, and the wiring report would call it an unclickable chart with
		# no explanation. Better a line in the migrate log than a picture.
		try:
			options = json.dumps(getattr(importlib.import_module(module_path), attribute)())
		except Exception as error:
			print(
				f"  dashboard: {chart_name} has no drilldown module "
				f"({module_path}.{attribute}: {type(error).__name__}: {error})"
			)
			continue

		doc = frappe.get_doc("Dashboard Chart", chart_name)
		if doc.custom_options == options and not doc.currency:
			continue
		doc.custom_options = options
		# A count chart, so hovering a bar should not read "EGP 30 people". Every
		# chart in this table counts people; none aggregates a money field, and
		# `currency` defaults to EGP on the DocType, so this is not decoration.
		doc.currency = None
		doc.save(ignore_permissions=True)
		print(f"  dashboard: {chart_name} drilldown written")


def create_domain_dashboards() -> None:
	"""Assemble the domain dashboards, once their cards and charts exist."""
	import importlib

	from sharm_dreams_group.hr import dashboard as workforce

	for name, module_path, sections_attr, _charts_attr, reused, layout_attr in DOMAIN_DASHBOARDS:
		module = importlib.import_module(module_path)
		sections = getattr(module, sections_attr)
		charts = [c for _t, _cards, cs in sections for c in cs]
		charts += [c for c in reused if c not in charts]
		# Each dashboard's own arrangement.  All three call the same
		# `section_layout`, and two of them have a band with the same title and
		# different cards in it, so this cannot be read from one shared table.
		layout = getattr(module, layout_attr, None) or {}

		doc = (
			frappe.get_doc("Dashboard", name)
			if frappe.db.exists("Dashboard", name)
			else frappe.get_doc(
				{
					"doctype": "Dashboard",
					"dashboard_name": name,
					"module": DASHBOARD_MODULE,
					"is_standard": 0,
				}
			)
		)
		doc.set("cards", [])
		doc.set("charts", [])
		for _title, section_cards, _section_charts in sections:
			for label in section_cards:
				if frappe.db.exists("Number Card", label):
					doc.append("cards", {"card": label})
		for chart in charts:
			if not frappe.db.exists("Dashboard Chart", chart):
				continue
			doc.append("charts", {"chart": chart, "width": "Full" if "Company" in chart else "Half"})
		doc.chart_options = workforce.section_layout(sections, layout)
		doc.save(ignore_permissions=True)
		print(f"  dashboard: {name} ({len(doc.cards)} cards, {len(doc.charts)} charts)")


# The `Custom` chart sources this app owns, as (source name, chart shape, colour).
#
# `Custom` is the only chart type that can bucket a date.  `_write_chart` makes a
# `Group By`, and `dashboard_chart.get_group_by_chart_config` groups on the
# field's raw value - so pointing one at `contract_end_date` draws one bar per
# distinct day instead of the handful of windows anyone reads.  A source whose
# method returns its own labels sidesteps that entirely.
#
# A `Custom` chart has no `group_by_based_on`, which is what every other chart on
# these dashboards is drillable *by*.  `CUSTOM_CHART_DRILLDOWN` puts back what
# that field would have carried, or the chart would be the one picture on the page.
CUSTOM_CHART_SOURCES = (
	# The windows chart reads the stock `Employee.contract_end_date`, which is empty
	# on this site, so today it is a single tall bar saying so.  It is here rather
	# than deferred because the alternative is the question not being answerable at
	# all until someone happens to build it.
	("Contract End Dates", "Bar", "#7048e8"),
	#
	# The two halves of HRMS's `Hiring vs Attrition Count`, split.
	#
	# Not a redesign: the stock chart draws joiners and leavers as two lines on one
	# axis, and 130 leavers in a single month sets that axis, so a month with nine
	# joiners is drawn nine pixels tall and the year reads flat. Each half on its own
	# axis draws the variation it is about.
	#
	# **Both are `Bar`, and that is a correctness requirement rather than a taste.**
	# A click resolves through `sharm_index_in_store`, which walks up from the tapped
	# node looking for membership in a chart component's store - so only a node that
	# is actually painted can be found. A `Line` paints none: thirteen months come
	# out as one `<path>` with no per-point element at all (measured: 0 circles, 0
	# rects), so every click on it resolves to -1 and opens nothing. A `Bar` paints
	# one `<rect class="bar">` per label and each is in the store, which is how every
	# other clickable chart on these dashboards works. `Line` and `Area` are both
	# unclickable for this reason, and `test_no_clickable_chart_paints_no_point`
	# now says so in the suite rather than leaving it to be rediscovered.
	#
	# Bars are also the better read here, which the earlier reasoning got backwards:
	# a bar starts at zero, so the twelve months with nobody leaving read as *zero*
	# rather than as a line hovering just above the axis and indistinguishable from
	# one. A count that is zero should look like zero.
	#
	# `timeseries` stays 0 - these are buckets by month rather than a series the desk
	# aggregates itself.
	("Monthly Joiners", "Bar", "#2f6f4e"),
	("Monthly Attrition", "Bar", "#d64545"),
)

CUSTOM_CHART_DRILLDOWN = {
	"Contract End Dates": (
		"sharm_dreams_group.sharm_dreams_group.dashboard_chart_source.contract_end_dates"
		".contract_end_dates.drilldown_options"
	),
	# The two split charts share `hiring_vs_attrition`'s month windows rather than
	# restating them, so the three charts cannot disagree about where a month starts.
	# That disagreement is invisible in a total and wrong in nine months of thirteen,
	# which is exactly the shape of bug `test_every_month_opens_the_rows_it_draws`
	# exists for.
	"Monthly Joiners": (
		"sharm_dreams_group.hr.dashboard_chart_source.monthly_joiners"
		".monthly_joiners.drilldown_options"
	),
	"Monthly Attrition": (
		"sharm_dreams_group.hr.dashboard_chart_source.monthly_attrition"
		".monthly_attrition.drilldown_options"
	),
}


def create_custom_chart_sources() -> None:
	"""Register the `Custom` chart sources and the charts that draw from them.

	Both halves are needed and neither is enough.  A `Dashboard Chart` of type
	`Custom` resolves its data through a `Dashboard Chart Source`, and it is that
	source which locates the JS naming the Python method - so a chart with no
	source record loads, finds nothing to evaluate, and renders empty, while a
	source with no chart is never asked.

	Written here rather than exported.  `Dashboard Chart Source.on_update` only
	exports when it is handling a web request, and when it does it writes the
	folder out of the database into the app - here the JS is the checked-in
	source of truth and the records are built to match it, so a migrate cannot
	overwrite working code with a stale copy.

	Idempotent, keyed by name, and it writes only the fields it owns so a chart
	someone has since retitled or recoloured in the desk keeps that.
	"""
	import importlib
	import json

	for source_name, shape, colour in CUSTOM_CHART_SOURCES:
		if not frappe.db.exists("Dashboard Chart Source", source_name):
			frappe.get_doc(
				{
					"doctype": "Dashboard Chart Source",
					"source_name": source_name,
					"module": DASHBOARD_MODULE,
					"timeseries": 0,
				}
			).insert(ignore_permissions=True)

		# Asked of the source module rather than written out here, so the bucket
		# labels the widget filters by and the labels the chart draws come from one
		# definition.  `json.dumps` here rather than inside it, to keep that module
		# free of any opinion about how the desk stores a field.
		options = CUSTOM_CHART_DRILLDOWN.get(source_name)
		custom_options = "{}"
		if options:
			module_path, _dot, attribute = options.rpartition(".")
			custom_options = json.dumps(getattr(importlib.import_module(module_path), attribute)())

		values = {
			"chart_type": "Custom",
			"source": source_name,
			"type": shape,
			"color": colour,
			"module": DASHBOARD_MODULE,
			"is_standard": 0,
			# Mandatory on the DocType whatever the chart type - a `Custom` chart
			# declares no row filters because its source's method decides what to
			# scope on, but the column still has to be present to insert.
			"filters_json": "{}",
			# `currency` defaults to EGP on the DocType and these charts count
			# contracts and people, so left alone a tooltip would read "EGP 9
			# people". Passing `None` does not clear it on insert - `_set_defaults`
			# fills a default into a falsy field - so it is written back after the doc
			# exists, at the bottom of this loop.
			"currency": None,
			"custom_options": custom_options,
		}
		if frappe.db.exists("Dashboard Chart", source_name):
			doc = frappe.get_doc("Dashboard Chart", source_name)
			if any(doc.get(field) != value for field, value in values.items()):
				for field, value in values.items():
					doc.set(field, value)
				doc.save(ignore_permissions=True)
			# Asked of the document rather than written as part of `values`, so it is
			# also fixed on a chart that has been wrong since an earlier migrate. The
			# `values` compare above skips the whole save when nothing else has
			# changed, and a currency that arrived with the DocType default is exactly
			# the kind of nothing.
			_clear_defaulted_currency(frappe.get_doc("Dashboard Chart", source_name), None)
			continue

		# `Dashboard Chart` is `autoname: field:chart_name`, so the document is
		# addressed by its chart name everywhere - which is what the section
		# layouts and `section_layout`'s lookup both rely on.
		frappe.get_doc(
			{
				"doctype": "Dashboard Chart",
				"chart_name": source_name,
				**values,
			}
		).insert(ignore_permissions=True)

		# The same default, undone on the insert path: `_set_defaults` fills a
		# default into a falsy field, so `None` above does not survive as "no
		# currency". This is the third insert in this module that needs it and the
		# second one that had to be written twice before someone noticed the first.
		_clear_defaulted_currency(frappe.get_doc("Dashboard Chart", source_name), None)

	print(f"  dashboard: {len(CUSTOM_CHART_SOURCES)} custom chart source(s) present")


def create_dashboard() -> None:
	"""Build the dashboard this app publishes.

	`SDG Workforce` is the one people land on: sections of cards and charts, read
	down.  A second dashboard, `SDG Analytics`, used to be built here as four tabs
	- Overview, Salary, Contracts, Benefits - and was removed: every card in it was
	already on the workforce dashboard, so it was the same page twice.

	`Dashboard` is a stock DocType, so nothing here is a new one and the existing
	`/desk/dashboard-view/<name>` route and its factory keep working.  The grouping
	lives in `chart_options`; see `dashboard.section_layout`.

	Idempotent, and rebuilt from the same definitions as the workspace cards, so the
	two cannot disagree about which cards exist.  Rows are replaced rather than added
	to: a second run must not leave a dashboard showing every card twice.
	"""
	from sharm_dreams_group.hr import dashboard

	for name, sections in ((DASHBOARD_NAME, dashboard.DASHBOARD_SECTIONS),):
		doc = (
			frappe.get_doc("Dashboard", name)
			if frappe.db.exists("Dashboard", name)
			else frappe.get_doc(
				{
					"doctype": "Dashboard",
					"dashboard_name": name,
					"module": DASHBOARD_MODULE,
					"is_standard": 0,
				}
			)
		)
		doc.set("cards", [])
		doc.set("charts", [])

		cards: list[str] = []
		charts: list[str] = []
		for _title, section_cards, section_charts in sections:
			cards += list(section_cards)
			charts += list(section_charts)

		# Every card this app owns, not just the workforce metrics: selecting by method
		# workforce and penalty methods both mean the penalty cards join the dashboard
		# too, and a card added later shows up here without anyone having to remember
		# to list it twice.  The layouts above do not name them, so they are appended
		# to the first section - they are one group of six numbers and belong with the
		# figures they qualify.
		#
		# Named methods, not `like 'sharm_dreams_group.%'`: that prefix also matched
		# the Account and HRM cards once those dashboards existed, and this dashboard
		# silently absorbed all twenty-four of them.  Narrowing it to `hr.%` would not
		# have been enough either - `hr.dashboard_hrm` is in `hr` too.
		placed = set(cards)
		for (label,) in frappe.db.sql(
			"select label from `tabNumber Card` where method in (%s, %s) "
			"and ifnull(is_public, 0) = 1 order by if(method = %s, 1, 0), idx, label",
			[dashboard.METRIC_METHOD, PENALTY_CARDS_METHOD, PENALTY_CARDS_METHOD],
			as_list=True,
		):
			if label not in placed:
				cards.append(label)
				placed.add(label)

		for label in cards:
			doc.append("cards", {"card": label})
		for chart in charts:
			if not frappe.db.exists("Dashboard Chart", chart):
				continue
			# the per-company breakdowns have a dozen categories and are unreadable side
			# by side, so they take the full width
			doc.append("charts", {"chart": chart, "width": "Full" if "Company" in chart else "Half"})

		doc.chart_options = dashboard.section_layout(sections)
		doc.save(ignore_permissions=True)
		print(f"  dashboard: {name} ({len(cards)} cards, {len(doc.charts)} charts)")

	_prune_our_charts()


def delete_retired_dashboard() -> None:
	"""Remove `SDG Analytics`, which this app used to build and no longer does.

	Deleting the document rather than leaving it is what lets the pruning above
	finish: `_prune_our_charts` keeps any chart still named by a Dashboard card
	row, so a retired dashboard sitting in the database pins its own charts in
	place for good.  Idempotent - it reports only when there was something to do.
	"""
	if not frappe.db.exists("Dashboard", RETIRED_DASHBOARD_NAME):
		return
	frappe.delete_doc("Dashboard", RETIRED_DASHBOARD_NAME, ignore_permissions=True, force=True)
	print(f"  dashboard: removed the retired {RETIRED_DASHBOARD_NAME}")


# The one way back into the app, kept OUTSIDE every section.
#
# The panel is replaced wholesale when you follow a link into another app: the desk
# resolves which sidebar belongs to the current route (`sidebar.js` ->
# `set_workspace_sidebar` -> `resolve_sidebar`), and a route in the HRMS Payroll
# workspace resolves to the Payroll panel, not ours.  Nothing in a section can bring
# you home after that, so this link sits above them all, always visible.
APP_HOME_LABEL = "Home"
APP_HOME_TARGET = ("Workspace", "Sharm Dreams Group")

# The other row that sits above every section, for the same reason: it is a way in,
# not a group of documents.  It was an "Overview" section holding this one link,
# which is a heading over a single row - the thing the section test exists to
# prevent - so it lives beside Home instead.
# `link_to` stays empty: it is a Dynamic Link validated against `link_type`, and
# "URL" is not a DocType, so a value there is rejected outright. The address
# lives in `url`, and sidebar_sections.js takes the target back off for same-site
# paths so it opens in this tab rather than a new one.
APP_TOP_LINKS = (("Documents", "URL", None, "/drive"),)

# The panel shows `Label`, not `link_to`.  Every Sharm Dreams Group DocType is called
# "SDG <Thing>" because that prefix is what keeps the app's tables from colliding with
# stock ones in the global DocType list, and the workspace, the reports and every import
# all refer to them by that name.
#
# The prefix is an implementation detail, though: in a panel that already has a
# "Setup" section, sixteen rows all reading "SDG ..." is noise.  So the label drops the
# prefix and `link_to` keeps it - renaming the DocTypes themselves would break every
# stored reference on the site, which is not a trade worth making for a label.
LABEL_PREFIXES_STRIPPED = ("SDG ",)

# Where this app's table would be indistinguishable from a stock one in the same
# section, the label says which is which.  Stripping the "SDG " prefix bought a tidier
# panel and created a collision: the mirror files `SDG Payroll Settings` next to
# stock `Payroll Settings`, so both rows would read "Payroll Settings".  Two rows
# reading the same thing in one list is worse than a prefix, so this one names its
# owner - the stock row is the one that is silently generic, so ours is the one that
# says so.
LABEL_DISAMBIGUATED = {
	("Our Setup / Payroll Settings", "SDG Payroll Settings"): "Sharm Payroll Settings",
}


def panel_label(place: str, link_type: str, spec_label: str, link_to: str) -> str:
	"""The label to show in the panel.

	For a DocType, the name without its "SDG " prefix: `SDG Medical Band` reads as
	`Medical Band` in a panel already inside the Sharm Dreams Group app, and sixteen
	rows all beginning "SDG ..." is noise.  The prefix stays in `link_to` - renaming the
	DocTypes would break every stored reference on the site.

	Where that would leave two identical rows in one section, the label names the owner
	instead; see LABEL_DISAMBIGUATED.

	Anything that is not a DocType keeps the label the spec gave it, because there is no
	DocType name to strip: the Dashboard document is called "SDG Workforce" but the row
	must read "Dashboard", not "Workforce".
	"""
	clarified = LABEL_DISAMBIGUATED.get((place, link_to))
	if clarified:
		return clarified
	if link_type != "DocType":
		return spec_label
	for prefix in LABEL_PREFIXES_STRIPPED:
		if link_to.startswith(prefix):
			return link_to[len(prefix) :]
	return link_to


# Icons, drawn from the sprite the desk actually loads (frappe/hooks.py
# `app_include_icons`: lucide + timeless, 1781 symbols).  A name that is not a
# `<symbol id>` in one of those files renders as an empty square, so the set is
# checked against the sprite by a test rather than trusted.
#
# Section headers get the section's own icon; a child link gets the icon for the
# thing it opens, so a panel of 17 Attendance rows is not 17 identical glyphs.
SECTION_ICONS = {
	"Dashboard": "layout-dashboard",
	"People": "users",
	"Payroll": "wallet",
	"Time & Attendance": "clock",
	"Performance": "trending-up",
	"Employee Lifecycle": "arrow-right-left",
	"Our Setup": "settings",
	"Reports": "file-chart-column",
}

# Icons for sub-sections.  Distinct from the section icons so a nested heading is
# visually a heading and not a second top-level group.
SUB_ICON = {
	# People
	"The Employee Record": "contact-round",
	"How People Are Categorised": "tag",
	"Finding People To Hire": "user-round",
	"Open Roles": "briefcase",
	"Interviews": "message-circle",
	"Offers": "handshake",
	"Appointment Letters": "file-text",
	# Payroll
	"Running A Payroll": "play",
	"Payroll Corrections": "pen-line",
	"Salary Structures": "landmark",
	"Benefits In Cash Or Kind": "hand-coins",
	"End Of Service": "award",
	"Staff Advances": "coins",
	"Income Tax": "scale",
	"How The Group Splits Cost": "split",
	# Time & Attendance
	"Leave": "calendar-check",
	"Leave Balances": "circle-dollar-sign",
	"Leave Rules": "book",
	"Attendance": "clock",
	# The employee's own surface. A tablet, not a clock: nothing else in this
	# section is a thing the employee touches themselves.
	"Self Services Employees": "tablet",
	"Shifts": "calendar-days",
	"Overtime": "clock",
	"Travel & Missions": "plane",
	"Vehicles": "truck",
	# Performance
	"Appraisals": "trending-up",
	"Goals": "target",
	"Training": "graduation-cap",
	"Skills": "sparkles",
	"Movement": "arrow-left-right",
	# Lifecycle
	"Joining": "user-plus",
	"Leaving": "log-out",
	"Final Settlement": "receipt",
	"Health & Insurance": "heart-pulse",
	"School Fees": "graduation-cap",
	"Expenses": "receipt",
	"Grievances": "triangle-alert",
	"Announcements": "bell",
	# Our Setup
	"Payroll Settings": "cog",
	"Insurance Bands": "shield",
	"Penalties": "triangle-alert",
	"People": "contact-round",
	"Hiring": "user-round",
	"Growth": "trending-up",
	"Lifecycle": "log-out",
	# Reports
	"People & Headcount": "users",
	"Payroll & Tax": "wallet",
	"Attendance & Leave": "clock",
	"Money & Loans": "scale",
	"Rates & Bands": "landmark",
	"Insurance": "shield-check",
	"Children": "graduation-cap",
	"Vocabularies": "tag",
	"Setup": "cog",
	"The Run": "play",
	"Structures": "landmark",
	"Tax": "scale",
	"Benefits & Deductions": "heart-pulse",
	"Our Settings": "cog",
	"Travel": "plane",
	"Records": "clock",
	"Fleet": "truck",
	"Tools": "cog",
	"Candidates": "user-round",
	"Sourcing": "target",
	"Welfare": "heart-pulse",
	"Concerns": "triangle-alert",
	"Comms": "bell",
}

# Item icons, keyed by the label shown in the panel (i.e. after the prefix is
# stripped) so a table is described by what it is, not by which app owns it.
ITEM_ICONS = {
	# this app's four dashboards, all reached through one collapsible section
	"Workforce Dashboard": "users",
	"HRM Dashboard": "clipboard-check",
	"Account Dashboard": "book-open",
	"Dashboard": "layout-dashboard",
	# stock HR pages reached from the app panel
	"Payroll": "wallet",
	"Tax & Benefits": "shield-check",
	"Recruitment": "user-round",
	"Performance": "trending-up",
	"Tenure": "calendar-days",
	"Documents": "file-text",
	"HR Setup": "settings",
	"Leaves": "calendar-check",
	"Shift & Attendance": "clock",
	# a DocType link is labelled from the DocType name, so this is looked up under
	# "Audit Trail" rather than under the spec's label
	"Audit Trail": "history",
	"Assistant": "star",
	"Home": "house",
	# getting started
	"Employee": "contact-round",
	"Employment Type": "briefcase",
	"Employee Grade": "award",
	"HR Settings": "cog",
	# payroll
	"Payroll Entry": "wallet",
	"Salary Slip": "receipt",
	"Payroll Period": "calendar-range",
	"Salary Structure": "landmark",
	"Salary Structure Assignment": "user-check",
	"Salary Component": "list-todo",
	"Additional Salary": "hand-coins",
	"Payroll Correction": "pen-line",
	"Arrear": "history",
	"Employee Other Income": "coins",
	"Employee Benefit Application": "clipboard-list",
	"Employee Benefit Claim": "clipboard-check",
	"Employee Benefit Ledger": "book",
	"Employee Incentive": "star",
	"Retention Bonus": "award",
	"Gratuity": "hand-coins",
	"Gratuity Rule": "scale",
	"Payroll Settings": "cog",
	"Income Tax Slab": "scale",
	"Egypt Annual Income Tax": "scale",
	"Bulk Salary Structure Assignment": "users",
	"Employee Tax Exemption Category": "scale",
	"Employee Tax Exemption Sub Category": "scale",
	"Employee Tax Exemption Declaration": "notebook-pen",
	"Employee Tax Exemption Proof Submission": "file-check",
	"Salary Withholding": "lock",
	"Employee Advance": "hand-coins",
	# time off
	"Leave Application": "calendar-check",
	"Leave Type": "calendar-days",
	"Leave Policy": "book",
	"Leave Policy Assignment": "user-check",
	"Leave Allocation": "circle-dollar-sign",
	"Leave Period": "calendar-range",
	"Leave Adjustment": "pen-line",
	"Leave Encashment": "coins",
	"Leave Ledger Entry": "book",
	"Leave Block List": "ban",
	"Compensatory Leave Request": "calendar-clock",
	"Leave Control Panel": "cog",
	"Holiday List Assignment": "calendar-heart",
	"Purpose of Travel": "map-pin",
	"Travel Request": "plane",
	"Vacation Tracker": "umbrella",
	"Out Missions": "plane-takeoff",
	# attendance
	"Attendance": "clock",
	"Attendance Request": "clipboard-list",
	"Shift Type": "clock",
	"Shift Assignment": "user-check",
	"Shift Assignment Tool": "cog",
	"Shift Schedule": "calendar-days",
	"Shift Schedule Assignment": "users",
	"Shift Location": "map-pin",
	"Shift Request": "clipboard-list",
	"Overtime Type": "clock",
	"Overtime Slip": "receipt",
	"Employee Checkin": "log-in",
	"Employee Attendance Tool": "cog",
	# the employee self-service. These seven keep their full names in their panel
	# labels - unlike this app's own `SDG *` tables, which are labelled from the
	# stripped name - so they are keyed here as the doctype names.
	#
	# `Employee Self Service Session` and `Employee Self Service Preauth` are a live token and a live biometric
	# challenge respectively, so their glyphs are the credential ones rather than
	# something administrative.
	"Employee Self Service Settings": "cog",
	"Employee Self Service Location": "map-pin",
	"Employee Self Service Biometric Credential": "fingerprint",
	"Employee Self Service Approval Category": "tags",
	"Employee Self Service Approval Request": "clipboard-check",
	"Employee Self Service Session": "key-round",
	"Employee Self Service Preauth": "shield-check",
	"Daily Work Summary": "notebook-pen",
	"Daily Work Summary Group": "users",
	"Vehicle Log": "truck",
	"Vehicle Service Item": "wrench",
	# recruitment
	"Job Applicant": "user-round",
	"Job Applicant Source": "target",
	"Job Opening": "briefcase",
	"Job Requisition": "clipboard-list",
	"Job Opening Template": "file-text",
	"Interview": "message-circle",
	"Interview Feedback": "notebook-pen",
	"Interview Type": "tag",
	"Job Offer": "handshake",
	"Job Offer Term Template": "file-text",
	"Offer Term": "file-text",
	"Employee Referral": "user-plus",
	"Staffing Plan": "users",
	"Appointment Letter": "file-text",
	"Appointment Letter Template": "file-text",
	# performance
	"Appraisal": "trending-up",
	"Appraisal Cycle": "refresh-cw",
	"Appraisal Template": "file-text",
	"Employee Performance Feedback": "notebook-pen",
	"Employee Feedback Criteria": "list-checks",
	"Goal": "target",
	"KRA": "list-todo",
	"Employee Skill Map": "map",
	"Skill": "sparkles",
	"Training Program": "graduation-cap",
	"Training Event": "calendar-days",
	"Training Result": "badge-check",
	"Training Feedback": "notebook-pen",
	"Employee Promotion": "trending-up",
	"Employee Transfer": "arrow-left-right",
	"Interest": "heart",
	# lifecycle
	"Employee Onboarding": "user-plus",
	"Employee Onboarding Template": "file-text",
	"Employee Separation": "log-out",
	"Employee Separation Template": "file-text",
	"Exit Interview": "message-circle",
	"Full and Final Statement": "receipt",
	"Employee Health Insurance": "heart-pulse",
	"Employee Grievance": "triangle-alert",
	"Grievance Type": "tag",
	"Expense Claim": "receipt",
	"Expense Claim Type": "tag",
	"Identification Document Type": "id-card",
	"PWA Notification": "bell",
	"HR Telemetry Milestone": "activity",
	# this app's own tables
	"Tax Bracket": "scale",
	"Gross Insurance Band": "shield",
	"Medical Band": "heart-pulse",
	"Family Medical Band": "users",
	"Sharm Payroll Settings": "cog",
	"Penalty Level": "triangle-alert",
	"Group Allocation": "split",
	"Employment Category": "tag",
	"Contract Type": "file-text",
	"Sub Department": "network",
	"Insured Person": "shield-check",
	"School Fee": "graduation-cap",
	# a coupon is handed over as cash, so the icon is a banknote rather than a gift
	"Coupon Granted": "banknote",
	"Coupon Band": "tags",
	# Reports.  One per report rather than one per sub-section: two rows reading
	# "Salary Register" and "Income Tax Computation" under the same glyph are
	# indistinguishable at a glance, which is the whole reason this panel gives
	# every row its own.  Grouped by the question the reader is asking, so the
	# glyph is the subject - a person, a payment, a day, a balance.
	# People & Headcount
	"Employee Analytics": "chart-column",
	"Employee Information": "contact-round",
	"Employee Exits": "log-out",
	"Appraisal Overview": "trending-up",
	"Recruitment Analytics": "user-round",
	"Employee Advance Summary": "coins",
	"Employee Birthday": "cake",
	# Payroll & Tax
	"Salary Register": "table",
	"Bank Remittance": "landmark",
	"Employee CTC Break-up": "split",
	"Income Tax Computation": "scale",
	"Income Tax Deductions": "file-chart-column",
	"Professional Tax Deductions": "badge-dollar-sign",
	"Provident Fund Deductions": "piggy-bank",
	"Salary Payments Based On Payment Mode": "hand-coins",
	"Accrued Earnings Report": "chart-bar",
	# Attendance & Leave
	"Monthly Attendance Sheet": "calendar-days",
	"Shift Attendance": "clock",
	"Employees working on a holiday": "sun",
	"Employee Leave Balance": "calendar-check",
	"Employee Leave Balance Summary": "calendar-clock",
	"Leave Ledger": "book",
	"Employee Hours Utilization Based On Timesheet": "timer",
	"Daily Work Summary Replies": "clipboard-list",
	# Money & Loans
	"Trial Balance": "scale",
	"General Ledger": "scroll-text",
	"Balance Sheet": "chart-column",
	"Profit and Loss Statement": "trending-up",
	"Bank Reconciliation Statement": "landmark",
	"Accounts Payable Summary": "circle-dollar-sign",
	"Accounts Receivable Summary": "hand-coins",
	# No "Loan Outstanding Report": it belongs to the `lending` app, which is on this
	# bench but not installed on this site, so the panel entry below it would have been a
	# card that opened nothing.  Both were removed together.
}

# Shown when a label is not in ITEM_ICONS.  Better a repeated neutral glyph than an
# empty square, and the test counts the misses so they do not pile up unnoticed.
FALLBACK_ICON = "file-text"


def panel_icon(label: str) -> str:
	return ITEM_ICONS.get(label, FALLBACK_ICON)


# Where each of this app's own tables belongs in the panel.
#
# Keyed "Section / Sub-section" -> the tables filed there.  A tax bracket configures the
# income tax the payroll section computes, so it sits in Payroll beside Income Tax Slab;
# the leave type sits in Time Off beside the stock Leave Type.  Filing by use rather than
# by ownership is deliberate: a "Setup" bucket of sixteen rows is just a second place to
# look for something.

# The side panel, mirrored: every HR DocType on the site, grouped so the heading
# says what the group is *for*.
#
# The panel used to launch rather than mirror - 29 links, no stock DocTypes - which
# was shorter but meant the app was not a place you could find things, only a place
# you could be sent. A mirror is only useful if it is organised, so each section is
# named for the job it does and the sub-sections name the kind of work.
#
# Every DocType appears exactly once. `HRMS_PANEL_GROUPS` is checked against the
# database on every migrate (see `apply_app_sidebar`), so a DocType added by an
# HRMS upgrade is reported rather than quietly left out - a mirror that silently
# misses things is the worst of both arrangements.
HRMS_PANEL_GROUPS = (
	# --- who works here, and how they got here ---------------------------
	(
		"People",
		(
			(
				"The Employee Record",
				("Employee", "Employee Grade", "Employment Type", "Identification Document Type"),
			),
			(
				"How People Are Categorised",
				("SDG Employment Category", "SDG Contract Type", "SDG Sub Department"),
			),
			(
				"Finding People To Hire",
				("Job Applicant", "Job Applicant Source", "Staffing Plan", "Employee Referral"),
			),
			("Open Roles", ("Job Opening", "Job Opening Template", "Job Requisition")),
			("Interviews", ("Interview", "Interview Feedback", "Interview Type")),
			("Offers", ("Job Offer", "Job Offer Term Template", "Offer Term")),
			("Appointment Letters", ("Appointment Letter", "Appointment Letter Template")),
		),
	),
	# --- what the group pays, and what it costs --------------------------
	(
		"Payroll",
		(
			("Running A Payroll", ("Payroll Entry", "Payroll Period", "Salary Slip")),
			(
				"Payroll Corrections",
				("Payroll Correction", "Arrear", "Additional Salary", "Employee Other Income"),
			),
			(
				"Salary Structures",
				(
					"Salary Structure",
					"Salary Structure Assignment",
					"Salary Component",
					"Bulk Salary Structure Assignment",
				),
			),
			(
				"Benefits In Cash Or Kind",
				(
					"Employee Benefit Application",
					"Employee Benefit Claim",
					"Employee Benefit Ledger",
					"Employee Incentive",
					"Retention Bonus",
				),
			),
			("End Of Service", ("Gratuity", "Gratuity Rule", "Salary Withholding")),
			("Staff Advances", ("Employee Advance",)),
			(
				"Income Tax",
				(
					"Income Tax Slab",
					"Employee Tax Exemption Category",
					"Employee Tax Exemption Sub Category",
					"Employee Tax Exemption Declaration",
					"Employee Tax Exemption Proof Submission",
				),
			),
			("How The Group Splits Cost", ("SDG Group Allocation",)),
		),
	),
	# --- when people work, and when they do not --------------------------
	(
		"Time & Attendance",
		(
			(
				"Leave",
				(
					"Leave Application",
					"Leave Type",
					"Leave Policy",
					"Leave Policy Assignment",
				),
			),
			(
				"Leave Balances",
				(
					"Leave Allocation",
					"Leave Period",
					"Leave Adjustment",
					"Leave Ledger Entry",
					"Leave Encashment",
					"Compensatory Leave Request",
				),
			),
			(
				"Leave Rules",
				(
					"Leave Control Panel",
					"Leave Block List",
					"Holiday List Assignment",
				),
			),
			(
				"Attendance",
				(
					"Attendance",
					"Attendance Request",
					"Employee Checkin",
					"Employee Attendance Tool",
					"Daily Work Summary",
					"Daily Work Summary Group",
				),
			),
			# The employee's own tables, beside the attendance they write rather than
			# under Payroll or a settings section: a punch zone and an approval
			# request are both attendance furniture.
			#
			# Labelled for what the employee does rather than for the tablet - see the
			# same label on the workspace card in `workspace.py`.  Both are display
			# strings only; the DocTypes keep their `Employee Self Service *` names.
			#
			# `Employee Self Service Session` and `Employee Self Service Preauth` are listed but readable by System
			# Manager only - a session row holds the plaintext token that authorises
			# every self-service action. The link is here so a System Manager debugging a
			# sign-in can get to them; it 403s for everybody else.
			(
				"Self Services Employees",
				(
					"Employee Self Service Settings",
					"Employee Self Service Location",
					"Employee Self Service Biometric Credential",
					"Employee Self Service Approval Category",
					"Employee Self Service Approval Request",
					"Employee Self Service Session",
					"Employee Self Service Preauth",
				),
			),
			(
				"Shifts",
				(
					"Shift Type",
					"Shift Assignment",
					"Shift Assignment Tool",
					"Shift Schedule",
					"Shift Schedule Assignment",
					"Shift Location",
					"Shift Request",
				),
			),
			("Overtime", ("Overtime Type", "Overtime Slip")),
			(
				"Travel",
				(
					"Purpose of Travel",
					"Travel Request",
				),
			),
			("Vehicles", ("Vehicle Log", "Vehicle Service Item")),
		),
	),
	# --- how people are judged and developed -----------------------------
	(
		"Performance",
		(
			(
				"Appraisals",
				(
					"Appraisal",
					"Appraisal Cycle",
					"Appraisal Template",
					"Employee Performance Feedback",
					"Employee Feedback Criteria",
				),
			),
			("Goals", ("Goal", "KRA")),
			("Training", ("Training Program", "Training Event", "Training Result", "Training Feedback")),
			("Skills", ("Skill", "Employee Skill Map")),
			("Movement", ("Employee Promotion", "Employee Transfer", "Interest")),
		),
	),
	# --- joining, moving and leaving -------------------------------------
	(
		"Employee Lifecycle",
		(
			("Joining", ("Employee Onboarding", "Employee Onboarding Template")),
			("Leaving", ("Employee Separation", "Employee Separation Template", "Exit Interview")),
			("Final Settlement", ("Full and Final Statement",)),
			("Health & Insurance", ("Employee Health Insurance", "SDG Insured Person")),
			("School Fees", ("SDG School Fee",)),
			("Expenses", ("Expense Claim", "Expense Claim Type")),
			("Cost Of Living Coupon", ("SDG Coupon Granted",)),
			("Grievances", ("Employee Grievance", "Grievance Type")),
			("Announcements", ("PWA Notification", "HR Telemetry Milestone")),
		),
	),
	# --- the tables this app owns ----------------------------------------
	(
		"Our Setup",
		(
			("Payroll Settings", ("SDG Payroll Settings", "Payroll Settings")),
			("Stock Settings", ("HR Settings",)),
			("Insurance Bands", ("SDG Gross Insurance Band", "SDG Medical Band", "SDG Family Medical Band")),
			("Penalties", ("SDG Penalty Level",)),
			("Coupon Bands", ("SDG Coupon Band",)),
		),
	),
)


# The reports the panel carries: 32 of the 236 on the site, grouped by the question
# a reader is actually asking.
#
# Curated, not exhaustive, and the number matters.  The site has 236 enabled reports
# and 146 of them are Accounts (52), Stock (50), Selling (23) and Manufacturing (21) -
# a phone book, and most of the rest is Helpdesk, CRM and Loan Management noise beside a
# payroll group.  A panel you scroll past is a panel nobody reads, so this lists the
# reports that answer "how many people, what did we pay, who is on leave, what does the
# group owe" and stops there.  The other ~200 stay reachable the way ERPNext intends:
# a DocType, then its Reports tab.
#
# There is deliberately no "All reports" row.  This Frappe has no all-reports page -
# `report_factory.js` routes reports per DocType (`/<doctype>/view/report/<name>`) and
# `sidebar_editor.js` makes the stock "Reports" entry a Section Break with no link, so
# there is nowhere for such a row to go.  A link to `/app/reports` was tried and 404s
# ("Page reports not found"); a row pointing at it would have been a dead link with a
# reassuring label, which is the failure mode this panel was built to avoid.
#
# Grouped by subject rather than by ERPNext module, because the modules do not answer
# the reader's question: `Employee Advance Summary` is filed under HR but belongs beside
# the other money.
REPORT_PANEL_GROUPS = (
	(
		"Reports",
		(
			(
				"People & Headcount",
				(
					("Employee Analytics", "Report", "Employee Analytics"),
					("Employee Information", "Report", "Employee Information"),
					("Employee Exits", "Report", "Employee Exits"),
					("Appraisal Overview", "Report", "Appraisal Overview"),
					("Recruitment Analytics", "Report", "Recruitment Analytics"),
					("Employee Advance Summary", "Report", "Employee Advance Summary"),
					("Employee Birthday", "Report", "Employee Birthday"),
				),
			),
			(
				"Payroll & Tax",
				(
					("Salary Register", "Report", "Salary Register"),
					("Bank Remittance", "Report", "Bank Remittance"),
					("Employee CTC Break-up", "Report", "Employee CTC Break-up"),
					("Income Tax Computation", "Report", "Income Tax Computation"),
					("Income Tax Deductions", "Report", "Income Tax Deductions"),
					("Professional Tax Deductions", "Report", "Professional Tax Deductions"),
					("Provident Fund Deductions", "Report", "Provident Fund Deductions"),
					(
						"Salary Payments Based On Payment Mode",
						"Report",
						"Salary Payments Based On Payment Mode",
					),
					("Accrued Earnings Report", "Report", "Accrued Earnings Report"),
				),
			),
			(
				"Attendance & Leave",
				(
					("Monthly Attendance Sheet", "Report", "Monthly Attendance Sheet"),
					("Shift Attendance", "Report", "Shift Attendance"),
					("Employees working on a holiday", "Report", "Employees working on a holiday"),
					("Employee Leave Balance", "Report", "Employee Leave Balance"),
					("Employee Leave Balance Summary", "Report", "Employee Leave Balance Summary"),
					("Leave Ledger", "Report", "Leave Ledger"),
					(
						"Employee Hours Utilization Based On Timesheet",
						"Report",
						"Employee Hours Utilization Based On Timesheet",
					),
					("Daily Work Summary Replies", "Report", "Daily Work Summary Replies"),
				),
			),
			(
				"Money & Loans",
				(
					("Trial Balance", "Report", "Trial Balance"),
					("General Ledger", "Report", "General Ledger"),
					("Balance Sheet", "Report", "Balance Sheet"),
					("Profit and Loss Statement", "Report", "Profit and Loss Statement"),
					("Bank Reconciliation Statement", "Report", "Bank Reconciliation Statement"),
					("Accounts Payable Summary", "Report", "Accounts Payable Summary"),
					("Accounts Receivable Summary", "Report", "Accounts Receivable Summary"),
					# "Loan Outstanding Report" used to be here.  It is a `lending` report and
					# `lending` is not installed on this site, so the card led nowhere.  If
					# the app is ever installed, add the row back with its icon above and
					# `as_on_date` in `dashboard_widgets.DEFAULT_FILTERS`; the report demands
					# that filter before it will answer.
				),
			),
		),
	),
)


# A blank row before these sections, so the panel reads as bands rather than one
# long list: look, pay, keep time, grow, move, configure.
PANEL_SPACERS = ("Payroll", "Employee Lifecycle")

# The one place every dashboard is listed.
#
# Three exist: the one built for the workforce, plus `SDG HRM` and `SDG Account`.
# A fourth, `SDG Analytics`, was built here and then removed - it repeated the
# workforce dashboard's own cards in four tabs, which the Dashboard section
# already covered.  Five more were asked for - Project, Recruitment, POS, CRM,
# Support - and are deliberately absent, because their data is empty
# (Project 0, Job Applicant 0, POS Invoice 0, Lead 0, HD Ticket 1) and a
# dashboard full of zeros reads as broken rather than as "not yet".  They are
# listed here in a comment so the intent is on the record, not forgotten.
#
#   ("Project Dashboard"), ("Recruitment Dashboard"), ("POS Dashboard"),
#   ("CRM Dashboard"), ("Support Dashboard"),
#
# The section is written with `collapsible` and `keep_closed` like every other
# section (see apply_app_sidebar), so it opens folded to one line.
APP_SIDEBAR_SECTIONS = (
	(
		"Dashboard",
		(
			# `Dashboard` -> /desk/dashboard-view/<name>.  That route needs a
			# DashboardViewFactory, which stock Frappe does not register; ours lives in
			# public/js/dashboard_view.js, and a test keeps the two in step.
			# Wrapped in a (sub_title, links) group because `panel_sections` reads a
			# flat section by looking at the *first* entry's length: a 3-tuple means a
			# link, anything else means a group.  A leading (None, links) is the way
			# to say "these are links, not sub-headings".
			(
				None,
				(
					("Workforce Dashboard", "Dashboard", DASHBOARD_NAME),
					("HRM Dashboard", "Dashboard", HRM_DASHBOARD_NAME),
					("Account Dashboard", "Dashboard", ACCOUNT_DASHBOARD_NAME),
				),
			),
		),
	),
	*HRMS_PANEL_GROUPS,
	*REPORT_PANEL_GROUPS,
)


# Where a Sidebar Item points, by link type.  Kept next to the spec above so a
# new link type cannot be added without saying how to check it exists.
_LINK_DOCTYPE = {
	"DocType": "DocType",
	"Workspace": "Workspace",
	"Report": "Report",
	"Page": "Page",
	"Dashboard": "Dashboard",
}


def _link_resolves(link_type: str, link_to: str) -> bool:
	"""Whether `link_to` names something the desk can actually open.

	A `Workspace Sidebar Item.link_to` is a Dynamic Link resolved by the browser.  A
	link whose target does not exist does not raise - it opens an empty page, so a typo
	is indistinguishable from a feature that has not been built yet.  Every entry is
	therefore checked against the database before it is written, and the unresolved
	ones are reported rather than written.
	"""
	doctype = _LINK_DOCTYPE.get(link_type)
	if not doctype:
		# URL and anything unmapped: nothing to check against, so allow it
		return True
	return bool(frappe.db.exists(doctype, link_to))


# A `Report` row needs nothing beyond `link_type` and `link_to`, which is not obvious
# and cost a detour to establish - `sidebar_item.js` reads `this.item.report` and
# *returns no path at all* when it is absent, so a Report row looks like it must carry
# the report's metadata itself.  It does not.  `boot.get_sidebar_items` attaches it:
#
#     if (item.link_type == "Report" and item.link_to
#             and frappe.db.exists("Report", item.link_to)
#             and not frappe.db.get_value("Report", item.link_to, "disabled")):
#         workspace_sidebar["report"] = {"report_type": ..., "ref_doctype": ...}
#
# so the router is handed `is_query_report` and `report_ref_doctype` and builds the
# right URL - `query-report/<name>` rather than the `report/<name>` that a bare
# `generate_route({type: "Report"})` would give.
#
# That same `if` is also why a report that is disabled, or that the signed-in user may
# not run, simply does not appear: `is_item_allowed` answers `report` as
# `not disabled and name in allowed_reports`, and Administrator short-circuits to True.
# The section therefore filters itself by role, which is why no role is declared here.
#
# An earlier version of this wrote the metadata onto the row's `route_options` field,
# which does reach the browser (boot copies it) but is a second source of truth for
# something boot already derives.  Removed rather than kept in step.


def app_dashboard_names() -> list[str]:
	"""Every Dashboard document this app builds, workforce and domain alike.

	Anything that walks the dashboards to decide whether something is still in use -
	`_prune_our_charts` above, the chart-coverage test - has to start from here.
	Hardcoding one name is how a chart used only by `SDG Account` ends up looking
	orphaned and gets deleted.
	"""
	return [DASHBOARD_NAME, *[name for name, _path, _s, _c, _reused, _layout in DOMAIN_DASHBOARDS]]


def _section_chart_names(section) -> set[str]:
	"""Every chart name a section names, whichever way the section is written.

	A section is either a row-per-kind arrangement (`cards` and `charts` keys) or an
	ordered list of `blocks`. Both are read here, and both have to be: this is what
	decides whether a chart is in use, so a shape it does not understand reads as "no
	chart is named anywhere" and deletes the dashboards.
	"""
	names = set(section.get("charts") or [])
	for block in section.get("blocks") or []:
		if block and block.get("type") == "charts":
			names.update(block.get("items") or [])
	return names


def _prune_our_charts() -> None:
	"""Delete charts this app owns that no layout names any more.

	`create_dashboard_cards` creates and updates but never removes, so renaming or
	dropping a chart leaves the record behind with nothing pointing at it.  Nothing
	breaks visibly - the chart is simply unreachable - which is how a duplicate
	slipped in before.

	The workspace has to be built before this runs, because it is one of the places
	a chart can still be named.
	"""
	import json

	referenced = set()
	# Every dashboard this app builds, not just the workforce one.  A chart named
	# only by `SDG HRM` or `SDG Account` is still in use, and reading one dashboard's
	# layout would leave it looking orphaned.
	for name in app_dashboard_names():
		if not frappe.db.exists("Dashboard", name):
			continue
		layout = frappe.parse_json(frappe.get_doc("Dashboard", name).chart_options) or {}
		for section in layout.get("sections", []):
			# A section names its charts in one of two ways: `charts` on a section
			# written the old way, or inside `blocks` on one written the new way. Only
			# reading the first is not a cosmetic omission here - every chart would look
			# orphaned and this function would delete the lot on the next run, which is
			# why it is written as one helper rather than as an `or` inline.
			referenced.update(_section_chart_names(section))
	if frappe.db.exists("Workspace", "Sharm Dreams Group"):
		from sharm_dreams_group.workspace import content_blocks

		referenced.update(b["data"]["chart_name"] for b in content_blocks() if b.get("type") == "chart")

	removed = 0
	for row in frappe.get_all(
		"Dashboard Chart",
		filters={"module": DASHBOARD_MODULE, "is_standard": 0},
		fields=["name", "chart_name"],
		limit_page_length=0,
	):
		if row["chart_name"] in referenced:
			continue
		# a chart can still be named by a Dashboard card row even after the layout
		# moved on, so check those before deleting
		used = frappe.db.count("Dashboard Chart Link", {"chart": row["name"]})
		if used:
			continue
		frappe.delete_doc("Dashboard Chart", row["name"], force=True, ignore_permissions=True)
		print(f"  dashboard: removed chart no layout names: {row['chart_name']}")
		removed += 1
	if removed:
		frappe.db.commit()


def _is_group(entry) -> bool:
	"""Whether a panel-spec entry is a `(sub_title, links)` group, not a link.

	A link is `(label, link_type, link_to)` or `(label, link_type, link_to, url)` -
	always three or four parts.  A group is always a pair whose second part is a
	collection of links.  Deciding per entry rather than by looking at the first
	one matters: a section holding a single `URL` link is four parts long, and the
	old "if the first entry is a triple, the section is flat" rule read that as a
	group and then failed to unpack it.
	"""
	return isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], (list, tuple))


def panel_sections() -> list:
	"""The panel spec, normalised: [(section, [(sub_section_or_None, links), ...]), ...].

	`APP_SIDEBAR_SECTIONS` mixes two shapes on purpose - a flat tuple of links for
	a section with no sub-headings, and a tuple of (sub_title, names) pairs where a
	name may itself be a triple for a non-DocType target.  Both are legitimate and
	neither reads well, so the shape is resolved once here and both
	`apply_app_sidebar` and the tests walk the result.  Normalising in two places
	is how they drifted before.
	"""
	out = []
	for title, sub_sections in APP_SIDEBAR_SECTIONS:
		if sub_sections and not _is_group(sub_sections[0]):
			flat = [(None, tuple(sub_sections))]
		else:
			flat = list(sub_sections)
		out.append(
			(
				title,
				[
					(
						sub_title,
						tuple(
							entry if isinstance(entry, tuple) else (entry, "DocType", entry)
							for entry in links
						),
					)
					for sub_title, links in flat
				],
			)
		)
	return out


def panel_links() -> list:
	"""Every link the panel asks for, as (place, label, link_type, link_to, url).

	`place` is "Section / Sub-section" where there is one: `panel_label` disambiguates
	by it, and two rows may legitimately share a name in different sections while
	clashing inside one.

	`url` is the fifth field because a `URL` row carries its address there and nowhere
	else - `link_to` is a Dynamic Link validated against `link_type`, and "URL" is not
	a DocType, so a value there is rejected outright.  It is empty for every other
	link type.
	"""
	out = []
	for title, groups in panel_sections():
		for sub_title, links in groups:
			place = f"{title} / {sub_title}" if sub_title else title
			for link in links:
				label, link_type, link_to = link[0], link[1], link[2]
				url = link[3] if len(link) > 3 else None
				out.append((place, label, link_type, link_to, url))
	return out


def apply_app_sidebar() -> None:
	"""Write the app's single side panel from `APP_SIDEBAR_SECTIONS`.

	Rows are replaced rather than appended, and a section is kept only if it still
	has a link that resolves - an empty section header reads as a bug.  Every
	section is collapsed by default, so the panel opens to a single line per
	section and the whole app is on screen at once.
	"""
	# Built as plain dicts first and written in one go at the end.
	#
	# This used to open the document and then spend a second or two resolving 126
	# links before saving it, and Frappe's `check_if_latest` refused the write
	# because the row had been touched in between - by the dashboard rebuild that
	# runs just before this in after_migrate.  Holding a document open across work
	# that might write to it is the mistake; the fix is to finish reading before
	# picking it up, not to retry the save.
	rows: list[dict] = []

	# the way home, above every section - see APP_HOME_LABEL
	rows.append(
		{
			"type": "Link",
			"label": APP_HOME_LABEL,
			"link_type": APP_HOME_TARGET[0],
			"link_to": APP_HOME_TARGET[1],
			"icon": panel_icon(APP_HOME_LABEL),
		}
	)
	# the other ways in, beside Home and above every section - see APP_TOP_LINKS
	for label, link_type, link_to, *rest in APP_TOP_LINKS:
		row = {
			"type": "Link",
			"label": label,
			"link_type": link_type,
			"link_to": link_to,
			"icon": panel_icon(label),
		}
		if link_to is None and rest:
			row["url"] = rest[0]
		rows.append(row)
	written = 1 + len(APP_TOP_LINKS)
	unresolved: list[str] = []
	dropped_sections: list[str] = []

	for title, groups in panel_sections():
		resolved_groups = []
		for sub_title, links in groups:
			resolved = [link for link in links if _link_resolves(link[1], link[2])]
			for label, link_type, link_to, *_rest in links:
				if (label, link_type, link_to) not in [(l[0], l[1], l[2]) for l in resolved]:
					place = f"{title} / {sub_title}" if sub_title else title
					unresolved.append(f"{place} / {label} -> {link_type} {link_to!r}")
			if resolved:
				resolved_groups.append((sub_title, resolved))

		if not resolved_groups:
			dropped_sections.append(title)
			continue

		if title in PANEL_SPACERS:
			rows.append({"type": "Spacer"})

		rows.append(
			{
				"type": "Section Break",
				"label": title,
				"icon": SECTION_ICONS.get(title, FALLBACK_ICON),
				"collapsible": 1,
				"collapsible_column": 1,
				# every section closed: the panel is long, and one header per
				# section is the whole point of it
				"keep_closed": 1,
			},
		)

		for sub_title, resolved in resolved_groups:
			if sub_title:
				# a section inside a section.  `child` and `indent` are both needed:
				# find_nested_items() files by `child`, and sidebar_item.html only
				# draws a section header as a child when `indent == 1`.
				rows.append(
					{
						"type": "Section Break",
						"label": sub_title,
						"child": 1,
						"indent": 1,
						"icon": SUB_ICON.get(sub_title, FALLBACK_ICON),
						"collapsible": 1,
						"keep_closed": 1,
					},
				)
			for spec_label, link_type, link_to, *rest in resolved:
				url = rest[0] if rest else None
				# A DocType link is labelled from the DocType name, minus the "SDG "
				# prefix: the prefix is what keeps this app's tables from colliding with
				# stock ones in the global DocType list, and the panel already says which
				# app it is in.  Anything else keeps the label the spec gave it, because
				# there is no DocType name to derive one from - "SDG Workforce" is a
				# Dashboard called SDG Workforce, but the row should read "Dashboard".
				shown = panel_label(f"{title} / {sub_title}", link_type, spec_label, link_to)
				row = {
					"type": "Link",
					"label": shown,
					"link_type": link_type,
					"link_to": link_to,
					"icon": panel_icon(shown),
					"child": 1,
				}
				if link_to is None and url:
					row["url"] = url
				rows.append(row)
				written += 1

	# Anything this app owns must be in the panel.  Reported rather than asserted:
	# the panel is a presentation choice, and a table that fell out of it should be a
	# line in the migrate log, not a failed deploy.
	from sharm_dreams_group.workspace import CARDS

	linked = {link_to for _place, _label, _lt, link_to, _url in panel_links()}
	orphans = [n for _g, names in CARDS for n in names if n not in linked]
	if orphans:
		print(f"  sidebar: OWN TABLES MISSING FROM THE PANEL: {', '.join(orphans)}")

	# everything is read now, so the write happens straight after the read
	if frappe.db.exists("Workspace Sidebar", APP_SIDEBAR):
		sidebar = frappe.get_doc("Workspace Sidebar", APP_SIDEBAR)
		sidebar.set("items", [])
	else:
		sidebar = frappe.get_doc(
			{
				"doctype": "Workspace Sidebar",
				"sidebar_name": APP_SIDEBAR,
				"title": APP_SIDEBAR,
				"app": "sharm_dreams_group",
				"module": DASHBOARD_MODULE,
				"standard": 0,
			}
		)
	for row in rows:
		sidebar.append("items", row)
	sidebar.app = "sharm_dreams_group"
	sidebar.module = DASHBOARD_MODULE
	sidebar.save(ignore_permissions=True)

	# an earlier version shipped a second sidebar on the same app; the panel is
	# picked by `app`, so leaving it makes the visible one arbitrary
	stale = frappe.db.get_all(
		"Workspace Sidebar",
		filters={"app": "sharm_dreams_group", "name": ["!=", APP_SIDEBAR]},
		pluck="name",
		limit_page_length=0,
	)
	for name in stale:
		frappe.delete_doc("Workspace Sidebar", name, ignore_permissions=True, force=True)

	print(
		f"  sidebar: {APP_SIDEBAR} {written} links across {len(APP_SIDEBAR_SECTIONS) - len(dropped_sections)} sections"
	)
	if stale:
		print(f"  sidebar: removed duplicate(s) for this app: {', '.join(stale)}")
	if dropped_sections:
		print(f"  sidebar: sections with nothing to show, omitted: {', '.join(dropped_sections)}")
	for item in unresolved:
		print(f"  sidebar: NOT LINKED, target does not exist: {item}")
