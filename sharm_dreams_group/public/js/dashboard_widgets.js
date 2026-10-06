// Copyright (c) 2026, Sharm Dreams Group and contributors
// For license information, please see license.txt

// Lets reports run without a Company being set.
//
// A report does not take its filters from whatever is asking for them.  Frappe asks
// the *report* for them, and ERPNext's reports declare Company as `reqd: 1` with
// `default: frappe.defaults.get_user_default("Company")`.  So a widget or a report
// is single-company whether or not anything asked for it, and with no Company set
// at all the run dies on "Please set filters".
//
// This drops both the `reqd` flag and the `default` for the Company filter of the
// reports listed below, so an unset Company means "all companies" and a user can
// still type one in to narrow it.
//
// Two seams, one list.  `frappe.report_utils.get_report_filters` is what a Dashboard
// Chart asks; the report *page* never calls it - it evaluates the report's own JS
// and reads `frappe.query_reports[name].filters` (query_report.js
// `get_report_settings`).  Relaxing only the first left every report opened from the
// panel still demanding a company, which is the other half of the same problem.
//
// The filter is *removed*, not replaced with the group parent.  A group parent is
// only correct for the reports whose SQL `upstream_patches.py` rewrites; for the
// rest `company = 'Sharm Dreams Group'` matches no row on any company-scoped
// doctype, because the parent holds no data.  Measured on this site: Employee Leave
// Balance returns 3410 rows with no company and **0** with the parent; Employee
// Exits 130 and 0; Salary Register 171 and 0.  Defaulting to the parent would have
// turned four working reports into empty ones.
//
// Every name here has been run server-side with a blank Company
// (`dashboard_widgets.verify_panel_reports`, asserted in test_dashboard_widgets.py),
// so a report that starts demanding one is caught rather than silently blank.

// "Loan Outstanding Report" was here once and is not now. It belongs to the `lending`
// app, which is present on this bench in apps.txt but NOT installed on this site -
// `lending` is absent from `installed_apps`, and none of its doctypes (Loan, Loan
// Application, Loan Repayment) exist. A name on this list that resolves to no Report
// is worse than a missing entry: relaxing a filter that has no report behind it means
// the card opens nothing and says nothing about why.
const COMPANY_OPTIONAL_REPORTS = new Set([
	"Accounts Payable",
	"Accounts Receivable",
	"Appraisal Overview",
	"Budget Variance Report",
	"Downtime Analysis",
	"Fixed Asset Register",
	"Item Shortage Report",
	"Job Card Summary",
	"Profit and Loss Statement",
	"Project Summary",
	"Purchase Order Analysis",
	"Quality Inspection Summary",
	"Sales Order Analysis",
	"Stock Ageing",
	"Work Order Summary",
	// unblocked by upstream_patches.py
	"Delivery Note Trends",
	"Item-wise Sales History",
	"Monthly Attendance Sheet",
	"Purchase Order Trends",
	"Purchase Receipt Trends",
	"Sales Order Trends",
	// Opened from the panel's Reports band.  Verified blank-company on this site;
	// see verify_panel_reports for the probe and for the ones that are NOT here
	// because they need a company, or need a companion filter, or are broken.
	"Accounts Payable Summary",
	"Accounts Receivable Summary",
	"Accrued Earnings Report",
	"Bank Reconciliation Statement",
	"Bank Remittance",
	"Daily Work Summary Replies",
	"Employee Advance Summary",
	// these two used to be in the "needs a company" bucket: their own Python threw
	// "Company is mandatory" before any filter was applied, so no client-side
	// relaxing could reach them.  upstream_patches.py now widens the subtree in
	// both, and they are here because the report tolerates a blank Company - not
	// merely because someone removed the asterisk.
	"Employee Analytics",
	"Employee Birthday",
	"Employee Exits",
	"Employee Information",
	"Employee Leave Balance",
	"Employee Leave Balance Summary",
	"Employees working on a holiday",
	"Income Tax Deductions",
	"Leave Ledger",
	// Professional Tax needed a fix before it could be here at all, and the fix is
	// worth recording: `component_type` is a regional Custom Field that only
	// `hrms/regional/india/setup.py` adds, so on this Egyptian site the column has
	// never existed and the report raised "Unknown column 'component_type'" from
	// inside its own query. `upstream_patches.py` now guards it the way its sibling
	// `provident_fund_deductions` already did, and the report returns empty - the
	// true answer, since no Salary Component here is a professional tax. Only then
	// could it be run blank-company and found not to need one.
	"Professional Tax Deductions",
	// The entry itself, after the note above: measured group-wide against the sum of
	// all 13 company runs to the penny, with a leaf still answering for itself only.
	"General Ledger",
	// The last one, and the only one that needed measuring before it could be
	// listed rather than merely unblocked. General Ledger reads the company in three
	// separate places and compared for equality in all of them, so widening it the
	// way the rest are widened made it run and return almost nothing: the group
	// parent has a chart of accounts and no ledger. What made it safe is that this
	// group needs no reconciliation at all -
	//
	//   * every company carries an IDENTICAL chart of accounts (Accrued Expenses and
	//     the rest appear exactly 14 times), so there is no 13-way union to merge;
	//   * the currency is EGP on every company, so summing across them is a real
	//     number rather than two units added together;
	//   * the 462 GL entries sit in all 13 leaf companies, 30-40 each;
	//   * and no GL entry carries a party, so nothing is counted twice.
	//
	// The group answer then equals the sum of all 13 company runs to the penny,
	// which is asserted rather than assumed. The fourth bullet is the fragile one
	// and has its own test: an intercompany posting lands in both sets of books,
	// this report eliminates nothing, and the day that stops being true is the day
	// the number becomes wrong in a way no assertion here would notice.
	//
	// The last group, company-optional but each insisting on a filter of its own
	// first: Trial Balance on a fiscal year, Employee Leave Balance on a date
	// range.  The desk still asks for those, and the report then answers for the
	// whole group.  Verified by verify_panel_reports.
	"Employee Leave Balance",
	"Provident Fund Deductions",
	"Recruitment Analytics",
	"Salary Payments Based On Payment Mode",
	"Salary Register",
	"Shift Attendance",
	"Trial Balance",
]);

