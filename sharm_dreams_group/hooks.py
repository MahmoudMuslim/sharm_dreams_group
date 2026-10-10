app_name = "sharm_dreams_group"
app_title = "Sharm Dreams Group"
app_publisher = "Sharm Dreams Group"
app_description = "Sharm Dreams Group customisations for Frappe: Egyptian payroll, insurance and tax bands, group cost allocation, staff loans, and related HR customisations"
app_email = "hr@sharmdreamsgroup.com"
# Resolved last, after Website Settings and Navbar Settings, both of which are
# unset here. Without it the desk shows the Frappe framework logo.
app_logo_url = "/assets/sharm_dreams_group/images/logo.png"
app_license = "agpl-3.0"

# Where this app opens from the desk app switcher.  `boot.app_data` prefers this
# hook over deriving the route from the first workspace it finds, and falls back
# to an empty string when neither is available - which is how an app ends up
# listed but unclickable.  HRMS declares the same hook.
app_home = "/desk/sharm-dreams-group"

required_apps = ["frappe/erpnext/hrms"]

# ------------------------------------------------------------------------------
# Employee portal
# ------------------------------------------------------------------------------
# The employee portal at `/employee-portal` is the single self-service surface.
# It uses page renderers (not `/api/method`) so it works on benches running the
# `press` app, which rejects `/api/` paths for non-system users.
#
# Two routes: `/employee-portal-api` for actions, `/employee-portal/setlang` for language.
# Both are defined in `sharm_dreams_group.portal.renderers.PortalApiRenderer` and
# `PortalLangRenderer`. Authorisation is via the `Employee Self Service Session`
# token minted at sign-in (National ID + Employee Number).
page_renderer = [
	"sharm_dreams_group.portal.renderers.PortalApiRenderer",
	"sharm_dreams_group.portal.renderers.PortalLangRenderer",
]

# ------------------------------------------------------------------------------
# Desk behaviour
# ------------------------------------------------------------------------------
# `dashboard_widgets` lets a report-backed chart run without a Company at all.
# A report-backed chart takes its filters from the *report*, not from the chart,
# and ERPNext's reports pin Company as mandatory with a user default - so a chart
# stays single-company even after the chart's own company filter is removed, and
# errors out entirely when no Company is set.
#
# `company_tree` is the complement: when a Company *is* chosen, it means the
# company and everything under it, so selecting the group parent reports the
# whole group instead of nothing.
# `employee_penalty` colours the Employee list from the penalty level the server
# already derived, so the list matches the number without a second source of
# truth.
# One bundle, not five files, and it MUST be the `.bundle.js` name.
#
# `desk.html` renders each entry through `include_script` -> `bundled_asset`:
#
#     if ".bundle." in path and not path.startswith("/assets"):
#         path = bundled_assets.get(path) or path
#     return abs_url(path)
#
# A `*.bundle.js` name is looked up in `assets.json` and rewritten to a hashed
# output path, so changing a source file changes the URL.  Anything else is passed
# to `abs_url()` unchanged - which is why an earlier version of this hook, listing
# bare filenames, made the desk emit `src="/sidebar_sections.js"` and 404: all five
# files were dead, silently, with no exception and nothing in the console.
#
# It has to be hashed, not merely valid.  nginx serves everything under `/assets/`
# with `Cache-Control: max-age=31536000` - a year, unconditionally - so a stable
# URL is a file the browser never asks for again.  With `/assets/...` paths a fix
# could sit on disk, be served correctly, and still never reach the browser; the
# desk kept running code whose line numbers no longer existed.  Only `bundled_asset`
# adds the hash.
#
# The bundle's contents are listed in `public/js/sharm_dreams_group.bundle.js`; a
# test checks the two lists agree.
app_include_js = [
	"sharm_dreams_group.bundle.js",
]

# Two stylesheets, because they are two surfaces with two different blast radii.
# `.bundle.css` for the same reason as the JS: nginx caches `/assets/` for a year,
# so only a hashed URL is ever refetched.  Scoped to `.dashboard-page` in the file
# itself, because the desk shares its widget styles with every other surface.
#
# `sharm_sidebar.bundle.css` is the side panel, scoped to `.body-sidebar-container`
# for the same reason: it is the framing shell the desk draws around every page, so
# an unscoped rule there follows the user onto a List and a Form.  It is a separate
# file rather than a section of the first so that neither file's scoping claim has
# to be diluted to describe the other.
app_include_css = [
	"sharm_dreams_group.bundle.css",
	"sharm_sidebar.bundle.css",
]

# There is deliberately no `override_whitelisted_methods` entry for
# `query_report.run`, although widening a report's company filter server-side
# looks like the obvious way to make a group parent work in a report opened
# directly.  It does not work: ERPNext's reports disagree about what a company
# filter may contain.  `frappe.get_list` accepts ("in", [companies]), but a
# script report that expands the company itself - `item_wise_sales_history` is
# one - needs a bare company name and emits invalid SQL from the pair form.
# Widening centrally therefore breaks the reports that already handle it, so
# each report has to be dealt with on its own terms.

# ------------------------------------------------------------------------------
# Egypt-specific statutory logic
# ------------------------------------------------------------------------------
# HRMS exposes a single regional hook on the Salary Slip for country specific
# deductions.  It runs inside SalarySlip.calculate_net_pay() at step 6, i.e.
# AFTER earnings and deductions have been resolved and lending has had its turn,
# and BEFORE employer contributions are computed and set_net_pay() runs.  That is
# the correct place to inject the Egyptian income tax chain.
#
# Region is resolved from Company.country, so this only activates for companies
# whose country is Egypt.
#
# The key MUST be `module.function` with no class name.  hrms.allow_regional
# builds it as:
#     function_path = f"{inspect.getmodule(fn).__name__}.{fn.__name__}"
# and then compares it verbatim.  Adding ".SalarySlip" makes the override silently
# never fire, which in a payroll app means no tax at all with no error.
regional_overrides = {
	"Egypt": {
		"hrms.payroll.doctype.salary_slip.salary_slip.apply_regional_deductions": (
			"sharm_dreams_group.payroll.egypt.deductions.apply_egypt_deductions"
		)
	}
}