function relax_company_filter(report_name, filters) {
	if (!COMPANY_OPTIONAL_REPORTS.has(report_name) || !Array.isArray(filters)) {
		return filters;
	}
	return filters.map((filter) => {
		if (filter.fieldname !== "company") {
			return filter;
		}
		const relaxed = { ...filter };
		// query_report.get_filter_value throws "Please set filters" on a reqd
		// filter with no value.
		delete relaxed.reqd;
		delete relaxed.mandatory;
		// A default would quietly re-scope the report to one company, which is
		// the thing this file exists to undo.
		delete relaxed.default;
		return relaxed;
	});
}

function patch_get_report_filters() {
	const utils = frappe.report_utils;
	if (!utils || typeof utils.get_report_filters !== "function" || utils.get_report_filters._sdg) {
		return;
	}
	const original = utils.get_report_filters;
	const wrapped = function (report_name) {
		return original.call(this, report_name).then((filters) => relax_company_filter(report_name, filters));
	};
	wrapped._sdg = true;
	utils.get_report_filters = wrapped;
}

// The report page's own seam.
//
// `get_report_settings` is wrapped rather than `setup_filters` because it is the
// last point at which `report_settings.filters` can be changed before
// `setup_filters` reads it (`const { filters = [] } = this.report_settings`) and
// builds the fields from it.  Wrapping `setup_filters` would mean reaching into
// `this.filters` after the field controls already exist.
//
// Both branches of the stock method are covered - the cached-settings path and the
// `get_script` path - because the wrapper rewrites whatever `report_settings` ends
// up holding, rather than caring where it came from.
function patch_report_page() {
	const views = frappe.views;
	if (!views || !views.QueryReport) return;
	const proto = views.QueryReport.prototype;
	if (typeof proto.get_report_settings !== "function" || proto.get_report_settings._sdg) {
		return;
	}
	const original = proto.get_report_settings;
	const wrapped = function () {
		return original.apply(this, arguments).then((done) => {
			const settings = this.report_settings;
			if (settings && Array.isArray(settings.filters)) {
				settings.filters = relax_company_filter(this.report_name, settings.filters);
			}
			return done;
		});
	};
	wrapped._sdg = true;
	proto.get_report_settings = wrapped;
}

// app_include_js files load in bundle order, which is not guaranteed relative to
// report_utils.js or query_report.js, so patch on ready and again once more if
// either was not there yet.
function init() {
	patch_get_report_filters();
	patch_report_page();
	if (!(frappe.report_utils && frappe.report_utils.get_report_filters)) {
		frappe.after_ajax(patch_get_report_filters);
	}
	if (!(frappe.views && frappe.views.QueryReport)) {
		frappe.after_ajax(patch_report_page);
	}
}

// `$(fn)` is jQuery's ready.  It replaces `frappe.dom_ready(...)`, which no longer
// exists: this call threw at load time, so every patch in this file was silently
// absent and the dashboard's cards rendered as plain numbers with no drill-down.
$(init);