# ------------------------------------------------------------------------------
# Document events
# ------------------------------------------------------------------------------
# Only Employee is hooked so far.  It recomputes the derived tenure fields and
# splits the master's packed next-of-kin cell, so those values cannot go stale
# when someone edits a joining date by hand.
#
# The group-allocation hooks (Salary Slip / Payroll Entry) are deliberately NOT
# registered until `SDG Group Allocation` exists: a hook pointing at a module
# that does not exist raises when the event fires, i.e. in the middle of a
# payroll run rather than at install time.
# The "Build SDG Salary Slips" button on a Payroll Entry resolves its action
# against the document - the desk calls `getattr(doc, options)` - so HRMS's
# controller has to grow the method.  The build itself lives in payroll/run.py.
override_doctype_class = {
	"Payroll Entry": "sharm_dreams_group.payroll.payroll_entry.SDGPayrollEntry",
}

# A button's action is dispatched through `run_doc_method`, which refuses to call
# anything absent from the doctype's whitelist - the desk reports that as
# "Function ... is not whitelisted" and, before it, "You are not permitted to
# access this resource".  Declaring the method here is the supported way to add it
# to a doctype this app does not own; the decorator on the method covers the
# module-function route.
override_whitelisted_methods = {
	"Payroll Entry": ["build_for_entry"],
}

doc_events = {
	# A new user should land on this app's workspace like everyone else, rather
	# than on ERPNext's public "Home" where the app looks absent.
	"User": {"after_insert": "sharm_dreams_group.workspace.on_user_create"},
	"Employee": {
		"validate": [
			"sharm_dreams_group.hr.employee.validate_employee",
			# Recomputes the penalty level from the recorded penalties.  Separate
			# from the hook above because it is a different concern that happens to
			# need the same event, and because it is the part that has to be right
			# for the list view to colour correctly.
			"sharm_dreams_group.hr.penalty.validate_employee",
			# Recomputes the children count and the monthly school fee total.
			"sharm_dreams_group.hr.children.validate_employee",
		],
	},
	"Job Applicant": {
		"before_insert": [
			"sharm_dreams_group.hr.job_opening.process_screening_answers",
		],
	},
}

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
# Role used by HR/payroll staff to maintain the band tables and settings.
fixtures = [
	{
		"doctype": "Role",
		"filters": [["role_name", "in", ["SDG Payroll Manager", "SDG Payroll User"]]],
	},
]

# ------------------------------------------------------------------------------
# Re-applied after every migrate
# ------------------------------------------------------------------------------
# Re-applied on every migrate, and idempotent:
#  * `create_custom_fields` - a field added to the specs in setup.py has to reach
#    an already-installed site; otherwise it silently never appears.
#  * `apply_workspace` - the links to the SDG workspace from `HR Setup`,
#    `Payroll` and `Tax & Benefits` are appended to HRMS's own workspace
#    documents, and an HRMS upgrade rewrites those, dropping the entry points
#    with no error.
after_migrate = "sharm_dreams_group.setup.after_migrate"

# ------------------------------------------------------------------------------
# App switcher
# ------------------------------------------------------------------------------
# Without this the app has no icon on the apps screen (the grid on the left), so
# a public workspace belonging to it is reachable only by typing the URL.
# `app_route` points at the workspace, so the icon lands somewhere useful.
add_to_apps_screen = [
	{
		"name": "sharm_dreams_group",
		"logo": "/assets/sharm_dreams_group/images/logo.png",
		"title": "Sharm Dreams Group",
		"route": "/desk/sharm-dreams-group",
		"has_permission": "sharm_dreams_group.workspace.has_app_permission",
	}
]

# ------------------------------------------------------------------------------
# Notes on design decisions (kept here deliberately, they are easy to lose)
# ------------------------------------------------------------------------------
# * One payroll run per paying company.  An employee shared across group
#   companies gets a single Salary Slip; the delegated cost shares are carried
#   as Additional Salary rows against the consuming company.
# * A row that has a Delegated Company is NOT the statutory employment.  It
#   therefore gets no personal exemption and is taxed at a flat 10% (the
#   group company's own withholding).  See payroll/egypt/deductions.py.
# * The staff loan deduction is written to the `total_loan_repayment` field on
#   the Salary Slip.  HRMS's own set_net_pay() already subtracts that field
#   *after* total_deduction, which reproduces the spreadsheet treatment where
#   loans do not reduce taxable income.  No custom arithmetic required.


# ------------------------------------------------------------------------------
# Scheduler
# ------------------------------------------------------------------------------
scheduler_events = {
	"daily": [
		"sharm_dreams_group.portal.compensatory.process_daily_compensatory_earnings"
	]
}

def boot_session(bootinfo):
	"""Tell the desk, once per page load, whether this user may see penalties.

	The permitted roles are resolved server-side so the browser cannot drift from
	the rule: the desk reads the answer rather than reimplementing the check.
	"""
	from sharm_dreams_group.hr.penalty import can_view_penalties, configured_roles

	bootinfo.sdg_can_view_penalties = can_view_penalties()
	bootinfo.sdg_penalty_roles = configured_roles()
